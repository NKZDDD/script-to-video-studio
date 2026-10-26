"""Best-effort, bounded diagnostics. Recording never waits for disk or the network."""
from functools import wraps
import json
from pathlib import Path
import queue
import re
import socket
import sqlite3
import ssl
import threading
import time
from urllib.parse import urlsplit
import uuid
import weakref

_manager = None
_queue = queue.Queue(maxsize=1024)
_start_lock = threading.Lock()
_thread = None
_dropped = 0
_failed = False
MAX_ROWS = 2000
RETENTION = 7 * 86400


def activate(manager):
    global _manager
    _manager = weakref.ref(manager)


def identity(manager=None):
    manager = manager or (_manager() if _manager else None)
    if manager is None:
        return None
    # Immutable snapshot; no network/state lock is acquired on a local action.
    user = getattr(manager, 'user', None) or {}
    if not isinstance(user, dict):
        return None
    directory = getattr(manager, '_diagnostics_dir', '')
    if not user.get('id') or not directory:
        return None
    return user['id'], user.get('username', ''), directory


def classify(exc):
    """Inspect exception types/chains locally; never upload exception strings."""
    seen, nodes = set(), [exc]
    while nodes and len(seen) < 20:
        item = nodes.pop(0)
        if not isinstance(item, BaseException) or id(item) in seen:
            continue
        seen.add(id(item))
        name = type(item).__name__.lower()
        status = getattr(item, 'status_code', None) or getattr(item, 'status', None) or getattr(item, 'code', None)
        response = getattr(item, 'response', None)
        if response is not None:
            status = getattr(response, 'status_code', status)
        if isinstance(item, socket.gaierror) or 'nameresolution' in name:
            return 'dns', 0
        if isinstance(item, ssl.SSLError) or 'ssl' in name or 'certificate' in name:
            return 'tls', 0
        if isinstance(item, TimeoutError) or 'timeout' in name:
            return 'timeout', 0
        if 'cancel' in name:
            return 'cancelled', 0
        if isinstance(status, int) and 400 <= status <= 599:
            return ('authentication' if status in (401,403) else 'http'), status
        nodes.extend([getattr(item, '__cause__', None), getattr(item, '__context__', None),
                      getattr(item, 'reason', None)])
    if any(s in type(exc).__name__.lower() for s in ('connection', 'urlerror')) or isinstance(exc, OSError):
        return 'connection', 0
    # Wrapped LLM errors may only preserve a status in their text. Never retain that text.
    match = re.search(r'\bHTTP\s*[:=]?\s*([45][0-9]{2})\b', str(exc), re.I)
    if match:
        status = int(match[1])
        return ('authentication' if status in (401,403) else 'http'), status
    return ('validation' if isinstance(exc, ValueError) else 'unknown'), 0


def host(value):
    try:
        result = urlsplit(value).hostname or ''
        return result if len(result) <= 253 and re.fullmatch(r'[a-zA-Z0-9.:-]*', result) else ''
    except (ValueError, TypeError):
        return ''


def version():
    from .client_build import VERSION
    value = str(VERSION)
    return value if re.fullmatch(r'[0-9]{1,6}(?:\.[0-9]{1,6}){2,3}', value) else ''


def emit(owner, kind, operation, started, *, error=None, status=0, request_id='', target=''):
    global _thread, _dropped
    if not owner:
        return
    try:
        category, inferred = classify(error) if error is not None else ('none', 0)
        if error is None and status >= 400:
            category = 'authentication' if status in (401, 403) else 'http'
        event = dict(event_id=str(uuid.uuid4()), at=int(time.time()), kind=kind, operation=operation,
            outcome='error' if error is not None or status >= 400 else 'success',
            duration_ms=min(604800000, max(0, round((time.monotonic()-started)*1000))),
            status=status or inferred, category=category, request_id=request_id, host=host(target), version=version())
        _queue.put_nowait((owner, event))
        with _start_lock:
            if _thread is None or not _thread.is_alive():
                _thread = threading.Thread(target=_writer, name='studio-diagnostics-spool', daemon=True)
                _thread.start()
    except Exception:
        _dropped += 1


def connect(directory):
    from . import paths
    directory = paths._redirect_data(str(directory))
    db = sqlite3.connect(Path(directory)/'usage-outbox.sqlite3', timeout=.25)
    db.execute('CREATE TABLE IF NOT EXISTS diagnostics '
               '(id INTEGER PRIMARY KEY, owner TEXT, at INTEGER, payload TEXT)')
    return db


