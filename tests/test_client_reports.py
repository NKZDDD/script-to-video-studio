import json
import queue
import threading
import time
from types import SimpleNamespace

import pytest
import requests

from core import client_reports as R, cloud_diagnostics as D, request_trace as T
from core.apiutil import HttpSession, ApiError
from core.studio_login import LoginRequired
from test_cloud_diagnostics import recorder


def response(status=200, data=None):
    r=requests.Response();r.status_code=status
    r._content=json.dumps(data or {}).encode();r.encoding='utf-8'
    r.headers['x-request-id']='request-1234567890'
    return r


def scope(mgr, session, pid='provider-a', model='model-1', rec=None):
    prov=SimpleNamespace(id=pid,session=session)
    return T.production(D.identity(mgr), [SimpleNamespace(root='private-path'),{'key':'task-a'},'out',prov,rec],
                        {'meta':{'model':model,'_usage':{'source':'managed','key_id':'key-id'}}})


def drain(q):
    out=[]
    while not q.empty():
        _,e=q.get_nowait();q.task_done();out.append(e)
    return out


def write_queue(q, monkeypatch):
    original=q.get
    def get():
        if q.empty():raise KeyboardInterrupt()
        return original()
    with monkeypatch.context() as m:
        m.setattr(q,'get',get)
        with pytest.raises(KeyboardInterrupt):D._writer()


@pytest.mark.parametrize('pid',['gate','julun','provider-custom'])
def test_generic_request_trace_distinguishes_outer_and_upstream_status_and_redacts(recorder, monkeypatch, pid):
    mgr,q=recorder
    session=HttpSession('sk-private-key','https://gateway.test',proxy='http://user:password@proxy.test:1234')
    replies=iter([response(data={'task_id':'task-1234567890'}),response(data={'status':'running'}),
                  response(data={'status':'running'}), response(data={'error':{'code':'AuthenticationError',
                  'message':"ARK API HTTP 401: The API key doesn't exist. Request id: upstream-1234567890 sk-private-key secret-prompt"}})])
    monkeypatch.setattr('core.apiutil.requests.request',lambda *a,**k:next(replies))
    with scope(mgr,session,pid):
        session.request('POST','/api/multimodal/create_task',json_body={'model':'model-fast-full','prompt':'secret-prompt'})
        for _ in range(2):session.request('POST','/api/multimodal/get_result',json_body={'model':'model-fast-full','taskId':'task-1234567890'})
        result=session.request('POST','/api/multimodal/get_result',json_body={'model':'model-fast-full','taskId':'task-1234567890'})
        assert result['error']['code']=='AuthenticationError'  # Logging must not alter provider handling.
    events=drain(q)
    assert [e['operation'] for e in events]==['submit','query','query','complete']
    fail=events[2]
    assert fail['status']==200 and fail['outcome']=='error'
    assert fail['details']['http_status']==200 and fail['details']['upstream_status']==401
    assert fail['details']['upstream_request_id']=='upstream-1234567890'
    assert fail['details']['provider_request_id']=='request-1234567890'
    assert fail['details']['key_fingerprint']==T.fingerprint('sk-private-key')
    assert fail['details']['provider']==pid and fail['details']['model']=='model-fast-full'
    assert fail['details']['proxy_host']=='proxy.test'
    text=json.dumps(events)
    for secret in ('sk-private-key','secret-prompt','password@','private-path'):
        assert secret not in text
    assert all(e['_local_only'] for e in events)


def test_real_request_credentials_override_default_fingerprint_and_401_is_retained(recorder,monkeypatch):
    mgr,q=recorder;session=HttpSession('default-key','https://example.test')
    monkeypatch.setattr('core.apiutil.requests.request',lambda *a,**k:response(401,{'error':'Unauthorized'}))
    with pytest.raises(ApiError), scope(mgr,session):
        session.request('POST','/v1/images/generations',headers={'Authorization':'Bearer actual-key'},retries=1)
    event=drain(q)[0]
    assert event['details']['http_status']==401
    assert event['details']['key_fingerprint']==T.fingerprint('actual-key')


def test_context_isolation_download_and_local_error_frames(recorder,monkeypatch):
    mgr,q=recorder;session=HttpSession('key','https://example.test')
    @T.download_call
    def save(session,item,dest):raise OSError('private-path secret-prompt')
    with pytest.raises(OSError),scope(mgr,session):save(session,'https://cdn.test/file?token=secret','private-path')
    events=drain(q);assert events[0]['operation']=='download'
    assert events[0]['details']['frames']
    assert 'secret' not in json.dumps(events)
    assert T._current.get() is None
    monkeypatch.setattr('core.apiutil.requests.request',lambda *a,**k:response())
    session.request('GET','/v1/models')
    assert q.empty() # No production context, no extra automatic request telemetry.


