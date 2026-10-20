import hashlib
import io
from pathlib import Path
from types import SimpleNamespace

import pytest

from core import updates


def release(**kw):
    raw = b"MZtest-installer"
    return dict(version="2026.10.21.0", flavor="all",
                url=updates.AUTH_URL + "/downloads/Studio.exe",
                bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest(), notes="", **kw)


@pytest.mark.parametrize("field,value", [
    ("url", "https://other.example/downloads/Studio.exe"),
    ("url", "http://login.respect.chat/downloads/Studio.exe"),
    ("url", "https://login.respect.chat/downloads/Studio.exe?token=secret"),
    ("sha256", "no"), ("bytes", 0), ("bytes", True),
    ("flavor", "v34"), ("version", "next"),
])
def test_reject_untrusted_release(field, value):
    row = release()
    row[field] = value
    with pytest.raises(ValueError):
        updates.validate_release(row)


def test_download_verifies_size_hash_and_never_follows_redirects(tmp_path, monkeypatch):
    class Response:
        status_code = 200
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def iter_content(self, n): yield b"MZtest-installer"
    calls = []
    def request(url, **kw):
        calls.append(kw)
        return Response()
    monkeypatch.setattr(updates.requests, "get", request)
    monkeypatch.setattr(updates.paths, "data_dir", lambda: str(tmp_path))
    m = updates.UpdateManager()
    m.release = release()
    m._download(m.release)
    assert m.phase == "ready" and m.installer.read_bytes() == b"MZtest-installer"
    assert calls[0]["allow_redirects"] is False
    assert "Authorization" not in calls[0]["headers"]
    m.release["sha256"] = "0" * 64
    m._download(m.release)
    assert m.phase == "error" and "校验" in m.message
    assert not list(tmp_path.rglob("*.part"))


def test_install_preserves_running_jobs_and_rechecks_file(tmp_path, monkeypatch):
    monkeypatch.setattr(updates, "INSTALLING", False)
    m = updates.UpdateManager()
    m.release = release()
    m.installer = tmp_path / "installer.exe"
    m.installer.write_bytes(b"MZtest-installer")
    m.phase = "ready"
    called = []
    m.install_callback = called.append
    with pytest.raises(ValueError, match="生产任务"):
        m.install(SimpleNamespace(active_count=lambda: 1))
    assert not called and not updates.INSTALLING
    m.installer.write_bytes(b"bad")
    with pytest.raises(ValueError, match="改变"):
        m.install(SimpleNamespace(active_count=lambda: 0))
    assert not called
    m.installer.write_bytes(b"MZtest-installer")
    m.install(SimpleNamespace(active_count=lambda: 0))
    assert called == [m.installer] and updates.INSTALLING


def test_no_downgrade_or_wrong_cached_installer_after_check(monkeypatch):
    import json
    row = release()
    row["version"] = "2020.1.1.0"
    class Response:
        status_code = 200
        raw = io.BytesIO(json.dumps({"release": row}).encode())
        def __enter__(self): return self
        def __exit__(self, *a): pass
    monkeypatch.setattr(updates.requests, "get", lambda *a, **k: Response())
    m = updates.UpdateManager()
    m.installer = Path("old.exe")
    m.check()
    assert m.phase == "current" and m.installer is None
    with pytest.raises(ValueError):
        m.download()