def _writer():
    global _failed, _dropped
    while True:
        owner, event = _queue.get()
        try:
            db = connect(owner[2])
            try:
                with db:
                    db.execute('INSERT INTO diagnostics(owner,at,payload) VALUES(?,?,?)',
                               (owner[0], event['at'], json.dumps(event)))
                    db.execute('DELETE FROM diagnostics WHERE at<?', (int(time.time())-RETENTION,))
                    db.execute('DELETE FROM diagnostics WHERE id <= '
                               '(SELECT MAX(id)-? FROM diagnostics)', (MAX_ROWS,))
                _failed = False
            finally:
                db.close()
        except Exception:
            _dropped += 1
            _failed = True
        finally:
            _queue.task_done()


def send(manager):
    if time.monotonic() < getattr(manager, '_diagnostics_retry_at', 0):
        return
    owner = identity(manager)
    if not owner:
        return
    with manager.lock:
        if not manager.tokens or not manager.user or manager.user.get('id') != owner[0]:
            return
        token = manager.tokens['access_token']
    # Called only by the background sender. Never refresh credentials implicitly.
    db = connect(owner[2])
    try:
        with db:
            db.execute('DELETE FROM diagnostics WHERE at<?', (int(time.time())-RETENTION,))
        rows = db.execute('SELECT id,payload FROM diagnostics WHERE owner IN (?,?) ORDER BY id LIMIT 20',
                          (owner[0], 'name:'+owner[1])).fetchall()
        if not rows:
            return
        events = [json.loads(row[1]) for row in rows]
        body = {'device_id': manager.device_id(), 'events': events,
                'dropped': min(_dropped, 2147483647), 'recording_error': _failed}
        while len(json.dumps(body).encode('utf-8')) > 14000 and len(events) > 1:
            events.pop()
            rows.pop()
        result = manager._post('diagnostics', body, token)
        if result.get('accepted') == [event['event_id'] for event in events]:
            with db:
                db.executemany('DELETE FROM diagnostics WHERE id=?', [(row[0],) for row in rows])
            manager._diagnostics_retry_delay = 0
        else:
            raise ValueError('diagnostics not acknowledged')
    except Exception:
        delay = min(300, max(15, getattr(manager, '_diagnostics_retry_delay', 0)*2))
        manager._diagnostics_retry_delay = delay
        manager._diagnostics_retry_at = time.monotonic()+delay
        raise
    finally:
        db.close()


def llm_call(fn):
    @wraps(fn)
    def wrapped(self, *args, **kwargs):
        owner, started, error = identity(), time.monotonic(), None
        try:
            return fn(self, *args, **kwargs)
        except Exception as exc:
            error = exc
            raise
        finally:
            emit(owner, 'llm', 'chat', started, error=error, target=self.base_url)
    return wrapped


def provider_call(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        owner, started, error = identity(), time.monotonic(), None
        # A resumed task can belong to an older account. Do not misattribute it.
        usage = kwargs.get('meta', {}).get('_usage', {})
        if usage.get('owner') and owner and usage['owner'] != owner[0]:
            owner = None
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            error = exc
            raise
        finally:
            provider = args[3] if len(args) > 3 else kwargs.get('prov')
            emit(owner, 'provider', 'deliver', started, error=error,
                 target=getattr(getattr(provider, 'session', None), 'base_url', '') or
                        getattr(provider, 'base_url', ''))
    return wrapped


def local_request(fn):
    @wraps(fn)
    def wrapped(self, *args, **kwargs):
        route = urlsplit(self.path).path
        # Local commands/plans, not thumbnail reads, status polling or filesystem paths.
        selected = route in {'/api/agent/selections', '/api/agent/plan', '/api/agent/preview',
                             '/api/agent/project', '/api/agent/dependencies'}
        if not selected:
            return fn(self, *args, **kwargs)
        manager = getattr(self.server, 'studio_login', None)
        owner, started, error = identity(manager) if manager else None, time.monotonic(), None
        self._diagnostic_status = 200
        try:
            return fn(self, *args, **kwargs)
        except Exception as exc:
            error = exc
            raise
        finally:
            if selected and (route != '/api/agent/project' or time.monotonic()-started >= 1 or
                             error is not None or self._diagnostic_status >= 400):
                emit(owner, 'local', route, started, error=error, status=self._diagnostic_status)
    return wrapped
