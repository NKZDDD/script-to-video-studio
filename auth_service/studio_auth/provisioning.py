"""Per-user provider credentials and untrusted client usage reports."""
import csv
import io
import json
import uuid
from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Header, Request
from fastapi.responses import Response
from pydantic import Field, field_validator

from .admin import Body, current, record, target_name, WEB
from fastapi.responses import FileResponse
from .service import AuthError

router = APIRouter()
KEY_FIELDS = ('api_key', 'image_1k_api_key', 'image_4k_api_key', 'video_api_key')


@router.get('/admin/provisioning.js')
def javascript():
    return FileResponse(WEB/'provisioning.js', media_type='text/javascript')


class ProviderConfig(Body):
    provider: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,64}$')
    base_url: str = Field(default='', max_length=500)
    api_key: str = Field(default='', max_length=8000)
    image_1k_api_key: str = Field(default='', max_length=2000)
    image_4k_api_key: str = Field(default='', max_length=2000)
    video_api_key: str = Field(default='', max_length=2000)
    remove: bool = False

    @field_validator('base_url')
    @classmethod
    def url(cls, value):
        value = value.strip().rstrip('/')
        if value:
            u = urlsplit(value)
            if u.scheme != 'https' or not u.hostname or u.username or u.password or u.query or u.fragment:
                raise ValueError('Base URL must be HTTPS without credentials or query')
        return value


def user_id(auth, name):
    with auth.store.connect() as db:
        row = db.execute('SELECT id FROM users WHERE username=?', (target_name(name),)).fetchone()
    if not row:
        raise AuthError(404, 'user_missing', '账号不存在')
    return row['id']


def configuration(auth, uid):
    from .key_library import configuration as library_configuration
    with auth.store.connect() as db:
        return library_configuration(db, uid)



@router.get('/admin/api/users/{name}/providers')
def providers(name: str, request: Request):
    current(request)
    auth = request.app.state.auth
    configs = configuration(auth, user_id(auth, name))
    return {'providers': [{ 'provider': pid, 'base_url': cfg.get('base_url', ''),
                           'configured': [k for k in KEY_FIELDS if cfg.get(k)],
                           'assignment_id': cfg.get('assignment_id', '')} for pid, cfg in configs.items()]}


@router.post('/admin/api/users/{name}/providers')
def assign(name: str, body: ProviderConfig, request: Request):
    actor, _ = current(request, csrf=True)
    auth = request.app.state.auth
    uid = user_id(auth, name)
    from .provider_names import LLM_PROVIDER
    if body.provider == LLM_PROVIDER:
        raise AuthError(422, 'use_key_library', '提示词改写 LLM 请通过 Key 库配置并分配')
    # Compatibility endpoint for old admin clients. Never edit a shared library entry.
    with auth.store.connect(write=True) as db:
        rows = db.execute('SELECT k.* FROM user_keys a JOIN key_library k ON k.id=a.key_id '
                          'WHERE a.user_id=? AND k.provider=?', (uid, body.provider)).fetchall()
        if len(rows) > 1 and not body.remove:
            raise AuthError(409, 'use_key_library', '该服务商有多把 Key，请使用 Key 库编辑')
        if not body.remove:
            cfg = json.loads(rows[0]['config']) if rows else {}
            cfg['base_url'] = body.base_url
            for k in KEY_FIELDS:
                if getattr(body, k).strip(): cfg[k] = getattr(body, k).strip()
            if not any(cfg.get(k) for k in KEY_FIELDS):
                raise AuthError(422, 'key_required', '首次分配至少填写一把 Key')
            count = db.execute('SELECT COUNT(DISTINCT k.provider) FROM user_keys a JOIN key_library k '
                               'ON k.id=a.key_id WHERE a.user_id=?', (uid,)).fetchone()[0]
            if not rows and count >= 48:
                raise AuthError(422, 'assignment_limit', '每个账号最多分配 48 家服务商')
        db.execute('DELETE FROM user_keys WHERE user_id=? AND key_id IN '
                   '(SELECT id FROM key_library WHERE provider=?)', (uid, body.provider))
        if not body.remove:
            kid = str(uuid.uuid4())
            db.execute('INSERT INTO key_library VALUES(?,?,?,?,?,?,?)',
                       (kid, body.provider, name+' ? '+body.provider, json.dumps(cfg), str(uuid.uuid4()), 1, auth.now()))
            db.execute('INSERT INTO user_keys VALUES(?,?,?)', (uid, kid, 0))
    record(request, actor, 'remove_provider' if body.remove else 'assign_provider', name + '/' + body.provider)
    return {'ok': True}


