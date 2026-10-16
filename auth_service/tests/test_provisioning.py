import uuid

from test_admin import env, login, post, PASSWORD


DEVICE = 'provision-test-device-001'


def client_token(c, name='customer'):
    r = c.post('/api/v1/auth/login', json=dict(username=name, password=PASSWORD, device_id=DEVICE))
    assert r.status_code == 200
    return {'Authorization': 'Bearer '+r.json()['access_token']}


def verify(c, headers):
    return c.post('/api/v1/auth/verify', json={'device_id': DEVICE}, headers=headers)


def event(**kw):
    return dict(event_id=str(uuid.uuid4()),sequence=1,provider='julun',model='test-model',kind='video',
                source='managed',assignment_id='test-assignment',task_id='',status='attempted',
                resumed=False,started_at=1800000000,updated_at=1800000000,**kw)


def test_assign_private_to_owner_only_and_expiry(env):
    c,a,clock=env
    h=client_token(c)
    assert c.get('/admin/api/users/customer/providers').status_code==401
    login(c)
    r=post(c,'users/customer/providers',dict(provider='julun',api_key='test-provider-secret',base_url='https://example.test'))
    assert r.status_code==200
    assert 'test-provider-secret' not in c.get('/admin/api/users/customer/providers').text
    cfg=verify(c,h).json()['provider_config']['julun']
    assert cfg['api_key']=='test-provider-secret'
    old=cfg['assignment_id']
    assert post(c,'users/customer/providers',dict(provider='julun',api_key='',base_url='https://new.example.test')).status_code==200
    cfg=verify(c,h).json()['provider_config']['julun']
    assert cfg['api_key']=='test-provider-secret' and cfg['assignment_id']!=old
    a.create_user('another',PASSWORD)
    assert verify(c,client_token(c,'another')).json()['provider_config']=={}
    a.manage_user('customer',expires_at=clock[0])
    assert verify(c,h).status_code==403
    with a.store.connect() as db:
        assert 'test-provider-secret' not in str([tuple(r) for r in db.execute('SELECT * FROM admin_audit')])


def test_remove_and_validation_csrf(env):
    c,a,clock=env
    login(c)
    assert post(c,'users/customer/providers',dict(provider='julun',api_key='example'),csrf='bad').status_code==403
    assert post(c,'users/customer/providers',dict(provider='julun')).status_code==422
    assert post(c,'users/customer/providers',dict(provider='julun',api_key='example',base_url='http://example.test')).status_code==422
    assert post(c,'users/customer/providers',dict(provider='chaomo',image_1k_api_key='th-example',image_4k_api_key='native-example')).status_code==200
    assert post(c,'users/customer/providers',dict(provider='chaomo',remove=True)).status_code==200
    assert c.get('/admin/api/users/customer/providers').json()['providers']==[]


def test_usage_upsert_out_of_order_isolation_csv(env):
    c,a,clock=env
    h=client_token(c)
    e=event()
    def report(e):
        return c.post('/api/v1/auth/usage',headers=h,json={'device_id':DEVICE,'events':[e]})
    assert report(e).status_code==200
    done=dict(e,sequence=3,status='completed',task_id='task-001')
    assert report(done).status_code==200
    assert report(e).status_code==200
    assert report(dict(done,model='another')).status_code==409
    assert report(dict(done,api_key='not-allowed')).status_code==422
    login(c)
    d=c.get('/admin/api/users/customer/usage').json()
    assert d['total']==1 and d['events'][0]['status']=='completed'
    assert d['summary'][0]['count']==1
    a.create_user('another',PASSWORD)
    assert c.get('/admin/api/users/another/usage').json()['total']==0
    assert c.get('/admin/api/users/customer/usage?source=personal').json()['total']==0
    csv=c.get('/admin/api/users/customer/usage?export=true')
    assert csv.status_code==200 and 'task-001' in csv.text
    assert 'test-provider-secret' not in csv.text
    a.manage_user('customer',enabled=False)
    assert report(dict(done,sequence=4)).status_code in (401,403)


def test_usage_csv_formula_and_filters(env):
    c,a,clock=env
    h=client_token(c)
    e=event();e.update(model='=HYPERLINK("bad")',source='personal')
    assert c.post('/api/v1/auth/usage',headers=h,json={'device_id':DEVICE,'events':[e]}).status_code==200
    login(c)
    assert "'=HYPERLINK" in c.get('/admin/api/users/customer/usage?export=true').text
    assert c.get('/admin/api/users/customer/usage?since=1800000001').json()['total']==0
    assert c.get('/admin/api/users/customer/usage?since=10&until=1').status_code==422
