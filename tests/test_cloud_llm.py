import copy
import json
import shutil
import subprocess
from pathlib import Path

import pytest
import requests

from core import cloud_config as C, model_catalog, paths
from core.llm import LLM
from server import app, agent_api


def route(**changes):
    return dict(dict(api_key='cloud-test-secret', base_url='https://rewrite.example.test',
                     models=['rewrite-model'], key_id='rewrite-key', key_name='后台改写'), **changes)


@pytest.fixture
def managed(tmp_path, monkeypatch):
    manager = object()
    monkeypatch.setattr(C, 'client_managed', lambda: True)
    monkeypatch.setattr(paths, 'data_dir', lambda: str(tmp_path))
    C.install(manager, 'user-a', {C.LLM_PROVIDER: {'keys': [route()]}})
    yield manager
    C.clear(manager)


def test_cloud_route_wins_over_local_and_override_without_mutation(managed):
    cfg = {'llm': {'api_key': 'old-personal-key', 'base_url': 'https://local.test',
                   'model': 'old-model', 'proxy': 'http://local-proxy.test', 'timeout': 123}}
    before = copy.deepcopy(cfg)
    llm = app.build_llm(cfg, {'api_key': 'override-secret', 'base_url': 'https://override.test',
                              'model': 'override-model', 'proxy': 'http://override.test'})
    assert (llm.api_key, llm.base_url, llm.model, llm.proxy) == (
        'cloud-test-secret', 'https://rewrite.example.test', 'rewrite-model', 'direct')
    assert llm.timeout == 123 and cfg == before


def test_actual_chat_request_from_agent_factory_uses_assigned_route(managed, monkeypatch):
    calls = []
    def post(url, **kw):
        calls.append((url, kw))
        response = requests.Response()
        response.status_code = 200
        response._content = json.dumps({'choices': [{'message': {'content': 'rewritten prompt'},
                                                     'finish_reason': 'stop'}]}).encode()
        return response
    from types import SimpleNamespace
    monkeypatch.setattr(LLM, '_session', lambda self, trust_env: SimpleNamespace(post=post))
    llm = agent_api.llm_factory(app, {})()
    assert llm.chat('Rewrite', 'test prompt') == 'rewritten prompt'
    assert len(calls) == 1
    url, request = calls[0]
    assert url == 'https://rewrite.example.test/v1/chat/completions'
    assert request['headers']['Authorization'] == 'Bearer cloud-test-secret'
    assert request['json']['model'] == 'rewrite-model'


def test_refresh_order_account_switch_disable_remove_and_logout(managed):
    C.install(managed, 'user-a', {C.LLM_PROVIDER: {'keys': [route(api_key='new-secret'), route()]}})
    assert app.build_llm({}).api_key == 'new-secret'
    C.install(managed, 'user-b', {C.LLM_PROVIDER: {'keys': [route(api_key='other-secret')]}})
    assert app.build_llm({}).api_key == 'other-secret'
    for config in ({C.LLM_PROVIDER: {'keys': []}}, {}):
        C.install(managed, 'user-b', config)
        with pytest.raises(ValueError, match='未分配'):
            app.build_llm({'llm': {'api_key': 'must-not-fallback'}})
    C.clear(managed)
    assert not agent_api.llm_ready(app, {'llm': {'api_key': 'must-not-fallback'}})


@pytest.mark.parametrize('change', [dict(base_url='https://user:password@example.test'),
                                   dict(base_url='http://unsafe.test'), dict(models=[]),
                                   dict(models=['a', 'b']), dict(api_key='')])
def test_invalid_cloud_route_fails_closed_without_fallback(managed, change):
    C.install(managed, 'user-a', {C.LLM_PROVIDER: {'keys': [route(**change)]}})
    with pytest.raises(ValueError, match='配置不完整'):
        app.build_llm({'llm': {'api_key': 'personal'}})


def test_llm_does_not_participate_in_media_catalog_discovery(managed, monkeypatch):
    monkeypatch.setattr(model_catalog, 'fetch', lambda *a, **k: pytest.fail('LLM must not fetch models here'))
    assert C.assigned_ids() == []
    assert C.catalog_config({'providers': {}})['providers'] == {}
    assert agent_api.llm_ready(app, {})


