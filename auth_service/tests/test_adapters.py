import builtins
import copy
import pytest
from test_admin import env, login, post, PASSWORD
from test_provisioning import client_token, verify, DEVICE


def bundle(version='1'):
    return {'protocol':1,'version':version,'modules':{'example':'import builtins\nbuiltins.adapter_should_not_execute = True\nclass Example: pass\n'},
            'providers':[{'id':'julun','name':'测试服务商','module':'example','class':'Example'}]}


def setup(c):
    h=client_token(c);login(c)
    assert post(c,'users/customer/providers',{'provider':'julun','api_key':'test-private-provider-key'}).status_code==200
    r=post(c,'adapters/import',{'bundle':bundle()});assert r.status_code==200,r.text
    assert post(c,'adapters/publish',{'sha256':r.json()['sha256']}).status_code==200
    return h,verify(c,h).json()['adapter_config']


def download(c,h,meta):
    return c.post('/api/v1/auth/adapters',headers=h,json={'device_id':DEVICE,'sha256':meta['sha256']})


def test_import_is_inert_publish_download_and_account_isolation(env):
    c,a,_=env
    assert c.get('/admin/api/adapters').status_code==401
    h,meta=setup(c)
    assert meta['enabled'] and meta['providers']==['julun']
    r=download(c,h,meta);assert r.status_code==200
    assert r.json()['bundle']==bundle()
    assert not hasattr(builtins,'adapter_should_not_execute')
    assert 'test-private-provider-key' not in r.text
    assert r.headers['cache-control']=='no-store'
    a.create_user('another',PASSWORD)
    other=client_token(c,'another');m=verify(c,other).json()['adapter_config']
    assert download(c,other,meta).status_code==409
    assert download(c,other,m).json()['bundle']['modules']=={}
    assert post(c,'adapters/import',{'bundle':bundle()},csrf='bad').status_code==403


def test_import_does_not_publish_and_rollback_preserves_keys(env):
    c,a,_=env
    h,meta=setup(c)
    r=post(c,'adapters/import',{'bundle':bundle('2')})
    assert r.status_code==200
    assert verify(c,h).json()['adapter_config']==meta
    original=c.get('/admin/api/adapters').json()['active']
    post(c,'adapters/publish',{'sha256':r.json()['sha256']})
    assert download(c,h,meta).status_code==409
    post(c,'adapters/publish',{'sha256':original})
    assert download(c,h,meta).status_code==200
    assert verify(c,h).json()['provider_config']['julun']['api_key']=='test-private-provider-key'


@pytest.mark.parametrize('change',['disabled','expired','revoked','device','version','unassigned'])
def test_download_checks_current_authorization(env,change):
    c,a,clock=env
    h,meta=setup(c)
    if change=='disabled': a.manage_user('customer',enabled=False)
    if change=='expired': a.manage_user('customer',expires_at=clock[0])
    if change=='revoked': post(c,'users/customer/revoke')
    if change=='device':
        r=c.post('/api/v1/auth/adapters',headers=h,json={'device_id':'another-device-123456','sha256':meta['sha256']})
        assert r.status_code==401;return
    if change=='version':
        from test_releases import build
        post(c,'releases',build())
    if change=='unassigned': post(c,'users/customer/providers',{'provider':'julun','remove':True})
    assert download(c,h,meta).status_code in (401,403,409,426)


def test_invalid_source_limits_and_missing_adapter(env):
    c,a,_=env;h=client_token(c);login(c)
    assert verify(c,h).json()['adapter_config']=={'enabled':False}
    b=bundle();b['modules']['example']='syntax error!'
    assert post(c,'adapters/import',{'bundle':b}).status_code==422
    b=bundle();b['modules']['../escape']=''
    assert post(c,'adapters/import',{'bundle':b}).status_code==422
    b=bundle();b['modules']['example']='x'*2100000
    assert post(c,'adapters/import',{'bundle':b}).status_code==413
    h,meta=setup(c)
    post(c,'users/customer/providers',{'provider':'not_published','api_key':'test-only'})
    assert verify(c,h).json()['adapter_config']['missing']==['not_published']
    assert download(c,h,meta).status_code==409


def test_large_import_and_normal_request_limits(env):
    c, _, _ = env
    login(c)
    b = bundle()
    b['modules']['example'] += '\n#' + 'x' * 550000 + '\n'
    r = post(c, 'adapters/import', {'bundle': b})
    assert r.status_code == 200, r.text
    assert c.get('/admin/api/adapters').json()['active'] == ''
    assert post(c, 'users/customer/settings', {'padding': 'x' * 17000}).status_code == 413
