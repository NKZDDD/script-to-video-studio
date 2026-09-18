"""Chaomo must receive complete images with matching multipart metadata."""
import base64
import io
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PIL import Image

from core import diagnose, produce
from core.apiutil import ApiError, TASK_FATAL
from core.providers.base import ImageTask
from core.providers.chaomo import ChaomoProvider
from core.store import Project, write_text


def picture(fmt="PNG", mode="RGB"):
    out = io.BytesIO()
    Image.new(mode, (48, 32), (40, 70, 120, 90) if mode == "RGBA" else (40, 70, 120)).save(out, fmt)
    return out.getvalue()


def uri(raw, mime="image/png"):
    return "data:" + mime + ";base64," + base64.b64encode(raw).decode("ascii")


@pytest.fixture
def provider(monkeypatch):
    p = ChaomoProvider(api_key="test", proxy="direct")
    monkeypatch.setattr(p.session, "request", Mock(return_value={"data": [{"url": "https://example.test/out.png"}]}))
    monkeypatch.setattr(p.session, "save_item", Mock())
    return p


@pytest.mark.parametrize("fmt,ext,mime", [
    ("PNG", "png", "image/png"), ("JPEG", "jpg", "image/jpeg"), ("WEBP", "webp", "image/webp"),
])
def test_multipart_metadata_uses_real_content_and_keeps_bytes(provider, fmt, ext, mime):
    raw = picture(fmt)
    logs = []
    provider.generate_image(ImageTask(prompt="x", refs=[uri(raw, "application/octet-stream")]), "out.png", log=logs.append)
    files = provider.session.request.call_args.kwargs["files"]
    assert [v for k, v in files if k == "image[]"] == [("ref_1." + ext, raw, mime)]
    assert any("48x32" in line and fmt in line for line in logs)


def test_transparent_png_is_not_reencoded(provider):
    raw = picture("PNG", "RGBA")
    got = provider._ref_bytes(uri(raw, "image/jpeg"), 2)
    assert got == (raw, "ref_2.png", "image/png")
    with Image.open(io.BytesIO(got[0])) as im:
        assert im.getpixel((0, 0))[3] == 90


@pytest.mark.parametrize("raw", [b"", b"<html>access denied</html>", b"<?xml version='1.0'?><Error>Denied</Error>",
                                  picture()[:33], picture()[:-20], picture("JPEG")[:-20], picture("WEBP")[:-20]],
                         ids=["empty", "html", "xml", "png-header", "png-truncated", "jpeg-truncated", "webp-truncated"])
def test_bad_second_ref_prevents_submission_without_silently_dropping_it(provider, raw):
    with pytest.raises(ApiError) as caught:
        provider.generate_image(ImageTask(prompt="x", refs=[uri(picture()), uri(raw)]), "out.png", log=lambda _: None)
    assert caught.value.kind == TASK_FATAL
    assert caught.value.err_code == "reference_invalid"
    assert "第 2 张" in str(caught.value)
    assert "任务明细" in str(caught.value)
    provider.session.request.assert_not_called()
    provider.session.save_item.assert_not_called()


@pytest.mark.parametrize("ref", ["data:image/png;base64,%%%", "data:image/png;base64,a", "data:image/png,hello", "unresolved-ref"])
def test_invalid_encoding_and_unresolved_refs_are_not_skipped(provider, ref):
    with pytest.raises(ApiError, match="第 2 张"):
        provider.generate_image(ImageTask(prompt="x", refs=[uri(picture()), ref]), "out.png", log=lambda _: None)
    provider.session.request.assert_not_called()


def test_unsupported_format_does_not_get_a_fake_png_name(provider):
    with pytest.raises(ApiError, match="BMP"):
        provider._ref_bytes(uri(picture("BMP")), 1)


@pytest.mark.parametrize("ctype", ["application/octet-stream", "image/jpeg", ""])
def test_explicit_url_is_checked_and_type_is_detected(provider, monkeypatch, ctype):
    raw = picture()
    response = Mock(content=raw, headers={"Content-Type": ctype})
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    get = Mock(return_value=response)
    monkeypatch.setattr("core.providers.chaomo.requests.get", get)
    assert provider._ref_bytes("https://example.test/wrong.jpg", 3) == (raw, "ref_3.png", "image/png")
    assert "Authorization" not in get.call_args.kwargs.get("headers", {})


