"""User-initiated, account-bound diagnostic reports; separate from automatic telemetry."""
import json
import re
from typing import Literal

from fastapi import APIRouter, Header, Request
from fastapi.responses import FileResponse
from pydantic import Field, field_validator

from .admin import Body, WEB, current
from .diagnostics import Event
from .service import AuthError, digest

router = APIRouter()
RETENTION = 30 * 86400
MAX_ROWS = 500
SCHEMA = '''
CREATE TABLE IF NOT EXISTS client_reports (
 id INTEGER PRIMARY KEY, user_id TEXT NOT NULL, report_id TEXT NOT NULL,
 device TEXT NOT NULL, received_at INTEGER NOT NULL, payload TEXT NOT NULL,
 UNIQUE(user_id,report_id)
);
CREATE INDEX IF NOT EXISTS client_reports_user ON client_reports(user_id,id);
'''


class Detail(Body):
    provider: str = Field(default='', max_length=160)
    model: str = Field(default='', max_length=160)
    key_source: Literal['managed', 'personal', 'unknown'] = 'unknown'
    key_id: str = Field(default='', max_length=160)
    key_fingerprint: str = Field(default='', pattern=r'^(?:[a-f0-9]{24})?$')
    task_fingerprint: str = Field(default='', pattern=r'^(?:[a-f0-9]{24})?$')
    task_id: str = Field(default='', max_length=160)
    adapter_source: Literal['server', 'builtin', 'plugin'] = 'builtin'
    adapter_hash: str = Field(default='', pattern=r'^(?:[a-f0-9]{64})?$')
    adapter_version: str = Field(default='', max_length=160)
    stage: Literal['submit', 'query', 'download', 'request', 'complete'] = 'request'
    method: str = Field(default='', pattern=r'^(?:GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)?$')
    endpoint: str = Field(default='', max_length=192)
    proxy_mode: Literal['direct', 'explicit', 'system'] = 'system'
    proxy_host: str = Field(default='', max_length=253, pattern=r'^[a-zA-Z0-9.:-]*$')
    auth_style: str = Field(default='', max_length=160)
    exception_type: str = Field(default='', max_length=80)
    error_code: str = Field(default='', max_length=80)
    error_hint: Literal['', 'key_not_found', 'authentication_rejected'] = ''
    upstream_status: int = Field(default=0, ge=0, le=599)
    http_status: int = Field(default=0, ge=0, le=599)
    provider_request_id: str = Field(default='', max_length=128)
    upstream_request_id: str = Field(default='', max_length=128)
    result_state: str = Field(default='', max_length=32)
    frames: list[str] = Field(default_factory=list, max_length=8)

    @field_validator('frames')
    @classmethod
    def safe_frames(cls, value):
        if any(len(s) > 160 for s in value):
            raise ValueError('代码位置过长')
        return [cls.safe_label(s) for s in value]

    @field_validator('provider', 'model', 'key_id', 'task_id', 'adapter_version', 'endpoint',
                     'auth_style', 'exception_type', 'error_code', 'provider_request_id', 'upstream_request_id', 'result_state')
    @classmethod
    def safe_label(cls, value):
        if (not re.fullmatch(r'[\w .:/(){}+@-]*', value) or
                re.search(r'\bsk-|bearer|secret|password|https?://', value, re.I)):
            raise ValueError('诊断标签包含不支持的内容')
        return value


class ReportEvent(Event):
    details: Detail | None = None


class Snapshot(Body):
    version: str = Field(pattern=r'^[0-9]{1,6}(?:\.[0-9]{1,6}){2,3}$')
    build: str = Field(default='', pattern=r'^(?:[a-f0-9]{64}|source|damaged)?$')
    system: Literal['Windows', 'Linux', 'Darwin', 'other'] = 'other'
    adapter_version: str = Field(default='', max_length=160)
    recording_error: bool = False
    dropped: int = Field(default=0, ge=0, le=2147483647)
    omitted: int = Field(default=0, ge=0, le=2147483647)

    _label = field_validator('adapter_version')(Detail.safe_label.__func__)


