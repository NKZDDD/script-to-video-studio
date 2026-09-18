"""XMD 协议回归：不使用真实凭据，不提交付费任务。"""
import base64
import hashlib
import hmac
import importlib.util
import io
import json
import random
from unittest.mock import Mock

import pytest
import requests
from PIL import Image

from core import diagnose, providers
from core.apiutil import ApiError, BATCH_FATAL, RETRYABLE, TASK_FATAL
from core.providers import xmd
from core.providers.base import VideoTask


@pytest.fixture
def provider():
    return xmd.XmdProvider(api_key="api_key=test-key;api_secret=test-secret", proxy="direct")


def response(data, status=200, content=b""):
    r = Mock()
    r.status_code = status
    r.json.return_value = data
    r.content = content
    r.__enter__ = Mock(return_value=r)
    r.__exit__ = Mock(return_value=False)
    return r


def image_bytes(fmt="PNG", size=(24, 36), mode="RGB"):
    b = io.BytesIO()
    Image.new(mode, size, (40, 70, 150, 80) if mode == "RGBA" else (40, 70, 150)).save(b, fmt)
    return b.getvalue()


def test_registry_and_external_plugin_load():
    assert providers.REGISTRY["xmd"] is xmd.XmdProvider
    assert "xmd" in providers._BUILTIN_ORDER
    spec = importlib.util.spec_from_file_location("stv_plugin_xmd", xmd.__file__)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    classes = providers._classes_in(mod)
    assert len(classes) == 1
    assert providers._check(classes[0]) == []
    cap = classes[0]().capabilities()["video"]
    assert cap["durations"] == [30]
    assert cap["max_refs"] == 10
    assert cap["model_options"][xmd.MODEL]["durations"] == [30]
    assert classes[0]().needs_bytes()


@pytest.mark.parametrize("raw", ["api_key=k;api_secret=s==", "api_key=k\napi_secret=s==",
                                  '{"api_key":"k","api_secret":"s=="}'])
def test_credentials_formats(raw):
    assert xmd.parse_credentials(raw) == {"api_key": "k", "api_secret": "s=="}


def test_signature_matches_exact_transmitted_unicode_body(provider, monkeypatch):
    monkeypatch.setattr(xmd.time, "time", lambda: 1234567890)
    monkeypatch.setattr(xmd.secrets, "token_hex", lambda _: "nonce-1")
    resp = response({"code": 0})
    request = Mock(return_value=resp)
    monkeypatch.setattr(xmd.requests, "request", request)
    provider._request("POST", "/v1/videos?query=ignored", obj={"prompt": "猫，暖阳", "duration": 30})
    args, kw = request.call_args
    assert args == ("POST", provider.default_base_url + "/v1/videos?query=ignored")
    assert kw["data"] == '{"prompt":"猫，暖阳","duration":30}'.encode()
    expected = hmac.new(b"test-secret", b"POST/v1/videos" + kw["data"] + b"nonce-11234567890",
                        hashlib.sha256).hexdigest()
    assert kw["headers"]["X-Sign"] == expected
    assert kw["headers"]["Content-Type"] == "application/json"
    assert "json" not in kw
    assert kw["allow_redirects"] is False
    assert kw["proxies"] == {"http": "", "https": ""}
    resp.__exit__.assert_called_once()


def test_upload_signs_empty_body_without_overriding_boundary(provider, monkeypatch):
    monkeypatch.setattr(xmd.time, "time", lambda: 100)
    monkeypatch.setattr(xmd.secrets, "token_hex", lambda _: "n")
    request = Mock(return_value=response({"code": 0, "url": "https://cdn/ref.png"}))
    monkeypatch.setattr(xmd.requests, "request", request)
    file = ("a.png", b"png-bytes", "image/png")
    provider._request("POST", "/v1/images", files={"file": file})
    kw = request.call_args.kwargs
    assert kw["headers"]["X-Sign"] == hmac.new(
        b"test-secret", b"POST/v1/imagesn100", hashlib.sha256).hexdigest()
    assert "Content-Type" not in kw["headers"]
    assert kw["data"] is None
    assert kw["files"] == {"file": file}


def test_retry_keeps_idempotency_body_and_refreshes_nonce(provider, monkeypatch):
    monkeypatch.setattr(xmd, "_wait", Mock())
    request = Mock(side_effect=[requests.ReadTimeout(), response({"code": 0, "job_id": "vj-1"})])
    monkeypatch.setattr(xmd.requests, "request", request)
    provider._request("POST", "/v1/videos", obj={"client_job_id": "my-order", "prompt": "x"})
    a, b = [c.kwargs for c in request.call_args_list]
    assert a["data"] == b["data"]
    assert json.loads(a["data"])["client_job_id"] == "my-order"
    assert a["headers"]["X-Nonce"] != b["headers"]["X-Nonce"]


