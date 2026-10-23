import importlib.util
import json
from pathlib import Path

import pytest

from core import submitted
from core.apiutil import ApiError
from core.providers.base import VideoTask
from core.providers.cangyuan_video import CangyuanVideoProvider, MODELS, _video_result


@pytest.mark.parametrize('model', list(MODELS))
def test_each_model_sends_only_documented_fields(model):
    p = CangyuanVideoProvider()
    opts = p.capabilities()['video']['model_options'][model]
    task = VideoTask('test', model=model, duration=(opts['durations'] or [10])[0],
                     ratio=(opts['ratios'] or ['16:9'])[0],
                     resolution=(opts['resolutions'] or [''])[0])
    if model.startswith('omni-v2v'):
        task.refs = ['https://example.test/ref.png']
        task.extra['video_refs'] = ['https://example.test/ref.mp4']
    body = p.build_video_body(task)
    assert set(body) <= set(MODELS[model]['params'])
    assert body['duration'] == task.duration
    assert 'seconds' not in body and 'size' not in body


def test_thirty_images_and_audio_not_capped_at_nine():
    p = CangyuanVideoProvider()
    refs = [f'https://example.test/{n}.jpg' for n in range(30)]
    task = VideoTask('test', refs=refs, model='sd11-seedance-2.5', duration=30,
                     extra={'audio_refs': ['https://example.test/a.mp3']})
    body = p.build_video_body(task)
    assert body['reference_image_urls'] == refs
    assert body['reference_audios'] == task.extra['audio_refs']
    task.refs.append('https://example.test/31.jpg')
    with pytest.raises(ApiError, match='30'):
        p.build_video_body(task)


@pytest.mark.parametrize('model,duration,extra', [
    ('sd10-seedance-2.0', 12, {}),
    ('sd10-seedance-2.5', 15, {}),
    ('sd13-seedance-2.5', 30, {'audio_refs': ['https://example.test/a.mp3']}),
    ('sd7-seedance-2.0-720p', 15, {'first_image_url': 'https://example.test/a.png'}),
    ('sd11-seedance-2.5', 15, {'last_image_url': 'https://example.test/a.png'}),
    ('omni-fast', 15, {'first_image_url': 'https://example.test/a.png'}),
])
def test_rejects_unsupported_inputs_without_submission(model, duration, extra):
    with pytest.raises(ApiError):
        CangyuanVideoProvider().build_video_body(VideoTask('test', model=model, duration=duration, extra=extra))


def test_kling_first_last_order_and_reference_conflict():
    p = CangyuanVideoProvider()
    refs = ['https://example.test/first.jpg', 'https://example.test/last.jpg']
    body = p.build_video_body(VideoTask('test', refs=refs, model='kl1-kling-3.0'))
    assert body['first_image_url'] == refs[0] and body['last_image_url'] == refs[1]
    assert 'reference_image_urls' not in body
    with pytest.raises(ApiError):
        p.build_video_body(VideoTask('test', refs=refs, model='sd14-seedance-2.0',
                                     extra={'first_image_url': refs[0]}))


def test_resolution_case_and_fixed_channel():
    p = CangyuanVideoProvider(base_url='https://ai.cangyuansuanli.cn/v1/')
    assert p.session.base_url == 'https://ai.cangyuansuanli.cn'
    body = p.build_video_body(VideoTask('test', model='mm2-minimax-h3', resolution='768p'))
    assert body['resolution'] == '768P'
    body = p.build_video_body(VideoTask('test', model='sd8-seedance-2.5-s', duration=30, resolution='720p'))
    assert 'resolution' not in body
    for model in ['sd7-seedance-2.0-720p', 'sd11-seedance-2.0']:
        with pytest.raises(ApiError):
            p.build_video_body(VideoTask('test', model=model, resolution='4K'))


def test_result_picker_ignores_echoed_video():
    echo = {'status': 'in_progress', 'reference_videos': ['https://example.test/ref.mp4']}
    assert not _video_result(echo)
    assert not _video_result(dict(echo, status='completed'))
    assert _video_result({'status': 'completed', 'video_url': 'https://example.test/out?token=abc'}) == 'https://example.test/out?token=abc'
    assert _video_result({'status': 'completed', 'data': [{'url': 'https://example.test/out.mp4'}]})


