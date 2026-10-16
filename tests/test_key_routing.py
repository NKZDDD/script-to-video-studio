import copy
import json

import pytest

from core import cloud_config as C, cloud_usage as U, model_catalog, paths, submitted
from core.apiutil import ApiError
from core.key_routing import worker
from core.store import Project
from server import app


@pytest.fixture
def library(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, 'data_dir', lambda: str(tmp_path))
    manager = object()
    keys = [dict(api_key='test-key-a', key_id='key-a', key_name='图片组 A', assignment_id='rev-a',
                 base_url='https://a.test', models=['gpt-image-a']),
            dict(api_key='test-key-b', key_id='key-b', key_name='图片组 B', assignment_id='rev-b',
                 base_url='https://b.test', models=['gpt-image-b'])]
    C.install(manager, 'user-a', {'julun': {'keys': keys}})
    yield tmp_path, manager, keys
    C.clear(manager)


def resolve(model='gpt-image-b', **kw):
    return app.resolve_provider_cfg({}, dict(provider='julun', model=model, **kw), 'asset')


def test_two_keys_union_and_model_routes(library):
    assert C.refresh_models('julun')['models'] == ['gpt-image-a', 'gpt-image-b']
    a, b = resolve('gpt-image-a'), resolve()
    assert a['api_key'] == 'test-key-a' and a['base_url'] == 'https://a.test'
    assert b['api_key'] == 'test-key-b' and b['base_url'] == 'https://b.test'
    assert b['_usage']['key_id'] == 'key-b'
    assert b['_usage']['key_name'] == '图片组 B'
    cap = next(c for c in app.caps_of({}) if c['id'] == 'julun')
    assert set(cap['image']['models']) == {'gpt-image-a', 'gpt-image-b'}
    with pytest.raises(ValueError, match='没有对应'):
        resolve('gpt-image-other')


def test_refresh_fetches_each_key_without_sending_metadata_as_secret(library, monkeypatch):
    root, manager, keys = library
    keys = copy.deepcopy(keys)
    for k in keys: k.pop('models')
    C.install(manager, 'user-a', {'julun': {'keys': keys}})
    seen = []
    def fetch(pid, config):
        seen.append(config.copy())
        model = 'gpt-image-' + config['api_key'][-1]
        return {'ok': True, 'models': [model], 'rows': [{'id': model}]}
    monkeypatch.setattr(model_catalog, 'fetch', fetch)
    assert C.refresh_models('julun')['models'] == ['gpt-image-a', 'gpt-image-b']
    assert {r['api_key'] for r in seen} == {'test-key-a', 'test-key-b'}
    assert all(set(r) == {'api_key', 'base_url', 'proxy'} for r in seen)
    assert resolve()['api_key'] == 'test-key-b'


def test_refresh_removes_failed_key_models_and_never_uses_other_key(library, monkeypatch):
    root, manager, keys = library
    assert resolve()['api_key'] == 'test-key-b'
    keys = copy.deepcopy(keys); keys[1].pop('models')
    C.install(manager, 'user-a', {'julun': {'keys': keys}})
    monkeypatch.setattr(model_catalog, 'fetch', lambda *a: {'ok': False, 'msg': 'HTTP 401'})
    result = C.refresh_models('julun')
    assert result['partial'] and result['models'] == ['gpt-image-a']
    with pytest.raises(ValueError): resolve()


def test_key_edit_invalidation_and_account_isolation(library):
    root, manager, keys = library
    old = resolve()
    keys = copy.deepcopy(keys); keys[1]['api_key'] = 'replacement'
    C.install(manager, 'user-a', {'julun': {'keys': keys}})
    assert C.catalog('julun') is None
    assert resolve()['api_key'] == 'replacement'
    assert old['api_key'] == 'test-key-b'  # Running batch remains frozen.
    C.install(manager, 'user-b', {})
    assert C.catalog('julun') is None and not C.managed('julun')


@pytest.mark.parametrize('status', [401, 402, 429, 503])
def test_generation_failure_does_not_switch_key(library, status):
    root, _, _ = library
    calls = []
    def factory(cfg):
        def generate(*args):
            calls.append(cfg['api_key'])
            raise ApiError('rejected', status=status)
        return generate
    run = worker(Project(str(root/'p')), resolve(), factory)
    with pytest.raises(ApiError): run({'output': 'out.png'}, lambda m: None, lambda: False)
    assert calls == ['test-key-b']


def test_pending_uses_original_key_after_reorder_or_model_removed(library):
    root, manager, keys = library
    pj = Project(str(root/'p')); task = {'output': 'out.png', 'key': 'image-1'}
    old = resolve()
    with submitted.tracking(pj.root, task['output'], provider='julun', model='gpt-image-b', _usage=old['_usage']):
        submitted.remember('original-task')
    keys = list(reversed(copy.deepcopy(keys))); keys[0]['models'] = ['new-model']
    C.install(manager, 'user-a', {'julun': {'keys': keys}})
    cfg = resolve(_resume_only=True)
    calls = []
    worker(pj, cfg, lambda c: lambda *a: calls.append(c['api_key']))(task, lambda m: None, lambda: False)
    assert calls == ['test-key-b']
    assert submitted.load(pj.root, task['output'])['task_id'] == 'original-task'
    keys = keys[1:]
    C.install(manager, 'user-a', {'julun': {'keys': keys}})
    with pytest.raises(ApiError, match='原任务'):
        worker(pj, resolve(_resume_only=True), lambda c: lambda *a: None)(task, lambda m: None, lambda: False)
    assert submitted.load(pj.root, task['output'])['task_id'] == 'original-task'


def test_usage_records_specific_key_and_never_credentials(library):
    root, _, _ = library
    cfg = resolve();pj = Project(str(root/'p'));task = {'output':'out.png','key':'img'}
    @U.tracked
    def generate(pj, task, out, prov, rec, *, meta):
        with submitted.tracking(pj.root, task['output'], **meta):
            submitted.new_attempt()
            raise ApiError('rejected', status=401)
    with pytest.raises(ApiError):
        generate(pj, task, '', None, None, meta={'provider':'julun','model':'gpt-image-b','_usage':cfg['_usage']})
    events = U.pending('user-a')
    assert len(events) == 1 and events[0]['key_id'] == 'key-b' and events[0]['status'] == 'failed'
    assert 'test-key-' not in json.dumps(events)


def test_runtime_options_added_after_resolution_reach_worker(library):
    root, _, _ = library
    cfg = resolve()
    cfg.update(agent_mode=True, soften_rounds=0)
    captured = []
    run = worker(Project(str(root/'p')), cfg, lambda c: lambda *a: captured.append(c))
    run({'output':'out.png'}, lambda m: None, lambda: False)
    assert captured[0]['agent_mode'] is True and captured[0]['soften_rounds'] == 0
    assert captured[0]['api_key'] == 'test-key-b' and '_key_candidates' not in captured[0]


def test_generic_chaomo_key_routes_by_model_without_name_heuristic(library):
    root, manager, keys = library
    C.install(manager, 'user-a', {'chaomo': {'keys': keys}})
    cfg = app.resolve_provider_cfg({}, {'provider':'chaomo','model':'gpt-image-b'}, 'asset')
    assert cfg['api_key'] == 'test-key-b'


def test_disabled_assignment_blocks_personal_fallback(library):
    root, manager, keys = library
    C.install(manager, 'user-a', {'julun': {'keys': []}})
    assert C.provider('julun', {'api_key':'personal'}).get('api_key') is None
    with pytest.raises(ValueError): resolve()
