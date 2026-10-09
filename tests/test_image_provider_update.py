import base64
import io
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from core import apiutil, produce, uploader
from core.providers.base import ImageTask
from core.providers.cangyuan import CangyuanProvider, model_options
from core.providers.chaomo_banana import ChaomoBananaProvider
from core.providers.kunji import KunjiProvider
from core.reference_images import limit_bytes, local_copy


def picture(fmt='JPEG', alpha=False):
    im = Image.effect_noise((128, 128), 60).convert('RGBA' if alpha else 'RGB')
    if alpha:
        im.putalpha(120)
    buf = io.BytesIO()
    im.save(buf, format=fmt)
    return buf.getvalue()


class Response:
    def __init__(self, raw):
        self.raw = raw
        self.status_code = 200
        self.headers = {'Content-Length': str(len(raw))}
    def raise_for_status(self): pass
    def iter_content(self, chunk_size): yield self.raw
    def close(self): pass
    def __enter__(self): return self
    def __exit__(self, *args): pass


@pytest.mark.parametrize('fmt', ['JPEG', 'WEBP'])
@pytest.mark.parametrize('transport', ['url', 'data', 'bare'])
def test_image_result_is_transcoded_to_real_png(fmt, transport, tmp_path, monkeypatch):
    raw = picture(fmt)
    session = apiutil.HttpSession('fake', 'https://example.invalid')
    monkeypatch.setattr(apiutil.requests, 'get', lambda *a, **kw: Response(raw))
    item = base64.b64encode(raw).decode()
    if transport == 'url': item = 'https://cdn.invalid/image.jpg'
    elif transport == 'data': item = 'data:image/jpeg;base64,' + item
    dest = tmp_path / 'asset.png'
    session.save_item(item, str(dest))
    assert dest.read_bytes().startswith(b'\x89PNG')
    with Image.open(dest) as im:
        im.load()
        assert im.size == (128, 128)
    assert session.result_source_bytes[str(dest)] == len(raw)


def test_invalid_jpeg_does_not_replace_good_output(tmp_path):
    dest = tmp_path / 'asset.png'
    old = picture('PNG')
    dest.write_bytes(old)
    bad = base64.b64encode(picture()[:-200]).decode()
    with pytest.raises(apiutil.ApiError):
        apiutil.HttpSession('', '').save_item('data:image/jpeg;base64,'+bad, str(dest))
    assert dest.read_bytes() == old


def test_cmyk_jpeg_is_converted_to_png(tmp_path):
    buf = io.BytesIO()
    Image.effect_noise((128,128), 60).convert('CMYK').save(buf, format='JPEG')
    dest = tmp_path/'result.png'
    apiutil.HttpSession('', '').save_item('data:image/jpeg;base64,'+base64.b64encode(buf.getvalue()).decode(), str(dest))
    with Image.open(dest) as im:
        assert im.format == 'PNG' and im.mode == 'RGB' and im.size == (128,128)


def test_banana_jpeg_upload_copy_url_keeps_mime(tmp_path, monkeypatch):
    p = KunjiProvider('fake')
    def request(*a, **kw):
        parts = kw['json_body']['contents'][0]['parts']
        assert parts[1]['file_data']['mime_type'] == 'image/jpeg'
        assert parts[2]['file_data']['mime_type'] == 'image/webp'
        return {'data':[{'b64_json':base64.b64encode(picture()).decode()}]}
    monkeypatch.setattr(p.session, 'request', request)
    p.generate_image(ImageTask('x',model='gemini-nano-banana-2.1',size='16:9',
        refs=['https://cdn.invalid/compressed.jpg?x=1','https://cdn.invalid/alpha.webp']), str(tmp_path/'out.png'))


def test_banana_real_worker_path_saves_jpeg_in_png_contract(tmp_path, monkeypatch):
    raw = picture()
    p = KunjiProvider(api_key='fake')
    seen = []
    def request(method, path, **kw):
        seen.append(kw['json_body'])
        return {'candidates': [{'content': {'parts': [{'inlineData': {
            'mimeType': 'image/jpeg', 'data': base64.b64encode(raw).decode()}}]}}]}
    monkeypatch.setattr(p.session, 'request', request)
    dest = tmp_path / 'asset.png'
    p.generate_image(ImageTask(prompt='茶壶', model='gemini-nano-banana-2.1', size='16:9',
                               extra={'resolution': '4K'}), str(dest))
    assert seen[0]['generationConfig']['imageConfig'] == {'imageSize': '4K', 'aspectRatio': '16:9'}
    assert Image.open(dest).format == 'PNG'