def test_capacity_waits_full_retry_after(provider, monkeypatch):
    wait = Mock()
    monkeypatch.setattr(xmd, "_wait", wait)
    monkeypatch.setattr(xmd.requests, "request", Mock(side_effect=[
        response({"code": 50311, "message": "busy", "retry_after": 75}, 503),
        response({"code": 0})]))
    provider._request("GET", "/v1/me")
    wait.assert_called_once_with(75, None)


def test_selftest_only_reads_quota_and_no_credential_in_transfer(provider, monkeypatch):
    request = Mock(return_value=response({"code": 0, "quota_remaining": 12}))
    monkeypatch.setattr(xmd.requests, "request", request)
    assert provider.selftest() == {"ok": True, "msg": "XMD 鉴权通过，剩余次数：12"}
    assert request.call_args.args == ("GET", provider.default_base_url + "/v1/me")
    assert provider.session.api_key == ""
    assert not any("test-" in str(v) for v in provider.session._download_headers().values())


def test_model_refresh_never_sends_packed_credentials(provider, monkeypatch):
    from core import model_catalog
    request = Mock()
    monkeypatch.setattr(model_catalog.requests, "Session", request)
    result = model_catalog.fetch("xmd", {"api_key": "api_key=test-key;api_secret=test-secret"})
    assert not result["ok"] and "固定模型" in result["msg"]
    request.assert_not_called()
    # 老 exe 刷模型会覆盖 session.api_key 后取 _headers，也必须拦住。
    provider.session.api_key = "api_key=test-key;api_secret=test-secret"
    with pytest.raises(ApiError, match="固定模型"):
        provider.session._headers()
    assert not any("test-" in str(v) for v in provider.session._download_headers().values())


@pytest.mark.parametrize("raw", ["", "just-a-key", "api_key=key", "api_secret=secret", "{bad json"])
def test_missing_credentials_fail_before_network(raw, monkeypatch):
    request = Mock()
    monkeypatch.setattr(xmd.requests, "request", request)
    with pytest.raises(ApiError) as caught:
        xmd.XmdProvider(raw).generate_video(VideoTask("x", duration=30), "out.mp4")
    assert caught.value.kind == BATCH_FATAL
    request.assert_not_called()


@pytest.mark.parametrize("fmt,mode", [("PNG", "RGBA"), ("JPEG", "RGB"), ("WEBP", "RGBA")])
def test_compliant_image_is_byte_identical(fmt, mode):
    raw = image_bytes(fmt, mode=mode)
    name, got, mime = xmd.prepare_image(raw, 2)
    assert got is raw
    assert name.startswith("ref_2.")
    assert mime in ("image/png", "image/jpeg", "image/webp")


def test_exact_byte_limit_is_accepted_unchanged(monkeypatch):
    raw = image_bytes()
    monkeypatch.setattr(xmd, "MAX_IMAGE_BYTES", len(raw))
    assert xmd.prepare_image(raw, 1)[1] is raw


def test_real_over_10mb_png_upload_is_bounded_and_original_untouched(provider, tmp_path, monkeypatch):
    raw = io.BytesIO()
    Image.frombytes("RGBA", (1700, 1700), random.Random(42).randbytes(1700 * 1700 * 4)).save(raw, "PNG")
    raw = raw.getvalue()
    assert len(raw) > 10_000_000
    file = tmp_path / "large.png"
    file.write_bytes(raw)
    request = Mock(return_value={"code": 0, "url": "https://cdn/ref.png"})
    monkeypatch.setattr(provider, "_request", request)
    logs = []
    assert provider._upload(str(file), 1, log=logs.append, cancel=None) == "https://cdn/ref.png"
    upload = request.call_args.kwargs["files"]["file"]
    assert len(upload[1]) <= 10_000_000
    with Image.open(io.BytesIO(upload[1])) as im:
        assert im.mode == "RGBA"
        assert im.getextrema()[3][0] < 255
    assert hashlib.sha256(file.read_bytes()).digest() == hashlib.sha256(raw).digest()
    assert any("原图未修改" in line for line in logs)


@pytest.mark.parametrize("fmt", ["JPEG", "WEBP"])
def test_lossy_formats_compress_to_budget_without_changing_format(fmt, monkeypatch):
    raw = io.BytesIO()
    Image.frombytes("RGB", (200, 200), random.Random(42).randbytes(200 * 200 * 3)).save(raw, fmt, quality=100)
    monkeypatch.setattr(xmd, "MAX_IMAGE_BYTES", 8000)
    _, data, _ = xmd.prepare_image(raw.getvalue(), 1)
    assert len(data) <= 8000
    with Image.open(io.BytesIO(data)) as im:
        assert im.format == fmt


