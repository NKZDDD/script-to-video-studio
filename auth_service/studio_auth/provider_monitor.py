"""Opt-in provider probes, separate from login/production and account diagnostics."""
import hashlib
import json
import queue
import secrets
import threading
import time
import uuid
from typing import Literal

from fastapi import APIRouter, Header, Request
from fastapi.responses import FileResponse
from pydantic import Field, field_validator

from .admin import Body, WEB, current, record
from .service import AuthError, digest
from .provider_probe import clean_url, probe, same_origin

router = APIRouter()
INTERVAL = 300
RETENTION = 7 * 86400
MAX_ROWS = 50000
SCHEMA = '''
CREATE TABLE IF NOT EXISTS provider_monitor_tokens (
 session_id TEXT PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
 token_hash TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS provider_monitors (
 provider TEXT PRIMARY KEY, key_id TEXT NOT NULL, revision TEXT NOT NULL,
 payload TEXT NOT NULL, enabled INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS provider_probe_results (
 id INTEGER PRIMARY KEY, provider TEXT NOT NULL, revision TEXT NOT NULL,
 source TEXT NOT NULL, user_id TEXT NOT NULL, username TEXT NOT NULL,
 device TEXT NOT NULL, event_id TEXT NOT NULL, at INTEGER NOT NULL,
 received_at INTEGER NOT NULL, payload TEXT NOT NULL,
 UNIQUE(user_id,source,event_id)
);
CREATE INDEX IF NOT EXISTS probe_latest ON provider_probe_results(provider,revision,source,id);
CREATE INDEX IF NOT EXISTS probe_received ON provider_probe_results(received_at);
CREATE INDEX IF NOT EXISTS probe_client_latest ON provider_probe_results(source,provider,user_id,device,id);
'''


class Definition(Body):
    provider: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,64}$')
    key_id: str = Field(min_length=1, max_length=64)
    url: str = Field(max_length=1000)
    kind: Literal['models', 'health', 'http'] = 'models'
    result_path: str = Field(default='data', max_length=100, pattern=r'^(?:[a-zA-Z0-9_-]+(?:\.[a-zA-Z0-9_-]+)*)?$')
    auth: Literal['none', 'bearer', 'x-api-key', 'api-key'] = 'bearer'
    key_field: Literal['api_key', 'image_1k_api_key', 'image_4k_api_key', 'video_api_key'] = 'api_key'
    enabled: bool = True

    @field_validator('url')
    @classmethod
    def target(cls, value):
        return clean_url(value.strip())


def definitions(auth, uid=None, *, secrets=False):
    with auth.store.connect() as db:
        rows = db.execute('SELECT m.*,k.config,k.revision AS key_revision,k.name AS key_name, '
                          'k.enabled AS key_enabled,k.provider AS key_provider FROM provider_monitors m '
                          'LEFT JOIN key_library k ON k.id=m.key_id ORDER BY m.provider').fetchall()
        assigned = {r[0] for r in db.execute('SELECT key_id FROM user_keys WHERE user_id=?', (uid,))} if uid else None
    result = []
    for row in rows:
        if assigned is not None and row['key_id'] not in assigned:
            continue
        spec = json.loads(row['payload'])
        valid = bool(row['key_enabled']) and row['key_provider'] == row['provider']
        cfg = json.loads(row['config'] or '{}')
        # Key edits invalidate prior results and cached client configurations.
        revision = hashlib.sha256((row['revision'] + (row['key_revision'] or '')).encode()).hexdigest()
        if spec['auth'] != 'none' and not cfg.get(spec['key_field']):
            valid = False
        base = cfg.get('base_url', '')
        if base and not same_origin(base, spec['url']):
            valid = False
        spec.update(revision=revision, key_name=row['key_name'] or 'Key 已不存在',
                    configured=valid, enabled=bool(row['enabled']))
        if secrets:
            spec['secret'] = cfg.get(spec['key_field'], '') if spec['auth'] != 'none' else ''
        result.append(spec)
    return result


