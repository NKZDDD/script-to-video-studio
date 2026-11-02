"""Background-only provider monitoring with an account-scoped, bounded outbox."""
import json
from pathlib import Path
import random
import sqlite3
import threading
import time
import uuid
import weakref

from auth_service.studio_auth.provider_probe import probe, clean_url

_workers = weakref.WeakKeyDictionary()
_lock = threading.Lock()
MAX_ROWS = 2000
RETENTION = 7 * 86400


def connect(directory):
    from . import paths
    directory = paths._redirect_data(str(directory))
    db = sqlite3.connect(Path(directory) / 'usage-outbox.sqlite3', timeout=.25)
    db.execute('CREATE TABLE IF NOT EXISTS provider_probe_outbox '
               '(id INTEGER PRIMARY KEY, owner TEXT NOT NULL, at INTEGER NOT NULL, payload TEXT NOT NULL)')
    return db


def store(directory, owner, event):
    db = connect(directory)
    try:
        with db:
            db.execute('INSERT INTO provider_probe_outbox(owner,at,payload) VALUES(?,?,?)', (owner, event['at'], json.dumps(event)))
            db.execute('DELETE FROM provider_probe_outbox WHERE at<?', (int(time.time()) - RETENTION,))
            db.execute('DELETE FROM provider_probe_outbox WHERE id <= (SELECT MAX(id)-? FROM provider_probe_outbox)', (MAX_ROWS,))
    finally:
        db.close()


def identity(manager):
    with manager.lock:
        if not manager.tokens or not manager.user or not getattr(manager, '_diagnostics_dir', ''):
            return None
        return (manager.user['id'], manager.generation, manager.tokens['access_token'], manager._diagnostics_dir)


def same(manager, owner):
    current = identity(manager)
    return bool(current and current[:2] == owner[:2])


def send(manager, owner, monitor_token=None):
    db = connect(owner[3])
    try:
        with db:
            db.execute('DELETE FROM provider_probe_outbox WHERE at<?', (int(time.time()) - RETENTION,))
        rows = db.execute('SELECT id,payload FROM provider_probe_outbox WHERE owner=? ORDER BY id LIMIT 20', (owner[0],)).fetchall()
        if not rows or not same(manager, owner):
            return
        events = [json.loads(row[1]) for row in rows]
        result = manager._post('provider-monitor/report', {'device_id': manager.device_id(), 'events': events}, monitor_token or owner[2])
        if result.get('accepted') != [event['event_id'] for event in events]:
            raise ValueError('monitor report not acknowledged')
        with db:
            db.executemany('DELETE FROM provider_probe_outbox WHERE id=? AND owner=?', [(row[0], owner[0]) for row in rows])
    finally:
        db.close()


