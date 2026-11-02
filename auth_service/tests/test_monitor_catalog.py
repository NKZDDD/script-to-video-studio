import json

import pytest

from studio_auth import provider_monitor as M
from studio_auth.adapter_format import validate
from studio_auth.monitor_catalog import read_catalog, resolve
from test_adapters import bundle
from test_admin import env, post
from test_provider_monitor import offline_probes, setup, headers, event, DEVICE


def rules_bundle(rule=None, pid='julun', version='monitor-test'):
    b = bundle(version)
    b['providers'][0]['id'] = pid
    rule = rule or dict(base_url='https://provider.example/api', path='/v1/models', kind='models', result_path='data', auth='bearer')
    b['modules']['provider_monitor_catalog'] = 'MONITORS = ' + repr({pid: rule})
    return b


def test_page_assets_and_missing_published_rules(env):
    c, auth, _ = env
    setup(c, auth)
    state = c.get('/admin/api/provider-monitor').json()
    assert state['adapter_rule_count'] == 0
    assert not state['adapter_version']
    html = c.get('/admin/').text
    assert '/admin/provider-monitor.css?v=1.11.2' in html
    css = c.get('/admin/provider-monitor.css')
    assert css.status_code == 200 and 'text/css' in css.headers['content-type']
    assert '#provider-monitor .pm-check input' in css.text
    imported = post(c, 'adapters/import', {'bundle': bundle('old-no-rules')}).json()
    post(c, 'adapters/publish', {'sha256': imported['sha256']})
    state = c.get('/admin/api/provider-monitor').json()
    assert state['adapter_version'] == 'old-no-rules'
    assert state['adapter_rule_count'] == 0


@pytest.mark.parametrize('base,expected', [
    ('', 'https://provider.example/api/v1/models'),
    ('https://provider.example', 'https://provider.example/api/v1/models'),
    ('https://custom.example/api/v1', 'https://custom.example/api/v1/models'),
    ('https://custom.example/prefix', 'https://custom.example/prefix/v1/models'),
])
def test_base_url_and_prefix_preservation(base, expected):
    rule = read_catalog(validate(rules_bundle()))['julun']
    spec, secret = resolve(rule, {'base_url': base, 'api_key': 'test-key'})
    assert spec['url'] == expected and secret == 'test-key'


@pytest.mark.parametrize('source', [
    'MONITORS = dict()', 'import os\nMONITORS = {}', 'MONITORS = __import__("os").environ',
    'MONITORS = {}\nraise RuntimeError()', 'OTHER = {}',
])
def test_metadata_is_only_literal_data(source):
    b = rules_bundle()
    b['modules']['provider_monitor_catalog'] = source
    with pytest.raises(ValueError, match='监测规则'):
        validate(b)


@pytest.mark.parametrize('field,value', [
    ('path', '//evil.example/models'), ('path', '/models?api_key=secret'),
    ('base_url', 'https://127.0.0.1'), ('base_url', 'http://provider.example'),
    ('auth', 'execute'), ('result_path', '__x[]'), ('key_fields', ['password']),
])
def test_invalid_rule_rejected_at_import(field, value):
    rule = read_catalog(rules_bundle())['julun']
    rule[field] = value
    with pytest.raises(ValueError, match='监测规则'):
        validate(rules_bundle(rule))


def test_missing_rule_unknown_host_and_grouped_key():
    assert read_catalog(bundle()) == {}
    with pytest.raises(ValueError, match='未提供监测规则'):
        resolve(None, {})
    with pytest.raises(ValueError, match='需要特殊签名'):
        resolve({'reason': '需要特殊签名'}, {})
    rule = read_catalog(rules_bundle())['julun']
    rule['base_url'] = ''
    with pytest.raises(ValueError, match='Base URL'):
        resolve(rule, {'api_key':'test'})
    rule.update(base_url='https://provider.example', key_fields=['image_1k_api_key', 'api_key'], key_prefixes=['1k', '4k', 'default'])
    spec, secret = resolve(rule, {'api_key':'1k=group-a;4k=group-b'})
    assert secret == 'group-a' and spec['key_field'] == 'api_key'
    spec, secret = resolve(rule, {'api_key':'1k=group-a;4k=group-b', 'image_1k_api_key':'dedicated'})
    assert secret == 'dedicated' and spec['key_field'] == 'image_1k_api_key'
    rule['auth'] = 'none'
    assert resolve(rule, {})[1] == ''


