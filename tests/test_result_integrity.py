"""Same-output downloads must never mix bytes or publish undecodable images."""
import base64
import io
import threading
import zlib
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest
import requests
from PIL import Image

from core import apiutil, diagnose
from core.apiutil import ApiError, HttpSession, TASK_FATAL
from core.executor import Job, run_batch
from core.providers.base import ImageTask
from core.providers.chaomo import ChaomoProvider
from core.providers.hvtald import HvtaldProvider
from image_fixtures import png_bytes


class Response:
    status_code = 200

    def __init__(self, body):
        self.body = body
        self.headers = {"Content-Length": str(len(body))}
        self.closed = False

    def iter_content(self, chunk_size):
        yield self.body

    def raise_for_status(self):
        pass

    def close(self):
        self.closed = True


def corrupt_png(kind="crc"):
    body = bytearray(png_bytes())
    tag = body.index(b"IDAT")
    length = int.from_bytes(body[tag - 4:tag], "big")
    if kind == "crc":
        body[tag + 4 + length // 2] ^= 1
    else:
        # Invalid zlib header, but correct PNG CRC: verify alone cannot catch it.
        body[tag + 4] = 0
        crc = zlib.crc32(body[tag:tag + 4 + length]).to_bytes(4, "big")
        body[tag + 4 + length:tag + 8 + length] = crc
    return bytes(body)


@pytest.mark.parametrize("provider", ["shared", "hvtald"])
@pytest.mark.parametrize("shorter_first", [False, True])
@pytest.mark.parametrize("first_fails", [False, True])
def test_overlapping_downloads_keep_whole_files_and_own_cleanup(
        tmp_path, monkeypatch, provider, shorter_first, first_fails):
    a = png_bytes(1, (384, 256) if shorter_first else (768, 512))
    b = png_bytes(2, (768, 512))
    if provider == "hvtald":
        a, b = [bytes(4) + b"ftypisom" + raw for raw in (a, b)]
        download = HvtaldProvider(api_key="user=U;password=P")._download
    else:
        download = HttpSession("unused", "https://api.test")._save_once
    dest = tmp_path / ("out.mp4" if provider == "hvtald" else "out.png")
    paused, release = threading.Event(), threading.Event()
    responses = []

    class PausingResponse(Response):
        def iter_content(self, chunk_size):
            yield self.body[:131072]
            paused.set()
            assert release.wait(10), "second download did not finish"
            if first_fails:
                raise requests.ReadTimeout("first stream broke")
            yield self.body[131072:]

    def get(url, **kwargs):
        response = PausingResponse(a) if url.endswith("/a") else Response(b)
        responses.append(response)
        return response

    monkeypatch.setattr(apiutil.requests, "get", get)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(download, "https://cdn.test/a", str(dest))
        try:
            assert paused.wait(10)
            assert not dest.exists(), "incomplete file was published"
            pool.submit(download, "https://cdn.test/b", str(dest)).result(timeout=10)
            assert dest.read_bytes() == b
        finally:
            release.set()
        if first_fails:
            with pytest.raises(requests.ReadTimeout):
                first.result(timeout=10)
        else:
            first.result(timeout=10)
    assert dest.read_bytes() == (b if first_fails else a)
    assert list(tmp_path.iterdir()) == [dest], "leftover temporary files"
    assert all(r.closed for r in responses)


@pytest.mark.parametrize("kind", ["crc", "pixels"])
@pytest.mark.parametrize("transport", ["http", "data_uri", "base64"])
def test_corrupt_result_never_overwrites_existing_image_or_triggers_failover(
        tmp_path, monkeypatch, kind, transport):
    body = corrupt_png(kind)
    dest = tmp_path / "out.png"
    original = png_bytes(2)
    dest.write_bytes(original)
    responses = []

    def get(*args, **kwargs):
        response = Response(body)
        responses.append(response)
        return response

    monkeypatch.setattr(apiutil.requests, "get", get)
    monkeypatch.setattr(apiutil.time, "sleep", lambda _: None)
    item = base64.b64encode(body).decode()
    if transport == "data_uri":
        item = "data:image/png;base64," + item
    elif transport == "http":
        item = "https://cdn.test/result.png"
    with pytest.raises(ApiError) as caught:
        HttpSession("unused", "https://api.test").save_item(item, str(dest))
    assert caught.value.kind == TASK_FATAL
    diag = diagnose.build(caught.value)
    assert diag["code"] == "RESULT_DOWNLOAD"
    assert not diagnose.should_failover(diag)
    assert dest.read_bytes() == original
    assert list(tmp_path.iterdir()) == [dest]
    assert len(responses) == (3 if transport == "http" else 0)
    assert all(r.closed for r in responses)
    if transport != "http":
        assert item not in str(caught.value.extra_fix)


def test_corrupt_download_retries_same_url_then_saves_valid_bytes(tmp_path):
    bad, good = Response(corrupt_png()), Response(png_bytes())
    dest = tmp_path / "out.png"
    url = "https://cdn.test/result.png"
    with patch.object(apiutil.requests, "get", side_effect=[bad, good]) as get, \
            patch.object(apiutil.time, "sleep"):
        HttpSession("unused", "https://api.test").save_item(url, str(dest))
    assert [call.args[0] for call in get.call_args_list] == [url, url]
    assert dest.read_bytes() == good.body
    assert bad.closed and good.closed
    assert list(tmp_path.iterdir()) == [dest]


def test_failed_integrity_does_not_resubmit_completed_generation(tmp_path):
    provider = ChaomoProvider(api_key="unused")
    calls = []

    def request(method, path, **kwargs):
        calls.append(method)
        return ({"id": "task-test"} if method == "POST" else
                {"status": "completed", "data": [{"url": "https://cdn.test/out.png"}]})

    provider.session.request = request
    job = Job("scstate", 1, 1, project_root=str(tmp_path), provider="chaomo")

    def worker(task, log, cancel):
        return provider.generate_image(ImageTask(prompt="test"),
                                       str(tmp_path / "out.png"), log=log)

    with patch.object(apiutil.requests, "get", side_effect=lambda *a, **k: Response(corrupt_png())) as get, \
            patch.object(apiutil.time, "sleep"):
        run_batch(job, [{"key": "test"}], worker, key_of=lambda t: t["key"], max_retry=2)
    assert calls == ["POST", "GET"]
    assert get.call_count == 3
    assert job.items["test"]["state"] == "failed"
    assert job.items["test"]["diag"]["code"] == "RESULT_DOWNLOAD"
    assert not (tmp_path / "out.png").exists()
    assert not list(tmp_path.glob("*.part"))


@pytest.mark.parametrize("fmt,ext", [("PNG", ".png"), ("JPEG", ".jpg"),
                                     ("WEBP", ".webp"), ("GIF", ".gif"), ("BMP", ".bmp")])
def test_valid_image_formats_are_preserved_byte_for_byte(tmp_path, fmt, ext):
    with Image.open(io.BytesIO(png_bytes())) as im, io.BytesIO() as out:
        im.save(out, fmt)
        body = out.getvalue()
    dest = tmp_path / ("out" + ext)
    HttpSession("unused", "https://api.test").save_item(
        base64.b64encode(body).decode(), str(dest))
    assert dest.read_bytes() == body
    assert list(tmp_path.iterdir()) == [dest]


def test_hvtald_http_error_closes_response_and_preserves_existing_file(tmp_path, monkeypatch):
    response = Response(b"")
    response.status_code = 403
    monkeypatch.setattr(apiutil.requests, "get", lambda *a, **k: response)
    dest = tmp_path / "out.mp4"
    dest.write_bytes(b"existing file")
    with pytest.raises(ApiError, match="403"):
        HvtaldProvider(api_key="user=U;password=P")._download("https://cdn.test/out", str(dest))
    assert response.closed
    assert dest.read_bytes() == b"existing file"
    assert list(tmp_path.iterdir()) == [dest]
