import json
import threading
import time
import uuid

import pytest

from studio_auth import provider_monitor as M
from test_admin import env, login, post, PASSWORD

DEVICE = 'monitor-test-device-12345'


@pytest.fixture(autouse=True)
def offline_probes(monkeypatch):
    monkeypatch.setattr(M, 'probe', lambda *a, **k: dict(at=1800000000, category='ok', status=200, duration_ms=20, model_count=2))


def setup(c, auth):
    login(c)
    response = post(c, 'keys', dict(provider='gate', name='检测 Key', api_key='never-show-this-secret', base_url='https://provider.example'))
    kid = response.json()['id']
    definition = dict(provider='gate', key_id=kid, url='https://provider.example/public/model_group/info', auth='bearer', kind='models', result_path='data')
    assert post(c, 'provider-monitor', definition).status_code == 200
    return kid, definition


def headers(auth, name='customer'):
    token = auth.login(name, PASSWORD, DEVICE, 'test', 'test')['access_token']
    return {'Authorization': 'Bearer ' + token}


def test_admin_auth_csrf_config_and_redaction(env):
    c, auth, _ = env
    assert c.get('/admin/api/provider-monitor').status_code == 401
    kid, definition = setup(c, auth)
    assert post(c, 'provider-monitor', definition, csrf='wrong').status_code == 403
    result = c.get('/admin/api/provider-monitor').json()
    assert result['monitors'][0]['key_name'] == '检测 Key'
    assert 'never-show-this-secret' not in json.dumps(result)
    assert post(c, 'provider-monitor', dict(definition, url='https://elsewhere.example/v1/models')).status_code == 422
    assert post(c, 'provider-monitor', dict(definition, url='https://127.0.0.1/health')).status_code == 422
    assert post(c, 'provider-monitor', dict(definition, key_field='video_api_key')).status_code == 422


def test_client_only_receives_selected_key_when_already_authorized(env):
    c, auth, _ = env
    kid, definition = setup(c, auth)
    h = headers(auth)
    url = '/api/v1/auth/provider-monitor/config'
    assert c.post(url, json={'device_id': DEVICE}).status_code == 401
    assert c.post(url, json={'device_id': DEVICE}, headers=h).json()['monitors'] == []
    assert post(c, 'users/customer/keys', {'key_ids': [kid]}).status_code == 200
    spec = c.post(url, json={'device_id': DEVICE}, headers=h).json()['monitors'][0]
    assert spec['secret'] == 'never-show-this-secret'
    assert post(c, 'users/customer/keys', {'key_ids': []}).status_code == 200
    assert c.post(url, json={'device_id': DEVICE}, headers=h).json()['monitors'] == []


def event(spec, **kw):
    return dict(provider=spec['provider'], revision=spec['revision'], event_id=str(uuid.uuid4()),
                at=1800000000, category='timeout', status=0, duration_ms=10000, model_count=None, **kw)


def test_reports_bound_to_account_device_revision_and_idempotent(env):
    c, auth, clock = env
    kid, definition = setup(c, auth)
    post(c, 'users/customer/keys', {'key_ids': [kid]})
    h = headers(auth)
    config = c.post('/api/v1/auth/provider-monitor/config', json={'device_id': DEVICE}, headers=h).json()
    spec = config['monitors'][0]
    h = {'Authorization': 'Bearer ' + config['monitor_token']}
    e = event(spec)
    body = {'device_id': DEVICE, 'events': [e]}
    for _ in range(2):
        assert c.post('/api/v1/auth/provider-monitor/report', json=body, headers=h).json()['accepted'] == [e['event_id']]
    rows = c.get('/admin/api/provider-monitor').json()['clients']
    assert len(rows) == 1 and rows[0]['username'] == 'customer' and rows[0]['device'] != DEVICE
    assert rows[0]['category'] == 'timeout' and not rows[0]['stale']
    for field in ['secret', 'url', 'username', 'raw_response']:
        bad = dict(body, events=[dict(e, **{field: 'private'})])
        assert c.post('/api/v1/auth/provider-monitor/report', json=bad, headers=h).status_code == 422
    clock[0] += 1000
    assert c.get('/admin/api/provider-monitor').json()['clients'][0]['stale']
    # Changing the configuration invalidates old results and queued offline reports.
    post(c, 'provider-monitor', dict(definition, auth='none'))
    assert c.get('/admin/api/provider-monitor').json()['clients'] == []
    assert c.post('/api/v1/auth/provider-monitor/report', json=dict(body, events=[event(spec)]), headers=h).status_code == 200
    with auth.store.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM provider_probe_results').fetchone()[0] == 1


