"""Durable, credential-free client report outbox. Not an authoritative billing ledger."""
import contextvars
import functools
import json
import sqlite3
import threading
import time
import uuid
import weakref
from contextlib import contextmanager
from pathlib import Path

from . import paths

_current = contextvars.ContextVar('cloud_usage', default=None)
_lock = threading.RLock()
_workers = weakref.WeakSet()
_error = ''


@contextmanager
def connection():
    path = Path(paths.data_dir()) / 'usage-outbox.sqlite3'
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=15)
    db.execute('CREATE TABLE IF NOT EXISTS reports(owner TEXT,event_id TEXT,sequence INTEGER,payload TEXT,dirty INTEGER,PRIMARY KEY(owner,event_id))')
    try:
        with db:
            yield db
    finally:
        db.close()


def persist(owner, event):
    global _error
    try:
        with _lock, connection() as db:
            db.execute('INSERT INTO reports VALUES(?,?,?,?,1) ON CONFLICT(owner,event_id) DO UPDATE SET '
                       'sequence=excluded.sequence,payload=excluded.payload,dirty=1',
                       (owner, event['event_id'], event['sequence'], json.dumps(event)))
        _error = ''
    except (OSError, sqlite3.Error):
        _error = '用量记录暂存失败，请检查数据目录和磁盘空间；生成结果不会因此重新提交'


def note(status, task_id=None):
    ctx = _current.get()
    if not ctx or not ctx.get('event'):
        return
    e = ctx['event']
    e.update(status=status, updated_at=int(time.time()), sequence=e['sequence']+1)
    if task_id:
        e['task_id'] = str(task_id)[:200]
    persist(ctx['owner'], e)


def begin():
    ctx = _current.get()
    if not ctx:
        return
    if ctx.get('event'):
        note('failed')  # The preceding attempt was retried before a task ID was obtained.
    now = int(time.time())
    e = dict(event_id=str(uuid.uuid4()), sequence=1, provider=ctx['provider'][:64], model=ctx['model'][:200],
             kind=ctx['kind'], source=ctx['source'], assignment_id=ctx.get('assignment_id', ''),
             task_id='', status='attempted', resumed=False, started_at=now, updated_at=now)
    ctx['event'] = e
    persist(ctx['owner'], e)


def reference():
    ctx = _current.get()
    if ctx and ctx.get('event'):
        return {'owner': ctx['owner'], 'event': dict(ctx['event'])}
    return None


def tracked(fn):
    @functools.wraps(fn)
    def wrapped(pj, task, out, prov, rec, *, meta, **kw):
        from .apiutil import ApiError, TASK_FATAL
        assignment = meta.get('_usage') or {}
        saved = (rec or {}).get('_usage_ref') or {}
        uid = assignment.get('owner')
        if not uid:
            meta = {k: v for k, v in meta.items() if k not in ('_usage', '_usage_kind')}
            return fn(pj, task, out, prov, rec, meta=meta, **kw)
        if saved.get('owner') and saved['owner'] != uid:
            raise ApiError('该任务属于另一个 Studio 账号，请切回原账号取回；不会重新生成', kind=TASK_FATAL)
        ctx = dict(assignment, provider=meta.get('provider', ''), model=meta.get('model', ''),
                   kind='video' if meta.get('_usage_kind') == 'video' else 'image')
        if rec:
            ctx['event'] = saved.get('event')
            if ctx['event']:
                with _lock, connection() as db:
                    row = db.execute('SELECT payload FROM reports WHERE owner=? AND event_id=?',
                                     (uid, ctx['event']['event_id'])).fetchone()
                if row:
                    ctx['event'] = json.loads(row[0])
            else:
                now = int(time.time())
                ctx['event'] = dict(event_id=str(uuid.uuid4()), sequence=1, provider=ctx['provider'][:64],
                    model=ctx['model'][:200], kind=ctx['kind'], source='unknown', assignment_id='',
                    task_id=str(rec['task_id'])[:200], status='pending', resumed=True, started_at=now, updated_at=now)
        token = _current.set(ctx)
        try:
            if rec:
                note('pending', rec['task_id'])
                from . import submitted
                # Preserve identity for another restart even when a custom resume never calls remember().
                submitted.update_usage(pj.root, task['output'], reference())
            result = fn(pj, task, out, prov, rec, meta=meta, **kw)
            note('completed')
            return result
        except Exception as exc:
            from . import submitted
            pending = submitted.load(pj.root, task['output'])
            note('pending' if pending else ('unknown' if getattr(exc, 'err_code', '') == 'submit_uncertain' else 'failed'))
            raise
        finally:
            _current.reset(token)
    return wrapped


def pending(owner, limit=20):
    with _lock, connection() as db:
        return [json.loads(r[0]) for r in db.execute('SELECT payload FROM reports WHERE owner=? AND dirty=1 ORDER BY rowid LIMIT ?', (owner, limit))]


def acknowledge(owner, events):
    with _lock, connection() as db:
        for e in events:
            db.execute('UPDATE reports SET dirty=0 WHERE owner=? AND event_id=? AND sequence=?',
                       (owner, e['event_id'], e['sequence']))


def status(owner):
    if not owner:
        return {'pending': 0, 'error': _error}
    try:
        with _lock, connection() as db:
            count = db.execute('SELECT COUNT(*) FROM reports WHERE owner=? AND dirty=1', (owner,)).fetchone()[0]
        return {'pending': count, 'error': _error}
    except (OSError, sqlite3.Error):
        return {'pending': None, 'error': '用量暂存目录不可用'}


def start_sender(manager):
    with _lock:
        if manager in _workers:
            return
        _workers.add(manager)
    reference = weakref.ref(manager)
    def run():
        while True:
            time.sleep(5)
            active = reference()
            if active is None:
                return
            try:
                active.send_usage()
            except Exception:
                pass  # Keep durable records; the UI shows pending count. Never retry generation.
            finally:
                del active
    threading.Thread(target=run, name='studio-usage-upload', daemon=True).start()


def copy_outbox(destination):
    if not (Path(paths.data_dir()) / 'usage-outbox.sqlite3').exists():
        return
    with _lock, connection() as source:
        target = sqlite3.connect(Path(destination) / 'usage-outbox.sqlite3')
        try:
            source.backup(target)
        finally:
            target.close()
