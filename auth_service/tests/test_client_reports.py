import json
import uuid

import pytest

from test_admin import env, login, PASSWORD
from studio_auth import client_reports as R

DEVICE='device-1234567890'


def report():
    return dict(report_id=str(uuid.uuid4()),device_id=DEVICE,created_at=1800000000,hours=1,reason='task',
                snapshot={'version':'2026.11.04.1','system':'Windows'},events=[dict(
                    event_id=str(uuid.uuid4()),at=1800000000,kind='provider',operation='query',outcome='error',
                    duration_ms=1000,status=200,category='authentication',details=dict(
                        provider='provider-any',model='model-fast-full',stage='query',http_status=200,
                        upstream_status=401,key_fingerprint='a'*24,task_id='task-1234567890',
                        frames=['apiutil.py:1100:request']))])


def headers(auth):
    token=auth.login('customer',PASSWORD,DEVICE,'test','test')['access_token']
    return {'Authorization':'Bearer '+token}


def test_manual_report_authenticated_idempotent_admin_scoped_and_filtered(env):
    c,auth,_=env;body=report();h=headers(auth)
    assert c.post('/api/v1/auth/client-reports',json=body).status_code==401
    for _ in range(2):
        result=c.post('/api/v1/auth/client-reports',json=body,headers=h)
        assert result.status_code==200,result.text
        assert result.json()=={'received':True,'report_id':body['report_id']}
    root='/admin/api/users/customer/client-reports'
    assert c.get(root).status_code==401
    login(c)
    rows=c.get(root).json();assert rows['total']==1
    device=rows['reports'][0]['device']
    assert device!=DEVICE and c.get(root,params={'device':device}).json()['total']==1
    assert c.get(root,params={'device':'f'*16}).json()['total']==0
    detail=c.get(root+'/'+body['report_id']).json()
    assert detail['events'][0]['details']['upstream_status']==401
    assert DEVICE not in json.dumps(detail) and 'device_id' not in detail
    auth.create_user('another',PASSWORD)
    assert c.get('/admin/api/users/another/client-reports/'+body['report_id']).status_code==404
    changed=dict(body,reason='other')
    assert c.post('/api/v1/auth/client-reports',json=changed,headers=h).status_code==409
    assert c.get(root,params={'device':"' OR 1=1"}).status_code==422


@pytest.mark.parametrize('change', ['key','path','extra','count'])
def test_manual_report_rejects_raw_secrets_and_unknown_fields(env,change):
    c,auth,_=env;body=report()
    if change=='key':body['events'][0]['details']['key_id']='sk-must-never-log'
    if change=='path':body['events'][0]['details']['endpoint']='/query?token=secret'
    if change=='extra':body['events'][0]['details']['prompt']='private prompt'
    if change=='count':body['events']*=201
    assert c.post('/api/v1/auth/client-reports',json=body,headers=headers(auth)).status_code==422


def test_manual_size_limit_and_retention(env,monkeypatch):
    c,auth,clock=env;h=headers(auth)
    assert c.post('/api/v1/auth/client-reports',content=' '*262145,headers=dict(h,**{'Content-Type':'application/json'})).status_code==413
    monkeypatch.setattr(R,'MAX_ROWS',2)
    for _ in range(3):assert c.post('/api/v1/auth/client-reports',json=report(),headers=h).status_code==200
    login(c);assert c.get('/admin/api/users/customer/client-reports').json()['total']==2
    clock[0]+=R.RETENTION+1;auth.prune()
    with auth.store.connect() as db:assert db.execute('SELECT COUNT(*) FROM client_reports').fetchone()[0]==0


def test_legacy_diagnostics_still_accept_unchanged_events(env):
    c,auth,_=env;body=report();event=body['events'][0];event.pop('details')
    assert c.post('/api/v1/auth/diagnostics',json={'device_id':DEVICE,'events':[event]},headers=headers(auth)).status_code==200


