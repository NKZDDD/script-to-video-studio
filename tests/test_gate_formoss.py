import importlib.util
from pathlib import Path
from unittest.mock import patch

import pytest

from core import model_catalog, providers
from core.apiutil import ApiError
from core.providers.base import VideoTask
from core.providers.gate import GateProvider, GATE_MODEL_ALIASES, gate_spec

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / 'provider_patches/zz_gate_formoss.py'
spec = importlib.util.spec_from_file_location('gate_formoss_test_plugin', PLUGIN)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.mark.parametrize('cls', [GateProvider, module.GateProvider])
@pytest.mark.parametrize('model,base', GATE_MODEL_ALIASES.items())
def test_channel_inherits_parameters_and_keeps_full_model_in_submission_and_resume(cls, model, base):
    p = cls(api_key='test-only')
    cap = p.capabilities()['video']
    assert model in cap['models']
    assert cap['model_options'][model] == cap['model_options'][base]
    limits = gate_spec(base)
    task = VideoTask(prompt='lake', refs=[f'https://cdn.example/{i}.png' for i in range(limits['max_images'])],
                     duration=limits['duration'][1], ratio='16:9', resolution=limits['resolutions'][-1], model=model,
                     extra={'generate_audio': False})
    body = p.build_video_body(task, model)
    assert body['model'] == model and len(body['inputs']) == limits['max_images'] + 1
    assert body['metadata']['generate_audio'] is False
    calls = []
    states = iter(('pending', 'running', 'success', 'success'))
    def request(method, path, **kw):
        calls.append((method, path, kw['json_body']))
        if path.endswith('create_task'):
            return {'task_id': 'existing-test-id'}
        return {'status': next(states), 'results': [{'result': {'content': {'video_url': 'https://cdn.example/out.mp4'}}}]}
    p.session.request = request
    p.session.save_item = lambda *_: None
    with patch('core.submitted.remember') as remember:
        result = p.generate_video(task, 'unused.mp4', log=lambda *_: None, poll_interval=0)
        remember.assert_called_once_with('existing-test-id', custom='gate', model=model)
    assert result['model'] == model
    p.resume({'task_id': 'existing-test-id', 'resume': {'custom': 'gate', 'model': model}},
             'unused.mp4', log=lambda *_: None, poll_interval=0)
    assert [c[1] for c in calls].count('/api/multimodal/create_task') == 1
    assert all(c[2] == {'model': model, 'taskId': 'existing-test-id'} for c in calls[1:])
    task.refs.append('https://cdn.example/too-many.png')
    with pytest.raises(ApiError):
        p.build_video_body(task, model)


def test_old_personal_catalog_keeps_aliases_but_managed_catalog_is_authoritative():
    cap = GateProvider().capabilities()
    old = {'models': ['seedance-2.0-mini']}
    merged = model_catalog.apply_catalog(cap, old, 'old-test-catalog')
    assert set(GATE_MODEL_ALIASES) <= set(merged['video']['models'])
    managed = model_catalog.apply_catalog(cap, dict(old, strict=True), 'managed')
    assert managed['video']['models'] == ['seedance-2.0-mini']


def test_plugin_is_full_source_copy_and_loads_with_existing_gate_id(tmp_path, monkeypatch):
    source = (ROOT/'core/providers/gate.py').read_text(encoding='utf8')
    expected = source.replace('from .. import submitted', 'from core import submitted').replace(
        'from ..apiutil import', 'from core.apiutil import').replace('from .base import', 'from core.providers.base import')
    assert PLUGIN.read_text(encoding='utf8') == expected
    target = tmp_path / PLUGIN.name
    target.write_text(expected, encoding='utf8')
    try:
        with monkeypatch.context() as m:
            m.setattr(providers.paths, 'plugins_dir', lambda: str(tmp_path))
            providers.reload_all()
            assert not providers.ERRORS
            assert Path(providers.SOURCES['gate']) == target
            assert set(GATE_MODEL_ALIASES) <= set(providers.REGISTRY['gate']().capabilities()['video']['models'])
    finally:
        providers.reload_all()