def test_detailed_logs_are_local_until_manual_report_and_base_telemetry_stays_compatible(recorder,monkeypatch):
    mgr,q=recorder
    D.emit(D.identity(mgr),'provider','query',time.monotonic(),details={'provider':'sample'},local_only=True)
    D.emit(D.identity(mgr),'local','/api/agent/plan',time.monotonic(),details={'exception_type':'ValueError'})
    write_queue(q,monkeypatch)
    calls=[]
    mgr._post=lambda a,b,t: calls.append((a,b)) or {'accepted':[e['event_id'] for e in b['events']]}
    D.send(mgr)
    assert len(calls)==1 and calls[0][0]=='diagnostics'
    assert len(calls[0][1]['events'])==1 and 'details' not in calls[0][1]['events'][0]
    captured=R.capture(D.identity(mgr),mgr.device_id(),'a'*36,'task',1)
    assert len(captured['events'])==2 and captured['events'][0]['details']['provider']=='sample'
    assert len(calls)==1 # Capture itself never sends.


def wait_done(mgr):
    deadline=time.monotonic()+4
    while time.monotonic()<deadline:
        status=R.status(mgr)
        if status['state'] not in ('collecting','sending'):return status
        time.sleep(.01)
    pytest.fail('manual report did not finish')


def test_manual_nonblocking_failure_retry_same_payload_and_no_automatic_resend(recorder,monkeypatch):
    mgr,q=recorder;entered,release=threading.Event(),threading.Event();calls=[]
    def post(action,body,access):
        calls.append((action,body));entered.set();assert release.wait(2)
        raise TimeoutError('private-url')
    mgr._post=post
    created=R.start(mgr,{'reason':'network','hours':1})
    assert entered.wait(2)
    assert R.status(mgr)['state']=='sending'
    assert mgr.lock.acquire(timeout=.1);mgr.lock.release()
    with pytest.raises(ValueError):R.start(mgr,{})
    release.set();failed=wait_done(mgr)
    assert failed['state']=='error' and failed['report_id']==created['report_id']
    assert len(calls)==1 and 'private-url' not in failed['message']
    payload=calls[0][1]
    mgr._post=lambda a,b,t:calls.append((a,b)) or {'received':True,'report_id':b['report_id']}
    R.start(mgr,{'report_id':created['report_id']})
    assert wait_done(mgr)['state']=='sent'
    assert calls[-1][1]==payload
    with pytest.raises(ValueError):R.start(mgr,{'report_id':created['report_id']})


def test_manual_account_isolation_and_old_server_error(recorder):
    mgr,q=recorder
    def old(*a):raise LoginRequired('old','raw response',404)
    mgr._post=old;created=R.start(mgr,{})
    assert '升级服务端' in wait_done(mgr)['message']
    mgr.user={'id':'bob-id','username':'bob'}
    assert R.status(mgr)['state']=='idle'
    with pytest.raises(ValueError):R.start(mgr,{'report_id':created['report_id']})


def test_capture_bounds_and_retention(recorder):
    mgr,q=recorder;owner=D.identity(mgr)
    with R.connect(owner[2]) as db:
        for i in range(220):
            db.execute('INSERT INTO diagnostic_recent(owner,at,payload) VALUES(?,?,?)',
                       (owner[0],int(time.time()),json.dumps({'event_id':str(i)})))
        db.execute('INSERT INTO diagnostic_recent(owner,at,payload) VALUES(?,?,?)', ('bob-id',int(time.time()),'{}'))
    got=R.capture(owner,mgr.device_id(),'a'*36,'other',1)
    assert len(got['events'])==200 and got['snapshot']['omitted']==20
    assert got['events'][0]['event_id']=='20'


def test_invalid_manual_range_does_not_send(recorder):
    mgr,q=recorder
    for body in ({'hours':True},{'hours':2},{'reason':'gate_only'}):
        with pytest.raises(ValueError):R.start(mgr,body)


def test_frontend_record_contains_only_allowed_local_evidence(recorder):
    mgr,q=recorder
    R.frontend_error(mgr,{'error_type':'TypeError','file':'agent.js','line':42,'column':3})
    event=drain(q)[0]
    assert event['_local_only'] and event['operation']=='ui.error'
    assert event['details']['frames']==['agent.js:42:3']
    with pytest.raises(ValueError):R.frontend_error(mgr,{'error_type':'TypeError','file':'C:/private/file.js'})


def test_additional_local_actions_are_archived_without_expanding_automatic_reports(recorder,monkeypatch):
    mgr,q=recorder
    class Handler:
        server=SimpleNamespace(studio_login=mgr)
        path='/api/agent/models/refresh?root=private-path'
        @D.local_request
        def run(self):
            try:raise ValueError('secret prompt')
            except ValueError as exc:self._diagnostic_exception=exc
            self._diagnostic_status=500
    Handler().run()
    owner,event=q.queue[0]
    assert event['_local_only'] and event['details']['exception_type']=='ValueError'
    write_queue(q,monkeypatch)
    with R.connect(owner[2]) as db:
        assert db.execute('SELECT COUNT(*) FROM diagnostics').fetchone()[0]==0
        assert db.execute('SELECT COUNT(*) FROM diagnostic_recent').fetchone()[0]==1