def test_key_changes_and_revocation_hide_or_drop_stale_reports(env):
    c, auth, _ = env
    kid, definition = setup(c, auth)
    post(c, 'users/customer/keys', {'key_ids': [kid]})
    spec = M.definitions(auth)[0]
    h = headers(auth)
    post(c, 'users/customer/keys', {'key_ids': []})
    assert c.post('/api/v1/auth/provider-monitor/report', json={'device_id': DEVICE, 'events': [event(spec)]}, headers=h).status_code == 200
    with auth.store.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM provider_probe_results').fetchone()[0] == 0
    post(c, 'keys', dict(id=kid, provider='other', name='检测 Key', base_url='https://provider.example'))
    assert not M.definitions(auth)[0]['configured']


def test_slow_probe_does_not_lock_database_or_admin_requests(env, monkeypatch):
    c, auth, _ = env
    kid, definition = setup(c, auth)
    entered, finish = threading.Event(), threading.Event()
    def slow(*args):
        entered.set()
        assert finish.wait(10)
        return dict(at=1800000000, category='ok', status=200, duration_ms=20, model_count=2)
    monkeypatch.setattr(M, 'probe', slow)
    try:
        response = post(c, 'provider-monitor/run')
        assert response.json()['queued'] == 1 and entered.wait(3)
        # Both a read and a write finish while the external probe is still waiting.
        assert c.get('/health').status_code == 200
        assert post(c, 'provider-monitor', dict(definition, enabled=False)).status_code == 200
        assert post(c, 'provider-monitor/run').json()['queued'] == 0
    finally:
        finish.set()
        c.app.state.provider_monitor.queue.join()
    assert c.get('/admin/api/provider-monitor').json()['monitors'][0]['server'] is None


def test_retention_bounds(env, monkeypatch):
    c, auth, clock = env
    setup(c, auth)
    spec = M.definitions(auth)[0]
    monkeypatch.setattr(M, 'MAX_ROWS', 2)
    with auth.store.connect(write=True) as db:
        for _ in range(5):
            M.save_result(db, spec, 'server', event(spec), clock[0])
        assert db.execute('SELECT COUNT(*) FROM provider_probe_results').fetchone()[0] == 2
        M.prune(db, clock[0] + M.RETENTION + 1)
        assert db.execute('SELECT COUNT(*) FROM provider_probe_results').fetchone()[0] == 0
        M.save_result(db, spec, 'server', event(spec), clock[0])
    clock[0] += M.RETENTION + 1
    auth.prune()  # Scheduled maintenance also clears records when monitors are off.
    with auth.store.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM provider_probe_results').fetchone()[0] == 0


def test_scoped_monitor_token_outlives_access_but_obeys_session_and_account(env):
    c, auth, clock = env
    kid, definition = setup(c, auth)
    post(c, 'users/customer/keys', {'key_ids': [kid]})
    access = headers(auth)
    body = {'device_id': DEVICE}
    config = c.post('/api/v1/auth/provider-monitor/config', json=body, headers=access).json()
    token = config['monitor_token']
    scoped = {'Authorization': 'Bearer ' + token}
    with auth.store.connect() as db:
        assert token not in '\n'.join(db.iterdump())
    from studio_auth.service import AuthError
    with pytest.raises(AuthError):
        auth.verify(token, DEVICE)  # Cannot authorize production or regular login APIs.
    clock[0] += 1000
    assert c.post('/api/v1/auth/provider-monitor/config', json=body, headers=access).status_code == 401
    response = c.post('/api/v1/auth/provider-monitor/config', json=body, headers=scoped)
    assert response.status_code == 200 and response.json()['monitor_token'] == token
    assert c.post('/api/v1/auth/provider-monitor/config', json={'device_id':'wrong-device-12345'}, headers=scoped).status_code == 401
    auth.manage_user('customer', enabled=False)
    assert c.post('/api/v1/auth/provider-monitor/config', json=body, headers=scoped).status_code in (401, 403)