def test_http_200_error_page_is_rejected_before_submission(provider, monkeypatch):
    response = Mock(content=b"<html>Access denied</html>", headers={"Content-Type": "image/png"})
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    monkeypatch.setattr("core.providers.chaomo.requests.get", Mock(return_value=response))
    with pytest.raises(ApiError, match="第 1 张"):
        provider.generate_image(ImageTask(prompt="x", refs=["https://example.test/ref.png"]), "out.png", log=lambda _: None)
    provider.session.request.assert_not_called()


def test_local_reference_bypasses_object_storage_but_video_still_uploads(provider, monkeypatch, tmp_path):
    path = tmp_path / "wrong.png"
    raw = picture("JPEG")
    path.write_bytes(raw)
    pj = SimpleNamespace(root=str(tmp_path))
    monkeypatch.setattr(produce.uploader, "configured", lambda _: True)
    upload = Mock(return_value="https://example.test/ref.png")
    monkeypatch.setattr(produce.uploader, "to_url", upload)
    resolve = produce.make_ref_resolver(pj, provider, {"upload": {"mode": "always"}}, "", 0, media="image")
    ref = resolve(path.name)
    upload.assert_not_called()
    assert provider._ref_bytes(ref, 1) == (raw, "ref_1.jpg", "image/jpeg")
    assert path.read_bytes() == raw
    video_resolve = produce.make_ref_resolver(pj, provider, {"upload": {"mode": "always"}}, "", 0, media="video")
    assert video_resolve(path.name) == "https://example.test/ref.png"
    upload.assert_called_once()


def test_order_and_all_four_files_are_preserved(provider):
    raws = [picture("JPEG"), picture("PNG"), picture("WEBP"), picture("PNG", "RGBA")]
    provider.generate_image(ImageTask(prompt="x", refs=[uri(raw) for raw in raws]), "out.png", log=lambda _: None)
    files = [v for k, v in provider.session.request.call_args.kwargs["files"] if k == "image[]"]
    assert [v[1] for v in files] == raws
    assert [v[0] for v in files] == ["ref_1.jpg", "ref_2.png", "ref_3.webp", "ref_4.png"]


@pytest.mark.parametrize("exc", [
    ApiError("local invalid reference", kind=TASK_FATAL, err_code="reference_invalid"),
    ApiError("任务失败：参考图无法解码：请确认上传的确实是图片文件", kind=TASK_FATAL, err_code="image_task_error"),
])
def test_invalid_reference_has_actionable_diagnosis_and_no_automatic_failover(exc):
    result = diagnose.build(exc)
    assert result["code"] == "REF_INVALID"
    assert not diagnose.should_failover(result)
    assert "任务明细" in result["where"]


@pytest.mark.parametrize("system", ["v61", "v34"])
@pytest.mark.parametrize("kind", ["asset", "scstate", "board", "region", "storyboard"])
def test_image_worker_uses_validated_bytes_in_each_workflow(provider, monkeypatch, tmp_path, system, kind):
    pj = Project(str(tmp_path))
    pj.init_dirs()
    pj.save_meta({"system": system})
    raw = picture("JPEG")
    (tmp_path / "ref.png").write_bytes(raw)
    write_text(pj.p("prompt.md"), "Image 1 = REF 背景场景；保留构图，补充暖色光线")
    monkeypatch.setattr(produce, "build_provider", lambda *a, **k: provider)
    monkeypatch.setattr(produce, "_ratio_warn", lambda *a, **k: None)
    monkeypatch.setattr(produce.uploader, "configured", lambda _: True)
    upload = Mock()
    monkeypatch.setattr(produce.uploader, "to_url", upload)
    provider.session.save_item.side_effect = lambda _, dest, **kw: Path(dest).write_bytes(raw)
    cfg = {"provider": "chaomo", "api_key": "test", "model": "gpt-image2-4K-Native", "upload": {"mode": "always"}}
    task = {"key": "P__ABC", "output": "result.png", "prompt_ref": "prompt.md",
            "reference_images": [{"image_n": 1, "asset_id": "REF", "file_ref": "ref.png"}]}
    result = produce.make_image_worker(pj, cfg, kind)(task, lambda _: None, lambda: False)
    assert result["output"] == "result.png"
    upload.assert_not_called()
    sent = [v for k, v in provider.session.request.call_args.kwargs["files"] if k == "image[]"]
    assert sent == [("ref_1.jpg", raw, "image/jpeg")]
    assert pj.registry(kind)[0]["status"] == "generated"
