import copy
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from core import cloud_adapters, cloud_config, paths, providers, model_catalog
from server import agent_api, app, provider_view


@pytest.fixture
def managed(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, 'data_dir', lambda: str(tmp_path))
    monkeypatch.setattr(paths, 'config_path', lambda: str(tmp_path / 'config.json'))
    monkeypatch.setattr(paths, 'default_projects_dir', lambda: str(tmp_path / 'projects'))
    monkeypatch.setattr(cloud_adapters, 'required', lambda: True)
    owner = object()
    keys = [dict(key_name='图片通道 A', key_id='a', api_key='fixture-a',
                 base_url='https://private-a.example', models=['gpt-image-a']),
            dict(key_name='图片通道 B', key_id='b', api_key='fixture-b',
                 base_url='https://private-b.example', models=['gpt-image-b'])]
    cloud_config.install(owner, 'fixture-user', {'julun': {'keys': keys}})
    cloud_config.refresh_models('julun')
    yield tmp_path, owner
    cloud_config.clear(owner)


@pytest.mark.parametrize('old_settings', [False, True])
def test_new_and_existing_installations_do_not_send_connection_fields(managed, old_settings):
    folder, _ = managed
    if old_settings:
        (folder / 'config.json').write_text(json.dumps({'providers': {
            'julun': {'base_url': 'https://personal.example', 'api_key': 'personal-key'},
            'hzyfai': {'base_url': 'https://unused.example', 'api_key': 'unused-key'}}}))
    cfg = app.load_config()
    before = copy.deepcopy(cfg)
    assert any(c.get('default_base_url') for c in app.caps_of(cfg))
    for payload in (app.api_get('/api/bootstrap', {}), agent_api.bootstrap(app, cfg)):
        raw = json.dumps(payload)
        for hidden in ('default_base_url', 'key_fields', 'account_form', 'personal.example',
                       'private-a.example', 'private-b.example', 'fixture-a', 'fixture-b', 'julun.cc'):
            assert hidden not in raw
        assert payload['providers_public'] == {} and payload['provider_accounts'] == {}
        assert len(payload['capabilities']) == 1
        cap = payload['capabilities'][0]
        assert cap['name'] == '图片通道 A / 图片通道 B'
        assert set(cap['image']['models']) == {'gpt-image-a', 'gpt-image-b'}
    assert cfg == before
    result = app.resolve_provider_cfg(cfg, {'provider': 'julun', 'model': 'gpt-image-b'}, 'asset')
    assert result['api_key'] == 'fixture-b' and result['base_url'] == 'https://private-b.example'
    if old_settings:
        assert json.loads((folder / 'config.json').read_text())['providers']['hzyfai']['api_key'] == 'unused-key'


def test_settings_cannot_write_local_provider_fallback(managed):
    folder, _ = managed
    body = {'providers': {'julun': {'base_url': 'https://personal.example', 'api_key': 'personal'}}}
    for request in (lambda: app.api_post('/api/config', body),
                    lambda: agent_api.post(app, '/api/agent/settings', body)):
        with pytest.raises(ValueError, match='后台统一管理'):
            request()
    assert not (folder / 'config.json').exists()
    agent_api.post(app, '/api/agent/settings', {'limits': {'global': 12}})
    assert app.load_config()['limits']['global'] == 12


def test_unassigned_provider_does_not_use_an_old_personal_key(managed):
    with pytest.raises(ValueError, match='未获后台授权'):
        cloud_config.provider('hzyfai', {'api_key': 'personal-key', 'base_url': 'https://personal.example'})
    assert set(cloud_config.catalog_config({'providers': {'hzyfai': {}}})['providers']) == {'julun'}