def prune(db, now):
    db.execute('DELETE FROM provider_probe_results WHERE received_at<?', (now - RETENTION,))
    db.execute('DELETE FROM provider_probe_results WHERE id <= (SELECT MAX(id)-? FROM provider_probe_results)', (MAX_ROWS,))


def save_result(db, spec, source, event, now, uid='', username='', device=''):
    db.execute('INSERT OR IGNORE INTO provider_probe_results '
               '(provider,revision,source,user_id,username,device,event_id,at,received_at,payload) VALUES(?,?,?,?,?,?,?,?,?,?)',
               (spec['provider'], spec['revision'], source, uid, username, device, event['event_id'],
                event['at'], now, json.dumps(event)))
    prune(db, now)


class Monitor:
    """Two bounded daemon workers; no network operation owns a database transaction."""
    def __init__(self, auth):
        self.auth = auth
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.pending = set()
        self.next_at = {}
        self.last_at = {}
        self.queue = queue.Queue(maxsize=64)
        self.failed = False
        self.threads = [threading.Thread(target=self.schedule, daemon=True, name='provider-monitor-schedule')]
        self.threads += [threading.Thread(target=self.work, daemon=True, name=f'provider-monitor-{i}') for i in range(2)]
        for thread in self.threads:
            thread.start()

    def enqueue(self, spec, manual=False):
        pid, now = spec['provider'], time.monotonic()
        if not spec['enabled'] or not spec['configured']:
            return False
        with self.lock:
            key = (pid, spec['revision'])
            if pid in self.pending or now < self.last_at.get(pid, -30) + 30:
                return False
            if not manual and now < self.next_at.get(key, 0):
                return False
            try:
                self.queue.put_nowait(pid)
            except queue.Full:
                return False
            self.pending.add(pid)
            self.last_at[pid] = now
            self.next_at[key] = now + INTERVAL
            # At most one schedule entry per provider; revisions cannot grow memory.
            self.next_at = {k: v for k, v in self.next_at.items() if k[0] != pid or k == key}
            return True

    def schedule(self):
        while not self.stop.wait(5):
            try:
                for spec in definitions(self.auth):
                    self.enqueue(spec)
            except Exception:
                self.failed = True

    def work(self):
        while not self.stop.is_set():
            try:
                pid = self.queue.get(timeout=.2)
            except queue.Empty:
                continue
            try:
                spec = next((s for s in definitions(self.auth, secrets=True) if s['provider'] == pid), None)
                if spec and spec['enabled'] and spec['configured']:
                    result = dict(probe(spec, spec['secret']), event_id=str(uuid.uuid4()))
                    latest = next((s for s in definitions(self.auth) if s['provider'] == pid), None)
                    if latest and latest['enabled'] and latest['configured'] and latest['revision'] == spec['revision']:
                        with self.auth.store.connect(write=True) as db:
                            # Re-check in the write transaction to avoid publishing a
                            # stale result if the administrator edits/disables a Key.
                            if still_current(db, spec):
                                save_result(db, spec, 'server', result, self.auth.now())
                    self.failed = False
            except Exception:
                self.failed = True
            finally:
                with self.lock:
                    self.pending.discard(pid)
                self.queue.task_done()

    def close(self):
        self.stop.set()
        for thread in self.threads:
            thread.join(timeout=.25)


def still_current(db, spec, uid=None):
    row = db.execute('SELECT m.revision,k.revision AS key_revision,m.enabled,k.enabled AS key_enabled '
                     'FROM provider_monitors m JOIN key_library k ON k.id=m.key_id '
                     'AND k.provider=m.provider WHERE m.provider=? AND m.key_id=?',
                     (spec['provider'], spec['key_id'])).fetchone()
    if not row or not row['enabled'] or not row['key_enabled']:
        return False
    if hashlib.sha256((row['revision'] + row['key_revision']).encode()).hexdigest() != spec['revision']:
        return False
    return uid is None or bool(db.execute('SELECT 1 FROM user_keys WHERE user_id=? AND key_id=?', (uid, spec['key_id'])).fetchone())


