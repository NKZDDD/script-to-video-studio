import hashlib
from urllib.parse import parse_qs, urlsplit

import pytest

from test_admin import env, login, post, PASSWORD
from test_provisioning import client_token, verify, DEVICE
from studio_auth import storage


CONFIG = dict(endpoint='https://test-account.r2.cloudflarestorage.com', bucket='test-bucket',
              public_base_url='https://cdn.example.test', prefix='studio-references',
              access_key='test-access-key', secret_key='test-secret-key', enabled=True)


def setup(c):
    h = client_token(c)
    login(c)
    assert post(c, 'storage', CONFIG).status_code == 200
    assert post(c, 'users/customer/storage', {'enabled': True}).status_code == 200
    cfg = verify(c, h).json()['storage_config']
    body = dict(device_id=DEVICE, revision=cfg['revision'], sha256=hashlib.sha256(b'png').hexdigest(),
                bytes=3, content_type='image/png')
    return h, cfg, body


def ticket(c, h, body):
    return c.post('/api/v1/auth/storage/sign', headers=h, json=body)


def test_admin_grants_and_downlink_never_disclose_credentials(env):
    c, a, _ = env
    assert c.get('/admin/api/storage').status_code == 401
    assert post(c, 'storage', CONFIG, csrf='bad').status_code == 401
    h, cfg, body = setup(c)
    assert post(c, 'storage', CONFIG, csrf='bad').status_code == 403
    assert cfg['assigned'] and cfg['available']
    assert cfg['prefix'].startswith('studio-references/'+verify(c,h).json()['user']['id']+'/')
    for response in (c.get('/admin/api/storage'), verify(c, h), c.get('/admin/api/users/customer/storage')):
        assert CONFIG['access_key'] not in response.text and CONFIG['secret_key'] not in response.text
    with a.store.connect() as db:
        audit = str([tuple(r) for r in db.execute('SELECT * FROM admin_audit')])
    assert CONFIG['secret_key'] not in audit and CONFIG['access_key'] not in audit
    a.create_user('another', PASSWORD)
    other=client_token(c,'another')
    assert verify(c,other).json()['storage_config']=={'assigned':False}
    assert ticket(c,other,body).status_code==403


def test_signed_put_is_bound_to_account_content_type_length_and_expiry(env):
    c, a, clock = env
    h, cfg, body = setup(c)
    a.manage_user('customer', expires_at=clock[0]+70)
    r = ticket(c, h, body)
    assert r.status_code == 200, r.text
    result = r.json()
    u = urlsplit(result['upload_url']); q = parse_qs(u.query)
    assert u.netloc == 'test-account.r2.cloudflarestorage.com'
    assert u.path == '/test-bucket/'+cfg['prefix']+'/'+body['sha256']+'.png'
    assert q['X-Amz-Expires'] == ['70']
    assert q['X-Amz-SignedHeaders'] == ['content-length;content-type;host']
    assert result['public_url'] == CONFIG['public_base_url']+'/'+cfg['prefix']+'/'+body['sha256']+'.png'
    assert result['headers'] == {'Content-Type': 'image/png'} and result['expires_at'] == clock[0]+70
    assert CONFIG['secret_key'] not in r.text
    assert r.headers['cache-control'] == 'no-store'


@pytest.mark.parametrize('change', ['revoke', 'disable', 'account', 'expire', 'device', 'logout', 'version'])
def test_unavailable_accounts_cannot_obtain_upload_signatures(env, monkeypatch, change):
    c, a, clock = env
    h, cfg, body = setup(c)
    called=[]
    monkeypatch.setattr(storage, 'sign', lambda *args: called.append(True))
    if change == 'revoke': post(c, 'users/customer/storage', {'enabled':False})
    elif change == 'disable': post(c, 'storage', dict(CONFIG, enabled=False))
    elif change == 'account': a.manage_user('customer', enabled=False)
    elif change == 'expire': a.manage_user('customer', expires_at=clock[0])
    elif change == 'device': body['device_id']='other-device-123456789'
    elif change == 'logout': post(c, 'users/customer/revoke')
    elif change == 'version':
        from test_releases import build
        post(c, 'releases', build())
    assert ticket(c, h, body).status_code in (401,403,426)
    assert not called


def test_rotation_and_old_revision_require_refresh(env):
    c, a, _ = env
    h, cfg, body = setup(c)
    assert post(c, 'storage', dict(CONFIG, access_key='', secret_key='', prefix='new-prefix')).status_code == 200
    assert ticket(c, h, body).status_code == 409
    new = verify(c, h).json()['storage_config']
    assert new['revision'] != cfg['revision'] and new['prefix'].startswith('new-prefix/')
    assert ticket(c, h, dict(body, revision=new['revision'])).status_code == 200
    with a.store.connect() as db:
        saved, _, _ = storage._load(db)
    assert saved['secret_key'] == CONFIG['secret_key']


@pytest.mark.parametrize('change', [dict(bytes=0),dict(bytes=25*1024*1024+1),dict(bytes=True),
    dict(content_type='application/x-msdownload'),dict(content_type='text/plain',bytes=1025),
    dict(key='studio-updates/all/Studio.exe'),dict(sha256='../escape'),dict(device_id='short')])
def test_upload_rejects_size_type_and_client_controlled_paths(env, monkeypatch, change):
    c, _, _ = env
    h, _, body = setup(c)
    monkeypatch.setattr(storage, 'sign', lambda *a: pytest.fail('must not sign'))
    assert ticket(c,h,dict(body,**change)).status_code == 422


@pytest.mark.parametrize('change', [dict(endpoint='http://x.r2.cloudflarestorage.com'),
    dict(endpoint='https://evil.example'),dict(public_base_url='https://127.0.0.1'),
    dict(public_base_url='https://x.r2.cloudflarestorage.com'),dict(prefix='../studio-updates'),
    dict(secret_key='中文密码'),dict(endpoint='https://key:secret@x.r2.cloudflarestorage.com')])
def test_settings_validation_does_not_echo_input(env, change):
    c, _, _ = env;login(c)
    r=post(c,'storage',dict(CONFIG,**change))
    assert r.status_code==422 and CONFIG['secret_key'] not in r.text


def test_missing_configuration_and_old_database_upgrade(env):
    c,a,_=env;login(c)
    assert post(c,'users/customer/storage',{'enabled':True}).status_code==422
    assert post(c,'storage',dict(CONFIG,secret_key='')).status_code==422
    h,cfg,body=setup(c)
    # Initialization is idempotent and preserves existing users, keys, versions and assignments.
    from studio_auth.store import Store
    Store(a.settings.database).initialize()
    assert verify(c,h).json()['storage_config']==cfg
    assert post(c,'users/customer/providers',{'provider':'julun','api_key':'migration-test-key'}).status_code==200
    # Simulate the deployed 1.6 schema: no R2 tables, but real account/session/Key data.
    with a.store.connect(write=True) as db:
        db.execute('DROP TABLE user_r2')
        db.execute('DROP TABLE r2_settings')
    Store(a.settings.database).initialize()
    after=verify(c,h).json()
    assert after['storage_config']=={'assigned':False}
    assert after['provider_config']['julun']['api_key']=='migration-test-key'
    assert c.get('/admin/storage.js').status_code==200
    assert c.get('/admin/storage.css').status_code==200