@pytest.mark.parametrize("raw", [b"", b"<html>failed</html>", image_bytes("BMP")])
def test_invalid_image_rejected_without_upload(provider, monkeypatch, raw):
    request = Mock()
    monkeypatch.setattr(provider, "_request", request)
    ref = "data:image/png;base64," + base64.b64encode(raw).decode()
    with pytest.raises(ApiError):
        provider._upload(ref, 1, log=lambda _: None, cancel=None)
    request.assert_not_called()


def test_remote_ref_also_goes_through_size_check_and_never_gets_auth(provider, monkeypatch):
    raw = image_bytes()
    resp = response({}, content=raw)
    get = Mock(return_value=resp)
    monkeypatch.setattr(xmd.requests, "get", get)
    prepare = Mock(return_value=("small.png", b"small-upload-copy", "image/png"))
    monkeypatch.setattr(xmd, "prepare_image", prepare)
    request = Mock(return_value={"code": 0, "url": "https://our-upload/ref.png"})
    monkeypatch.setattr(provider, "_request", request)
    provider._upload("https://other-cdn/oversize.png", 1, log=print, cancel=None)
    assert prepare.call_args.args[0] == raw
    assert request.call_args.kwargs["files"]["file"][1] == b"small-upload-copy"
    assert "headers" not in get.call_args.kwargs
    resp.__exit__.assert_called_once()


@pytest.mark.parametrize("changes", [
    {"prompt": ""}, {"prompt": "字" * 3001}, {"duration": 15}, {"model": "wrong-model"},
    {"ratio": "2:3"}, {"refs": ["r"] * 11}, {"extra": {"gallery_ids": [True]}},
    {"refs": ["r"] * 10, "extra": {"gallery_ids": [21]}},
    {"extra": {"shield": "true"}}, {"extra": {"video_refs": ["v.mp4"]}},
])
def test_invalid_task_fails_before_upload_or_paid_submission(provider, monkeypatch, changes):
    request = Mock()
    upload = Mock()
    monkeypatch.setattr(provider, "_request", request)
    monkeypatch.setattr(provider, "_upload", upload)
    args = {"prompt": "cat", "duration": 30, **changes}
    with pytest.raises(ApiError) as caught:
        provider.generate_video(VideoTask(**args), "out.mp4")
    assert caught.value.kind == TASK_FATAL
    request.assert_not_called()
    upload.assert_not_called()


def test_complete_flow_uses_job_id_status_success_and_immediate_download(provider, monkeypatch):
    request = Mock(side_effect=[{"code": 0, "job_id": "vj_123", "status": "queued"},
                                {"code": 0, "status": "running"},
                                {"code": 0, "status": "success", "video_url": "https://cdn/v.mp4"}])
    monkeypatch.setattr(provider, "_request", request)
    upload = Mock(side_effect=["https://cdn/1.png", "https://cdn/2.png"])
    monkeypatch.setattr(provider, "_upload", upload)
    wait = Mock()
    monkeypatch.setattr(xmd, "_wait", wait)
    save = Mock()
    monkeypatch.setattr(provider.session, "save_item", save)
    logs = []
    out = provider.generate_video(VideoTask("猫", duration=30, refs=["r1", "r2"],
                                           extra={"shield": True, "gallery_ids": [21]}),
                                  "out.mp4", log=logs.append, poll_interval=1)
    body = request.call_args_list[0].kwargs["obj"]
    assert body["images"] == ["https://cdn/1.png", "https://cdn/2.png"]
    assert [c.args[0] for c in upload.call_args_list] == ["r1", "r2"]
    assert body["gallery_ids"] == [21]
    assert body["model"] == "seedance_2.5"
    assert body["duration"] == 30
    assert body["shield"] is True
    assert body["client_job_id"].startswith("stv-")
    assert all(c.args == ("GET", "/v1/videos/vj_123") for c in request.call_args_list[1:])
    assert wait.call_args.args[0] >= 10
    save.assert_called_once_with("https://cdn/v.mp4", "out.mp4", log=logs.append)
    assert out["task_id"] == "vj_123"
    assert "XMD 生成已完成，开始下载视频" in logs


