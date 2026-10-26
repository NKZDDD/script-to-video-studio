import json
import time
import uuid

import pytest

from test_admin import env, login, PASSWORD
from studio_auth import diagnostics as D

DEVICE = 'device-1234567890'


def event(**kw):
    return dict(event_id=str(uuid.uuid4()), at=1800000000, kind='auth', operation='verify',
                outcome='error', duration_ms=15000, category='timeout', **kw)


def flush(c):
    c.app.state.diagnostics.queue.join()


def account(c, **params):
    return c.get('/admin/api/users/customer/diagnostics', params=params)


def test_server_request_correlation_and_no_credentials(env):
    c, auth, _ = env
    rid = str(uuid.uuid4())
    r = c.post('/api/v1/auth/login', headers={'X-Studio-Request-Id':rid},
               json=dict(username='customer', password=PASSWORD, device_id=DEVICE))
    assert r.status_code == 200
    assert r.headers['x-studio-request-id'] == rid
    assert account(c).status_code == 401
    flush(c)
    login(c)
    data = account(c, request_id=rid).json()
    item = data['events'][0]
    assert item['source'] == 'server' and item['identity'] == 'authenticated'
    assert item['status'] == 200 and item['duration_ms'] >= 0
    assert item['operation'] == 'login'
    with auth.store.connect() as db:
        dump = str([tuple(row) for row in db.execute('SELECT * FROM request_diagnostics')])
    for secret in (PASSWORD, r.json()['access_token'], r.json()['refresh_token'], DEVICE):
        assert secret not in dump


def test_failed_login_is_unverified_claim_and_verify_error_has_account(env):
    c, auth, _ = env
    token = auth.login('customer', PASSWORD, DEVICE, 'test', 'test')['access_token']
    assert c.post('/api/v1/auth/login', json=dict(username='customer',password='wrong',device_id=DEVICE)).status_code == 401
    assert c.post('/api/v1/auth/verify', headers={'Authorization':'Bearer '+token},
                  json={'device_id':'wrong-device-123456'}).status_code == 401
    flush(c)
    login(c)
    data = account(c, outcome='error').json()['events']
    assert len(data) == 2
    assert {e['identity'] for e in data} == {'claimed', 'session'}
    assert all(e['status'] == 401 for e in data)


def test_client_reports_bound_to_authenticated_account_idempotent_and_private(env):
    c, auth, _ = env
    auth.create_user('another', PASSWORD)
    tokens = auth.login('customer', PASSWORD, DEVICE, 'test', 'test')
    headers = {'Authorization':'Bearer '+tokens['access_token']}
    item = event(request_id=str(uuid.uuid4()))
    body = dict(device_id=DEVICE, events=[item])
    assert c.post('/api/v1/auth/diagnostics', json=body).status_code == 401
    assert c.post('/api/v1/auth/diagnostics', json=dict(body, user_id='another'),headers=headers).status_code == 422
    for _ in range(2):
        r = c.post('/api/v1/auth/diagnostics', json=body,headers=headers)
        assert r.json()['accepted'] == [item['event_id']]
    login(c)
    rows = account(c, source='client').json()['events']
    assert len(rows) == 1 and rows[0]['identity'] == 'reported'
    assert rows[0]['duration_ms'] == 15000 and rows[0]['category'] == 'timeout'
    assert c.get('/admin/api/users/another/diagnostics').json()['total'] == 0
    for field, value in [('message','secret'),('host','https://user:secret@example.test/path?key=secret'),
                         ('operation','POST /path?key=secret'),('category','invented')]:
        assert c.post('/api/v1/auth/diagnostics', json=dict(body,events=[dict(item,**{field:value})]),headers=headers).status_code == 422
    assert account(c, source="' OR 1=1--").status_code == 422


def test_retention_and_row_cap(env, monkeypatch):
    c, auth, clock = env
    monkeypatch.setattr(D, 'MAX_ROWS', 3)
    with auth.store.connect(write=True) as db:
        for i in range(6):
            D.save(db, 'test', 'customer', 'client', event(), clock[0]-i)
        D.prune(db, clock[0])
        assert db.execute('SELECT COUNT(*) FROM request_diagnostics').fetchone()[0] == 3
    clock[0] += D.RETENTION+1
    auth.prune()
    with auth.store.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM request_diagnostics').fetchone()[0] == 0


