import base64
import importlib.util
import io
from pathlib import Path
from unittest.mock import Mock

import pytest
from PIL import Image

from core.apiutil import ApiError
from core.providers import _check, _classes_in
from core.providers.base import ImageTask
from core.providers.hzyfai import HzyfaiProvider, IMAGE_MODELS, IMAGE_SIZES
from test_agent_workspace import pj, plan_for, publish, resolve  # noqa: F401


def image_bytes(fmt="PNG"):
    stream = io.BytesIO()
    Image.new("RGB", (16, 16), "red").save(stream, fmt)
    return stream.getvalue()


def provider():
    p = HzyfaiProvider(api_key="fixture")
    p.session.request = Mock(return_value={"request_id": "tracking-only", "data": [{"url": "https://cdn.example/result.png"}]})
    p.session.save_item = Mock()
    return p


@pytest.mark.parametrize("model", IMAGE_MODELS)
@pytest.mark.parametrize("size", IMAGE_SIZES)
def test_generation_fields_and_tracking_id(model, size):
    p = provider()
    out = p.generate_image(ImageTask("test image", model=model, size=size), "out.png")
    args, kw = p.session.request.call_args
    assert args == ("POST", "/v1/images/generations")
    assert kw["json_body"] == {"model": model, "prompt": "test image", "size": size, "n": 1, "response_format": "url"}
    assert kw["retries"] == 1
    assert out["task_id"] == ""
    p.session.save_item.assert_called_once_with("https://cdn.example/result.png", "out.png")


@pytest.mark.parametrize("base", ["https://hzyfai.com", "https://hzyfai.com/", "https://hzyfai.com/v1", "https://hzyfai.com/v1/"])
def test_base_url_does_not_duplicate_version(base):
    assert HzyfaiProvider(base_url=base).session.base_url == "https://hzyfai.com"


def test_reference_order_bytes_and_mime_preserved_without_nine_image_limit(tmp_path):
    png, jpg = image_bytes(), image_bytes("JPEG")
    wrong_extension = tmp_path / "actually-jpeg.png"
    wrong_extension.write_bytes(jpg)
    uri = "data:image/png;base64," + base64.b64encode(png).decode()
    p = provider()
    p.generate_image(ImageTask("combine", refs=[str(wrong_extension)] + [uri]*11, size="1536x1024"), "out.png")
    args, kw = p.session.request.call_args
    assert args == ("POST", "/v1/images/edits")
    uploads = [v for k,v in kw["files"] if k=="image[]"]
    assert len(uploads) == 12
    assert uploads[0] == ("reference-1.jpg", jpg, "image/jpeg")
    assert [v[1] for v in uploads[1:]] == [png]*11
    assert dict((k,v[1]) for k,v in kw["files"] if k!="image[]")["size"] == "1536x1024"


@pytest.mark.parametrize("raw", [b"", b"<html>error</html>", image_bytes("JPEG")[:-20]])
def test_invalid_image_rejected_before_post(raw):
    p = provider()
    with pytest.raises(ApiError, match="第 1 张参考图"):
        p.generate_image(ImageTask("edit", refs=["data:image/png;base64,"+base64.b64encode(raw).decode()]), "out.png")
    p.session.request.assert_not_called()


@pytest.mark.parametrize("params", [{"model":"not-supported"}, {"size":"4K"}, {"extra":{"resolution":"2K"}}, {"prompt":""}, {"n":2}])
def test_unsupported_parameters_are_not_silently_changed(params):
    p = provider()
    with pytest.raises(ApiError):
        p.generate_image(ImageTask(**dict({"prompt":"x","size":"1024x1024"},**params)), "out.png")
    p.session.request.assert_not_called()


def test_cancel_and_empty_response():
    p = provider()
    with pytest.raises(ApiError, match="取消"):
        p.generate_image(ImageTask("x"), "out.png", cancel=lambda:True)
    p.session.request.assert_not_called()
    p.session.request.return_value = {"data": []}
    with pytest.raises(ApiError) as caught:
        p.generate_image(ImageTask("x"), "out.png")
    assert caught.value.kind == "task_fatal"
    p.session.save_item.assert_not_called()


def test_base64_output():
    p = provider()
    encoded = base64.b64encode(image_bytes()).decode()
    p.session.request.return_value = {"data":[{"b64_json":encoded}]}
    p.generate_image(ImageTask("x"), "out.png")
    assert p.session.save_item.call_args.args[0].endswith(encoded)


@pytest.mark.parametrize("size,resolution", [("4096x4096", ""), ("1024x1024", "4K"), ("4096x4096", "4K")])
@pytest.mark.parametrize("with_reference", [False, True])
def test_4k_uses_verified_pixel_size_for_generation_and_edits(size, resolution, with_reference):
    p = provider()
    refs = ["data:image/png;base64," + base64.b64encode(image_bytes()).decode()] if with_reference else []
    p.generate_image(ImageTask("test", model="gpt-image-2", size=size, refs=refs,
                               extra={"resolution": resolution}), "out.png")
    _, kw = p.session.request.call_args
    fields = {k: v[1] for k, v in kw["files"] if k != "image[]"} if with_reference else kw["json_body"]
    assert fields["size"] == "4096x4096"
    assert "resolution" not in fields
    p.session.request.assert_called_once()


@pytest.mark.parametrize("params", [
    {"model": "gpt-image-2.5", "size": "4096x4096"},
    {"model": "gpt-image-2.5", "extra": {"resolution": "4K"}},
    {"size": "1536x1024", "extra": {"resolution": "4K"}},
    {"size": "1024x1536", "extra": {"resolution": "4K"}},
])
def test_4k_does_not_invent_other_model_support_or_change_aspect(params):
    p = provider()
    with pytest.raises(ApiError):
        p.generate_image(ImageTask(**dict({"prompt": "x", "size": "1024x1024"}, **params)), "out.png")
    p.session.request.assert_not_called()


def test_existing_production_preview_accepts_plugin_4k_options(pj):
    from core import agent_runtime as runtime
    from core.store import write_json

    caps = [HzyfaiProvider().capabilities()]
    plan = plan_for(pj, count=1)
    plan["asset_tasks"][0]["params"]["size"] = "1:1"
    publish(pj, plan)
    setting = runtime.validate_settings({"asset": {"provider": "hzyfai", "model": "gpt-image-2",
                                        "size": "4096x4096", "image_resolution": "4K"}}, caps)
    write_json(pj.p("07_检查与记录", "production-settings.json"), setting)
    preview = runtime.preview(pj, {}, caps, resolve)
    assert not preview["blocked"]
    assert preview["_tasks"][0]["params"] == {"size": "4096x4096", "resolution": "4K"}


def test_capability_and_external_plugin():
    root = Path(__file__).resolve().parents[1]
    patch = root/'provider_patches/zz_hzyfai_image.py'
    assert patch.read_bytes() == (root/'core/providers/hzyfai.py').read_bytes()
    spec = importlib.util.spec_from_file_location('hzyfai_external_test', patch)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    classes = _classes_in(module)
    assert len(classes)==1 and not _check(classes[0])
    p = classes[0]()
    assert p.needs_bytes() and not p.needs_url() and not p.accepts_url()
    assert p.capabilities()['image']['refs_unlimited']
    assert p.capabilities() == HzyfaiProvider().capabilities()
    options = p.capabilities()['image']['model_options']['gpt-image-2']
    assert '4096x4096' in options['sizes'] and options['resolutions'] == ['4K']