def test_metadata_and_refresh_do_not_reintroduce_routes(managed, monkeypatch):
    monkeypatch.setattr(model_catalog, 'refresh', lambda *args: {'ok': False, 'msg': 'HTTP 503 https://private-a.example/v1/models'})
    result = app.api_post('/api/models/refresh', {'provider': 'julun'})
    assert 'private-a.example' not in json.dumps(result) and 'HTTP 503' in result['rows'][0]['msg']
    state = app.api_get('/api/providers/status', {})
    assert state['providers'] == [{'id': 'julun', 'name': '图片通道 A / 图片通道 B',
                                   'supports': list(providers.REGISTRY['julun'].supports), 'source': '后台分配'}]
    raw = [{'id': 'julun', 'name': 'original', 'supports': ['image'],
            'default_base_url': 'https://private-a.example', 'account_form': {'secret': 'private'},
            'image': {'models': ['gpt-image-a'], 'sizes': ['1024x1024'],
                      'notes': '说明 https://docs.example/page ，保留比例', 'docs': 'https://docs.example'}}]
    clean = provider_view.capabilities(raw)
    assert 'https://' not in json.dumps(clean)
    assert clean[0]['image']['sizes'] == ['1024x1024']
    assert raw[0]['default_base_url'] == 'https://private-a.example'


def test_json_diagnostics_hide_routes_but_preserve_prompts_results_and_status(managed):
    data = {'error': 'HTTP 503 from https://private-a.example/v1/videos',
            'logs': ["HTTPSConnectionPool(host='old-upstream.example', port=443): TLS EOF"],
            'items': {'one': {'diag': {'raw': 'private-b.example: 请求失败', 'title': '服务忙'}}},
            'prompt': '文字中保留 https://private-a.example',
            'source': '原文中保留 https://private-a.example',
            'title': '标题中保留 https://private-a.example',
            'url': 'https://private-a.example/generated.png',
            'release': {'url': 'https://cdn.example/Studio.exe'}}
    sent = []
    handler = SimpleNamespace(_send=lambda *args: sent.append(args))
    app.Handler._json(handler, data, 503)
    status, raw, *_ = sent[0]
    clean = json.loads(raw)
    assert status == 503 and 'HTTP 503' in clean['error']
    assert 'private-a.example' not in clean['error']
    assert 'old-upstream.example' not in clean['logs'][0] and 'TLS EOF' in clean['logs'][0]
    assert 'private-b.example' not in clean['items']['one']['diag']['raw']
    assert clean['prompt'] == data['prompt'] and clean['url'] == data['url']
    assert clean['source'] == data['source'] and clean['title'] == data['title']
    assert clean['release'] == data['release']
    assert 'private-a.example' in data['error']  # do not modify stored diagnostics


def test_actual_managed_provider_renderer_is_read_only_and_escapes_names():
    source = (Path(__file__).resolve().parents[1] / 'web/agent.js').read_text(encoding='utf-8')
    helpers = [next(line for line in source.splitlines() if line.startswith(prefix))
               for prefix in ('const esc =', 'const option =')]
    start = source.index('function providerForm()')
    end = source.index('\nfunction ', start + 1)
    js = '\n'.join(helpers) + '\n' + source[start:end] + r'''
const assert=require('node:assert/strict');
const box={innerHTML:''};const $=()=>box;
const btn=(text,action)=>`<button data-action="${action}">${text}</button>`;
const S={provider:'julun',boot:{adapter_status:{managed:true},providers_public:{},provider_keys_configured:{julun:{source:'managed'}},capabilities:[{id:'julun',name:'图片 <A>',image:{models:['gpt-image-a','gpt-image-b']},default_base_url:'https://must-not-appear.example'}]}};
providerForm();
assert(!/must-not-appear|Base URL|api_key|个人备用|save-provider|<input|<form/.test(box.innerHTML));
assert(box.innerHTML.includes('图片 &lt;A&gt;') && box.innerHTML.includes('gpt-image-b'));
assert(box.innerHTML.includes('refresh-cloud-config') && box.innerHTML.includes('refresh-models'));
S.boot.capabilities[0].name='后台修改的名称';providerForm();assert(box.innerHTML.includes('后台修改的名称'));
S.boot.capabilities=[];providerForm();assert(box.innerHTML.includes('refresh-cloud-config'));
'''
    result = subprocess.run(['node', '-'], input=js, capture_output=True, encoding='utf-8', timeout=20)
    assert result.returncode == 0, result.stderr