def test_log_failure_does_not_break_requests_and_admin_reports_gap(env, monkeypatch):
    c, auth, _ = env
    def fail(*args):
        raise OSError('no disk')
    monkeypatch.setattr(D, 'save', fail)
    r = c.post('/api/v1/auth/login', json=dict(username='customer',password=PASSWORD,device_id=DEVICE))
    assert r.status_code == 200
    flush(c)
    login(c)
    data = account(c).json()
    assert data['server_recording_error'] and data['server_dropped_since_start'] == 1
    assert c.get('/admin/diagnostics.js').status_code == 200


def test_body_limits_and_telemetry_does_not_generate_more_request_logs(env):
    c, auth, _ = env
    token = auth.login('customer', PASSWORD, DEVICE, 'test', 'test')['access_token']
    r = c.post('/api/v1/auth/diagnostics',headers={'Authorization':'Bearer '+token},
               json=dict(device_id=DEVICE,events=[event() for _ in range(21)]))
    assert r.status_code == 422
    flush(c)
    login(c)
    assert account(c, source='server').json()['total'] == 0


def test_diagnostics_page_filters_escapes_and_ignores_closed_results():
    from pathlib import Path
    import shutil
    import subprocess
    if not shutil.which('node'):
        pytest.skip('Node.js required for browser logic test')
    script = r'''
const assert=require('node:assert/strict');
const elements=new Map();let listener,dialog,requests=[],deferred;
function element(id){if(!elements.has(id))elements.set(id,{innerHTML:'',textContent:'',open:false,
 reset(){this.fields={hours:'24',source:'',outcome:'',kind:'',request_id:''};},
 replaceChildren(){this.innerHTML='';},showModal(){this.open=true;},close(){this.open=false;this.onclose?.();},
 querySelector:element,addEventListener(type,fn){listener=fn;}});return elements.get(id);}
global.document={createElement(){dialog=element('dialog');return dialog;},body:{append(){}},querySelector:element};
global.FormData=class{constructor(form){this.fields=form.fields;}*[Symbol.iterator](){yield* Object.entries(this.fields);}};
global.rows=[{username:'customer'}];global.esc=v=>String(v??'').replaceAll('<','&lt;').replaceAll('>','&gt;');
global.date=v=>'time-'+v;
global.api=async path=>{requests.push(path);if(deferred)return await new Promise(r=>deferred=r);
 return {total:51,events:[{at:1,received_at:2,source:'client',kind:'llm',operation:'<script>',duration_ms:15000,
 outcome:'error',category:'timeout',status:0,request_id:'<img>',device:'<svg>',version:'test'}]};};
'''
    script += (Path(__file__).resolve().parents[1]/'studio_auth/web/diagnostics.js').read_text(encoding='utf-8')
    script += r'''
(async()=>{
 const tick=()=>new Promise(r=>setImmediate(r));let stopped=false;
 listener({target:{closest:()=>({dataset:{index:'0'}})},stopImmediatePropagation(){stopped=true;}});
 await tick();assert(stopped&&dialog.open);assert(requests[0].startsWith('users/customer/diagnostics?'));
 assert(element('#diagnostics-rows').innerHTML.includes('&lt;script&gt;'));
 assert(!element('#diagnostics-rows').innerHTML.includes('<script>'));
 assert(element('#diagnostics-count').textContent.includes('51'));
 element('#diagnostics-next').onclick();await tick();assert(requests.at(-1).includes('page=2'));
 const form=element('#diagnostics-filter');form.fields.outcome='error';form.onsubmit({preventDefault(){}});
 await tick();assert(requests.at(-1).includes('outcome=error')&&requests.at(-1).includes('page=1'));
 deferred=true;form.onsubmit({preventDefault(){}});await tick();dialog.close();
 deferred({total:0,events:[]});await tick();assert.equal(element('#diagnostics-rows').innerHTML,'');
})().catch(e=>{console.error(e);process.exitCode=1;});
'''
    result = subprocess.run(['node','-'],input=script,text=True,encoding='utf-8',capture_output=True,timeout=20)
    assert result.returncode == 0, result.stderr