class Usage(Body):
    event_id: str = Field(pattern=r'^[a-f0-9-]{36}$')
    sequence: int = Field(ge=1, le=1000000)
    provider: str = Field(min_length=1, max_length=64)
    model: str = Field(max_length=200)
    kind: Literal['image', 'video']
    source: Literal['managed', 'personal', 'unknown']
    assignment_id: str = Field(default='', max_length=64)
    key_id: str = Field(default='', max_length=64)
    key_name: str = Field(default='', max_length=100)
    task_id: str = Field(default='', max_length=200)
    status: Literal['attempted', 'submitted', 'completed', 'failed', 'pending', 'unknown']
    resumed: bool = False
    started_at: int = Field(ge=0, le=253402300799)
    updated_at: int = Field(ge=0, le=253402300799)


class UsageReport(Body):
    device_id: str = Field(min_length=16, max_length=128)
    events: list[Usage] = Field(min_length=1, max_length=20)


@router.post('/api/v1/auth/usage')
def report(body: UsageReport, request: Request, authorization: str = Header(default='', max_length=256)):
    auth = request.app.state.auth
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != 'bearer':
        raise AuthError(401, 'invalid_session', '请先登录')
    result = auth.verify(parts[1], body.device_id)
    uid = result['user']['id']
    auth.rate([('usage:' + uid, 3000)])
    with auth.store.connect(write=True) as db:
        for event in body.events:
            old = db.execute('SELECT sequence,payload FROM usage_reports WHERE user_id=? AND event_id=?',
                             (uid, event.event_id)).fetchone()
            payload = event.model_dump()
            if old:
                previous = json.loads(old['payload'])
                if any(previous.get(k, '') != payload[k] for k in ('provider', 'model', 'kind', 'source', 'assignment_id', 'key_id', 'key_name', 'started_at', 'resumed')):
                    raise AuthError(409, 'usage_conflict', '用量记录标识冲突')
                if old['sequence'] >= event.sequence:
                    continue
            db.execute('INSERT INTO usage_reports VALUES(?,?,?,?,?) ON CONFLICT(user_id,event_id) '
                       'DO UPDATE SET sequence=excluded.sequence,payload=excluded.payload,received_at=excluded.received_at',
                       (uid, event.event_id, event.sequence, json.dumps(payload), auth.now()))
    return {'ok': True, 'accepted': [e.event_id for e in body.events]}


@router.get('/admin/api/users/{name}/usage')
def usage(name: str, request: Request, page: int = 1, provider: str = '', source: str = '', key_id: str = '',
          since: int = 0, until: int = 253402300799, export: bool = False):
    current(request)
    auth = request.app.state.auth
    uid = user_id(auth, name)
    if page < 1 or page > 1000000 or since < 0 or until < since or source not in ('', 'managed', 'personal', 'unknown'):
        raise AuthError(422, 'invalid_filter', '筛选条件不正确')
    # JSON fields are fixed expressions; all supplied values are bound parameters.
    where = "user_id=? AND json_extract(payload,'$.started_at') BETWEEN ? AND ?"
    args = [uid, since, until]
    if provider:
        where += " AND json_extract(payload,'$.provider')=?"
        args.append(provider[:64])
    if key_id:
        where += " AND json_extract(payload,'$.key_id')=?"
        args.append(key_id[:64])
    if source:
        where += " AND json_extract(payload,'$.source')=?"
        args.append(source)
    with auth.store.connect() as db:
        total = db.execute('SELECT COUNT(*) FROM usage_reports WHERE '+where, args).fetchone()[0]
        summary = db.execute("SELECT json_extract(payload,'$.provider') provider,json_extract(payload,'$.model') model,"
                             "json_extract(payload,'$.source') source,json_extract(payload,'$.status') status,"
                             "json_extract(payload,'$.key_id') key_id,json_extract(payload,'$.key_name') key_name,"
                             "json_extract(payload,'$.resumed') resumed,COUNT(*) count FROM usage_reports WHERE "+where+
                             " GROUP BY provider,model,source,status,resumed,key_id,key_name", args).fetchall()
        rows = db.execute('SELECT payload,received_at FROM usage_reports WHERE '+where+
                          ' ORDER BY received_at DESC,event_id LIMIT ? OFFSET ?',
                          args + [10000 if export else 50, 0 if export else (page-1)*50]).fetchall()
    # Legacy reports have no Key identity; do not infer one.
    events = [dict({'key_id': '', 'key_name': ''}, **json.loads(r['payload']), received_at=r['received_at']) for r in rows]
    if export:
        if total > 10000:
            raise AuthError(422, 'export_too_large', '超过 10000 条，请缩小时间范围后导出')
        fields = list(Usage.model_fields) + ['received_at']
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(fields)
        for event in events:
            writer.writerow([("'"+str(event[k])) if str(event[k]).lstrip().startswith(('=', '+', '-', '@')) else event[k] for k in fields])
        return Response('\ufeff'+output.getvalue(), media_type='text/csv', headers={'Content-Disposition': 'attachment; filename="studio-usage.csv"'})
    return {'events': events, 'summary': [dict(r) for r in summary], 'total': total, 'page': page,
            'notice': '客户端上报，仅供对账；生成尝试、成功落盘不等于服务商实际计费。旧任务取回不计作新生成。'}