@router.get('/admin/provider-monitor.js')
def javascript():
    return FileResponse(WEB / 'provider-monitor.js', media_type='text/javascript')


def public_result(row, now):
    item = json.loads(row['payload'])
    item.update(source=row['source'], username=row['username'], device=row['device'],
                received_at=row['received_at'], stale=not now-900 <= row['at'] <= now+300)
    return item


@router.get('/admin/api/provider-monitor')
def view(request: Request):
    current(request)
    auth = request.app.state.auth
    specs = definitions(auth)
    clients = []
    with auth.store.connect() as db:
        for spec in specs:
            row = db.execute("SELECT * FROM provider_probe_results WHERE provider=? AND revision=? AND source='server' ORDER BY id DESC LIMIT 1",
                             (spec['provider'], spec['revision'])).fetchone()
            spec['server'] = public_result(row, auth.now()) if row else None
        rows = db.execute("SELECT r.* FROM provider_probe_results r JOIN "
                          "(SELECT MAX(id) AS id FROM provider_probe_results WHERE source='client' GROUP BY provider,user_id,device) latest "
                          "ON latest.id=r.id ORDER BY r.id DESC LIMIT 200").fetchall()
        revisions = {s['provider']: s['revision'] for s in specs if s['enabled'] and s['configured']}
        for row in rows:
            if revisions.get(row['provider']) == row['revision']:
                clients.append(dict(public_result(row, auth.now()), provider=row['provider']))
    monitor = request.app.state.provider_monitor
    with monitor.lock:
        pending = sorted(monitor.pending)
    return {'monitors': specs, 'clients': clients, 'server_time': auth.now(), 'interval': INTERVAL,
            'pending': pending, 'recording_error': monitor.failed}


@router.post('/admin/api/provider-monitor')
def configure(body: Definition, request: Request):
    actor, _ = current(request, csrf=True)
    auth = request.app.state.auth
    with auth.store.connect(write=True) as db:
        key = db.execute('SELECT * FROM key_library WHERE id=?', (body.key_id,)).fetchone()
        if not key or key['provider'] != body.provider or not key['enabled']:
            raise AuthError(422, 'monitor_key', '请选择该服务商已启用的检测 Key')
        cfg = json.loads(key['config'])
        if body.auth != 'none' and not cfg.get(body.key_field):
            raise AuthError(422, 'monitor_key', '所选 Key 未填写该凭据字段')
        if cfg.get('base_url') and not same_origin(cfg['base_url'], body.url):
            raise AuthError(422, 'monitor_origin', '检测地址域名必须与所选 Key 的 Base URL 一致')
        if (not db.execute('SELECT 1 FROM provider_monitors WHERE provider=?', (body.provider,)).fetchone()
                and db.execute('SELECT COUNT(*) FROM provider_monitors').fetchone()[0] >= 64):
            raise AuthError(422, 'monitor_limit', '最多配置 64 家监测服务商')
        db.execute('INSERT INTO provider_monitors VALUES(?,?,?,?,?) ON CONFLICT(provider) DO UPDATE SET '
                   'key_id=excluded.key_id,revision=excluded.revision,payload=excluded.payload,enabled=excluded.enabled',
                   (body.provider, body.key_id, str(uuid.uuid4()), json.dumps(body.model_dump()), int(body.enabled)))
    record(request, actor, 'configure_provider_monitor', body.provider)
    return {'ok': True}


class Run(Body):
    provider: str = Field(default='', pattern=r'^[a-zA-Z0-9_-]{0,64}$')


@router.post('/admin/api/provider-monitor/run')
def run(body: Run, request: Request):
    actor, _ = current(request, csrf=True)
    queued = sum(request.app.state.provider_monitor.enqueue(s, manual=True)
                 for s in definitions(request.app.state.auth) if not body.provider or s['provider'] == body.provider)
    record(request, actor, 'run_provider_monitor', body.provider or 'all')
    return {'queued': queued}


class Device(Body):
    device_id: str = Field(min_length=16, max_length=128, pattern=r'^[a-zA-Z0-9_-]+$')


