import importlib.util
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock

import pytest

from core import model_catalog, providers
from core.apiutil import ApiError, BATCH_FATAL, RETRYABLE, TASK_FATAL
from core.providers import hongniao
from core.providers.base import VideoTask


@pytest.fixture
def provider():
    return hongniao.HongniaoProvider('test-key')


@pytest.fixture
def api_server():
    calls = []
    row = {'id': 'video-sd25-30s', 'type': 'video_generation', 'tasks': [
        {'taskKind': 'video.generate', 'parameters': [
            {'name': 'seconds', 'options': [{'value': '5'}, {'value': '30'}]},
            {'name': 'aspect_ratio', 'options': [{'value': '9:16'}]},
            {'name': 'images', 'maxItems': 30},
            {'name': 'videos', 'maxItems': 2}]}]}
    media = b'\x00\x00\x00\x18ftypmp42' + b'\0' * 1024

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(self, data, status=200):
            payload = json.dumps(data).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            calls.append(('GET', self.path, self.headers.get('Authorization'), None))
            if self.path == '/api/v1/models':
                self.respond({'code': 200, 'data': {'models': [row]}})
            elif self.path == '/api/v1/videos/task_test':
                self.respond({'id': 'task_test', 'status': 'completed',
                              'result': {'video_url': base + '/cdn/result.mp4'}})
            elif self.path == '/cdn/result.mp4':
                self.send_response(200)
                self.send_header('Content-Length', str(len(media)))
                self.end_headers()
                self.wfile.write(media)
            else:
                self.respond({'message': 'not found'}, 404)

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            calls.append(('POST', self.path, self.headers.get('Authorization'), body))
            self.respond({'id': 'task_test', 'status': 'queued'})

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    base = f'http://127.0.0.1:{server.server_port}'
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield base, calls, media
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_real_http_flow_and_live_catalog(api_server, monkeypatch, tmp_path):
    base, calls, media = api_server
    pc = {'api_key': 'test-key', 'base_url': base + '/api', 'proxy': 'direct'}
    p = hongniao.HongniaoProvider(**pc)
    assert p.selftest()['ok']
    catalog = model_catalog.fetch('hongniao', pc)
    assert catalog['ok'] and catalog['models'] == ['video-sd25-30s']
    cap = model_catalog.apply_catalog(p.capabilities(), catalog, 'live')
    options = cap['video']['model_options']['video-sd25-30s']
    assert options['durations'] == [5, 30] and options['ratios'] == ['9:16']
    assert options['max_refs'] == 30 and options['max_video_refs'] == 2
    monkeypatch.setattr(hongniao, '_wait', lambda *a: None)
    target = tmp_path / 'result.mp4'
    result = p.generate_video(VideoTask('猫', duration=30,
        refs=['https://ref/1.png', 'data:image/png;base64,AAAA'],
        extra={'video_refs': ['https://ref/1.mp4'], 'audio_refs': ['https://ref/1.mp3'],
               'parameters': {'cameraLock': True}}), str(target), log=lambda _: None)
    assert result['task_id'] == 'task_test' and target.read_bytes() == media
    post = next(c for c in calls if c[0] == 'POST')
    assert post[1:3] == ('/api/v1/videos', 'Bearer test-key')
    assert post[3]['seconds'] == '30'
    assert post[3]['images'] == ['https://ref/1.png', 'data:image/png;base64,AAAA']
    assert post[3]['videos'] == ['https://ref/1.mp4']
    assert post[3]['audios'] == ['https://ref/1.mp3']
    assert post[3]['parameters'] == {'cameraLock': True}
    assert calls[-1][1:3] == ('/cdn/result.mp4', None)


@pytest.mark.parametrize('base', ['', 'https://an.hongniaoai.com',
                                  'https://an.hongniaoai.com/api/',
                                  'https://an.hongniaoai.com/api/v1'])
def test_provider_registration_and_address(base):
    p = hongniao.HongniaoProvider(base_url=base)
    assert p.session.base_url == 'https://an.hongniaoai.com/api'
    assert providers.REGISTRY['hongniao'].name == '红鸟'
    assert len(p.capabilities()['video']['models']) == 5
    assert p.capabilities()['supports'] == ['video']


@pytest.mark.parametrize('result', [{'video_url': 'https://cdn/result.mp4'},
                                   {'result': {'video_url': 'https://cdn/result.mp4'}}])
