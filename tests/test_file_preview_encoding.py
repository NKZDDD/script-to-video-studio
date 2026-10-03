"""Project text previews declare the encoding without rewriting source files."""
from pathlib import Path
import threading
from urllib.parse import urlencode
from urllib.request import urlopen

import pytest

from server import app
from test_agent_workspace import pj  # noqa: F401


@pytest.mark.parametrize("filename,registered_type,expected_type", [
    ("production-contract.md", "text/markdown", "text/plain"),
    ("production-contract.md", None, "text/plain"),
    ("AGENT_TASK.MD", "application/octet-stream", "text/plain"),
    ("说明.markdown", "text/markdown", "text/plain"),
    ("提示词.txt", "text/plain", "text/plain"),
    ("tasks.json", "application/json", "application/json"),
])
def test_text_preview_declares_utf8(pj, monkeypatch, filename, registered_type, expected_type):
    raw = '{"说明":"中文生产契约，人物身份；路径保持不变。<b>原文</b>"}\r\n'.encode("utf-8")
    rel = "00_项目说明/" + filename
    target = Path(pj.p(rel))
    target.write_bytes(raw)
    monkeypatch.setattr(app, "load_config", lambda: {"projects_dir": str(Path(pj.root).parent)})
    monkeypatch.setattr(app.mimetypes, "guess_type", lambda path: (registered_type, None))
    server = app.ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/file?" + urlencode({"root": pj.root, "rel": rel})
        with urlopen(url) as response:
            assert response.status == 200
            assert response.headers.get_content_charset() == "utf-8"
            assert response.headers.get_content_type() == expected_type
            assert int(response.headers["Content-Length"]) == len(raw)
            body = response.read()
            assert body == raw
            assert "中文生产契约" in body.decode(response.headers.get_content_charset())
        assert target.read_bytes() == raw
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)
