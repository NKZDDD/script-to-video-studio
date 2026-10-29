from test_admin import env, login, post, PASSWORD
from test_releases import build, headers


def test_batch_registration_login_configuration_and_stats(env):
    c, auth, _ = env
    login(c)
    # Existing Studio minimum must not become a batch minimum or download URL.
    assert post(c, 'releases', build('2099.1.1.1')).status_code == 200
    identity = dict(flavor='batch', version='2026.10.29.1', build_sha256='c'*64)
    assert post(c, 'releases/builds', identity).status_code == 200
    h = headers('2026.10.29.1', 'c', 'batch')
    latest = c.get('/api/v1/updates/latest?flavor=batch', headers=h).json()
    assert latest['release'] is None and latest['client']['allowed']
    token = c.post('/api/v1/auth/login', headers=h, json=dict(username='customer', password=PASSWORD,
        device_id='batch-device-123456', device_name='Respect 批量客户端'))
    assert token.status_code == 200
    check = c.post('/api/v1/auth/verify', headers={**h, 'Authorization': 'Bearer '+token.json()['access_token']},
                   json={'device_id':'batch-device-123456'})
    assert check.status_code == 200 and check.json()['valid']
    assert 'provider_config' in check.json()
    stats = c.get('/admin/api/releases/statistics?flavor=batch').json()
    assert stats['flavor'] == 'batch'
    with auth.store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM client_observations WHERE flavor='batch'").fetchone()[0] == 1
    # Publishing batch policy affects batch only.
    row = build('2026.10.29.2', 'd')
    row['flavor'] = 'batch'
    assert post(c, 'releases', row).status_code == 200
    assert not c.get('/api/v1/updates/latest?flavor=batch', headers=h).json()['client']['allowed']
    assert c.get('/api/v1/updates/latest', headers=headers('2099.1.1.1')).json()['client']['allowed']


def test_batch_unknown_build_rejected_without_weakening_other_flavors(env):
    c, _, _ = env
    login(c)
    h = headers('2026.10.29.1', 'c', 'batch')
    assert not c.get('/api/v1/updates/latest?flavor=batch', headers=h).json()['client']['allowed']
    assert post(c, 'releases/builds', dict(flavor='batch',version='2026.10.29.1',build_sha256='c'*64)).status_code == 200
    assert not c.get('/api/v1/updates/latest?flavor=batch', headers=headers('2026.10.29.1','d','batch')).json()['client']['allowed']
    assert not c.get('/api/v1/updates/latest', headers=headers('2026.10.29.1','c','all')).json()['client']['allowed']
