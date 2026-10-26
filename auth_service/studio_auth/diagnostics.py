"""Bounded diagnostic evidence, distinct from authorization and billing records."""
import ipaddress
import json
import queue
import re
import threading
import time
import uuid
from typing import Literal

from fastapi import APIRouter, Header, Request
from fastapi.responses import FileResponse
from pydantic import Field

from .admin import Body, WEB, current
from .service import AuthError, digest

router = APIRouter()
RETENTION = 30 * 86400
MAX_ROWS = 50000
AUTH_ACTIONS = {'login', 'verify', 'refresh', 'logout', 'storage/sign', 'adapters'}
SCHEMA = '''
CREATE TABLE IF NOT EXISTS request_diagnostics (
 id INTEGER PRIMARY KEY, user_id TEXT, username TEXT NOT NULL,
 source TEXT NOT NULL, event_id TEXT NOT NULL, at INTEGER NOT NULL,
 received_at INTEGER NOT NULL, kind TEXT NOT NULL, outcome TEXT NOT NULL,
 request_id TEXT NOT NULL, payload TEXT NOT NULL,
 UNIQUE(user_id,source,event_id)
);
CREATE INDEX IF NOT EXISTS diagnostics_account ON request_diagnostics(username,id);
CREATE INDEX IF NOT EXISTS diagnostics_received ON request_diagnostics(received_at);
'''


class Event(Body):
    event_id: str = Field(pattern=r'^[a-f0-9-]{36}$')
    at: int = Field(ge=0, le=4102444800)
    kind: Literal['auth', 'llm', 'provider', 'local']
    operation: str = Field(pattern=r'^[a-zA-Z0-9_./-]{1,96}$')
    outcome: Literal['success', 'error']
    duration_ms: int = Field(ge=0, le=604800000)
    status: int = Field(default=0, ge=0, le=599)
    category: Literal['none', 'dns', 'tls', 'timeout', 'connection', 'http',
                      'authentication', 'validation', 'cancelled', 'unknown'] = 'none'
    request_id: str = Field(default='', pattern=r'^(?:[a-f0-9-]{36})?$')
    host: str = Field(default='', max_length=253, pattern=r'^[a-zA-Z0-9.:-]*$')
    version: str = Field(default='', pattern=r'^(?:[0-9]{1,6}(?:\.[0-9]{1,6}){2,3})?$')


class Report(Body):
    device_id: str = Field(min_length=16, max_length=128, pattern=r'^[a-zA-Z0-9_-]+$')
    events: list[Event] = Field(min_length=1, max_length=20)
    dropped: int = Field(default=0, ge=0, le=2147483647)
    recording_error: bool = False


def prune(db, now):
    db.execute('DELETE FROM request_diagnostics WHERE received_at<?', (now-RETENTION,))
    # IDs are monotonic within the retained table. Bound the window without
    # scanning 50,000 rows on every login or report upload.
    db.execute('DELETE FROM request_diagnostics WHERE id <= '
               '(SELECT MAX(id)-? FROM request_diagnostics)', (MAX_ROWS,))


def save(db, uid, name, source, event, now):
    db.execute('INSERT OR IGNORE INTO request_diagnostics '
               '(user_id,username,source,event_id,at,received_at,kind,outcome,request_id,payload) '
               'VALUES(?,?,?,?,?,?,?,?,?,?)',
               (uid, name, source, event['event_id'], event['at'], now, event['kind'],
                event['outcome'], event.get('request_id', ''), json.dumps(event, ensure_ascii=False)))


class Recorder:
    """No database I/O on the ASGI request path; drop telemetry rather than block it."""
    def __init__(self, auth):
        self.auth = auth
        self.queue = queue.Queue(maxsize=1024)
        self.dropped = 0
        self.failed = False
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True, name='auth-diagnostics')
        self.thread.start()

    def put(self, event):
        try:
            self.queue.put_nowait(event)
        except queue.Full:
            self.dropped += 1

    def run(self):
        while not self.stop.is_set() or not self.queue.empty():
            try:
                batch = [self.queue.get(timeout=.1)]
            except queue.Empty:
                continue
            while len(batch) < 64:
                try:
                    batch.append(self.queue.get_nowait())
                except queue.Empty:
                    break
            try:
                now = self.auth.now()
                with self.auth.store.connect(write=True) as db:
                    for event in batch:
                        token_hash = event.pop('_session_hash', '')
                        name = event.pop('_username', '')
                        uid = event.pop('_user_id', None)
                        verified = bool(uid)
                        if token_hash and not uid:
                            row = db.execute('SELECT u.id,u.username FROM sessions s JOIN users u ON u.id=s.user_id '
                                             'WHERE s.access_hash=? OR s.refresh_hash=?',
                                             (token_hash, token_hash)).fetchone()
                            if row:
                                uid, name = row['id'], row['username']
                        # A login name is a claim, not proof that authentication succeeded.
                        event['identity'] = 'authenticated' if verified else ('session' if uid else 'claimed')
                        save(db, uid, name, 'server', event, now)
                    prune(db, now)
                self.failed = False
            except Exception:
                self.dropped += len(batch)
                self.failed = True
            finally:
                for _ in batch:
                    self.queue.task_done()

    def close(self):
        self.stop.set()
        self.thread.join(timeout=2)