def test_bootstrap_masks_secret_displays_effective_source_and_rounds_only_save(managed, tmp_path, monkeypatch):
    cfg = {'llm': {'api_key': 'local-secret', 'base_url': 'https://local.test'},
           'projects_dir': str(tmp_path), 'providers': {}, 'limits': {'global': 8, 'per_provider': {}}}
    monkeypatch.setattr(app, 'load_config', lambda: copy.deepcopy(cfg))
    monkeypatch.setattr(app, 'caps_of', lambda cfg=None: [])
    saved = []
    monkeypatch.setattr(app, 'save_config', lambda value: saved.append(value))
    d = agent_api.bootstrap(app, cfg)
    assert d['llm']['ready'] and d['llm']['managed'] and d['llm']['source'] == 'managed'
    assert d['llm']['key_name'] == '后台改写' and d['llm']['model'] == 'rewrite-model'
    assert d['llm']['base_url'] == 'https://rewrite.example.test'
    assert 'cloud-test-secret' not in json.dumps(d) and 'local-secret' not in json.dumps(d)
    for call in (lambda: app.api_post('/api/config', {'llm': {'api_key': 'attempt'}}),
                 lambda: agent_api.post(app, '/api/agent/settings', {'llm': {'api_key': 'attempt'}})):
        with pytest.raises(ValueError, match='后台统一管理'):
            call()
    assert not saved
    agent_api.post(app, '/api/agent/settings', {'defaults': {'soften_rounds': 3}})
    assert saved[-1]['defaults']['soften_rounds'] == 3
    assert saved[-1]['llm']['api_key'] == 'local-secret'
    assert 'cloud-test-secret' not in json.dumps(saved)
    C.install(managed, 'user-a', {})
    d = agent_api.bootstrap(app, cfg)['llm']
    assert not d['ready'] and not d['api_key_set'] and '未分配' in d['message']
    assert not d['base_url'] and not d['model']


def test_personal_mode_keeps_local_configuration_and_provider_fallback(monkeypatch):
    monkeypatch.setattr(C, 'client_managed', lambda: False)
    assert app.build_llm({'llm': {'api_key': 'local-key'}}).api_key == 'local-key'
    assert app.build_llm({'llm': {'provider': 'chaomo'},
                          'providers': {'chaomo': {'llm_api_key': 'local-provider-key'}}}).api_key == 'local-provider-key'


def test_managed_llm_form_and_save_action_behavior():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is required for the frontend behavior check')
    source = (Path(__file__).resolve().parents[1] / 'web/agent.js').read_text(encoding='utf-8')
    form = source[source.index('function llmForm()'):source.index('function providerForm()')]
    action = source[source.index(" 'save-llm':"):source.index(" 'test-upload':")]
    script = r'''
const assert=require('node:assert/strict');
const S={boot:{llm:{managed:true,ready:true,api_key_set:true,key_name:'Assigned',model:'model-a',base_url:'https://rewrite.test'}}};
const page={innerHTML:''},form={reportValidity:()=>true};
const $=s=>s==='#settings-content'?page:form;
const esc=s=>String(s).replaceAll('<','&lt;');
const input=(name,value)=>`<input name="${name}" value="${value}">`;
const field=(label,value)=>label+value;
const btn=(label,action)=>`<button data-action="${action}">${label}</button>`;
let saved;
const api=async(path,body)=>{saved=body;},reloadBoot=async()=>{},settings=()=>{},notice=()=>{};
class FormData { constructor() {return [['soften_rounds','4'],['base_url','https://untrusted.test'],['api_key','untrusted']];} }
''' + form + '\nconst actions={' + action + '};\n' + r'''
(async()=>{
llmForm();assert.ok(page.innerHTML.includes('Assigned'));assert.ok(page.innerHTML.includes('model-a'));
assert.ok(page.innerHTML.includes('refresh-cloud-config'));
assert.ok(!page.innerHTML.includes('name="api_key"'));
await actions['save-llm']();assert.deepEqual(saved,{defaults:{soften_rounds:4}});
S.boot.llm={managed:false};llmForm();assert.ok(page.innerHTML.includes('name="api_key"'));
await actions['save-llm']();assert.equal(saved.llm.api_key,'untrusted');
})().catch(e=>{console.error(e);process.exit(1)});
'''
    result = subprocess.run([node, '-e', script], capture_output=True, text=True, encoding='utf-8', timeout=15)
    assert result.returncode == 0, result.stderr
