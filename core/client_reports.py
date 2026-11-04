"""Manual diagnostic reports. Only start()/retry from the UI can send a report."""
import json
import platform
import threading
import time
import uuid
from contextlib import contextmanager

from . import cloud_diagnostics as D
from .request_trace import safe

_lock = threading.Lock()
_busy = set()


@contextmanager
def connect(directory):
    db = D.connect(directory)
    try:
        with db:
            db.execute('CREATE TABLE IF NOT EXISTS manual_reports '
                       '(id TEXT PRIMARY KEY, owner TEXT, at INTEGER, state TEXT, message TEXT, payload TEXT)')
            yield db
    finally:
        db.close()


def owner_of(manager):
    from .studio_login import LoginRequired
    owner = D.identity(manager)
    if not owner:
        raise LoginRequired()
    return owner


def frontend_error(manager, body):
    owner = owner_of(manager)
    kind, file = body.get('error_type'), body.get('file')
    if kind not in ('Error', 'TypeError', 'ReferenceError', 'SyntaxError', 'RangeError', 'UnhandledRejection'):
        raise ValueError('前端异常类型不正确')
    if file not in ('agent.js', 'login.js', 'updates.js', 'output-preview.js', 'script'):
        raise ValueError('前端异常来源不正确')
    line, column = body.get('line', 0), body.get('column', 0)
    if any(type(v) is not int or not 0 <= v <= 10000000 for v in (line, column)):
        raise ValueError('前端异常位置不正确')
    D.emit(owner, 'local', 'ui.error', time.monotonic(), error=RuntimeError(), local_only=True,
           details={'stage': 'request', 'exception_type': kind, 'frames': [f'{file}:{line}:{column}']})
    return {'recorded': True}


def status(manager):
    owner = owner_of(manager)
    with _lock, connect(owner[2]) as db:
        row = db.execute('SELECT id,state,message,at FROM manual_reports WHERE owner=? ORDER BY at DESC,rowid DESC LIMIT 1', (owner[0],)).fetchone()
        if not row:
            return {'state': 'idle', 'message': '尚未主动上报日志'}
        rid, state, message, at = row
        if state in ('collecting', 'sending') and (owner[0], rid) not in _busy:
            state, message = 'error', '上次上报已中断，点击重试可继续发送同一份报告'
            db.execute('UPDATE manual_reports SET state=?,message=? WHERE id=?', (state, message, rid))
        return {'report_id': rid, 'state': state, 'message': message, 'created_at': at}


def capture(owner, device, rid, reason, hours):
    from . import updates, providers
    # Let already queued records reach disk, with a short bound; never block production.
    deadline = time.monotonic()+.5
    while D._queue.unfinished_tasks and time.monotonic() < deadline:
        time.sleep(.02)
    with connect(owner[2]) as db:
        args = (owner[0], 'name:'+owner[1], int(time.time())-hours*3600)
        where = 'owner IN (?,?) AND at>=?'
        total = db.execute('SELECT COUNT(*) FROM diagnostic_recent WHERE '+where, args).fetchone()[0]
        rows = db.execute('SELECT payload FROM diagnostic_recent WHERE '+where+' ORDER BY id DESC LIMIT 200', args).fetchall()
    events = [json.loads(r[0]) for r in reversed(rows)]
    build = updates.MANAGER.verified_headers.get('X-Studio-Build', '')
    import re
    if not re.fullmatch(r'(?:[a-f0-9]{64}|source|damaged)?', build):
        build = ''
    system = platform.system()
    snapshot = dict(version=D.version(), build=build,
                    system=system if system in ('Windows', 'Linux', 'Darwin') else 'other',
                    adapter_version=safe(providers.status().get('adapter_version')),
                    recording_error=D._failed, dropped=min(D._dropped, 2147483647), omitted=max(0,total-len(events)))
    body = dict(report_id=rid, device_id=device, created_at=int(time.time()), hours=hours,
                reason=reason, snapshot=snapshot, events=events)
    while len(json.dumps(body, ensure_ascii=False).encode('utf-8')) > 250000 and events:
        events.pop(0)
        snapshot['omitted'] += 1
    return body