def validate_config(data):
    if not isinstance(data, dict) or data.get('protocol') != 1:
        raise ValueError('unsupported monitor protocol')
    rows = data.get('monitors')
    if not isinstance(rows, list) or len(rows) > 64:
        raise ValueError('invalid monitors')
    import re
    clean = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('invalid monitor')
        pid = row.get('provider', '')
        if not isinstance(pid, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', pid) or pid in seen:
            raise ValueError('invalid provider')
        seen.add(pid)
        if (not isinstance(row.get('revision'), str) or not re.fullmatch(r'[a-f0-9]{64}', row['revision'])
                or row.get('kind') not in ('models', 'health', 'http')
                or row.get('auth') not in ('none', 'bearer', 'x-api-key', 'api-key')
                or not isinstance(row.get('secret'), str) or len(row['secret']) > 8000
                or not isinstance(row.get('result_path'), str) or len(row['result_path']) > 100
                or not isinstance(row.get('key_name'), str) or len(row['key_name']) > 100):
            raise ValueError('invalid monitor settings')
        clean.append({k: row[k] for k in ('provider', 'revision', 'kind', 'auth', 'secret', 'result_path', 'key_name')}
                     | {'url': clean_url(row['url'])})
    return clean


class Worker:
    def __init__(self, manager):
        self.manager = weakref.ref(manager)
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.owner = None
        self.monitor_token = None
        self.specs = []
        self.config_until = 0
        self.fetch_at = time.monotonic() + random.uniform(5, 20)
        self.probe_at = 0
        self.send_at = 0
        self.manual_at = 0
        self.rows = {}
        self.state = 'waiting'
        self.recording_error = False

    def run(self):
        while True:
            self.wake.wait(5)
            self.wake.clear()
            manager = self.manager()
            if manager is None:
                return
            try:
                self.tick(manager)
            except Exception:
                with self.lock:
                    self.state = 'monitor_error'
                self.fetch_at = time.monotonic() + 300
            finally:
                del manager

    def tick(self, manager):
        owner = identity(manager)
        now = time.monotonic()
        if not owner:
            self.specs = []
            self.monitor_token = None
            with self.lock:
                self.owner = None
                self.rows = {}
                self.state = 'logged_out'
            return
        if self.owner != owner[:2]:
            self.specs, self.config_until = [], 0
            self.monitor_token = None
            self.fetch_at = min(self.fetch_at, now + random.uniform(5, 20))
            with self.lock:
                self.owner = owner[:2]
                self.rows = {}
                self.state = 'waiting'
        if now >= self.fetch_at:
            self.fetch_at = now + 300 + random.uniform(0, 30)
            try:
                data = manager._post('provider-monitor/config', {'device_id': manager.device_id()}, self.monitor_token or owner[2])
                specs = validate_config(data)
                token = data.get('monitor_token')
                if not isinstance(token, str) or not 40 <= len(token) <= 128:
                    raise ValueError('invalid monitor token')
                if not same(manager, owner):
                    return
                self.specs = specs
                self.monitor_token = token
                self.config_until = now + 900
                with self.lock:
                    valid = {(s['provider'], s['revision']) for s in specs}
                    self.rows = {pid: row for pid, row in self.rows.items() if (pid, row['revision']) in valid}
                    self.state = 'ready' if specs else 'not_assigned'
            except Exception as exc:
                with self.lock:
                    self.state = 'server_unavailable'
                if getattr(exc, 'status', 0) in (401, 403) or getattr(exc, 'code', '') in ('invalid_session', 'login_required'):
                    self.specs = []
                    self.config_until = 0
                    self.monitor_token = None
        if now >= self.config_until:
            self.specs = []
        if now >= self.probe_at and self.specs:
            self.probe_at = now + 300 + random.uniform(0, 30)
            for spec in list(self.specs):
                if not same(manager, owner) or time.monotonic() >= self.config_until:
                    break
                result = probe(spec, spec['secret'])
                from .client_build import VERSION
                event = dict(result, provider=spec['provider'], revision=spec['revision'],
                             event_id=str(uuid.uuid4()), version=VERSION)
                # An in-flight result remains attached to its original account.
                try:
                    store(owner[3], owner[0], event)
                    self.recording_error = False
                except (OSError, sqlite3.Error):
                    self.recording_error = True
                if same(manager, owner):
                    with self.lock:
                        self.rows[spec['provider']] = dict(event, key_name=spec['key_name'], auth=spec['auth'])
        if now >= self.send_at and same(manager, owner):
            self.send_at = time.monotonic() + 30
            try:
                send(manager, owner, self.monitor_token)
            except Exception:
                self.send_at = time.monotonic() + 60

    def trigger(self):
        with self.lock:
            now = time.monotonic()
            if now < self.manual_at + 30:
                return False
            self.manual_at = now
            self.fetch_at = self.probe_at = 0
        self.wake.set()
        return True


def start(manager):
    with _lock:
        worker = _workers.get(manager)
        if worker is None:
            worker = _workers[manager] = Worker(manager)
            threading.Thread(target=worker.run, name='studio-provider-monitor', daemon=True).start()
    return worker


def status(manager):
    with _lock:
        worker = _workers.get(manager)
    if worker is None:
        return {'state': 'waiting', 'results': []}
    owner = identity(manager)
    with worker.lock:
        rows = list(worker.rows.values()) if owner and worker.owner == owner[:2] else []
        return {'state': worker.state, 'recording_error': worker.recording_error,
                'results': [dict(r, stale=int(time.time()) - r['at'] > 900) for r in rows]}
