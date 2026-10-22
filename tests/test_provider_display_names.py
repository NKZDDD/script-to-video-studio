"""Account Key labels change presentation without changing model routing or saved tasks."""
import copy
import json
from pathlib import Path
import subprocess

import pytest

from core import cloud_config as C, model_catalog, paths, providers
from server import app, agent_api


@pytest.fixture
def assigned(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, 'data_dir', lambda: str(tmp_path))
    manager = object()
    keys = [dict(key_name='图片通道 A', key_id='a', api_key='test-a',
                 base_url='https://a.test', models=['gpt-image-a']),
            dict(key_name='图片通道 B', key_id='b', api_key='test-b',
                 base_url='https://b.test', models=['gpt-image-b'])]
    C.install(manager, 'user-a', {'julun': {'keys': keys}})
    yield manager, keys
    C.clear(manager)


def test_name_refresh_keeps_model_routes_and_running_snapshot(assigned):
    manager, keys = assigned
    selection = dict(provider='julun', model='gpt-image-b')
    before = app.resolve_provider_cfg({}, selection, 'asset')
    original_name = providers.REGISTRY['julun'].name
    cap = next(c for c in app.caps_of({}) if c['id'] == 'julun')
    assert cap['name'] == '图片通道 A / 图片通道 B'
    assert set(cap['image']['models']) == {'gpt-image-a', 'gpt-image-b'}
    changed = copy.deepcopy(keys)
    changed[0]['key_name'] = '标准出图'
    changed[1]['key_name'] = '高清出图'
    C.install(manager, 'user-a', {'julun': {'keys': changed}})
    after = app.resolve_provider_cfg({}, selection, 'asset')
    assert C.display_name('julun') == '标准出图 / 高清出图'
    assert after['api_key'] == before['api_key'] == 'test-b'
    assert after['base_url'] == before['base_url'] == 'https://b.test'
    assert after['key_id'] == before['key_id'] == 'b'
    assert before['key_name'] == '图片通道 B'  # Existing batch stays frozen.
    assert providers.REGISTRY['julun'].name == original_name
    assert selection == dict(provider='julun', model='gpt-image-b')


def test_names_do_not_leak_between_accounts_or_after_logout(assigned):
    manager, keys = assigned
    assert '图片通道' in C.display_name('julun')
    C.install(manager, 'user-b', {'julun': {'keys': [dict(keys[0], key_name='另一个账号')]}})
    assert C.display_name('julun') == '另一个账号'
    C.install(manager, 'user-b', {})
    assert C.display_name('julun', '内置名') == '内置名'
    C.install(manager, 'user-b', {'julun': {'key_name': '旧版下发名'}})
    assert C.display_name('julun') == '旧版下发名'
    C.clear(manager)
    assert C.display_name('julun', '内置名') == '内置名'


@pytest.mark.parametrize('rows,expected', [
    ([{'key_name': ' A '}, {'key_name': 'B'}, {'key_name': 'A'}, {'key_name': ''}], 'A / B'),
    ([], '内置名'), ([{}], '内置名'),
])
def test_order_duplicates_and_older_servers(assigned, rows, expected):
    manager, _ = assigned
    C.install(manager, 'user-a', {'julun': {'keys': rows}})
    assert C.display_name('julun', '内置名') == expected


def test_both_client_bootstraps_and_plugin_status_use_account_names(assigned):
    for data in (app.api_get('/api/bootstrap', {}), agent_api.bootstrap(app, app.load_config()),
                 app.api_get('/api/providers/status', {})):
        rows = data.get('capabilities', data.get('providers'))
        assert next(c for c in rows if c['id'] == 'julun')['name'] == '图片通道 A / 图片通道 B'
        assert 'test-a' not in json.dumps(data) and 'test-b' not in json.dumps(data)


def test_fallback_and_plugin_reload_do_not_restore_builtin_names(assigned, monkeypatch):
    def failed(*a):
        raise ValueError('offline catalog')
    monkeypatch.setattr(model_catalog, 'capabilities', failed)
    assert next(c for c in app.caps_of({}) if c['id'] == 'julun')['name'] == '图片通道 A / 图片通道 B'
    original = providers.status()
    monkeypatch.setattr(providers, 'reload_all', lambda: original)
    result = app.api_post('/api/providers/reload', {})
    assert next(c for c in result['providers'] if c['id'] == 'julun')['name'] == '图片通道 A / 图片通道 B'


def test_actual_select_renderers_escape_admin_names_keep_ids_and_refresh():
    root = Path(__file__).resolve().parents[1] / 'web'
    agent = (root / 'agent.js').read_text(encoding='utf-8')
    legacy = (root / 'index.html').read_text(encoding='utf-8')
    lines = [next(line for line in agent.splitlines() if line.startswith(prefix)) for prefix in
             ('const esc =', 'const option =', 'const providerName=', 'function providerSelect(')]
    old_select = legacy[legacy.index('function providerOptions('):legacy.index('\n}', legacy.index('function providerOptions('))+2]
    script = '\n'.join(lines) + '\n' + old_select + r'''
const assert = require('node:assert/strict');
const name = '高清 <img src=x onerror="bad()"> & "组"';
const S = {boot:{capabilities:[{id:'julun',name,supports:['image']}]}};
const BOOT = S.boot;
for (const html of [providerSelect('image','provider','julun'), providerOptions('image','julun')]) {
 assert(html.includes('value="julun"'));
 assert(html.includes('&lt;img') && html.includes('&amp;') && html.includes('&quot;'));
 assert(!html.includes('<img'));
}
assert.equal(providerName('julun'), name);
S.boot.capabilities[0].name='刷新后的后台名称';
assert(providerSelect('image','provider','julun').includes('刷新后的后台名称'));
assert.equal(providerName('julun'),'刷新后的后台名称');
assert.equal(providerName('legacy-id'),'legacy-id');
'''
    result = subprocess.run(['node', '-'], input=script, capture_output=True, encoding='utf-8', timeout=20)
    assert result.returncode == 0, result.stderr