def client_user(request, token, device):
    parts = token.split()
    if len(parts) != 2 or parts[0].lower() != 'bearer' or not 40 <= len(parts[1]) <= 128:
        raise AuthError(401, 'invalid_session', '请先登录')
    auth = request.app.state.auth
    with auth.store.connect() as db:
        session = db.execute('SELECT s.* FROM provider_monitor_tokens t JOIN sessions s ON s.id=t.session_id '
                             'WHERE t.token_hash=?', (digest(parts[1]),)).fetchone()
        if session:
            if (session['revoked_at'] is not None or session['expires_at'] <= auth.now()
                    or session['device_hash'] != digest(device)):
                raise AuthError(401, 'invalid_session', '监测会话已失效，请重新登录')
            user = db.execute('SELECT * FROM users WHERE id=?', (session['user_id'],)).fetchone()
            auth._account(user)
            return auth.public_user(user), parts[1]
    verified = auth.verify(parts[1], device)
    return verified['user'], None


@router.post('/api/v1/auth/provider-monitor/config')
def client_config(body: Device, request: Request, authorization: str = Header(default='', max_length=256)):
    user, monitor_token = client_user(request, authorization, body.device_id)
    auth = request.app.state.auth
    auth.rate([('provider-monitor-config:' + user['id'], 120)])
    if monitor_token is None:
        access = authorization.split()[1]
        monitor_token = secrets.token_urlsafe(48)
        with auth.store.connect(write=True) as db:
            session = db.execute('SELECT * FROM sessions WHERE access_hash=?', (digest(access),)).fetchone()
            if not session or session['revoked_at'] is not None or session['expires_at'] <= auth.now():
                raise AuthError(401, 'invalid_session', '登录状态已改变，请重新检查连接')
            db.execute('INSERT INTO provider_monitor_tokens VALUES(?,?) ON CONFLICT(session_id) '
                       'DO UPDATE SET token_hash=excluded.token_hash', (session['id'], digest(monitor_token)))
    specs = [s for s in definitions(auth, user['id'], secrets=True) if s['enabled'] and s['configured']]
    return {'protocol': 1, 'interval': INTERVAL, 'monitors': specs, 'monitor_token': monitor_token}


class Result(Body):
    provider: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,64}$')
    revision: str = Field(pattern=r'^[a-f0-9]{64}$')
    event_id: str = Field(pattern=r'^[a-f0-9-]{36}$')
    at: int = Field(ge=0, le=4102444800)
    duration_ms: int = Field(ge=0, le=604800000)
    status: int = Field(ge=0, le=599)
    category: Literal['ok', 'authentication', 'rate_limited', 'server_error', 'http_error', 'redirect',
                      'dns', 'tls', 'timeout', 'connection', 'invalid_response', 'unsafe_target', 'not_configured']
    model_count: int | None = Field(default=None, ge=0, le=100000)
    version: str = Field(default='', pattern=r'^(?:[0-9]{1,6}(?:\.[0-9]{1,6}){2,3})?$')


class Results(Device):
    events: list[Result] = Field(min_length=1, max_length=20)


@router.post('/api/v1/auth/provider-monitor/report')
def report(body: Results, request: Request, authorization: str = Header(default='', max_length=256)):
    user, _ = client_user(request, authorization, body.device_id)
    auth = request.app.state.auth
    auth.rate([('provider-monitor-report:' + user['id'], 600)])
    specs = {s['provider']: s for s in definitions(auth, user['id']) if s['enabled'] and s['configured']}
    with auth.store.connect(write=True) as db:
        for event in body.events:
            spec = specs.get(event.provider)
            if spec and spec['revision'] == event.revision and still_current(db, spec, user['id']):
                save_result(db, spec, 'client', event.model_dump(), auth.now(), user['id'], user['username'], digest(body.device_id)[:16])
    # Stale/revoked observations are acknowledged and discarded, never re-attributed.
    return {'accepted': [event.event_id for event in body.events]}
