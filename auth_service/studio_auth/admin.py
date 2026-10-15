"""Browser administration, with separate principals and HTTPS-only cookie sessions."""
from contextlib import contextmanager
from pathlib import Path
import secrets
import sqlite3

from argon2.exceptions import VerificationError, InvalidHashError
from fastapi import APIRouter, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .service import AuthError, PASSWORDS, PASSWORD_SLOTS, digest, username, validate_password

COOKIE = '__Host-studio_admin'
WEB = Path(__file__).parent / 'web'
router = APIRouter()


@contextmanager
def password_slot():
    if not PASSWORD_SLOTS.acquire(blocking=False):
        raise AuthError(503, 'auth_busy', '服务繁忙，请稍后重试')
    try:
        yield
    finally:
        PASSWORD_SLOTS.release()


def set_admin(auth, name, password, *, create=False):
    name = username(name)
    validate_password(password)
    with password_slot():
        encoded = PASSWORDS.hash(password)
    with auth.store.connect(write=True) as db:
        if create:
            db.execute('INSERT INTO administrators VALUES(?,?)', (name, encoded))
        else:
            if not db.execute('SELECT 1 FROM administrators WHERE username=?', (name,)).fetchone():
                raise ValueError('管理员不存在')
            db.execute('UPDATE administrators SET password_hash=? WHERE username=?', (encoded, name))
        db.execute('DELETE FROM admin_sessions WHERE username=?', (name,))


class Body(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class Credentials(Body):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=1, max_length=128)

    @field_validator('username')
    @classmethod
    def normalize(cls, value):
        return username(value)


class NewUser(Credentials):
    password: str = Field(min_length=12, max_length=128)
    expires_at: int | None = Field(default=None, ge=0, le=253402300799)
    max_devices: int = Field(default=0, ge=0, le=100000)


class UpdateUser(Body):
    enabled: bool | None = None
    expires_at: int | None = Field(default=None, ge=0, le=253402300799)
    max_devices: int | None = Field(default=None, ge=0, le=100000)


class Password(Body):
    password: str = Field(min_length=12, max_length=128)


def current(request, *, csrf=False):
    auth = request.app.state.auth
    token = request.cookies.get(COOKIE, '')
    if not 40 <= len(token) <= 128:
        raise AuthError(401, 'admin_login_required', '请先登录管理后台')
    with auth.store.connect() as db:
        row = db.execute('SELECT username FROM admin_sessions WHERE token_hash=? AND expires_at>?',
                         (digest(token), auth.now())).fetchone()
    if not row:
        raise AuthError(401, 'admin_login_required', '管理员登录已失效，请重新登录')
    expected = digest('csrf:' + token)
    if csrf and not secrets.compare_digest(request.headers.get('x-csrf-token', '').encode(), expected.encode()):
        raise AuthError(403, 'csrf_failed', '页面校验已失效，请刷新后重试')
    return row['username'], expected


def record(request, actor, action, target):
    auth = request.app.state.auth
    with auth.store.connect(write=True) as db:
        db.execute('INSERT INTO admin_audit(at,administrator,action,target) VALUES(?,?,?,?)',
                   (auth.now(), actor, action, target))


@router.get('/admin/')
@router.get('/admin')
def index():
    return FileResponse(WEB/'index.html', media_type='text/html')


@router.get('/admin/app.js')
def javascript():
    return FileResponse(WEB/'app.js', media_type='text/javascript')


@router.get('/admin/style.css')
def stylesheet():
    return FileResponse(WEB/'style.css', media_type='text/css')


@router.post('/admin/api/login')
def login(body: Credentials, request: Request, response: Response):
    auth = request.app.state.auth
    ip = request.client.host if request.client else 'unknown'
    auth.rate([('admin:ip:'+digest(ip), 20), ('admin:user:'+digest(body.username), 10)])
    with auth.store.connect() as db:
        row = db.execute('SELECT * FROM administrators WHERE username=?', (body.username,)).fetchone()
    with password_slot():
        try:
            matched = PASSWORDS.verify(row['password_hash'] if row else auth.dummy_hash, body.password)
        except (VerificationError, InvalidHashError):
            matched = False
    if not row or not matched:
        raise AuthError(401, 'invalid_credentials', '管理员账号或密码不正确')
    token = secrets.token_urlsafe(32)
    with auth.store.connect(write=True) as db:
        latest = db.execute('SELECT password_hash FROM administrators WHERE username=?', (body.username,)).fetchone()
        if not latest or latest['password_hash'] != row['password_hash']:
            raise AuthError(401, 'invalid_credentials', '管理员信息已更新，请重新登录')
        db.execute('DELETE FROM admin_sessions WHERE expires_at<=? OR token_hash=?',
                   (auth.now(), digest(request.cookies.get(COOKIE, ''))))
        db.execute('INSERT INTO admin_sessions VALUES(?,?,?)', (digest(token), body.username, auth.now()+8*3600))
    response.set_cookie(COOKIE, token, max_age=8*3600, secure=True, httponly=True, samesite='strict', path='/')
    record(request, body.username, 'login', body.username)
    return {'username': body.username, 'csrf_token': digest('csrf:'+token)}