def test_real_client_capture_and_trace_are_accepted_end_to_end(env,tmp_path,monkeypatch):
    import sys
    import queue
    import threading
    from pathlib import Path
    from types import SimpleNamespace
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2]))
    from core import client_reports as C,cloud_diagnostics as D,request_trace as T
    from core.apiutil import HttpSession,ApiError
    from core.studio_login import LoginManager
    import requests
    c,auth,_=env;h=headers(auth)
    manager=LoginManager();manager.user={'id':'local-id','username':'customer'}
    manager._diagnostics_dir=str(tmp_path);manager._device=DEVICE
    monkeypatch.setattr(D,'_manager',None);D.activate(manager)
    q=queue.Queue(maxsize=20);monkeypatch.setattr(D,'_queue',q);monkeypatch.setattr(D,'_thread',threading.current_thread())
    session=HttpSession('sk-private-credential','https://example.test',proxy='direct')
    response=requests.Response();response.status_code=401;response._content=b'{"error":"Unauthorized"}'
    monkeypatch.setattr('core.apiutil.requests.request',lambda *a,**kw:response)
    with pytest.raises(ApiError),T.production(D.identity(manager),[SimpleNamespace(root='private-project'),{'key':'task1'},'',SimpleNamespace(id='custom',session=session),None],{'meta':{'model':'model1'}}):
        session.request('POST','/v1/images/generations',json_body={'prompt':'private-prompt'})
    original=q.get
    def get():
        if q.empty():raise KeyboardInterrupt()
        return original()
    monkeypatch.setattr(q,'get',get)
    with pytest.raises(KeyboardInterrupt):D._writer()
    payload=C.capture(D.identity(manager),DEVICE,str(uuid.uuid4()),'task',1)
    result=c.post('/api/v1/auth/client-reports',json=payload,headers=h)
    assert result.status_code==200,result.text
    login(c);data=c.get('/admin/api/users/customer/client-reports/'+payload['report_id']).json()
    assert len(data['events'])==2 and data['events'][0]['details']['http_status']==401
    for value in ('sk-private-credential','private-project','private-prompt'):
        assert value not in json.dumps(data)


@pytest.mark.parametrize('account_expired',[False,True])
def test_client_report_recovers_access_expiry_with_real_auth_service(env,tmp_path,monkeypatch,account_expired):
    import sys
    from pathlib import Path
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2]))
    from core.studio_login import LoginManager,LoginRequired
    from core import cloud_adapters,cloud_config,cloud_storage,cloud_diagnostics,cloud_usage,provider_monitor
    c,auth,clock=env
    tokens=auth.login('customer',PASSWORD,DEVICE,'test','test')
    manager=LoginManager();manager._device=DEVICE;manager._diagnostics_dir=str(tmp_path)
    manager._set_tokens(tokens);manager.user=tokens['user'];manager.local_session='local-cookie'
    for module in (cloud_adapters,cloud_config,cloud_storage):monkeypatch.setattr(module,'install',lambda *a:None)
    monkeypatch.setattr(cloud_diagnostics,'activate',lambda *a:None)
    monkeypatch.setattr(cloud_usage,'start_sender',lambda *a:None)
    monkeypatch.setattr(provider_monitor,'start',lambda *a:None)
    calls=[]
    def post(action,body,access=None):
        result=c.post('/api/v1/auth/'+action,json=body,headers={'Authorization':'Bearer '+access} if access else {})
        calls.append((action,result.status_code))
        if result.status_code>=400:
            err=result.json()['error']
            raise LoginRequired(err['code'],err['message'],result.status_code)
        return result.json()
    monkeypatch.setattr(manager,'_post',post)
    clock[0]+=auth.settings.access_seconds+1
    if account_expired:auth.manage_user('customer',expires_at=clock[0]-1)
    # Ordinary local navigation remains authorized while short-lived access expires.
    manager.require('respect_studio_login=local-cookie')
    body=report()
    if account_expired:
        with pytest.raises(LoginRequired) as exc:manager.send_client_report(body,tokens['user']['id'])
        assert exc.value.code=='account_expired' and manager.tokens is None
        assert calls==[('client-reports',401),('verify',401),('refresh',403)]
    else:
        assert manager.send_client_report(body,tokens['user']['id'])=={'received':True,'report_id':body['report_id']}
        assert calls==[('client-reports',401),('verify',401),('refresh',200),('verify',200),('client-reports',200)]
        assert manager.tokens['access_token']!=tokens['access_token']
    with auth.store.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM client_reports').fetchone()[0]==(0 if account_expired else 1)