def identify(scope, raw):
    """Inspect only named identity fields; never retain bodies or credentials."""
    if not scope.get('state', {}).get('_diagnostic'):
        return
    try:
        body = json.loads(raw)
        if not isinstance(body, dict):
            return
        state = scope['state']
        name = body.get('username', '')
        if isinstance(name, str) and re.fullmatch(r'[a-z0-9][a-z0-9_.@+\-]{2,63}', name.strip().lower()):
            state['_diag_username'] = name.strip().lower()
        device = body.get('device_id', '')
        if isinstance(device, str) and re.fullmatch(r'[a-zA-Z0-9_-]{16,128}', device):
            state['_diag_device'] = digest(device)[:16]
        token = body.get('refresh_token', '')
        if isinstance(token, str) and re.fullmatch(r'[a-zA-Z0-9_-]{40,128}', token):
            state['_diag_session'] = digest(token)
    except (ValueError, TypeError):
        pass


class RequestLog:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        action = scope.get('path', '').removeprefix('/api/v1/auth/')
        if scope['type'] != 'http' or action not in AUTH_ACTIONS:
            return await self.app(scope, receive, send)
        started = time.monotonic()
        headers = dict(scope['headers'])
        request_id = headers.get(b'x-studio-request-id', b'').decode('ascii', errors='ignore')
        try:
            request_id = str(uuid.UUID(request_id))
        except ValueError:
            request_id = str(uuid.uuid4())
        state = scope.setdefault('state', {})
        state['_diagnostic'] = True
        parts = headers.get(b'authorization', b'').split()
        if len(parts) == 2 and parts[0].lower() == b'bearer' and re.fullmatch(rb'[a-zA-Z0-9_-]{40,128}', parts[1]):
            state['_diag_session'] = digest(parts[1].decode())
        status = 500
        async def logged_send(message):
            nonlocal status
            if message['type'] == 'http.response.start':
                status = message['status']
                message['headers'] = list(message.get('headers', [])) + [(b'x-studio-request-id', request_id.encode())]
            await send(message)
        try:
            await self.app(scope, receive, logged_send)
        finally:
            recorder = getattr(scope['app'].state, 'diagnostics', None)
            if recorder:
                version = headers.get(b'x-studio-version', b'').decode('ascii', errors='ignore')
                if not re.fullmatch(r'[0-9]{1,6}(?:\.[0-9]{1,6}){2,3}', version):
                    version = ''
                try:
                    peer = str(ipaddress.ip_address((scope.get('client') or ('',))[0]))
                except ValueError:
                    peer = ''
                recorder.put(dict(event_id=str(uuid.uuid4()), at=recorder.auth.now(), kind='auth',
                    operation=action, outcome='success' if status < 400 else 'error', status=status,
                    duration_ms=round((time.monotonic()-started)*1000), request_id=request_id,
                    device=state.get('_diag_device', ''), version=version, peer=peer,
                    category='none' if status < 400 else ('authentication' if status in (401,403) else 'http'),
                    _username=state.get('_diag_username', ''), _session_hash=state.get('_diag_session', ''),
                    _user_id=state.get('_diag_user')))


@router.post('/api/v1/auth/diagnostics')
def report(body: Report, request: Request, authorization: str = Header(default='', max_length=256)):
    auth = request.app.state.auth
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != 'bearer' or not 40 <= len(parts[1]) <= 128:
        raise AuthError(401, 'invalid_session', '请先登录')
    user = auth.verify(parts[1], body.device_id)['user']
    auth.rate([('diagnostics:' + user['id'], 600)])
    with auth.store.connect(write=True) as db:
        for item in body.events:
            event = item.model_dump()
            event['device'] = digest(body.device_id)[:16]
            event['identity'] = 'reported'
            event['client_dropped_since_start'] = body.dropped
            event['client_recording_error'] = body.recording_error
            save(db, user['id'], user['username'], 'client', event, auth.now())
        prune(db, auth.now())
    return {'accepted': [e.event_id for e in body.events]}


@router.get('/admin/diagnostics.js')
def javascript():
    return FileResponse(WEB/'diagnostics.js', media_type='text/javascript')


@router.get('/admin/api/users/{name}/diagnostics')
def view(name: str, request: Request, page: int = 1, source: str = '', outcome: str = '',
         kind: str = '', hours: int = 24, request_id: str = ''):
    from .provisioning import user_id
    from .service import username
    current(request)
    auth = request.app.state.auth
    user_id(auth, name)
    if (source not in ('', 'server', 'client') or outcome not in ('', 'success', 'error') or
            kind not in ('', 'auth', 'llm', 'provider', 'local') or not 1 <= hours <= 720 or
            not 1 <= page <= 1000 or (request_id and not re.fullmatch(r'[a-f0-9-]{36}', request_id))):
        raise AuthError(422, 'invalid_filter', '诊断筛选参数不正确')
    where, args = 'username=? AND received_at>=?', [username(name), auth.now()-hours*3600]
    for key, value in [('source', source), ('outcome', outcome), ('kind', kind), ('request_id', request_id)]:
        if value:
            where += ' AND '+key+'=?'
            args.append(value)
    with auth.store.connect() as db:
        total = db.execute('SELECT COUNT(*) FROM request_diagnostics WHERE '+where, args).fetchone()[0]
        rows = db.execute('SELECT source,received_at,payload FROM request_diagnostics WHERE '+where+
                          ' ORDER BY id DESC LIMIT 50 OFFSET ?', args+[(page-1)*50]).fetchall()
    recorder = request.app.state.diagnostics
    return {'events': [dict(json.loads(r['payload']), source=r['source'], received_at=r['received_at']) for r in rows],
            'total': total, 'page': page, 'retention_days': 30, 'max_rows': MAX_ROWS,
            'server_dropped_since_start': recorder.dropped, 'server_recording_error': recorder.failed}
