import base64
import importlib.util
import io
from pathlib import Path
from unittest.mock import Mock

import pytest
from PIL import Image

from core.apiutil import ApiError
from core.providers import _check, _classes_in
from core.providers.ake import AkeProvider, IMAGE_MODELS
from core.providers.base import ImageTask


def image_data(fmt="PNG"):
    stream = io.BytesIO()
    Image.new("RGB", (8, 6), "red").save(stream, format=fmt)
    return stream.getvalue()


def provider():
    p = AkeProvider(api_key="test")
    p.session.request = Mock(return_value={"data": [{"url": "https://cdn.example/result.png"}]})
    p.session.save_item = Mock()
    return p


@pytest.mark.parametrize("model,size", [("gpt-image-2", "3840x2160"),
                                       ("gpt-image-2", "2048x2048"),
                                       ("grok-imagine-image-2.0", "1024x1024")])
def test_generation_uses_documented_json(model, size):
    p = provider()
    p.generate_image(ImageTask("a scene", model=model, size=size), "output.png")
    args, kw = p.session.request.call_args
    assert args == ("POST", "/v1/images/generations")
    assert kw["json_body"] == {"model": model, "prompt": "a scene", "size": size, "n": 1}
    assert kw["retries"] == 1
    p.session.save_item.assert_called_once_with("https://cdn.example/result.png", "output.png")


def test_multiple_references_are_uploaded_in_order_without_truncation(tmp_path):
    raw = image_data("JPEG")
    # The filename and MIME declaration are wrong; actual JPEG bytes decide both.
    ref = tmp_path / "reference.png"
    ref.write_bytes(raw)
    uri = "data:image/png;base64," + base64.b64encode(raw).decode()
    p = provider()
    p.generate_image(ImageTask("a scene", refs=[str(ref)] + [uri] * 10,
                               model="gpt-image-2", size="3840x2160"), "out.png")
    args, kw = p.session.request.call_args
    assert args == ("POST", "/v1/images/edits")
    uploads = [value for field, value in kw["files"] if field == "image"]
    assert len(uploads) == 11
    assert all(item[1:] == (raw, "image/jpeg") for item in uploads)
    assert [u[0] for u in uploads] == [f"reference-{i}.jpg" for i in range(1, 12)]
    assert not any(k in ("images", "image[]", "resolution", "aspect_ratio") for k, _ in kw["files"])


@pytest.mark.parametrize("raw", [b"", b"<html>upstream error</html>", image_data("JPEG")[:-20]])
def test_invalid_reference_is_rejected_before_generation(raw):
    p = provider()
    uri = "data:image/png;base64," + base64.b64encode(raw).decode()
    with pytest.raises(ApiError, match="第 1 张参考图"):
        p.generate_image(ImageTask("x", refs=[uri], size="2048x2048"), "out.png")
    p.session.request.assert_not_called()


@pytest.mark.parametrize("model,size,res", [
    ("gpt-image-2", "4096x4096", ""), ("gpt-image-2", "9:16", ""),
    ("grok-imagine-image-2.0", "3840x2160", ""),
    ("grok-imagine-image-2.0", "1024x1024", "4K"),
    ("gpt-image-2", "2048x2048", "4K"),
])
def test_wrong_size_is_not_silently_changed(model, size, res):
    p = provider()
    with pytest.raises(ApiError):
        p.generate_image(ImageTask("x", model=model, size=size, extra={"resolution": res}), "out.png")
    p.session.request.assert_not_called()


@pytest.mark.parametrize("size,res,expected", [("16:9", "4K", "3840x2160"),
                                             ("1:1", "2K", "2048x2048"),
                                             ("", "4K", "3840x2160")])
def test_documented_size_aliases(size, res, expected):
    p = provider()
    p.generate_image(ImageTask("x", size=size, extra={"resolution": res}), "out.png")
    assert p.session.request.call_args.kwargs["json_body"]["size"] == expected


def test_base64_result_is_saved_and_empty_result_is_not_success():
    p = provider()
    encoded = base64.b64encode(image_data()).decode()
    p.session.request.return_value = {"data": [{"b64_json": encoded}]}
    p.generate_image(ImageTask("x", size="2048x2048"), "out.png")
    assert p.session.save_item.call_args.args[0].endswith(encoded)
    p.session.save_item.reset_mock()
    p.session.request.return_value = {"data": []}
    with pytest.raises(ApiError, match="没有返回可用图片"):
        p.generate_image(ImageTask("x", size="2048x2048"), "out.png")
    p.session.save_item.assert_not_called()


def test_image_and_video_reference_routing():
    p = AkeProvider()
    assert not _check(AkeProvider)
    for model in IMAGE_MODELS:
        assert p.needs_bytes(model)
        assert not p.needs_url(model, "image")
        assert not p.accepts_url(model, "image")
    assert p.needs_url("wan3.0-video", "video")
    assert p.accepts_url("wan3.0-video", "video")
    assert not p.needs_bytes("wan3.0-video")
    assert p.capabilities()["image"]["refs_unlimited"]


def test_external_plugin_is_complete_and_matches_builtin():
    root = Path(__file__).resolve().parents[1]
    plugin = root / "provider_patches" / "zz_ake_image.py"
    assert plugin.read_bytes() == (root / "core" / "providers" / "ake.py").read_bytes()
    spec = importlib.util.spec_from_file_location("ake_external_test", plugin)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    classes = _classes_in(module)
    assert len(classes) == 1 and classes[0].id == "ake"
    assert not _check(classes[0])
    assert classes[0]().capabilities() == AkeProvider().capabilities()