def test_completion_does_not_require_progress_100(provider, monkeypatch, result):
    request = Mock(return_value=dict(id='t1', status='completed', progress=80, **result))
    monkeypatch.setattr(provider, '_request', request)
    save = Mock()
    monkeypatch.setattr(provider.session, 'save_item', save)
    provider.generate_video(VideoTask('cat'), 'video.mp4')
    assert request.call_count == 1
    save.assert_called_once()


@pytest.mark.parametrize('failure', ['missing_url', 'download', 'submit_timeout', 'missing_id', 'poll_timeout'])
def test_no_paid_resubmission_after_uncertain_or_completed_task(provider, monkeypatch, failure):
    receipt = {'id': 't1', 'status': 'completed', 'video_url': 'https://cdn/result.mp4'}
    if failure == 'missing_url': receipt.pop('video_url')
    if failure == 'missing_id': receipt.pop('id')
    request = Mock(return_value=receipt)
    if failure == 'submit_timeout': request.side_effect = ApiError('timeout', kind=RETRYABLE)
    monkeypatch.setattr(provider, '_request', request)
    monkeypatch.setattr(provider.session, 'save_item', Mock(side_effect=ApiError('download failed')))
    with pytest.raises(ApiError) as caught:
        provider.generate_video(VideoTask('cat'), 'video.mp4',
                                poll_timeout=0 if failure == 'poll_timeout' else 10)
    assert caught.value.kind == TASK_FATAL
    assert caught.value.err_code == 'result_download_failed'
    assert request.call_count == 1


def test_failed_task_preserves_error_and_does_not_download(provider, monkeypatch):
    monkeypatch.setattr(provider, '_request', Mock(return_value={
        'id': 't1', 'status': 'failed', 'error': {'code': 'generation_failed', 'message': '参考图不可用'}}))
    save = Mock()
    monkeypatch.setattr(provider.session, 'save_item', save)
    with pytest.raises(ApiError, match='t1.*参考图不可用') as caught:
        provider.generate_video(VideoTask('cat'), 'video.mp4')
    assert caught.value.err_code == 'generation_failed'
    save.assert_not_called()


def test_transient_poll_failure_keeps_same_task(provider, monkeypatch):
    request = Mock(side_effect=[{'id': 't1', 'status': 'queued'},
                               ApiError('busy', kind=RETRYABLE),
                               {'status': 'completed', 'video_url': 'https://cdn/result.mp4'}])
    monkeypatch.setattr(provider, '_request', request)
    monkeypatch.setattr(hongniao, '_wait', lambda *a: None)
    monkeypatch.setattr(provider.session, 'save_item', Mock())
    provider.generate_video(VideoTask('cat'), 'video.mp4')
    assert [call.args[:2] for call in request.call_args_list] == [
        ('POST', '/v1/videos'), ('GET', '/v1/videos/t1'), ('GET', '/v1/videos/t1')]


@pytest.mark.parametrize('code,kind', [(401, BATCH_FATAL), (402, BATCH_FATAL), (429, RETRYABLE), (400, TASK_FATAL)])
def test_business_errors_in_http_200_are_not_success(provider, monkeypatch, code, kind):
    client = Mock()
    response = Mock(status_code=200)
    response.json.return_value = {'code': code, 'message': 'error test-key'}
    client.request.return_value.__enter__ = Mock(return_value=response)
    client.request.return_value.__exit__ = Mock(return_value=False)
    factory = Mock()
    factory.return_value.__enter__ = Mock(return_value=client)
    factory.return_value.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(hongniao.requests, 'Session', factory)
    with pytest.raises(ApiError) as caught:
        provider._request('GET', '/v1/models')
    assert caught.value.kind == kind
    assert 'test-key' not in str(caught.value)


def test_cancel_before_submit(provider, monkeypatch):
    request = Mock()
    monkeypatch.setattr(provider, '_request', request)
    with pytest.raises(ApiError, match='取消'):
        provider.generate_video(VideoTask('cat'), 'video.mp4', cancel=lambda: True)
    request.assert_not_called()


