import sqlite3

import pytest
from fastapi.testclient import TestClient

from studio_auth.app import create_app
from studio_auth.admin import COOKIE, set_admin
from studio_auth.settings import Settings
from studio_auth.store import Store

PASSWORD = 'admin-test-password-only'
ORIGIN = 'https://auth.example.test'


@pytest.fixture
def env(tmp_path):
    clock = [1800000000]
    with TestClient(create_app(Settings(tmp_path/'auth.sqlite3'), clock=lambda: clock[0]), base_url=ORIGIN) as client:
        auth = client.app.state.auth
        set_admin(auth, 'operator', PASSWORD, create=True)
        auth.create_user('customer', PASSWORD)
        yield client, auth, clock


def login(client, name='operator', password=PASSWORD):
    r = client.post('/admin/api/login', headers={'Origin':ORIGIN}, json={'username':name,'password':password})
    return r


def post(client, path, body=None, csrf=None):
    if csrf is None:
        csrf = client.get('/admin/api/me').json().get('csrf_token', '')
    return client.post('/admin/api/'+path, json=body or {}, headers={'Origin':ORIGIN, 'X-CSRF-Token':csrf})


def test_admin_separate_from_customer_and_no_leaks(env):
    c, a, _ = env
    assert c.get('/admin/api/users').status_code == 401
    assert login(c, 'customer').status_code == 401
    r = login(c)
    assert r.status_code == 200
    assert all(s in r.headers['set-cookie'] for s in ('Secure','HttpOnly','SameSite=strict','Path=/'))
    token = c.cookies.get(COOKIE)
    assert 'token' not in c.get('/admin/api/users').text
    assert 'password' not in c.get('/admin/api/users').text
    with a.store.connect() as db:
        dump = '\n'.join(db.iterdump())
    assert token not in dump and PASSWORD not in dump
    # An administrator does not implicitly become a Studio customer.
    assert c.post('/api/v1/auth/login', json={'username':'operator','password':PASSWORD,'device_id':'device-1234567890'}).status_code == 401


@pytest.mark.parametrize('origin', [None, 'https://evil.example', 'null'])
def test_csrf_origin_and_missing_token(env, origin):
    c, _, _ = env
    login(c)
    headers = {'Origin':origin} if origin else {}
    assert c.post('/admin/api/users', json={}, headers=headers).status_code == 403
    assert post(c,'users/customer/revoke',csrf='bad').status_code == 403


def test_create_update_reset_revoke_and_sessions(env):
    c, a, _ = env
    login(c)
    body = {'username':'newuser','password':PASSWORD,'expires_at':1900000000,'max_devices':2}
    assert post(c,'users',body).status_code == 200
    assert post(c,'users',body).status_code == 409
    tokens = a.login('newuser', PASSWORD, 'device-1234567890', '<script>device</script>', 'test')
    r = c.get('/admin/api/users/newuser/sessions')
    assert len(r.json()['sessions']) == 1 and 'hash' not in r.text
    assert post(c,'users/newuser/settings', {'expires_at':None}).status_code == 200
    assert a.verify(tokens['access_token'], 'device-1234567890')['valid']
    assert post(c,'users/newuser/revoke').status_code == 200
    from studio_auth.service import AuthError
    with pytest.raises(AuthError):a.verify(tokens['access_token'], 'device-1234567890')
    assert post(c,'users/newuser/settings', {'enabled':False}).status_code == 200
    with pytest.raises(AuthError):a.login('newuser', PASSWORD, 'device-1234567890', 'test', 'test')
    assert post(c,'users/newuser/settings', {'enabled':True,'max_devices':1}).status_code == 200
    assert post(c,'users/newuser/password', {'password':'new-password-123456'}).status_code == 200
    with pytest.raises(AuthError):a.login('newuser', PASSWORD, 'device-1234567890','test','test')
    assert a.login('newuser','new-password-123456','device-1234567890','test','test')['access_token']
    with a.store.connect() as db:
        actions = [r[0] for r in db.execute('SELECT action FROM admin_audit WHERE administrator=?', ('operator',))]
    assert 'create_user' in actions and 'reset_password' in actions


def test_session_expiry_logout_and_cli_password_reset(env):
    c, a, clock = env
    login(c)
    assert post(c,'logout').status_code == 200
    assert c.get('/admin/api/me').status_code == 401
    login(c)
    clock[0] += 8*3600
    assert c.get('/admin/api/users').status_code == 401
    login(c)
    set_admin(a,'operator','replacement-password-1234')
    assert c.get('/admin/api/users').status_code == 401
    assert login(c).status_code == 401


def test_pagination_search_validation_security_headers(env):
    c, _, _ = env
    login(c)
    assert c.get('/admin/api/users?q=customer').json()['total'] == 1
    assert c.get('/admin/api/users?q=%27%20OR%201=1').json()['total'] == 0
    assert c.get('/admin/api/users?page=2').json()['users'] == []
    assert post(c,'users', {'username':'invalid name','password':PASSWORD}).status_code == 422
    assert post(c,'users/customer/settings', {'max_devices':-1}).status_code == 422
    assert post(c,'users/customer/settings', {'enabled':None}).status_code == 422
    assert post(c,'users/customer/settings', {'role':'admin'}).status_code == 422
    assert post(c,'users/customer/password', {'password':'short'}).status_code == 422
    assert c.get('/admin/').status_code == 200
    assert "frame-ancestors 'none'" in c.get('/admin/').headers['content-security-policy']
    for name in ('app.js','style.css'):
        assert c.get('/admin/'+name).status_code == 200
    # Existing Python-only API keeps rejecting browser calls.
    assert c.post('/api/v1/auth/verify', json={}, headers={'Origin':ORIGIN}).status_code == 403


def test_rate_limit_admin_login(env):
    c, _, _ = env
    for _ in range(10):assert login(c,password='wrong-password').status_code == 401
    assert login(c).status_code == 429


def test_existing_v1_database_upgrades_without_losing_customer(tmp_path):
    db = tmp_path/'old.sqlite3'
    store = Store(db);store.initialize()
    with sqlite3.connect(db) as conn:
        conn.execute('INSERT INTO users VALUES(?,?,?,?,?,?,?)', ('id','olduser','hash',1,None,0,123))
        conn.execute('DROP TABLE admin_sessions');conn.execute('DROP TABLE administrators');conn.execute('DROP TABLE admin_audit')
    store.initialize()
    with store.connect() as conn:
        assert conn.execute('SELECT username FROM users').fetchone()[0] == 'olduser'
        assert conn.execute('SELECT COUNT(*) FROM administrators').fetchone()[0] == 0
