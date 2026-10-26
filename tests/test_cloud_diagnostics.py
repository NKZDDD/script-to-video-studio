import json
import queue
import socket
import ssl
import threading
import time
from types import SimpleNamespace
from urllib.error import HTTPError, URLError

import pytest

from core import cloud_diagnostics as D, studio_login as L, paths


@pytest.mark.parametrize('error,category,status', [
    (URLError(socket.gaierror(-2,'secret hostname')), 'dns', 0),
    (URLError(ssl.SSLError('secret certificate')), 'tls', 0),
    (TimeoutError('secret endpoint'), 'timeout', 0),
    (HTTPError('https://secret/?key=secret',401,'bad',{},None), 'authentication',401),
    (ConnectionError('secret proxy'), 'connection',0),
    (ValueError('secret body'), 'validation',0),
    (RuntimeError('HTTP 502 secret body'), 'http',502),
])
def test_error_category_without_raw_error_payload(error, category, status):
    assert D.classify(error) == (category,status)


@pytest.fixture
def recorder(tmp_path, monkeypatch):
    captured = queue.Queue(maxsize=10)
    monkeypatch.setattr(D, '_queue', captured)
    monkeypatch.setattr(D, '_thread', threading.current_thread())
    monkeypatch.setattr(D, '_manager', None)
    monkeypatch.setattr(D, '_dropped', 0)
    manager=L.LoginManager()
    manager.user={'id':'alice-id','username':'alice'}
    manager.tokens={'access_token':'test-access'}
    manager._diagnostics_dir=str(tmp_path)
    manager._device='device-1234567890'
    D.activate(manager)
    return manager,captured


def test_emit_has_no_disk_network_or_sensitive_values(recorder, monkeypatch):
    manager,q=recorder
    monkeypatch.setattr(D,'connect',lambda *a: pytest.fail('disk on event path'))
    D.emit(D.identity(), 'llm','chat',time.monotonic(),error=TimeoutError('key-secret prompt-secret'),
           target='https://user:password-secret@example.test/path?key=query-secret')
    owner, event=q.get_nowait()
    assert owner[0]=='alice-id'
    assert event['host']=='example.test' and event['category']=='timeout'
    assert 'secret' not in json.dumps(event)
    # Full queue must not wait for the slow writer.
    for _ in range(20):
        D.emit(owner,'local','test',time.monotonic())
    assert D._dropped==10


def insert(directory, owner, at=None):
    db=D.connect(directory)
    try:
        with db:
            db.execute('INSERT INTO diagnostics(owner,at,payload) VALUES(?,?,?)',
                       (owner,at or int(time.time()),json.dumps({'event_id':owner})))
    finally:
        db.close()


def test_outbox_retry_ack_account_isolation_and_retention(recorder):
    mgr,q=recorder
    for owner in ('alice-id','name:alice','bob-id'):
        insert(mgr._diagnostics_dir,owner)
    insert(mgr._diagnostics_dir,'old',int(time.time())-D.RETENTION-1)
    calls=[]
    def offline(*args):
        calls.append(args)
        raise TimeoutError('offline')
    mgr._post=offline
    with pytest.raises(TimeoutError):D.send(mgr)
    D.send(mgr)
    assert len(calls)==1 # backoff, no forced verification/refresh
    assert {e['event_id'] for e in calls[0][1]['events']}=={'alice-id','name:alice'}
    mgr._diagnostics_retry_at=0
    mgr._post=lambda action,body,token: {'accepted':[e['event_id'] for e in body['events']]}
    D.send(mgr)
    db=D.connect(mgr._diagnostics_dir)
    try:assert db.execute('SELECT owner FROM diagnostics').fetchall()==[('bob-id',)]
    finally:db.close()


def test_inflight_work_keeps_original_account_and_ignores_secrets(recorder):
    mgr,q=recorder
    @D.llm_call
    def chat(self,system,user):
        mgr.user={'id':'bob-id','username':'bob'}
        raise TimeoutError('secret')
    with pytest.raises(TimeoutError):chat(SimpleNamespace(base_url='https://llm.example'), 'secret prompt','secret prompt')
    owner,event=q.get_nowait()
    assert owner[0]=='alice-id' and event['kind']=='llm'
    assert D.identity()[0]=='bob-id'


def test_auth_transport_retains_diagnostic_type_and_request_id(recorder, monkeypatch):
    mgr,q=recorder
    from core import updates
    monkeypatch.setattr(updates.MANAGER,'require_allowed',lambda: {})
    def fail(request,**kw):
        assert request.get_header('X-studio-request-id')
        raise URLError(socket.gaierror(-2,'private DNS name'))
    mgr.opener=SimpleNamespace(open=fail)
    with pytest.raises(L.LoginRequired) as error:
        mgr._post('verify',{'device_id':mgr.device_id()}, 'test-access')
    assert error.value.code=='auth_unavailable'
    owner,event=q.get_nowait()
    assert event['category']=='dns' and event['status']==0
    assert len(event['request_id'])==36
    with pytest.raises(L.LoginRequired):mgr._post('diagnostics',{},'test-access')
    assert q.empty() # uploader cannot recursively record its own failures


def test_local_operations_record_status_and_never_record_query_paths(recorder):
    mgr,q=recorder
    class Handler:
        server=SimpleNamespace(studio_login=mgr)
        path='/api/agent/selections?root=private-project'
        @D.local_request
        def run(self):
            self._diagnostic_status=500
            return 'unchanged result'
    assert Handler().run()=='unchanged result'
    _,event=q.get_nowait()
    assert event['kind']=='local' and event['operation']=='/api/agent/selections'
    assert event['status']==500 and event['outcome']=='error'
    assert 'private-project' not in json.dumps(event)


def test_unrelated_file_routes_do_not_need_diagnostic_login_state():
    class Handler:
        path='/file?root=private-path'
        @D.local_request
        def run(self):
            return 'file contents'
    assert Handler().run()=='file contents'
    assert D.identity(SimpleNamespace()) is None


def test_worker_count_and_retention_bounds(recorder, monkeypatch):
    mgr,q=recorder
    monkeypatch.setattr(D,'MAX_ROWS',2)
    insert(mgr._diagnostics_dir,'old',int(time.time())-D.RETENTION-1)
    for _ in range(5):D.emit(D.identity(), 'auth','login',time.monotonic())
    # Run the real writer against a finite queue in this thread.
    original=q.get
    def get():
        if q.empty():raise KeyboardInterrupt()
        return original()
    monkeypatch.setattr(q,'get',get)
    with pytest.raises(KeyboardInterrupt):D._writer()
    db=D.connect(mgr._diagnostics_dir)
    try:
        assert db.execute('SELECT COUNT(*) FROM diagnostics').fetchone()[0]==2
        assert not db.execute('SELECT id FROM diagnostics WHERE owner="old"').fetchall()
    finally:db.close()


def test_outbox_follows_data_directory_redirect(tmp_path):
    original, target = tmp_path/'old', tmp_path/'new'
    original.mkdir();target.mkdir()
    (target/'config.json').write_text('{}')
    (original/paths.DATA_REDIRECT).write_text(json.dumps({'data_dir':str(target)}))
    db = D.connect(original)
    try:
        assert (target/'usage-outbox.sqlite3').is_file()
        assert not (original/'usage-outbox.sqlite3').exists()
    finally:
        db.close()