def _project(tmp_path, raw):
    (tmp_path / '03_提示词').mkdir()
    (tmp_path / '07_检查与记录').mkdir()
    (tmp_path / '05_分段视频').mkdir()
    (tmp_path / '03_提示词' / 'tasks.json').write_text(json.dumps({'video_tasks': [
        {'key': 'EP01-SEG002', 'output': '05_分段视频/EP01-SEG002.mp4'},
        {'key': 'EP01-SEG003', 'output': '05_分段视频/EP01-SEG003.mp4'}]}), encoding='utf-8')
    (tmp_path / '07_检查与记录' / 'failures.json').write_text(json.dumps([
        {'stage': 'video', 'target': 'EP01-SEG002', 'code': 'RESULT_DOWNLOAD', 'raw': raw}],
        ensure_ascii=False), encoding='utf-8')
    return tmp_path / '05_分段视频'


def _raised(provider, dest, **kw):
    with pytest.raises(ApiError) as caught:
        provider.generate_video(VideoTask('cat', duration=30), str(dest), **kw)
    return str(caught.value)


@pytest.mark.parametrize('raw', [
    '红鸟任务 task_1790_ab_video_generation 查询超过 2400 秒，请先核对服务商结果再重试',
    '红鸟任务 task_1790_ab_video_generation 已完成，视频下载未成功：连接被重置',
    '红鸟任务 task_1790_ab_video_generation 查询已取消，服务商那边的任务不受影响；重跑这一段会继续查询它',
    '红鸟任务 task_1790_ab_video_generation 查询中断：红鸟接口错误：余额不足'])
def test_rerun_follows_recorded_task_instead_of_paying_again(provider, monkeypatch, tmp_path, raw):
    videos = _project(tmp_path, raw)
    request = Mock(return_value={'status': 'completed', 'video_url': 'https://cdn/r.mp4'})
    monkeypatch.setattr(provider, '_request', request)
    save = Mock()
    monkeypatch.setattr(provider.session, 'save_item', save)
    result = provider.generate_video(VideoTask('cat', duration=30),
                                     str(videos / 'EP01-SEG002.mp4'), log=lambda _: None)
    assert [c.args[:2] for c in request.call_args_list] == [
        ('GET', '/v1/videos/task_1790_ab_video_generation')]
    assert result['task_id'] == 'task_1790_ab_video_generation'
    assert save.call_args.args == ('https://cdn/r.mp4', str(videos / 'EP01-SEG002.mp4'))


@pytest.mark.parametrize('raw,seg', [
    ('红鸟任务 task_x 失败：参考图不可用', 'EP01-SEG002'),
    ('红鸟任务 task_x 查询超过 2400 秒', 'EP01-SEG003'),
    ('红鸟查询不到原任务 task_x（红鸟接口错误：not found），重跑这一段将重新提交', 'EP01-SEG002')])
def test_rerun_submits_when_no_task_is_pending_for_this_segment(provider, monkeypatch, tmp_path, raw, seg):
    videos = _project(tmp_path, raw)
    request = Mock(return_value={'id': 'task_new', 'status': 'completed', 'video_url': 'https://cdn/r.mp4'})
    monkeypatch.setattr(provider, '_request', request)
    monkeypatch.setattr(provider.session, 'save_item', Mock())
    provider.generate_video(VideoTask('cat', duration=30), str(videos / f'{seg}.mp4'))
    assert request.call_args_list[0].args[:2] == ('POST', '/v1/videos')


def test_interrupted_follow_keeps_task_id_for_next_rerun(provider, monkeypatch, tmp_path):
    videos = _project(tmp_path, '红鸟任务 task_old 查询超过 2400 秒')
    dest = videos / 'EP01-SEG002.mp4'
    monkeypatch.setattr(hongniao, '_wait', lambda *a: None)
    monkeypatch.setattr(provider, '_request', Mock(return_value={'status': 'processing'}))
    cancelled = iter([False, False, True])
    message = _raised(provider, dest, cancel=lambda: next(cancelled, True))
    assert hongniao._PENDING.search(message).group(1) == 'task_old'

    monkeypatch.setattr(provider, '_request', Mock(side_effect=ApiError('红鸟接口错误：余额不足',
                                                                     status=402, kind=BATCH_FATAL)))
    message = _raised(provider, dest)
    assert hongniao._PENDING.search(message).group(1) == 'task_old'

    monkeypatch.setattr(provider, '_request', Mock(side_effect=ApiError('红鸟接口错误：not found',
                                                                     status=404, kind=TASK_FATAL)))
    assert not hongniao._PENDING.search(_raised(provider, dest))


def test_provider_file_can_load_as_old_exe_plugin():
    path = Path(hongniao.__file__)
    spec = importlib.util.spec_from_file_location('test_hongniao_plugin', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert providers._check(module.HongniaoProvider) == []