def test_download_failure_keeps_id_then_resume_only_gets(tmp_path, monkeypatch):
    p = CangyuanVideoProvider()
    calls = []
    def request(method, path, **kw):
        calls.append((method, path, kw))
        if method == 'POST':
            return {'id': 'existing-task', 'status': 'queued'}
        return {'id': 'existing-task', 'status': 'completed', 'video_url': 'https://example.test/out.mp4'}
    monkeypatch.setattr(p.session, 'request', request)
    def broken(*args, **kwargs):
        raise ApiError('download interrupted')
    monkeypatch.setattr(p.session, 'save_item', broken)
    with submitted.tracking(str(tmp_path), 'out.mp4', provider=p.id):
        with pytest.raises(ApiError):
            p.generate_video(VideoTask('test'), str(tmp_path/'out.mp4'))
    record = submitted.load(str(tmp_path), 'out.mp4')
    assert record['task_id'] == 'existing-task'
    monkeypatch.setattr(p.session, 'save_item', lambda *a, **kw: None)
    result = p.resume(record, str(tmp_path/'out.mp4'))
    assert result['resumed'] and result['task_id'] == 'existing-task'
    assert [v[0] for v in calls] == ['POST', 'GET', 'GET']
    assert calls[0][2]['retries'] == 1


def test_explicit_provider_failure_is_classified(monkeypatch):
    p = CangyuanVideoProvider()
    monkeypatch.setattr(p.session, 'request', lambda *a, **kw: {'status': 'failed', 'error': 'generation failed'})
    with pytest.raises(ApiError) as e:
        p.resume({'task_id': 'failed-task'}, 'unused.mp4')
    assert e.value.provider_task_failed


def test_standalone_plugin_keeps_image_provider_separate():
    root = Path(__file__).resolve().parents[1]
    patch = root/'provider_patches/zz_cangyuan_video.py'
    assert patch.read_bytes() == (root/'core/providers/cangyuan_video.py').read_bytes()
    spec = importlib.util.spec_from_file_location('cangyuan_patch_test', patch)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    from core.providers import _classes_in, _check
    from core.providers.cangyuan import CangyuanProvider
    classes = _classes_in(module)
    assert len(classes) == 1 and classes[0].id == 'cangyuan_video'
    assert not _check(classes[0])
    assert CangyuanProvider.id == 'cangyuan' and CangyuanProvider.supports == ('image',)


def test_full_key_catalog_separates_32_images_34_videos_and_other_models():
    from core.model_catalog import apply_catalog
    from core.providers.cangyuan import CangyuanProvider
    fixture = json.loads((Path(__file__).parent/'fixtures/cangyuan-models-20261013.json').read_text())
    rows = [{'id': mid} for ids in fixture.values() for mid in ids]
    for provider, kind in [(CangyuanProvider(), 'image'), (CangyuanVideoProvider(), 'video')]:
        cap = apply_catalog(provider.capabilities(), {'ok': True, 'rows': rows, 'strict': True}, 'fixture')
        assert set(cap[kind]['models']) == set(fixture[kind])
        assert not cap['model_catalog']['unknown']


def test_latest_image_flash_and_banana_four_k_have_their_own_limits():
    from core.providers.base import ImageTask
    from core.providers.cangyuan import CangyuanProvider
    p = CangyuanProvider()
    for model, maximum in [('doubao-seedream-5-0-flash', 10), ('nano-banana2-4k', 16),
                            ('gemini-3-pro-image-preview', 9), ('midjourney-1k', 4)]:
        refs = [f'https://example.test/{n}.jpg' for n in range(maximum)]
        task = ImageTask('test', model=model, size='16:9', refs=refs)
        path, body = p.build_image_body(task)
        assert body['images'] == refs
        assert path == ('/v1/images/generations' if model.startswith('midjourney-') else '/v1/images/edits')
        task.refs.append('https://example.test/over.jpg')
        with pytest.raises(ApiError):
            p.build_image_body(task)


NEW_CONTRACTS = json.loads((Path(__file__).parent / 'fixtures/cangyuan-video-added-20261023.json')
                           .read_text(encoding='utf-8'))['models']