@pytest.mark.parametrize('alpha', [False, True])
def test_reference_budget_preserves_source_and_alpha(alpha, tmp_path):
    raw = picture('PNG', alpha)
    path = tmp_path / 'original.png'
    path.write_bytes(raw)
    logs = []
    prepared = local_copy(path, tmp_path, maximum=8000, log=logs.append)
    assert Path(prepared).stat().st_size <= 8000
    assert path.read_bytes() == raw
    with Image.open(prepared) as im:
        im.load()
        if alpha:
            assert 'A' in im.getbands()
    assert logs and '原图保留' in logs[0]


def test_under_budget_does_not_reencode():
    raw = picture('PNG')
    assert limit_bytes(raw)[0] is raw


@pytest.mark.parametrize('mode', ['bytes', 'data', 'url'])
def test_all_reference_transports_obey_budget(mode, tmp_path, monkeypatch):
    path = tmp_path / 'source.png'
    raw = picture('PNG');path.write_bytes(raw)
    class Provider:
        def capabilities(self): return {'image': {'ref_max_bytes': 8000}}
        def needs_url(self, *a): return mode == 'url'
        def needs_bytes(self, *a): return mode == 'bytes'
        def accepts_url(self, *a): return mode == 'url'
    received = []
    def upload(p, *a, **kw):
        received.append(Path(p).read_bytes());return 'https://cdn.invalid/prepared.jpg'
    monkeypatch.setattr(uploader, 'to_url', upload)
    resolver = produce.make_ref_resolver(SimpleNamespace(root=str(tmp_path)), Provider(), {}, 'm', 0)
    result = resolver(str(path))
    data = received[0] if mode == 'url' else Path(result).read_bytes() if mode == 'bytes' else base64.b64decode(result.partition(',')[2])
    assert len(data) <= 8000
    assert path.read_bytes() == raw


def test_cangyuan_fixed_model_never_silently_switches_channel():
    p = CangyuanProvider()
    path, body = p.build_image_body(ImageTask('x', model='gpt-image-2.5-flare-4k', size='9:16'))
    assert body['model'] == 'gpt-image-2.5-flare-4k'
    assert not {'quality', 'image_size', 'tier', 'series'} & body.keys()
    with pytest.raises(apiutil.ApiError):
        p.build_image_body(ImageTask('x', model='gpt-image-2.5-flare-4k', size='9:16', extra={'resolution':'1K'}))


def test_cangyuan_banana_uses_quality_and_https_json_refs():
    refs = [f'https://cdn.invalid/{i}.png' for i in range(14)]
    path, body = CangyuanProvider().build_image_body(ImageTask('x', model='gemini-nano-banana-2.1',
        size='16:9', refs=refs, extra={'resolution':'4K'}))
    assert path == '/v1/images/edits'
    assert body['images'] == refs and body['quality'] == '4k'
    assert body['size'] == '16:9'
    assert not {'aspect_ratio', 'image_size', 'output_resolution'} & body.keys()
    with pytest.raises(apiutil.ApiError):
        CangyuanProvider().build_image_body(ImageTask('x', model='gemini-nano-banana-2.1', size='1024x1024'))


@pytest.mark.parametrize('model,maximum', [('gpt-image-2',9), ('gpt-image-2.5-flare-4k',16),
    ('gemini-nano-banana-2.1',14), ('doubao-seedream-5-0-pro',10), ('grok-imagine-image',8), ('midjourney-v7',5)])
def test_cangyuan_reference_counts_are_per_model(model, maximum):
    refs = [f'https://cdn.invalid/{i}.png' for i in range(maximum)]
    p = CangyuanProvider()
    assert p.build_image_body(ImageTask('x',model=model,size='1:1',refs=refs))[1]['images'] == refs
    with pytest.raises(apiutil.ApiError):
        p.build_image_body(ImageTask('x',model=model,size='1:1',refs=refs+['https://cdn.invalid/last.png']))