def publish_rules(c, path='/public/model_group/info'):
    rule = dict(base_url='https://default.example/api', path=path, kind='models', result_path='data', auth='bearer')
    b = rules_bundle(rule, 'gate', 'monitor-' + path.rsplit('/', 1)[-1])
    r = post(c, 'adapters/import', {'bundle':b})
    assert r.status_code == 200, r.text
    assert post(c, 'adapters/publish', {'sha256':r.json()['sha256']}).status_code == 200
    return r.json()['sha256']


def test_auto_configuration_only_requires_key_and_never_exposes_credentials(env):
    c, auth, _ = env
    kid, _ = setup(c, auth)
    publish_rules(c)
    auto = dict(provider='gate', key_id=kid, mode='auto', enabled=True)
    assert post(c, 'provider-monitor', auto).status_code == 200
    state = c.get('/admin/api/provider-monitor').json()
    assert state['adapter_version'] == 'monitor-info'
    assert state['adapter_rule_count'] == 1
    assert 'never-show-this-secret' not in json.dumps(state)
    assert state['presets'][0]['available'] and state['presets'][0]['url'] == 'https://provider.example/public/model_group/info'
    assert state['monitors'][0]['mode'] == 'auto'
    h = headers(auth)
    body = {'device_id':DEVICE}
    assert c.post('/api/v1/auth/provider-monitor/config', json=body, headers=h).json()['monitors'] == []
    post(c, 'users/customer/keys', {'key_ids':[kid]})
    config = c.post('/api/v1/auth/provider-monitor/config', json=body, headers=h).json()
    spec = config['monitors'][0]
    assert config['protocol'] == 1 and spec['secret'] == 'never-show-this-secret'
    assert spec['url'] == state['presets'][0]['url']
    with auth.store.connect() as db:
        saved = json.loads(db.execute('SELECT payload FROM provider_monitors').fetchone()[0])
    assert saved['mode'] == 'auto' and saved['url'] == ''
    post(c, 'keys', dict(id=kid, provider='gate', name='Key', base_url=''))
    assert M.definitions(auth)[0]['url'] == 'https://default.example/api/public/model_group/info'


def test_published_rules_and_rollback_invalidate_inflight_results(env):
    c, auth, clock = env
    kid, manual = setup(c, auth)
    previous = publish_rules(c)
    auto = dict(provider='gate', key_id=kid, mode='auto')
    assert post(c, 'provider-monitor', auto).status_code == 200
    post(c, 'users/customer/keys', {'key_ids':[kid]})
    before = M.definitions(auth)[0]
    with auth.store.connect(write=True) as db:
        M.save_result(db, before, 'server', event(before), clock[0])
    publish_rules(c, '/public/new-models')
    after = M.definitions(auth)[0]
    assert after['revision'] != before['revision'] and after['url'].endswith('/public/new-models')
    assert c.get('/admin/api/provider-monitor').json()['monitors'][0]['server'] is None
    with auth.store.connect(write=True) as db:
        assert not M.still_current(db, before)
        assert M.still_current(db, after)
    response = c.post('/api/v1/auth/provider-monitor/report', headers=headers(auth), json={'device_id':DEVICE, 'events':[event(before)]})
    assert response.status_code == 200
    assert c.get('/admin/api/provider-monitor').json()['clients'] == []
    post(c, 'adapters/publish', {'sha256':previous})
    assert M.definitions(auth)[0]['revision'] == before['revision']
    assert post(c, 'provider-monitor', manual).status_code == 200
    manual_revision = M.definitions(auth)[0]['revision']
    publish_rules(c, '/another-path')
    assert M.definitions(auth)[0]['revision'] == manual_revision
    assert M.definitions(auth)[0]['url'] == manual['url']


def test_no_rule_is_explicit_and_does_not_guess_a_models_endpoint(env):
    c, auth, _ = env
    kid, manual = setup(c, auth)
    state = c.get('/admin/api/provider-monitor').json()
    assert not state['presets'][0]['available'] and '未提供监测规则' in state['presets'][0]['reason']
    auto = dict(provider='gate', key_id=kid, mode='auto')
    assert post(c, 'provider-monitor', auto).status_code == 422
    published = publish_rules(c)
    assert post(c, 'provider-monitor', auto).status_code == 200
    plain = post(c, 'adapters/import', {'bundle':bundle('old-no-rules')}).json()['sha256']
    assert M.definitions(auth)[0]['configured']
    post(c, 'adapters/publish', {'sha256':plain})
    assert not M.definitions(auth)[0]['configured']
    assert post(c, 'provider-monitor', dict(auto, enabled=False)).status_code == 200
    post(c, 'adapters/publish', {'sha256':published})
    assert not M.definitions(auth)[0]['enabled']
    assert post(c, 'provider-monitor', manual).status_code == 200