def test_text_only_request_omits_images_and_optional_switches(provider, monkeypatch):
    request = Mock(side_effect=[{"code": 0, "job_id": "vj_1"},
                                {"code": 0, "status": "success", "video_url": "https://cdn/v.mp4"}])
    monkeypatch.setattr(provider, "_request", request)
    monkeypatch.setattr(provider.session, "save_item", Mock())
    provider.generate_video(VideoTask("猫", duration=30, ratio=""), "out.mp4")
    body = request.call_args_list[0].kwargs["obj"]
    assert not {"images", "gallery_ids", "ratio", "server", "shield"} & body.keys()


@pytest.mark.parametrize("code,status,kind", [(40101, 200, BATCH_FATAL), (40201, 402, BATCH_FATAL),
                                               (40000, 400, TASK_FATAL), (50311, 503, RETRYABLE),
                                               (40004, 400, TASK_FATAL)])
def test_business_error_classification(provider, monkeypatch, code, status, kind):
    monkeypatch.setattr(xmd.requests, "request", Mock(return_value=response(
        {"code": code, "message": "error test-secret test-key"}, status)))
    with pytest.raises(ApiError) as caught:
        provider._request("GET", "/v1/me", retries=1)
    assert caught.value.kind == kind
    assert "test-secret" not in str(caught.value)
    assert "test-key" not in str(caught.value)


def test_task_failure_40003_is_not_misdiagnosed_as_expired_url(provider, monkeypatch):
    request = Mock(side_effect=[{"code": 0, "job_id": "vj_1"},
                                {"code": 0, "status": "failed", "error": {"code": 40003}}])
    monkeypatch.setattr(provider, "_request", request)
    save = Mock()
    monkeypatch.setattr(provider.session, "save_item", save)
    with pytest.raises(ApiError, match="参考图不可用") as caught:
        provider.generate_video(VideoTask("cat", duration=30), "out.mp4")
    assert "已过期" not in str(caught.value)
    save.assert_not_called()
    assert sum(c.args[0] == "POST" for c in request.call_args_list) == 1


def test_success_without_url_does_not_poll_forever_or_resubmit(provider, monkeypatch):
    request = Mock(side_effect=[{"code": 0, "job_id": "vj_1"}, {"code": 0, "status": "success"}])
    monkeypatch.setattr(provider, "_request", request)
    with pytest.raises(ApiError) as caught:
        provider.generate_video(VideoTask("cat", duration=30), "out.mp4")
    assert caught.value.kind == TASK_FATAL
    assert not diagnose.should_failover(diagnose.build(caught.value))
    assert request.call_count == 2


def test_download_failure_never_resubmits_or_switches_provider(provider, monkeypatch):
    request = Mock(side_effect=[{"code": 0, "job_id": "vj_1"},
                                {"code": 0, "status": "success", "video_url": "https://cdn/v.mp4"}])
    monkeypatch.setattr(provider, "_request", request)
    error = ApiError("download failed", kind=TASK_FATAL, err_code="result_download_failed")
    monkeypatch.setattr(provider.session, "save_item", Mock(side_effect=error))
    with pytest.raises(ApiError) as caught:
        provider.generate_video(VideoTask("cat", duration=30), "out.mp4")
    assert caught.value is error
    assert not diagnose.should_failover(diagnose.build(caught.value))
    assert request.call_count == 2


def test_cancel_before_submit_makes_no_requests(provider, monkeypatch):
    request = Mock()
    monkeypatch.setattr(provider, "_request", request)
    with pytest.raises(ApiError, match="取消"):
        provider.generate_video(VideoTask("cat", duration=30), "out.mp4", cancel=lambda: True)
    request.assert_not_called()


def test_transient_poll_error_keeps_original_job(provider, monkeypatch):
    request = Mock(side_effect=[{"code": 0, "job_id": "vj_1"},
                                ApiError("busy", kind=RETRYABLE, retry_after=75),
                                {"code": 0, "status": "success", "video_url": "https://cdn/v.mp4"}])
    monkeypatch.setattr(provider, "_request", request)
    monkeypatch.setattr(provider.session, "save_item", Mock())
    wait = Mock()
    monkeypatch.setattr(xmd, "_wait", wait)
    provider.generate_video(VideoTask("cat", duration=30), "out.mp4")
    assert sum(c.args[0] == "POST" for c in request.call_args_list) == 1
    assert wait.call_args.args[0] == 75


def test_missing_submit_receipt_stops_automatic_same_provider_retry(provider, monkeypatch):
    request = Mock(side_effect=ApiError("network", kind=RETRYABLE))
    monkeypatch.setattr(provider, "_request", request)
    with pytest.raises(ApiError, match="订单号 stv-") as caught:
        provider.generate_video(VideoTask("cat", duration=30), "out.mp4")
    assert caught.value.kind == TASK_FATAL