def test_cangyuan_task_uses_same_creation_query_endpoint(tmp_path, monkeypatch):
    p = CangyuanProvider()
    requests_seen = []
    raw = picture()
    def request(method, path, **kw):
        requests_seen.append((method,path,kw))
        if method == 'POST':return {'id':'task_test','status':'queued'}
        return {'status':'completed','data':[{'b64_json':base64.b64encode(raw).decode()}]}
    monkeypatch.setattr(p.session,'request',request)
    dest = tmp_path/'result.png'
    p.generate_image(ImageTask('x',model='gpt-image-2',size='1:1',refs=['https://cdn.invalid/a.png']),str(dest))
    assert [(a,b) for a,b,c in requests_seen] == [('POST','/v1/images/edits'),('GET','/v1/images/edits/task_test')]
    assert Image.open(dest).format == 'PNG'


def test_chaomo_banana_metadata_compares_before_transcode(tmp_path, monkeypatch):
    raw = picture()
    p = ChaomoBananaProvider(api_key='banana-fake')
    def request(*args, **kw):
        assert kw['json_body']['image_size'] == '2K'
        assert 'size' not in kw['json_body']
        return {'data':[{'b64_json':base64.b64encode(raw).decode()}],
                'metadata':{'width':128,'height':128,'bytes':len(raw),'format':'JPEG'}}
    monkeypatch.setattr(p.session,'request',request)
    dest = tmp_path/'asset.png'
    p.generate_image(ImageTask('x',model='gemini-nano-banana-2.1',size='16:9',extra={'resolution':'2K'}),str(dest))
    assert Image.open(dest).format == 'PNG'


@pytest.mark.parametrize('ratio', ['', '16:9'])
@pytest.mark.parametrize('override', ['', '9:16'])
def test_legacy_banana_size_keeps_resolution_in_agent_preview(tmp_path, ratio, override):
    from core import agent_projects as A, agent_runtime as R
    from core.store import write_json
    from test_agent_workspace import plan_for, publish, resolve
    cap = KunjiProvider().capabilities()
    model = 'gemini-nano-banana-2.1'
    pj = A.create_project(str(tmp_path), {'title':'旧香蕉项目','source':'fixture',
        'options':{'image_provider':'kunji','image_model':model}}, [cap])
    plan = plan_for(pj, count=1)
    plan['asset_tasks'][0]['params'] = {'size':'4K', 'ratio':ratio}
    publish(pj, plan);A.scan(pj)
    settings = R.validate_settings({'asset':{'provider':'kunji','model':model,
        'concurrency':1,'size':override}}, [cap])
    write_json(pj.p('07_检查与记录','production-settings.json'), settings)
    preview = R.preview(pj, {}, [cap], resolve)
    assert not preview['blocked'], preview['blocked']
    assert preview['_tasks'][0]['params']['resolution'] == '4K'
    assert preview['_tasks'][0]['params']['size'] == (override or ratio or 'auto')


def test_cangyuan_custom_pixels_and_series_reach_preview(tmp_path):
    from core import agent_projects as A, agent_runtime as R
    from core.store import write_json
    from test_agent_workspace import plan_for, publish, resolve
    cap = CangyuanProvider().capabilities()
    model = 'gpt-image-2.5-x'
    pj = A.create_project(str(tmp_path), {'title':'沧元参数','source':'fixture',
        'options':{'image_provider':'cangyuan','image_model':model}}, [cap])
    publish(pj, plan_for(pj, count=1));A.scan(pj)
    settings = R.validate_settings({'asset':{'provider':'cangyuan','model':model,
        'concurrency':1,'size':'2560x1440','series':'sunburst','image_resolution':'2K'}}, [cap])
    write_json(pj.p('07_检查与记录','production-settings.json'), settings)
    preview = R.preview(pj, {}, [cap], resolve)
    assert not preview['blocked'], preview['blocked']
    params = preview['_tasks'][0]['params']
    body = CangyuanProvider().build_image_body(ImageTask('x', model=model, size=params['size'], extra=params))[1]
    assert (body['size'],body['tier'],body['series']) == ('2560x1440','2k','sunburst')