@router.get('/admin/api/me')
def me(request: Request):
    name, csrf = current(request)
    return {'username': name, 'csrf_token': csrf}


@router.post('/admin/api/logout')
def logout(request: Request, response: Response):
    name, _ = current(request, csrf=True)
    with request.app.state.auth.store.connect(write=True) as db:
        db.execute('DELETE FROM admin_sessions WHERE token_hash=?', (digest(request.cookies[COOKIE]),))
    response.delete_cookie(COOKIE, secure=True, httponly=True, samesite='strict', path='/')
    record(request, name, 'logout', name)
    return {'ok': True}


@router.get('/admin/api/users')
def users(request: Request, q: str = '', page: int = 1):
    current(request)
    q = q.strip()[:64]
    page = max(1, min(page, 1000000))
    auth = request.app.state.auth
    with auth.store.connect() as db:
        total = db.execute('SELECT COUNT(*) FROM users WHERE instr(username,?)>0', (q,)).fetchone()[0]
        rows = db.execute('SELECT id,username,enabled,expires_at,max_devices,created_at FROM users '
                          'WHERE instr(username,?)>0 ORDER BY created_at DESC,username LIMIT 50 OFFSET ?',
                          (q, (page-1)*50)).fetchall()
    return {'users': [dict(r) for r in rows], 'total': total, 'page': page, 'server_time': auth.now()}


@router.post('/admin/api/users')
def create_user(body: NewUser, request: Request):
    actor, _ = current(request, csrf=True)
    try:
        with password_slot():
            request.app.state.auth.create_user(body.username, body.password, body.expires_at, body.max_devices)
    except sqlite3.IntegrityError:
        raise AuthError(409, 'user_exists', '该账号已存在') from None
    record(request, actor, 'create_user', body.username)
    return {'ok': True}


def target_name(value):
    try:
        return username(value)
    except ValueError:
        raise AuthError(422, 'invalid_username', '账号格式不正确') from None


def change(request, name, **values):
    try:
        request.app.state.auth.manage_user(name, **values)
    except ValueError as exc:
        raise AuthError(400, 'invalid_update', str(exc)) from None


@router.post('/admin/api/users/{name}/settings')
def update_user(name: str, body: UpdateUser, request: Request):
    actor, _ = current(request, csrf=True)
    name = target_name(name)
    values = body.model_dump(exclude_unset=True)
    if not values or any(values[k] is None for k in ('enabled', 'max_devices') if k in values):
        raise AuthError(422, 'invalid_update', '请选择要修改的设置')
    change(request, name, **values)
    record(request, actor, 'update_settings', name)
    return {'ok': True}


@router.post('/admin/api/users/{name}/password')
def password(name: str, body: Password, request: Request):
    actor, _ = current(request, csrf=True)
    name = target_name(name)
    with password_slot():
        change(request, name, password=body.password)
    record(request, actor, 'reset_password', name)
    return {'ok': True}


@router.post('/admin/api/users/{name}/revoke')
def revoke(name: str, request: Request):
    actor, _ = current(request, csrf=True)
    name = target_name(name)
    change(request, name, revoke=True)
    record(request, actor, 'revoke_sessions', name)
    return {'ok': True}


@router.get('/admin/api/users/{name}/sessions')
def sessions(name: str, request: Request):
    current(request)
    name = target_name(name)
    auth = request.app.state.auth
    with auth.store.connect() as db:
        rows = db.execute('SELECT s.id,s.device_name,s.created_at,s.last_seen_at,s.expires_at,s.revoked_at '
                          'FROM sessions s JOIN users u ON u.id=s.user_id WHERE u.username=? '
                          'ORDER BY s.created_at DESC LIMIT 100', (name,)).fetchall()
    return {'sessions': [dict(r) for r in rows], 'server_time': auth.now()}
