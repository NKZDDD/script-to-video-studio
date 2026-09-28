"""Exercise Julun result transport, including interruption without regenerating."""
import importlib.util
from pathlib import Path
import re

import pytest
import requests

from core import apiutil
from core.providers import julun


def box(kind, payload):
    return (len(payload) + 8).to_bytes(4, "big") + kind + payload


VIDEO = box(b"ftyp", b"isom0000") + box(b"mdat", bytes(range(256)) * 1400) + box(b"moov", b"fixture")
URL = "https://storage.example/result.mp4"


class Response:
    def __init__(self, data, status=200, headers=None, broken=False):
        self.data, self.status_code, self.broken = data, status, broken
        self.headers = headers or {"Content-Length": str(len(data))}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(response=self)

    def iter_content(self, size):
        for start in range(0, len(self.data), size):
            yield self.data[start:start + size]
        if self.broken:
            raise requests.exceptions.ChunkedEncodingError("fixture disconnection")


@pytest.fixture(params=["builtin", "plugin"])
def module(request):
    if request.param == "builtin":
        return julun
    path = Path(__file__).resolve().parents[1] / "provider_patches/zz_julun_latest.py"
    spec = importlib.util.spec_from_file_location("julun_download_fixture", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def install_transport(monkeypatch, module, handler):
    calls = []

    class Transport:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get(self, url, **kw):
            calls.append((url, kw["headers"]))
            return handler(len(calls), kw["headers"])

    monkeypatch.setattr(module.requests, "Session", Transport)
    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    return calls


def partial():
    return Response(VIDEO[:131072], headers={"Content-Length": str(len(VIDEO))}, broken=True)


def segment(headers):
    lo, hi = map(int, re.fullmatch(r"bytes=(\d+)-(\d+)", headers["Range"]).groups())
    return Response(VIDEO[lo:hi + 1], 206, {"Content-Range": f"bytes {lo}-{hi}/{len(VIDEO)}"})


def test_normal_download_atomic(monkeypatch, module, tmp_path):
    dest = tmp_path / "result.mp4"
    dest.write_bytes(b"previous video")

    def handler(n, headers):
        assert dest.read_bytes() == b"previous video"
        assert "Range" not in headers
        return Response(VIDEO)

    calls = install_transport(monkeypatch, module, handler)
    module.JulunProvider().session.save_item(URL, dest)
    assert len(calls) == 1
    assert dest.read_bytes() == VIDEO
    assert list(tmp_path.glob(".julun-*")) == []


def test_resume_existing_task_downloads_only_missing_ranges(monkeypatch, module, tmp_path):
    calls = install_transport(monkeypatch, module, lambda n, h: partial() if n == 1 else segment(h))
    p = module.JulunProvider(api_key="fixture-secret")
    queried = []

    def query(method, path, **kw):
        assert method == "GET", "recovering must never submit another paid task"
        queried.append(path)
        return {"status": "completed", "progress": 95, "result_url": URL}

    monkeypatch.setattr(p.session, "request", query)
    dest = tmp_path / "result.mp4"
    result = p.resume({"task_id": "existing", "resume": {"custom": "julun"}}, dest, log=lambda _: None)
    assert result["resumed"] is True and queried == ["/v1/videos/existing"]
    assert dest.read_bytes() == VIDEO
    assert calls[1][1]["Range"].startswith("bytes=126976-")
    assert all("Authorization" not in h for _, h in calls)
    assert len(calls) > 2


def test_broken_range_retries_same_segment(monkeypatch, module, tmp_path):
    def handler(n, headers):
        if n == 1:
            return partial()
        r = segment(headers)
        if n == 2:
            r.data, r.broken = r.data[:20], True
        return r

    calls = install_transport(monkeypatch, module, handler)
    dest = tmp_path / "out.mp4"
    module.JulunProvider().session.save_item(URL, dest)
    assert calls[1][1]["Range"] == calls[2][1]["Range"]
    assert dest.read_bytes() == VIDEO


def test_ignored_range_restarts_instead_of_appending(monkeypatch, module, tmp_path):
    calls = install_transport(monkeypatch, module, lambda n, h: partial() if n == 1 else Response(VIDEO))
    dest = tmp_path / "out.mp4"
    module.JulunProvider().session.save_item(URL, dest)
    assert len(calls) == 2 and "Range" in calls[1][1]
    assert dest.read_bytes() == VIDEO


@pytest.mark.parametrize("fault", ["offset", "total", "overlap", "long", "short", "etag", "compressed"])
def test_corrupt_resume_never_publishes_or_removes_old_video(monkeypatch, module, tmp_path, fault):
    def handler(n, headers):
        if n == 1:
            r = partial()
            if fault == "etag":
                r.headers["ETag"] = '"old"'
            return r
        r = segment(headers)
        if fault == "offset":
            r.headers["Content-Range"] = f"bytes 0-65535/{len(VIDEO)}"
        elif fault == "total":
            r.headers["Content-Range"] = r.headers["Content-Range"].replace(str(len(VIDEO)), str(len(VIDEO) + 1))
        elif fault == "overlap":
            r.data = b"x" + r.data[1:]
        elif fault == "long":
            r.data += b"x"
        elif fault == "short":
            r.data = r.data[:-1]
        elif fault == "etag":
            assert headers["If-Range"] == '"old"'
            r.headers["ETag"] = '"new"'
        else:
            r.headers["Content-Encoding"] = "gzip"
        return r

    calls = install_transport(monkeypatch, module, handler)
    dest = tmp_path / "out.mp4"
    dest.write_bytes(b"previous video")
    with pytest.raises(apiutil.ApiError) as exc:
        module.JulunProvider().session.save_item(URL, dest)
    assert exc.value.kind == apiutil.TASK_FATAL
    assert exc.value.err_code == "result_download_failed"
    assert dest.read_bytes() == b"previous video"
    assert list(tmp_path.glob(".julun-*")) == []
    assert len(calls) <= 4


@pytest.mark.parametrize("url,auth", [
    ("https://julun.cc/v1/videos/id/content", True),
    ("https://julun.cc.evil.example/video.mp4", False),
    ("https://julun.cc:9999/video.mp4", False),
    ("http://julun.cc/video.mp4", False),
    (URL, False),
])
def test_credentials_only_sent_to_provider_origin(monkeypatch, module, tmp_path, url, auth):
    calls = install_transport(monkeypatch, module, lambda n, h: Response(b"", status=403))
    with pytest.raises(apiutil.ApiError):
        module.JulunProvider(api_key="fixture-secret").session.save_item(url, tmp_path / "out.mp4")
    assert len(calls) == 1
    assert ("Authorization" in calls[0][1]) is auth


@pytest.mark.parametrize("data", [b"<html>error</html>", VIDEO[:-5], b""], ids=["html", "truncated", "empty"])
def test_invalid_mp4_rejected(monkeypatch, module, tmp_path, data):
    install_transport(monkeypatch, module, lambda n, h: Response(data))
    dest = tmp_path / "out.mp4"
    with pytest.raises(apiutil.ApiError):
        module.JulunProvider().session.save_item(URL, dest)
    assert not dest.exists()


def test_cancel_during_range_stops_without_publishing(monkeypatch, module, tmp_path):
    stopped = False

    def handler(n, headers):
        nonlocal stopped
        if n == 1:
            return partial()
        stopped = True
        return segment(headers)

    calls = install_transport(monkeypatch, module, handler)
    dest = tmp_path / "out.mp4"
    with pytest.raises(apiutil.ApiError):
        module.JulunProvider().session.save_item(URL, dest, cancel=lambda: stopped)
    assert len(calls) == 2 and not dest.exists()
    assert list(tmp_path.glob(".julun-*")) == []


def test_images_still_use_host_downloader(monkeypatch, module):
    saved = []
    monkeypatch.setattr(apiutil.HttpSession, "save_item", lambda self, *a, **kw: saved.append(a))
    module.JulunProvider().session.save_item("https://cdn.example/image.png", "image.png")
    assert saved == [("https://cdn.example/image.png", "image.png")]
