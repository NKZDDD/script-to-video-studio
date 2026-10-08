"""Latest Kunji banana names must use Gemini end-to-end, including external plugins."""
import base64
import importlib.util
import io
from pathlib import Path

import pytest
from PIL import Image

from core.model_catalog import apply_catalog
from core.providers import kunji
from core.providers.base import ImageTask


MODELS = (
    'gemini-nano-banana-2.1',
    'gemini-3-pro-image',
    'gemini-3-pro-image-preview',
    'gemini-3.1-flash-image',
    'gemini-3.1-flash-image-preview',
)


@pytest.fixture(params=['builtin', 'plugin'])
def mod(request):
    if request.param == 'builtin':
        return kunji
    path = Path(__file__).resolve().parents[1] / 'provider_patches/zz_kunji_latest.py'
    spec = importlib.util.spec_from_file_location('kunji_latest_fixture', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('model', MODELS)
@pytest.mark.parametrize('with_refs', [False, True])
def test_new_names_use_gemini_and_save_image(mod, model, with_refs, tmp_path):
    buf = io.BytesIO()
    Image.new('RGB', (64, 64), 'yellow').save(buf, format='PNG', compress_level=0)
    raw = buf.getvalue()
    b64 = base64.b64encode(raw).decode()
    data_uri = 'data:image/png;base64,' + b64
    refs = [data_uri, 'https://cdn.example/second.png'] if with_refs else []
    p = mod.KunjiProvider(api_key='1k=banana-test;4k=other-test')
    calls = []

    def request(method, path, **kw):
        calls.append((method, path, kw))
        assert p.session.api_key == 'banana-test'
        return {'candidates': [{'content': {'parts': [
            {'inlineData': {'mimeType': 'image/png', 'data': b64}}
        ]}}]}

    p.session.request = request
    dest = tmp_path / 'generated.png'
    p.generate_image(ImageTask(prompt='一把黄色茶壶', model=model, size='1K',
                               refs=refs, extra={'ratio': '1:1'}), str(dest))
    assert len(calls) == 1
    method, path, kw = calls[0]
    assert method == 'POST'
    assert path == f'/v1beta/models/{model}:generateContent'
    assert 'files' not in kw
    body = kw['json_body']
    assert body['generationConfig']['imageConfig'] == {'imageSize': '1K', 'aspectRatio': '1:1'}
    parts = body['contents'][0]['parts']
    assert parts[0] == {'text': '一把黄色茶壶'}
    if with_refs:
        assert parts[1] == {'inline_data': {'mime_type': 'image/png', 'data': b64}}
        assert parts[2] == {'file_data': {'mime_type': 'image/png', 'file_uri': refs[1]}}
    else:
        assert len(parts) == 1
    assert dest.read_bytes() == raw
    assert p.accepts_url(model, 'image') and not p.needs_bytes(model)


def test_old_catalog_cannot_hide_new_banana_names(mod):
    cap = apply_catalog(mod.KunjiProvider().capabilities(),
                        {'rows': [{'id': 'gpt-image-2'}]}, 'old snapshot')
    assert set(MODELS).issubset(cap['image']['models'])


def test_gpt_still_uses_openai_image_endpoint(mod):
    p = mod.KunjiProvider(api_key='fake')
    calls = []
    def request(method, path, **kw):
        calls.append(path)
        return {'data': [{'b64_json': 'aGk='}]}
    p.session.request = request
    p.session.save_item = lambda *a, **kw: None
    p.generate_image(ImageTask(prompt='x', model='gpt-image-2', size='1K'), 'unused.png')
    assert calls == ['/v1/images/generations']
    assert p.needs_bytes('gpt-image-2') and not p.accepts_url('gpt-image-2', 'image')


def test_plugin_matches_builtin_source():
    root = Path(__file__).resolve().parents[1]
    assert (root / 'provider_patches/zz_kunji_latest.py').read_bytes() == (root / 'core/providers/kunji.py').read_bytes()