class Report(Body):
    report_id: str = Field(pattern=r'^[a-f0-9-]{36}$')
    device_id: str = Field(min_length=16, max_length=128, pattern=r'^[a-zA-Z0-9_-]+$')
    created_at: int = Field(ge=0, le=4102444800)
    hours: Literal[1, 24] = 1
    reason: Literal['task', 'network', 'interface', 'other'] = 'task'
    snapshot: Snapshot
    events: list[ReportEvent] = Field(max_length=200)


def prune(db, now):
    db.execute('DELETE FROM client_reports WHERE received_at<?', (now-RETENTION,))
    db.execute('DELETE FROM client_reports WHERE id <= (SELECT MAX(id)-? FROM client_reports)', (MAX_ROWS,))


@router.post('/api/v1/auth/client-reports')
def receive(body: Report, request: Request, authorization: str = Header(default='', max_length=256)):
    auth = request.app.state.auth
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != 'bearer' or not 40 <= len(parts[1]) <= 128:
        raise AuthError(401, 'invalid_session', '请先登录')
    user = auth.verify(parts[1], body.device_id)['user']
    auth.rate([('client-reports:'+user['id'], 30)])
    data = body.model_dump(exclude_none=True)
    data['device'] = digest(data.pop('device_id'))[:16]
    data['identity'] = 'user_reported'
    raw = json.dumps(data, ensure_ascii=False)
    with auth.store.connect(write=True) as db:
        existing = db.execute('SELECT payload FROM client_reports WHERE user_id=? AND report_id=?',
                              (user['id'], body.report_id)).fetchone()
        if existing and existing['payload'] != raw:
            raise AuthError(409, 'report_conflict', '此上报编号已有不同内容，请创建新的报告')
        if not existing:
            db.execute('INSERT INTO client_reports(user_id,report_id,device,received_at,payload) VALUES(?,?,?,?,?)',
                       (user['id'], body.report_id, data['device'], auth.now(), raw))
        prune(db, auth.now())
    return {'report_id': body.report_id, 'received': True}


@router.get('/admin/client-reports.js')
def javascript():
    return FileResponse(WEB/'client-reports.js', media_type='text/javascript')


@router.get('/admin/api/users/{name}/client-reports')
def listing(name: str, request: Request, page: int = 1, device: str = ''):
    from .provisioning import user_id
    current(request)
    auth = request.app.state.auth
    uid = user_id(auth, name)
    if not 1 <= page <= 1000 or not re.fullmatch(r'(?:[a-f0-9]{16})?', device):
        raise AuthError(422, 'invalid_filter', '报告筛选参数不正确')
    where, args = 'user_id=? AND received_at>=?', [uid, auth.now()-RETENTION]
    if device:
        where += ' AND device=?'
        args.append(device)
    with auth.store.connect() as db:
        count = db.execute('SELECT COUNT(*) FROM client_reports WHERE '+where, args).fetchone()[0]
        rows = db.execute('SELECT report_id,device,received_at,payload FROM client_reports WHERE '+where+
                          ' ORDER BY id DESC LIMIT 25 OFFSET ?', args+[(page-1)*25]).fetchall()
    reports = []
    for row in rows:
        data = json.loads(row['payload'])
        reports.append(dict(report_id=row['report_id'], device=row['device'], received_at=row['received_at'],
                            created_at=data['created_at'], reason=data['reason'], snapshot=data['snapshot'],
                            events=len(data['events']), errors=sum(e['outcome']=='error' for e in data['events'])))
    return {'reports': reports, 'total': count, 'page': page}


@router.get('/admin/api/users/{name}/client-reports/{report_id}')
def detail(name: str, report_id: str, request: Request):
    from .provisioning import user_id
    current(request)
    auth = request.app.state.auth
    uid = user_id(auth, name)
    with auth.store.connect() as db:
        row = db.execute('SELECT received_at,payload FROM client_reports WHERE user_id=? AND report_id=? AND received_at>=?',
                         (uid, report_id, auth.now()-RETENTION)).fetchone()
    if not row:
        raise AuthError(404, 'report_missing', '未找到该账号的报告或报告已过期')
    return dict(json.loads(row['payload']), received_at=row['received_at'])
