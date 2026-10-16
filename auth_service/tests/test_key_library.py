import json

from test_admin import env, login, post
from test_provisioning import client_token, verify, event, DEVICE


def add(c, name='模型组 A', secret='example-key-a', **kw):
    r = post(c, 'keys', dict(provider='julun', name=name, api_key=secret, **kw))
    assert r.status_code == 200, r.text
    return r.json()['id']


def test_library_names_multi_account_sharing_and_model_bindings(env):
    c,a,clock = env;login(c)
    k1 = add(c, models=['gpt-image-a']);k2 = add(c, '模型组 B', 'example-key-b', models=['gpt-image-b'])
    assert post(c,'users/customer/keys',{'key_ids':[k2,k1]}).status_code == 200
    cfg = verify(c,client_token(c)).json()['provider_config']['julun']['keys']
    assert [k['key_id'] for k in cfg] == [k2,k1]
    assert cfg[0]['models'] == ['gpt-image-b'] and cfg[0]['key_name'] == '模型组 B'
    a.create_user('another', 'temporary-another-password')
    assert post(c,'users/another/keys',{'key_ids':[k1]}).status_code == 200
    with a.store.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM key_library').fetchone()[0] == 2
        assert db.execute('SELECT COUNT(*) FROM user_keys').fetchone()[0] == 3
    d = c.get('/admin/api/keys')
    assert 'example-key-' not in d.text
    assert d.json()['providers']['julun'].startswith('巨轮')


def test_library_edit_blank_preserves_id_secret_disable_and_unassign(env):
    c,a,clock = env;login(c);kid=add(c)
    post(c,'users/customer/keys',{'key_ids':[kid]})
    h=client_token(c)
    assert post(c,'keys',{'id':kid,'provider':'julun','name':'重命名','api_key':'','models':['model-new']}).status_code==200
    cfg=verify(c,h).json()['provider_config']['julun']['keys'][0]
    assert cfg['api_key']=='example-key-a' and cfg['key_name']=='重命名' and cfg['key_id']==kid
    assert post(c,'keys',{'id':kid,'provider':'julun','name':'重命名','enabled':False}).status_code==200
    assert verify(c,h).json()['provider_config']['julun']['keys']==[]
    post(c,'users/customer/keys',{'key_ids':[]})
    assert verify(c,h).json()['provider_config']=={}


def test_key_library_authorization_and_atomic_assignment(env):
    c,a,clock=env
    assert c.get('/admin/api/keys').status_code==401
    login(c);kid=add(c)
    assert post(c,'keys',{'provider':'julun','name':'a','api_key':'test'},csrf='bad').status_code==403
    post(c,'users/customer/keys',{'key_ids':[kid]})
    for ids in ([kid,kid],[kid,'missing']):
        assert post(c,'users/customer/keys',{'key_ids':ids}).status_code==422
        assert c.get('/admin/api/users/customer/keys').json()['key_ids']==[kid]
    assert post(c,'keys',{'id':kid,'provider':'chaomo','name':'a'}).status_code==422
    assert post(c,'keys',{'provider':'julun','name':'a','api_key':'x','base_url':'http://unsafe.test'}).status_code==422


def test_usage_grouping_filter_and_legacy_export(env):
    c,a,clock=env;h=client_token(c);login(c);kid=add(c)
    e1=event();e1.update(key_id=kid,key_name='模型组 A',status='completed')
    e2=event();e2.update(key_id='other',key_name='模型组 B',status='failed')
    old=event()
    assert c.post('/api/v1/auth/usage',headers=h,json={'device_id':DEVICE,'events':[e1,e2,old]}).status_code==200
    d=c.get('/admin/api/users/customer/usage?key_id='+kid).json()
    assert d['total']==1 and d['summary'][0]['key_id']==kid and d['summary'][0]['status']=='completed'
    assert c.get('/admin/api/users/customer/usage?export=true').status_code==200
    assert c.post('/api/v1/auth/usage',headers=h,json={'device_id':DEVICE,'events':[dict(e1,sequence=2,key_id='another')]}).status_code==409


def test_old_assignments_migrate_once_without_merging_secrets(env):
    c,a,clock=env
    with a.store.connect(write=True) as db:
        uid=db.execute("SELECT id FROM users WHERE username='customer'").fetchone()[0]
        db.execute('INSERT INTO provider_assignments VALUES(?,?,?,?,?)',(uid,'julun',json.dumps({'api_key':'old-example'}),'old-revision',clock[0]))
    a.store.initialize();a.store.initialize()
    login(c)
    d=c.get('/admin/api/keys').json()['keys']
    assert len(d)==1 and d[0]['provider']=='julun'
    cfg=verify(c,client_token(c)).json()['provider_config']['julun']['keys'][0]
    assert cfg['api_key']=='old-example' and cfg['assignment_id']=='old-revision'