@pytest.mark.parametrize('model', NEW_CONTRACTS)
def test_added_video_contract_reaches_submit_without_renaming(model, monkeypatch):
    """Exercise real payload assembly and submit with all documented reference types."""
    doc = NEW_CONTRACTS[model]
    params = {p['name']: p for p in doc['params']}
    refs = lambda name: [f'https://example.test/{name}/{n}'
                         for n in range(params.get(name, {}).get('maxItems', 0))]
    extra = {'video_refs': refs('reference_videos'), 'audio_refs': refs('reference_audios')}
    if 'generate_audio' in params:
        extra['generate_audio'] = True
    if 'seed' in params:
        extra['seed'] = 42
    task = VideoTask('lake', model=model, duration=params['duration']['maximum'],
                     ratio=params['aspect_ratio']['suggestions'][-1],
                     resolution=params['resolution']['suggestions'][-1],
                     refs=refs('reference_image_urls'), extra=extra)
    calls = []
    provider = CangyuanVideoProvider()
    def request(method, path, **kw):
        calls.append((method, path, kw))
        return {'id': 'fixture-task'}
    monkeypatch.setattr(provider.session, 'request', request)
    monkeypatch.setattr(provider, '_retrieve', lambda *a, **kw: {'task_id': 'fixture-task'})
    result = provider.generate_video(task, 'unused.mp4', log=lambda _: None)
    assert result['model'] == model
    method, path, kw = calls[0]
    assert (method, path) == ('POST', '/v1/videos') and kw['retries'] == 1
    body = kw['json_body']
    assert body['model'] == model and body['duration'] == params['duration']['maximum']
    assert body['resolution'] == task.resolution and body['aspect_ratio'] == task.ratio
    assert set(body) <= set(params)
    for field in ('reference_image_urls', 'reference_videos', 'reference_audios'):
        assert body.get(field, []) == refs(field)
    options = provider.capabilities()['video']['model_options'][model]
    assert options['max_refs'] == params['reference_image_urls']['maxItems']
    assert options['durations'] == list(range(params['duration']['minimum'], params['duration']['maximum'] + 1))
    assert options['resolutions'] == params['resolution']['suggestions']
    task.refs.append('https://example.test/over.jpg')
    with pytest.raises(ApiError, match='最多'):
        provider.build_video_body(task)
    assert len(calls) == 1


def test_added_seedance_frames_and_per_model_media_support():
    p = CangyuanVideoProvider()
    first, last = 'https://example.test/first.jpg', 'https://example.test/last.jpg'
    task = VideoTask('lake', model='sd16-seedance-2.0', duration=15, resolution='4K',
                     refs=[first], extra={'first_image_url': first})
    with pytest.raises(ApiError, match='首尾帧'):
        p.build_video_body(task)
    task.extra['last_image_url'] = last
    body = p.build_video_body(task)
    assert body['resolution'] == '4k' and body['reference_image_urls'] == [first]
    assert body['first_image_url'] == first and body['last_image_url'] == last
    for model in ('sd17-seedance-2.0', 'sd17-seedance-2.0-2'):
        task = VideoTask('lake', model=model, extra={'audio_refs': [first]})
        assert p.build_video_body(task)['reference_audios'] == [first]
        task.extra['video_refs'] = [first]
        with pytest.raises(ApiError, match='reference_videos'):
            p.build_video_body(task)
    with pytest.raises(ApiError, match='reference_audios'):
        p.build_video_body(VideoTask('lake', model='sd17-seedance-2.0-3', extra={'audio_refs': [first]}))
    task = VideoTask('lake', model='wan6-wan3.0', extra={'first_image_url': first})
    assert p.build_video_body(task)['first_image_url'] == first
    task.refs = [last]
    with pytest.raises(ApiError, match='混用'):
        p.build_video_body(task)


@pytest.mark.parametrize('model,duration', [('sd8-seedance-2.5-n', 15),
                                          ('sd17-seedance-2.5-2', 5),
                                          ('gv4-grok-imagine-video-1.5', 5)])
def test_added_models_do_not_silently_change_duration(model, duration):
    with pytest.raises(ApiError, match='秒'):
        CangyuanVideoProvider().build_video_body(VideoTask('lake', model=model, duration=duration))


def test_new_explicit_restrictions_and_suggestions_are_distinct():
    p = CangyuanVideoProvider()
    for model, resolution in [('kling-o3', '4K'), ('wan6-wan3.0', '1080P')]:
        with pytest.raises(ApiError, match='不支持'):
            p.build_video_body(VideoTask('lake', model=model, resolution=resolution))
    with pytest.raises(ApiError, match='2500'):
        p.build_video_body(VideoTask('x' * 2501, model='kling-o3'))
    assert p.capabilities()['video']['model_options']['kling-o3']['max_prompt_chars'] == 2500
    # Recommendations are not exhaustive enums; don't invent a ban on unlisted ratios.
    assert p.build_video_body(VideoTask('lake', model='sd17-seedance-2.5-4', ratio='5:4'))['aspect_ratio'] == '5:4'


def test_new_models_survive_old_non_strict_catalog_but_respect_account_bindings():
    from core.model_catalog import apply_catalog
    cap = CangyuanVideoProvider().capabilities()
    old = {'ok': True, 'models': ['sd11-seedance-2.5']}
    assert set(NEW_CONTRACTS) <= set(apply_catalog(cap, old, 'old')['video']['models'])
    account = dict(old, strict=True, models=['sd17-seedance-2.5-4'])
    assert apply_catalog(cap, account, 'account')['video']['models'] == account['models']