def start(manager, body):
    owner = owner_of(manager)
    retry_id = body.get('report_id')
    reason, hours = body.get('reason', 'task'), body.get('hours', 1)
    if reason not in ('task', 'network', 'interface', 'other') or type(hours) is not int or hours not in (1, 24):
        raise ValueError('请选择问题类型和最近 1 小时或 24 小时')
    with _lock:
        if any(uid == owner[0] for uid, _ in _busy):
            raise ValueError('已有日志正在上报，请稍候')
        with connect(owner[2]) as db:
            if retry_id:
                row = db.execute('SELECT payload,state FROM manual_reports WHERE owner=? AND id=?', (owner[0], retry_id)).fetchone()
                if not row or row[1] == 'sent':
                    raise ValueError('未找到可重试的报告')
                payload = json.loads(row[0]) if row[0] else None
                if payload and '_options' in payload:
                    reason, hours = payload['_options']['reason'], payload['_options']['hours']
                    payload = None
                rid = retry_id
            else:
                rid, payload = str(uuid.uuid4()), None
                db.execute('INSERT INTO manual_reports VALUES(?,?,?,?,?,?)',
                           (rid, owner[0], int(time.time()), 'collecting', '正在收集脱敏日志',
                            json.dumps({'_options': {'reason': reason, 'hours': hours}})))
            db.execute('UPDATE manual_reports SET state=?,message=? WHERE id=?', ('collecting', '正在收集脱敏日志', rid))
            db.execute('DELETE FROM manual_reports WHERE at<? AND id<>?', (int(time.time())-D.RETENTION, rid))
            db.execute('DELETE FROM manual_reports WHERE rowid NOT IN '
                       '(SELECT rowid FROM manual_reports ORDER BY at DESC,rowid DESC LIMIT 20) '
                       'AND state NOT IN (\'collecting\',\'sending\')')
        _busy.add((owner[0], rid))
    thread = threading.Thread(target=_send, args=(manager, owner, rid, reason, hours, payload), daemon=True, name='studio-manual-report')
    try:
        thread.start()
    except Exception:
        with _lock:
            _busy.discard((owner[0], rid))
        raise
    return {'report_id': rid, 'state': 'collecting', 'message': '已开始收集，完成后将显示服务器接收结果'}


def _send(manager, owner, rid, reason, hours, payload):
    state, message = 'error', '日志暂存失败，请检查本机数据目录后重试'
    try:
        payload = payload or capture(owner, manager.device_id(), rid, reason, hours)
        with connect(owner[2]) as db:
            db.execute('UPDATE manual_reports SET payload=?,state=?,message=? WHERE id=? AND owner=?',
                       (json.dumps(payload, ensure_ascii=False), 'sending', '正在发送日志', rid, owner[0]))
        try:
            result = manager.send_client_report(payload, owner[0])
        except Exception as exc:
            code = getattr(exc, 'status', 0)
            auth_message = {
                'account_disabled': '账号已停用，请联系管理员；本次日志已保留',
                'account_expired': '账号使用期限已到，请联系管理员续期后重试；本次日志已保留',
                'invalid_session': '当前登录会话已失效，请重新登录同一账号后重试',
                'login_required': '登录续期未完成或会话已失效，请重新登录同一账号后重试',
                'refresh_reused': '当前登录会话已撤销，请重新登录同一账号后重试',
                'access_expired': '登录凭证续期后仍未通过，请重新登录同一账号后重试',
                'report_account_changed': '上报期间登录账号已改变，请登录原账号后重试',
            }.get(getattr(exc, 'code', ''))
            message = (auth_message or
                       ('服务端尚未支持主动日志上报，请管理员升级服务端后重试' if code in (404, 405) else
                       '日志上报鉴权未通过（HTTP 401），请重新登录同一账号后重试' if code == 401 else
                       '日志上报被拒绝（HTTP 403），请管理员检查访问权限；本次日志已保留' if code == 403 else
                       '服务端拒绝报告格式，请管理员检查客户端与服务端版本' if code in (413, 422) else
                       '日志发送未确认成功，请检查连接后点击重试；重试不会重复创建报告'))
            raise
        if result.get('received') is not True or result.get('report_id') != rid:
            message = '服务端未确认接收，点击重试可继续发送同一份报告'
            raise ValueError('not_acknowledged')
        state, message = 'sent', '服务器已收到日志，请把上报编号发给管理员'
    except Exception as exc:
        if str(exc) == 'account_changed':
            message = '上报期间账号已切换，请登录原账号后重试'
    finally:
        try:
            with connect(owner[2]) as db:
                db.execute('UPDATE manual_reports SET state=?,message=? WHERE owner=? AND id=?', (state, message, owner[0], rid))
        finally:
            with _lock:
                _busy.discard((owner[0], rid))
