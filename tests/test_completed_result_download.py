"""completed 之后卡住：只重取结果，不重新提交生成。"""
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import pytest
import requests

from core import apiutil, diagnose
from core.apiutil import ApiError, HttpSession, TASK_FATAL
from core.providers.base import ImageTask
from core.providers.chaomo import ChaomoProvider
from core.executor import Job, run_batch


PNG = bytes.fromhex("89504e470d0a1a0a") + bytes(2048) + bytes.fromhex("49454e44ae426082")


class Response:
    status_code = 200
    headers = {"Content-Length": str(len(PNG))}

    def __init__(self, broken=False):
        self.broken = broken
        self.closed = False

    def raise_for_status(self):
        pass

    def iter_content(self, chunk_size):
        if self.broken:
            yield PNG[:100]
            raise requests.ConnectionError("Read timed out")
        yield PNG

    def close(self):
        self.closed = True


def test_download_uses_short_idle_timeout_instead_of_generation_timeout():
    seen = []
    def get(*args, **kwargs):
        seen.append(kwargs["timeout"])
        return Response()
    with tempfile.TemporaryDirectory() as folder, patch.object(apiutil.requests, "get", get):
        HttpSession("key", "https://api", timeout=900).save_item(
            "https://cdn/result.png", str(Path(folder, "out.png")))
    assert seen == [(15, 30)]


def test_stream_timeout_retries_same_result_and_closes_both_responses():
    broken, good = Response(broken=True), Response()
    logs = []
    with tempfile.TemporaryDirectory() as folder:
        dest = Path(folder, "out.png")
        with patch.object(apiutil.requests, "get", side_effect=[broken, good]) as get, \
                patch.object(apiutil.time, "sleep"):
            HttpSession("key", "https://api").save_item(
                "https://cdn/result.png", str(dest), log=logs.append)
        assert dest.read_bytes() == PNG
        assert not Path(str(dest) + ".part").exists()
        assert [c.args[0] for c in get.call_args_list] == ["https://cdn/result.png"] * 2
        assert broken.closed and good.closed
        assert any("重新下载" in line for line in logs)


def test_permanent_download_timeout_does_not_retry_generation_or_fail_over():
    with tempfile.TemporaryDirectory() as folder:
        dest = Path(folder, "out.png")
        with patch.object(apiutil.requests, "get", side_effect=requests.ReadTimeout("stalled")) as get, \
                patch.object(apiutil.time, "sleep"):
            with pytest.raises(ApiError) as caught:
                HttpSession("key", "https://api").save_item("https://cdn/result.png", str(dest))
        assert get.call_count == 3
        assert caught.value.kind == TASK_FATAL
        diag = diagnose.build(caught.value)
        assert diag["code"] == "RESULT_DOWNLOAD"
        assert not diagnose.should_failover(diag)
        assert any("https://cdn/result.png" in line for line in diag["fix"])
        assert not dest.exists()


def test_batch_does_not_resubmit_a_completed_task_when_all_downloads_timeout():
    provider = ChaomoProvider(api_key="test")
    calls = []
    def request(method, path, **kwargs):
        calls.append(method)
        return ({"id": "START_R01"} if method == "POST" else
                {"status": "completed", "data": [{"url": "https://cdn/result.png"}]})
    provider.session.request = request
    with tempfile.TemporaryDirectory() as folder:
        job = Job("storyboard", 1, 1, project_root=folder, provider="chaomo")
        def worker(task, log, cancel):
            return provider.generate_image(ImageTask(prompt="test"),
                                           str(Path(folder, "out.png")), log=log)
        with patch.object(apiutil.requests, "get", side_effect=requests.ReadTimeout("stalled")) as get, \
                patch.object(apiutil.time, "sleep"):
            run_batch(job, [{"key": "START_R01"}], worker, key_of=lambda t: t["key"], max_retry=2)
        assert calls == ["POST", "GET"]
        assert get.call_count == 3
        assert job.items["START_R01"]["state"] == "failed"
        assert job.items["START_R01"]["diag"]["code"] == "RESULT_DOWNLOAD"


def test_http_error_closes_auth_fallback_responses_and_preserves_other_partial():
    class Forbidden(Response):
        status_code = 403

        def raise_for_status(self):
            raise requests.HTTPError("403 Forbidden", response=self)

    first, second = Forbidden(), Forbidden()
    with tempfile.TemporaryDirectory() as folder:
        dest = Path(folder, "out.png")
        part = Path(str(dest) + ".part")
        part.write_bytes(b"another existing download")
        with patch.object(apiutil.requests, "get", side_effect=[first, second]) as get:
            with pytest.raises(ApiError) as caught:
                HttpSession("key", "https://api").save_item("https://cdn/result.png", str(dest))
        assert get.call_count == 2
        assert first.closed and second.closed
        assert part.read_bytes() == b"another existing download"
        assert diagnose.build(caught.value)["code"] == "RESULT_DOWNLOAD"


@pytest.mark.parametrize("stall", ["headers", "body"])
def test_chaomo_completed_then_stalled_http_download_recovers_without_new_post(stall):
    """真实 HTTP 停在响应头/正文：只提交一次，第二次 GET 取回同一张图。"""
    release = threading.Event()
    downloads = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            downloads.append(self.path)
            if len(downloads) == 1:
                if stall == "body":
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(PNG)))
                    self.end_headers()
                    self.wfile.write(PNG[:100])
                    self.wfile.flush()
                release.wait(3)
                return
            self.send_response(200)
            self.send_header("Content-Length", str(len(PNG)))
            self.end_headers()
            self.wfile.write(PNG)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/result.png"
        provider = ChaomoProvider(api_key="test", proxy="direct", timeout=0.1)
        calls, logs = [], []
        def request(method, path, **kwargs):
            calls.append((method, path))
            if method == "POST":
                return {"id": "START_R01"}
            return {"status": "completed", "data": [{"url": url}]}
        provider.session.request = request
        with tempfile.TemporaryDirectory() as folder, patch.object(apiutil.time, "sleep"):
            dest = Path(folder, "out.png")
            result = provider.generate_image(ImageTask(prompt="test"), str(dest), log=logs.append)
            assert dest.read_bytes() == PNG
        assert result["task_id"] == "START_R01"
        assert calls == [("POST", "/v1/images/generations"), ("GET", "/v1/images/START_R01")]
        assert downloads == ["/result.png", "/result.png"]
        completed = next(i for i, line in enumerate(logs) if "状态: completed" in line)
        assert any("下载" in line for line in logs[completed + 1:])
        assert logs[-1] == "超模 图片结果已就绪"
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