@pytest.mark.parametrize('model', ['gpt-image-2-2k', 'doubao-seedream-5-0-pro'])
def test_current_channel_or_pixels_override_old_independent_resolution(tmp_path, model):
    from core import agent_projects as A, agent_runtime as R
    from core.store import write_json
    from test_agent_workspace import plan_for, publish, resolve
    cap = CangyuanProvider().capabilities()
    pj = A.create_project(str(tmp_path), {'title':'旧参数切换通道','source':'fixture',
        'options':{'image_provider':'cangyuan','image_model':model}}, [cap])
    plan = plan_for(pj, count=1)
    plan['asset_tasks'][0]['params'] = {'size':'16:9','resolution':'4K'}
    publish(pj, plan);A.scan(pj)
    settings = R.validate_settings({'asset':{'provider':'cangyuan','model':model,
        'concurrency':1,'size':'2048x2048'}}, [cap])
    write_json(pj.p('07_检查与记录','production-settings.json'), settings)
    preview = R.preview(pj, {}, [cap], resolve)
    assert not preview['blocked'], preview['blocked']
    params = preview['_tasks'][0]['params']
    body = CangyuanProvider().build_image_body(ImageTask('x',model=model,size=params['size'],extra=params))[1]
    assert body['model'] == model and body['size'] == '2048x2048'
    assert 'quality' not in body and 'tier' not in body


def test_remote_reference_is_measured_and_reuploaded_when_oversize(tmp_path, monkeypatch):
    raw = picture('PNG')
    monkeypatch.setattr(apiutil.requests, 'get', lambda *a, **kw: Response(raw))
    p = CangyuanProvider()
    monkeypatch.setattr(p, 'capabilities', lambda: {'image':{'ref_max_bytes':8000}})
    uploads = []
    def upload(path, *a, **kw):
        uploads.append(Path(path).read_bytes());return 'https://cdn.invalid/small.jpg'
    monkeypatch.setattr(uploader, 'to_url', upload)
    resolve = produce.make_ref_resolver(SimpleNamespace(root=str(tmp_path)), p, {}, 'm', 0)
    assert resolve('https://cdn.invalid/original.png') == 'https://cdn.invalid/small.jpg'
    assert len(uploads) == 1 and len(uploads[0]) <= 8000


@pytest.mark.parametrize('remote', [True, False])
def test_reference_cache_reuses_concurrent_complete_copy_on_windows(tmp_path, monkeypatch, remote):
    from core import reference_images as refs
    raw = picture('PNG')
    original = tmp_path/'source.png';original.write_bytes(raw)
    def concurrent_publish(source, dest):
        # 另一线程已完成同一哈希的发布，并正在读取；Windows 拒绝 replace。
        Path(dest).write_bytes(Path(source).read_bytes())
        raise PermissionError('sharing violation')
    monkeypatch.setattr(refs.os, 'replace', concurrent_publish)
    monkeypatch.setattr(apiutil.requests, 'get', lambda *a, **kw: Response(raw))
    result = refs.remote_copy('https://cdn.invalid/source.png', tmp_path) if remote else refs.local_copy(original, tmp_path, maximum=8000)
    with Image.open(result) as im:
        im.load()
    assert original.read_bytes() == raw
    assert not list((tmp_path/'07_检查与记录'/'reference-cache').glob('.*'))


def test_chaomo_banana_512_is_declared_and_sent(tmp_path, monkeypatch):
    from core import agent_runtime as R
    p = ChaomoBananaProvider()
    model = 'gemini-nano-banana-2.1'
    row = R.validate_settings({'asset':{'provider':p.id,'model':model,'concurrency':1,
        'image_resolution':'512'}}, [p.capabilities()])['asset']
    sent = []
    def request(*a, **kw):
        sent.append(kw['json_body'])
        return {'data':[{'b64_json':base64.b64encode(picture()).decode()}]}
    monkeypatch.setattr(p.session,'request',request)
    p.generate_image(ImageTask('x',model=model,size='16:9',extra={'resolution':row['image_resolution']}), str(tmp_path/'x.png'))
    assert sent[0]['image_size'] == '512'
