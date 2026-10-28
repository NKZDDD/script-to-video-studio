"""Provider contract: keyed capabilities, exact request fields, no duplicate paid jobs."""
import base64
import copy
import json
from pathlib import Path

import pytest
import requests

from core import model_catalog as MC, providers, submitted
from core.apiutil import ApiError
from core.providers.base import VideoTask
from core.providers import zhongheng as Z


@pytest.fixture
def provider():
    p = Z.ZhonghengProvider(api_key='fixture', proxy='direct')
    p._specs = copy.deepcopy(Z.MODEL_SPECS)
    return p


@pytest.fixture
def clock(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(Z.time, 'monotonic', lambda: now[0])
    monkeypatch.setattr(Z.time, 'sleep', lambda seconds: now.__setitem__(0, now[0] + seconds))
    return now


def task(model='XXseedacn2.5', **overrides):
    return VideoTask(**dict(dict(model=model, prompt='风景', duration=5, ratio='9:16', resolution='720p'), **overrides))


@pytest.mark.parametrize('model', Z.MODEL_SPECS)
def test_all_authorized_video_models_create_query_save(model, provider, monkeypatch, clock):
    calls, saved = [], []
    spec = Z.MODEL_SPECS[model]
    def request(method, path, **kw):
        calls.append((method, path, kw))
        if method == 'POST':
            return dict(task_id='vid_fixture', status='processing', next_poll_seconds=7)
        return dict(status='succeeded', download_url='https://result.test/video?download=1')
    monkeypatch.setattr(provider.session, 'request', request)
    monkeypatch.setattr(provider.session, 'save_item', lambda url, dest, **kw: saved.append(url))
    t = task(model, duration=spec['duration_options'][0], resolution=spec['resolution_options'][0],
             refs=['https://material.test/image.png'])
    result = provider.generate_video(t, 'unused.mp4', poll_interval=5, log=lambda _: None)
    assert [c[:2] for c in calls] == [('POST', '/v1/videos'), ('GET', '/v1/tasks/vid_fixture')]
    assert calls[0][2]['json_body'] == dict(model=model, prompt='风景', duration=t.duration,
                                          ratio=t.ratio, resolution=t.resolution, images=t.refs)
    assert calls[0][2]['headers']['Idempotency-Key']
    assert calls[0][2]['retries'] == 2 and calls[0][2]['submit'] is False
    assert clock[0] == 7 and len(saved) == 1 and result['task_id'] == 'vid_fixture'


def test_exact_discrete_durations_not_ranges_or_marketing_text(provider):
    assert provider.build_video_body(task('Aseedance2.0', duration=15), Z.MODEL_SPECS['Aseedance2.0'])['duration'] == 15
    with pytest.raises(ApiError, match='不支持 16'):
        provider.build_video_body(task('Aseedance2.0', duration=16), Z.MODEL_SPECS['Aseedance2.0'])
    with pytest.raises(ApiError, match='不支持 28'):
        provider.build_video_body(task('wan3/1080', duration=28), Z.MODEL_SPECS['wan3/1080'])
    assert provider.build_video_body(task('BBseadanco-480', duration=10, resolution='480p'), Z.MODEL_SPECS['BBseadanco-480'])['duration'] == 10


def test_30_images_ten_audio_and_video_preserved(provider):
    t = task(refs=[f'https://fixture.test/{i}.png' for i in range(30)],
             extra={'video_refs': ['https://fixture.test/v.mp4'] * 10,
                    'audio_refs': ['https://fixture.test/a.wav'] * 10})
    body = provider.build_video_body(t, Z.MODEL_SPECS[t.model])
    assert body['images'] == t.refs
    assert len(body['reference_videos']) == len(body['reference_audios']) == 10
    t.refs.append('https://fixture.test/31.png')
    with pytest.raises(ApiError, match='未裁剪'):
        provider.build_video_body(t, Z.MODEL_SPECS[t.model])


@pytest.mark.parametrize('kwargs', [dict(ratio='2:3'), dict(duration=True), dict(duration=4.5),
    dict(resolution='4K'), dict(extra={'quality': 'high'}), dict(extra={'generate_audio': False}),
    dict(extra={'video_refs': 'https://fixture.test/v.mp4'}),
    dict(extra={'start_frame': 'https://fixture.test/a.png'}),
    dict(refs=['https://fixture.test/a.png'] * 31)])
def test_invalid_input_no_upload_or_creation(provider, monkeypatch, kwargs):
    calls = []
    monkeypatch.setattr(provider.session, 'request', lambda *a, **kw: calls.append(a))
    with pytest.raises(ApiError):
        provider.generate_video(task(**kwargs), 'unused.mp4')
    assert calls == []


def test_zero_reference_support_not_treated_as_missing(provider):
    with pytest.raises(ApiError, match='支持上限 0'):
        provider.build_video_body(task('OOsd2.5', duration=15,
            extra={'video_refs': ['https://fixture.test/v.mp4']}), Z.MODEL_SPECS['OOsd2.5'])


def test_supported_ratio_audio_and_frames_are_explicit(provider):
    spec = dict(Z.MODEL_SPECS['XXseedacn2.5'], aspect_ratio_options=['9:16'],
                supports_generate_audio=True, reference_materials=['start_frame', 'end_frame'])
    t = task(ratio='9:16', extra={'generate_audio': False, 'start_frame': 'https://fixture.test/a.png',
                                 'end_frame': 'https://fixture.test/b.png'})
    body = provider.build_video_body(t, spec)
    assert body['ratio'] == '9:16' and body['generate_audio'] is False and 'end_frame' in body
    t.refs = ['https://fixture.test/c.png']
    with pytest.raises(ApiError):
        provider.build_video_body(t, spec)


def test_catalog_normalizes_types_options_without_exposing_chat_or_secrets(monkeypatch):
    p = Z.ZhonghengProvider(api_key='fixture', base_url='https://fixture.test/v1/')
    assert p.session.base_url == 'https://fixture.test'
    fetched = []
    spec = dict(Z.MODEL_SPECS['XXseedacn2.5'], duration_options=[8, 12],
                aspect_ratio_options=['9:16'], resolution_options=['720p'])
    def request(method, path, **kw):
        fetched.append(path)
        return {'data': [{'id': 'opaque-new-id', 'capabilities': spec},
                         {'id': 'opaque-chat', 'capabilities': {'capability_type': 'chat'}}]}
    monkeypatch.setattr(p.session, 'request', request)
    monkeypatch.setattr(providers, 'build', lambda *a: p)
    class Session:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def get(self, url, **kw):
            assert url == 'https://fixture.test/v1/models' and kw['allow_redirects'] is False
            class Response:
                status_code = 200
                def json(self): return {'data': [{'id': 'opaque-new-id'}, {'id': 'opaque-chat'}]}
            return Response()
    monkeypatch.setattr(MC.requests, 'Session', Session)
    result = MC.fetch('zhongheng', {'api_key': 'fixture'})
    assert result['ok'] and result['strict'] and result['models'] == ['opaque-new-id']
    cap = MC.apply_catalog(p.capabilities(), result, 'test')['video']
    assert cap['models'] == ['opaque-new-id']
    assert cap['model_options']['opaque-new-id']['durations'] == [8, 12]
    assert cap['model_options']['opaque-new-id']['ratios'] == ['9:16']
    assert p.list_models() == ['opaque-new-id'] and len(fetched) == 1
    assert 'fixture' not in json.dumps(result)


def test_no_network_in_capabilities_and_documented_ratio_choices(provider, monkeypatch):
    monkeypatch.setattr(provider.session, 'request', lambda *a, **k: pytest.fail('network on local UI'))
    cap = provider.capabilities()['video']
    assert len(cap['models']) == 28
    assert cap['ratios'] == ['9:16', '16:9', '1:1']
    assert all(options['ratios'] == ['9:16', '16:9', '1:1'] for options in cap['model_options'].values())


@pytest.mark.parametrize('resolution', ['480p', '720p', '768p', '1080p', '2k', '4K'])
@pytest.mark.parametrize('ratio', ['9:16', '16:9', '1:1'])
def test_declared_parameters_are_sent_without_client_pixel_calculation(provider, resolution, ratio):
    # 4K is a future declared capability fixture, not a claim about today's catalog.
    spec = dict(Z.MODEL_SPECS['XXseedacn2.5'], resolution_options=[resolution],
                aspect_ratio_options=['9:16', '16:9', '1:1'])
    t = task(ratio=ratio, resolution=resolution.lower(),
             extra={'size': '720*1280', 'width': 720, 'height': 1280, 'aspect_ratio': '16:9'})
    body = provider.build_video_body(t, spec)
    assert body == dict(model=t.model, prompt=t.prompt, duration=5, ratio=ratio, resolution=resolution)


@pytest.mark.parametrize('ratio', ['', 'auto', 'AUTO'])
def test_declared_defaults_apply_when_parameters_are_automatic(provider, ratio):
    spec = dict(Z.MODEL_SPECS['XXseedacn2.5'], aspect_ratio_options=['16:9', '9:16'],
                resolution_options=['720p', '2k'],
                defaults={'duration': 5, 'aspect_ratio': '9:16', 'resolution': '2k'})
    body = provider.build_video_body(task(ratio=ratio, resolution=''), spec)
    assert body['ratio'] == '9:16' and body['resolution'] == '2k'
    assert 'size' not in body


@pytest.mark.parametrize('ratio', ['9:16', '16:9', '1:1'])
@pytest.mark.parametrize('ratio_choices', [[], None])
def test_empty_ratio_options_use_unified_documentation_in_ui_catalog_and_request(provider, ratio, ratio_choices):
    spec = dict(Z.MODEL_SPECS['XXseedacn2.5'], resolution_options=[],
                defaults={'aspect_ratio': '9:16', 'resolution': '4K'})
    if ratio_choices is None:
        spec.pop('aspect_ratio_options')
    else:
        spec['aspect_ratio_options'] = ratio_choices
    body = provider.build_video_body(task(ratio=ratio, resolution=''), spec)
    # Ratio is sent unchanged; empty resolution metadata does not invent 4K or pixel sizes.
    assert body == dict(model='XXseedacn2.5', prompt='风景', duration=5, ratio=ratio)
    assert Z.model_options(spec)['ratios'] == ['9:16', '16:9', '1:1']
    assert Z.catalog_row('XXseedacn2.5', spec)['capability_schema']['aspect_ratios'] == ['9:16', '16:9', '1:1']


def test_declared_ratio_choices_override_unified_fallback(provider):
    spec = dict(Z.MODEL_SPECS['XXseedacn2.5'], aspect_ratio_options=['16:9'])
    assert Z.model_options(spec)['ratios'] == ['16:9']
    with pytest.raises(ApiError, match='ratio=9:16'):
        provider.build_video_body(task(ratio='9:16'), spec)


@pytest.mark.parametrize('ratio', ['', 'auto', 'AUTO'])
def test_legacy_automatic_ratio_without_model_default_requires_selection(provider, monkeypatch, ratio):
    calls = []
    monkeypatch.setattr(provider.session, 'request', lambda *a, **kw: calls.append(a))
    with pytest.raises(ApiError, match='请在生产页选择 9:16、16:9、1:1'):
        provider.generate_video(task(ratio=ratio), 'unused.mp4')
    assert calls == []


def test_documented_ratio_default_does_not_enable_undeclared_resolution(provider):
    spec = dict(Z.MODEL_SPECS['XXseedacn2.5'], aspect_ratio_options=[], resolution_options=[],
                defaults={'aspect_ratio': '9:16', 'resolution': '4K'})
    body = provider.build_video_body(task(ratio='auto', resolution=''), spec)
    assert body == dict(model='XXseedacn2.5', prompt='风景', duration=5, ratio='9:16')


def test_20261028_model_snapshot_keeps_new_models_and_revised_limits(provider):
    assert Z.MODEL_SPECS['Xseadanco2.0']['max_image_refs'] == 9
    assert Z.MODEL_SPECS['OOsd2.5-30秒']['duration_options'] == [30]
    spec = Z.MODEL_SPECS['Fseadanco2.0mini']
    assert spec['resolution_options'] == ['720p']
    with pytest.raises(ApiError, match='支持上限 0'):
        provider.build_video_body(task('Fseadanco2.0mini', extra={'video_refs': ['https://fixture.test/v.mp4']}), spec)
    assert Z.MODEL_SPECS['BBseadanco-1080']['max_audio_refs'] == 5


def test_real_request_path_uses_model_declared_fields(provider, monkeypatch):
    provider._specs['XXseedacn2.5'].update(aspect_ratio_options=['9:16'], resolution_options=['2k'])
    calls = []
    def request(method, path, **kw):
        calls.append((method, path, kw))
        return dict(task_id='vid_documented', status='succeeded', video_url='https://result.test/v.mp4')
    monkeypatch.setattr(provider.session, 'request', request)
    monkeypatch.setattr(provider.session, 'save_item', lambda *a, **k: None)
    provider.generate_video(task(ratio='9:16', resolution='2K'), 'unused.mp4', log=lambda _: None)
    assert len(calls) == 1 and calls[0][:2] == ('POST', '/v1/videos')
    assert calls[0][2]['json_body'] == dict(model='XXseedacn2.5', prompt='风景', duration=5,
                                          ratio='9:16', resolution='2k')


def test_upload_local_data_uri_and_direct_url(provider, tmp_path, monkeypatch):
    calls = []
    def request(method, path, **kw):
        calls.append((method, path, kw))
        return {'success': True, 'data': [{'url': 'https://material.test/upload.png'}]}
    monkeypatch.setattr(provider.session, 'request', request)
    f = tmp_path/'ref.png'; f.write_bytes(b'image-bytes')
    for ref in [str(f), 'data:image/png;base64,' + base64.b64encode(b'image-bytes').decode()]:
        assert provider._upload(ref) == 'https://material.test/upload.png'
    assert provider._upload('https://fixture.test/a.png') == 'https://fixture.test/a.png'
    assert len(calls) == 2
    assert all(c[1] == '/v1/media' and c[2]['submit'] is False for c in calls)
    assert calls[0][2]['files'][0][0] == 'file'
    assert calls[0][2]['files'][0][1][1] == b'image-bytes'
    with pytest.raises(ApiError): provider._upload('http://fixture.test/a.png')
    with pytest.raises(ApiError): provider._upload('data:image/png;base64,@@bad')
    f.write_bytes(b'')
    with pytest.raises(ApiError): provider._upload(str(f))


def test_upload_error_never_creates_a_task(provider, monkeypatch):
    calls = []
    def request(method, path, **kw):
        calls.append(path)
        return {'success': False, 'message': 'upload failed'}
    monkeypatch.setattr(provider.session, 'request', request)
    with pytest.raises(ApiError):
        provider.generate_video(task(refs=['data:image/png;base64,aW1hZ2U=']), 'unused.mp4')
    assert calls == ['/v1/media']


def test_query_and_download_failures_keep_id_and_resume_uses_only_get(provider, tmp_path, monkeypatch, clock):
    calls = []
    def request(method, path, **kw):
        calls.append((method, path))
        if method == 'POST': return {'task_id': 'vid_existing', 'status': 'processing'}
        return {'status': 'succeeded', 'video_url': 'https://result.test/v.mp4'}
    monkeypatch.setattr(provider.session, 'request', request)
    monkeypatch.setattr(provider.session, 'save_item', lambda *a, **kw: (_ for _ in ()).throw(ApiError('download failed')))
    with submitted.tracking(str(tmp_path), 'out.mp4', provider=provider.id):
        with pytest.raises(ApiError): provider.generate_video(task(), str(tmp_path/'out.mp4'))
    record = submitted.load(str(tmp_path), 'out.mp4')
    assert record['task_id'] == 'vid_existing'
    monkeypatch.setattr(provider.session, 'save_item', lambda *a, **kw: None)
    assert provider.resume(record, str(tmp_path/'out.mp4'))['resumed']
    assert [c[0] for c in calls] == ['POST', 'GET', 'GET']


@pytest.mark.parametrize('error', [ApiError('offline'), ApiError('busy', status=503)])
def test_ambiguous_submission_stops_new_generation(provider, monkeypatch, error):
    monkeypatch.setattr(provider.session, 'request', lambda *a, **kw: (_ for _ in ()).throw(error))
    with pytest.raises(ApiError) as e: provider.generate_video(task(), 'unused.mp4')
    assert e.value.err_code == 'submit_uncertain' and e.value.kind == 'task_fatal'


def test_http_retries_preserve_body_and_idempotency_key(provider, monkeypatch, clock):
    calls = []
    def http(method, url, **kw):
        calls.append((method, url, kw))
        if len(calls) == 1: raise requests.ReadTimeout('timeout')
        class Response:
            status_code = 200
            content = b'json'
            def json(self): return {'task_id': 'vid_once', 'status': 'succeeded', 'video_url': 'https://result.test/v.mp4'}
        return Response()
    monkeypatch.setattr(requests, 'request', http)
    monkeypatch.setattr(provider.session, 'save_item', lambda *a, **kw: None)
    assert provider.generate_video(task(), 'unused.mp4')['task_id'] == 'vid_once'
    assert len(calls) == 2
    assert calls[0][2]['json'] == calls[1][2]['json']
    assert calls[0][2]['headers']['Idempotency-Key'] == calls[1][2]['headers']['Idempotency-Key']


def test_poll_backoff_deadline_failure_detail_and_no_echo_download(provider, monkeypatch, clock):
    calls = []
    def request(method, path, **kw):
        calls.append((method, path, clock[0]))
        if len(calls) == 1: raise ApiError('temporary', status=429)
        return {'status': 'processing', 'video_url': 'https://fixture.test/reference.mp4', 'next_poll_seconds': 12}
    monkeypatch.setattr(provider.session, 'request', request)
    monkeypatch.setattr(provider.session, 'save_item', lambda *a, **kw: pytest.fail('not finished'))
    with pytest.raises(ApiError, match='查询超时'):
        provider.resume({'task_id': 'vid_kept'}, 'unused.mp4', poll_interval=5, poll_timeout=18)
    assert all(c[0] == 'GET' for c in calls) and clock[0] == 18
    monkeypatch.setattr(provider.session, 'request', lambda *a, **kw: dict(status='failed',
        error='upstream_error', error_code='invalid_prompt', error_detail='提示词未通过内容审核'))
    with pytest.raises(ApiError, match='提示词未通过内容审核') as e:
        provider.resume({'task_id': 'vid_failed'}, 'unused.mp4')
    assert e.value.provider_task_failed and e.value.err_code == 'invalid_prompt'


def test_cancel_after_submission_remembers_id(provider, tmp_path, monkeypatch):
    posted = [False]
    def request(*a, **kw):
        posted[0] = True
        return {'task_id': 'vid_cancel', 'status': 'processing'}
    monkeypatch.setattr(provider.session, 'request', request)
    with submitted.tracking(str(tmp_path), 'out.mp4', provider=provider.id):
        with pytest.raises(ApiError, match='已取消'):
            provider.generate_video(task(), 'unused.mp4', cancel=lambda: posted[0])
    assert submitted.load(str(tmp_path), 'out.mp4')['task_id'] == 'vid_cancel'


def test_each_key_reads_its_own_authorized_catalog(monkeypatch):
    def request(session, *a, **kw):
        return {'data': [{'id': session.api_key, 'capabilities': Z.MODEL_SPECS['XXseedacn2.5']}]}
    from core.apiutil import HttpSession
    monkeypatch.setattr(HttpSession, 'request', request)
    assert Z.ZhonghengProvider('a').list_models() == ['a']
    assert Z.ZhonghengProvider('b').list_models() == ['b']
