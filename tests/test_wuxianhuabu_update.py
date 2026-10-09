"""Current official models, request limits and standalone plugin compatibility."""
import importlib.util
from pathlib import Path
from unittest.mock import Mock

import pytest

from core.apiutil import ApiError
from core.model_catalog import _from_schema, apply_catalog
from core.providers import _check, _classes_in
from core.providers.base import VideoTask
from core.providers.wuxianhuabu import MODELS, DEFAULT_MODEL, WuxianhuabuProvider


def provider():
    p = WuxianhuabuProvider(api_key='test-only')
    p.session.request = Mock(return_value={'download_url': 'https://cdn.example/video.mp4'})
    p.session.save_item = Mock()
    return p


@pytest.mark.parametrize('model', list(MODELS))
def test_every_public_video_model_uses_its_exact_name_and_parameters(model):
    p = provider()
    lim = MODELS[model]
    duration = (lim.get('durations') or [15])[-1]
    p.generate_video(VideoTask('test', model=model, duration=duration, ratio='9:16'), 'out.mp4')
    args, kw = p.session.request.call_args
    assert args == ('POST', '/v1/videos')
    assert kw['json_body']['model'] == model
    assert kw['json_body']['seconds'] == duration
    assert kw['json_body']['resolution'] == lim['resolution']
    assert kw['headers']['Idempotency-Key']
    p.session.save_item.assert_called_once_with('https://cdn.example/video.mp4', 'out.mp4')


@pytest.mark.parametrize('model,duration', [('dola-2.5-720p', 15), ('video-sd25-30s', 20)])
def test_discrete_durations_are_not_treated_as_a_range(model, duration):
    p = provider()
    with pytest.raises(ApiError, match='时长只能'):
        p.generate_video(VideoTask('test', model=model, duration=duration), 'out.mp4')
    p.session.request.assert_not_called()


@pytest.mark.parametrize('model,images,videos,audios', [
    ('sd-2.5-ss-720p', 30, 10, 10),
    ('lltai-vs-2.5', 30, 10, 10),
    ('seedance-2.5-dl-720p', 9, 0, 1),
    ('seedance-2.5-dlcf-720p', 9, 0, 1),
    ('dola-2.5-720p', 9, 0, 0),
    ('seedance2.5-720-wd', 31, 11, 11),
])
def test_reference_limits_preserve_all_uploaded_order(model, images, videos, audios):
    p = provider()
    refs = [f'https://cdn.example/{i}.png' for i in range(images)]
    vids = [f'https://cdn.example/{i}.mp4' for i in range(videos)]
    sounds = [f'https://cdn.example/{i}.wav' for i in range(audios)]
    p.generate_video(VideoTask('test', model=model, duration=30, refs=refs,
        extra={'video_refs': vids, 'audio_refs': sounds}), 'out.mp4')
    body = p.session.request.call_args.kwargs['json_body']
    assert body.get('reference_images', []) == refs
    assert body.get('reference_videos', []) == vids
    assert body.get('reference_audios', []) == sounds


@pytest.mark.parametrize('extra', [{'video_refs': ['https://cdn.example/v.mp4']},
                                  {'audio_refs': ['https://cdn.example/a.wav']}])
def test_dola_disallowed_reference_types_stop_before_create(extra):
    p = provider()
    with pytest.raises(ApiError, match='超了 0 条'):
        p.generate_video(VideoTask('test', model='dola-2.5-720p', duration=30, extra=extra), 'out.mp4')
    p.session.request.assert_not_called()


def test_existing_query_and_download_flow_is_used_for_new_models():
    p = provider()
    p.session.request.return_value = {'id': 'existing-id', 'status': 'queued'}
    p.session.poll = Mock(return_value='https://cdn.example/v.mp4')
    result = p.generate_video(VideoTask('test', model=DEFAULT_MODEL, duration=30), 'out.mp4')
    assert result['task_id'] == 'existing-id'
    assert p.session.poll.call_args.args == ('/v1/videos/{id}', 'existing-id')
    p.session.save_item.assert_called_once_with('https://cdn.example/v.mp4', 'out.mp4')


def test_old_snapshot_does_not_hide_new_plugin_models():
    cap = apply_catalog(WuxianhuabuProvider().capabilities(),
                        {'rows': [{'id': 'seedance2.5'}]}, 'old frozen snapshot')
    assert set(MODELS).issubset(cap['video']['models'])
    assert cap['video']['default_model'] == DEFAULT_MODEL
    assert cap['video']['model_options']['dola-2.5-720p']['durations'] == [5, 10, 30]
    assert cap['video']['model_options']['seedance-2.5-dl-720p']['max_audio_refs'] == 1


def test_schema_short_reference_fields_preserve_zero():
    assert _from_schema({'max_images': 9, 'max_videos': 0, 'max_audios': 0}) == {
        'max_refs': 9, 'max_video_refs': 0, 'max_audio_refs': 0}


def test_fresh_declared_limit_overrides_previously_unspecified_count():
    cap = apply_catalog(WuxianhuabuProvider().capabilities(), {'rows': [
        {'id': 'seedance2.5-720-wd', 'capability_schema': {'max_images': 12}}]}, 'current key')
    options = cap['video']['model_options']['seedance2.5-720-wd']
    assert options['max_refs'] == 12 and not options['refs_unlimited']


def test_standalone_plugin_loads_and_matches_builtin():
    root = Path(__file__).resolve().parents[1]
    path = root/'provider_patches/zz_wuxianhuabu_latest.py'
    assert path.read_bytes() == (root/'core/providers/wuxianhuabu.py').read_bytes()
    spec = importlib.util.spec_from_file_location('test_external_wuxianhuabu', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert _classes_in(mod) == [mod.WuxianhuabuProvider]
    assert _check(mod.WuxianhuabuProvider) == []
    assert mod.WuxianhuabuProvider().capabilities() == WuxianhuabuProvider().capabilities()
