"""Admin-distributed provider code. Stored and validated here; executed only on clients."""
import json
from fastapi import APIRouter, Header, Request
from fastapi.responses import FileResponse
from pydantic import Field

from .admin import Body, WEB, current, record
from .adapter_format import PROTOCOL, digest, encode, select, validate
from .service import AuthError

router = APIRouter()


def _active(db):
    row = db.execute('SELECT b.payload FROM adapter_policy p JOIN adapter_bundles b ON b.sha256=p.sha256 WHERE p.id=1').fetchone()
    return json.loads(row['payload']) if row else None


def assigned_bundle(auth, uid):
    from .key_library import configuration
    with auth.store.connect() as db:
        bundle = _active(db)
        cfg = configuration(db, uid)
    if bundle is None:
        return None, []
    ids = {pid for pid, item in cfg.items() if item.get('keys')}
    selected = select(bundle, ids)
    missing = sorted(ids - {p['id'] for p in selected['providers']})
    return selected, missing


def configuration(auth, uid):
    bundle, missing = assigned_bundle(auth, uid)
    if bundle is None:
        return {'enabled': False}
    return {'enabled': True, 'protocol': PROTOCOL, 'version': bundle['version'],
            'sha256': digest(bundle), 'providers': [p['id'] for p in bundle['providers']], 'missing': missing}


@router.get('/admin/adapters.js')
def javascript():
    return FileResponse(WEB / 'adapters.js', media_type='text/javascript')


@router.get('/admin/adapters.css')
def stylesheet():
    return FileResponse(WEB / 'adapters.css', media_type='text/css')


@router.get('/admin/api/adapters')
def inventory(request: Request):
    current(request)
    with request.app.state.auth.store.connect() as db:
        active = db.execute('SELECT sha256 FROM adapter_policy WHERE id=1').fetchone()
        rows = db.execute('SELECT sha256,version,providers,created_at FROM adapter_bundles ORDER BY created_at DESC, rowid DESC').fetchall()
    return {'active': active['sha256'] if active else '', 'bundles':
            [dict(row, providers=json.loads(row['providers'])) for row in rows]}


class Import(Body):
    bundle: dict


@router.post('/admin/api/adapters/import')
def import_bundle(body: Import, request: Request):
    actor, _ = current(request, csrf=True)
    try:
        bundle = validate(body.bundle)
        if not bundle['providers']:
            raise ValueError('不能发布空适配包')
    except (ValueError, TypeError, KeyError, RecursionError) as exc:
        raise AuthError(422, 'adapter_invalid', str(exc)[:200]) from None
    checksum = digest(bundle)
    auth = request.app.state.auth
    with auth.store.connect(write=True) as db:
        db.execute('INSERT OR IGNORE INTO adapter_bundles VALUES(?,?,?,?,?)',
                   (checksum, bundle['version'], json.dumps(bundle['providers'], ensure_ascii=False), encode(bundle).decode(), auth.now()))
    record(request, actor, 'import_adapters', checksum)
    return {'ok': True, 'sha256': checksum, 'version': bundle['version'], 'providers': bundle['providers']}


class Publish(Body):
    sha256: str = Field(pattern=r'^[a-f0-9]{64}$')


@router.post('/admin/api/adapters/publish')
def publish(body: Publish, request: Request):
    actor, _ = current(request, csrf=True)
    with request.app.state.auth.store.connect(write=True) as db:
        if not db.execute('SELECT 1 FROM adapter_bundles WHERE sha256=?', (body.sha256,)).fetchone():
            raise AuthError(404, 'adapter_missing', '请先导入适配包')
        db.execute('INSERT INTO adapter_policy VALUES(1,?) ON CONFLICT(id) DO UPDATE SET sha256=excluded.sha256', (body.sha256,))
    record(request, actor, 'publish_adapters', body.sha256)
    return {'ok': True}


class Download(Publish):
    device_id: str = Field(min_length=16, max_length=128)


@router.post('/api/v1/auth/adapters')
def download(body: Download, request: Request, authorization: str = Header(default='', max_length=256)):
    from .releases import require_client
    require_client(request)
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != 'bearer' or not 40 <= len(parts[1]) <= 128:
        raise AuthError(401, 'invalid_session', '请先登录')
    auth = request.app.state.auth
    result = auth.verify(parts[1], body.device_id)
    auth.rate([('adapters:'+result['user']['id'], 300)])
    bundle, missing = assigned_bundle(auth, result['user']['id'])
    if bundle is None or missing or digest(bundle) != body.sha256:
        raise AuthError(409, 'adapters_changed', '适配包或账号授权已改变，请刷新后台配置；缺少适配器时请管理员补充发布')
    return {'bundle': bundle}
