import hashlib
import io
from pathlib import Path
from types import SimpleNamespace

import pytest

from core import updates


def release(**kw):
    raw = b"MZtest-installer"
    # Keep the update fixture newer than the build under test as releases advance.
    parts = updates.VERSION.split('.')
    parts[-1] = str(int(parts[-1]) + 1)
    return dict(version='.'.join(parts), flavor="all",
                url=updates.AUTH_URL + "/downloads/Studio.exe",
                bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest(), notes="", **kw)


@pytest.mark.parametrize("field,value", [
    ("url", "https://127.0.0.1/downloads/Studio.exe"),
    ("url", "https://localhost/downloads/Studio.exe"),
    ("url", "https://bucket.r2.cloudflarestorage.com/Studio.exe"),
    ("url", "https://cdn.example:8080/Studio.exe"),
    ("url", "https://cdn.example/../Studio.exe"),
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


@pytest.mark.parametrize("url", [
    "https://pub-example.r2.dev/studio-updates/all/Studio.exe",
    "https://downloads.respect.chat/studio-updates/all/Studio.exe",
    updates.AUTH_URL + "/downloads/Studio.exe",
])
def test_authenticated_catalog_can_use_public_https_download_hosts(url):
    row = release()
    row["url"] = url
    assert updates.validate_release(row)["url"] == url


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
    m.release["url"] = "https://pub-example.r2.dev/studio-updates/all/Studio.exe"
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


def test_cancelled_jobs_must_drain_before_install_or_close():
    from core.executor import JobManager
    jobs = JobManager()
    first = jobs.create("image", 1, 1)
    first.cancel()
    assert jobs.active_count() == 0
    assert updates.active_jobs(jobs) == 1
    # History pruning must not lose a cancelled worker which is still running.
    for _ in range(90):
        finished = jobs.create("image", 1, 1)
        finished.status, finished.finished_at = "done", 123
    assert first.id in jobs.jobs
    m = updates.UpdateManager()
    with pytest.raises(ValueError, match="生产任务"):
        m.install(jobs)
    first.finished_at = 123
    assert updates.active_jobs(jobs) == 0


def test_no_downgrade_or_wrong_cached_installer_after_check(monkeypatch):
    import json
    row = release()
    row["version"] = "2020.1.1.0"
    class Response:
        status_code = 200
        raw = io.BytesIO(json.dumps({"release": row, "policy_protocol": 1,
                                     "client": {"allowed": True, "code": "ok"}}).encode())
        def __enter__(self): return self
        def __exit__(self, *a): pass
    monkeypatch.setattr(updates.requests, "get", lambda *a, **k: Response())
    m = updates.UpdateManager()
    m.installer = Path("old.exe")
    m.check()
    assert m.phase == "current" and m.installer is None
    with pytest.raises(ValueError):
        m.download()


def response_for(monkeypatch, data):
    import json
    class Response:
        status_code = 200
        def __init__(self): self.raw = io.BytesIO(json.dumps(data).encode())
        def __enter__(self): return self
        def __exit__(self, *a): pass
    monkeypatch.setattr(updates.requests, "get", lambda *a, **k: Response())


@pytest.mark.parametrize("payload", [
    {"release": None},
    {"policy_protocol": 1, "release": None, "client": {}},
    {"policy_protocol": 1, "release": None, "client": {"allowed": "true"}},
])
def test_missing_or_malformed_policy_never_unlocks(monkeypatch, payload):
    from core.studio_login import LoginRequired
    response_for(monkeypatch, payload)
    m = updates.UpdateManager()
    assert not m.status()["allowed"]
    with pytest.raises(LoginRequired):
        m.require_allowed()
    assert not m.allowed and m.phase == "error"


def test_mandatory_version_download_available_without_auth(monkeypatch):
    from core.studio_login import LoginRequired
    response_for(monkeypatch, {"release": release(), "policy_protocol": 1,
        "client": {"allowed": False, "code": "update_required", "message": "must update"}})
    m = updates.UpdateManager()
    with pytest.raises(LoginRequired) as exc:
        m.require_allowed()
    assert exc.value.status == 426
    assert m.phase == "available" and m.release and not m.allowed


def test_latest_is_also_checked_while_downloading(monkeypatch):
    response_for(monkeypatch, {"release": release(), "policy_protocol": 1,
        "client": {"allowed": False, "code": "update_required", "message": "must update"}})
    m = updates.UpdateManager()
    m.allowed = True
    m.phase, m.release = "downloading", release()
    m.check()
    assert not m.allowed and m.phase == "downloading"


def test_heartbeat_preserves_verified_download(monkeypatch):
    row = release()
    response_for(monkeypatch, {"release": row, "policy_protocol": 1, "client": {"allowed": True}})
    m = updates.UpdateManager()
    m.phase, m.release, m.installer = "ready", row, Path("checked.exe")
    m.check()
    assert m.phase == "ready" and m.installer == Path("checked.exe")


def test_auth_request_reuses_identity_from_the_same_online_check(monkeypatch):
    response_for(monkeypatch, {"release": release(), "policy_protocol": 1, "client": {"allowed": True}})
    calls = []
    def identity():
        calls.append(True)
        return {"version": updates.VERSION, "flavor": "all", "build_sha256": "f"*64}
    monkeypatch.setattr(updates, "identity", identity)
    m = updates.UpdateManager()
    assert m.require_allowed()["X-Studio-Build"] == "f"*64
    assert len(calls) == 1


def test_corrupt_build_can_repair_same_version_but_not_unlock(monkeypatch):
    row = release()
    row["version"] = updates.VERSION
    response_for(monkeypatch, {"release": row, "policy_protocol": 1, "client": {"allowed": True}})
    monkeypatch.setattr(updates, "identity", lambda: (_ for _ in ()).throw(ValueError("broken")))
    m = updates.UpdateManager()
    assert m.check()["allowed"] is False and m.phase == "available"
    assert m.code == "client_damaged"
    started = []
    monkeypatch.setattr(updates.threading, "Thread", lambda **kw: SimpleNamespace(start=lambda: started.append(kw)))
    m.download()
    assert started


def test_server_can_pause_version_gate_without_changing_the_client(monkeypatch):
    # The service hides recommendations so older clients do not reject an R2 URL.
    response_for(monkeypatch, {"release":None, "policy_protocol":1, "enforcement_enabled":False,
        "client":{"allowed":True, "code":"version_check_paused", "message":"transition"}})
    m = updates.UpdateManager()
    assert m.require_allowed()["X-Studio-Version"] == updates.VERSION
    assert m.allowed and m.release is None and m.phase == "current"


@pytest.mark.parametrize("public_version", [None, "2026.10.20.1"])
def test_registered_test_build_can_run_without_being_recommended(monkeypatch, public_version):
    row = release() if public_version else None
    if row:
        row["version"] = public_version
    response_for(monkeypatch, {"release":row, "policy_protocol":1,
        "enforcement_enabled":True, "client":{"allowed":True, "code":"ok"}})
    info = {"version":updates.VERSION, "flavor":"all", "build_sha256":"f"*64}
    monkeypatch.setattr(updates, "identity", lambda: info)
    m = updates.UpdateManager()
    assert m.require_allowed()["X-Studio-Build"] == info["build_sha256"]
    assert m.allowed and m.phase == "current"
    with pytest.raises(ValueError):
        m.download()


def test_desktop_downloads_once_then_allows_explicit_retry(monkeypatch):
    row = release()
    response_for(monkeypatch, {"release": row, "policy_protocol": 1,
                              "client": {"allowed": False, "message": "must update"}})
    started = []
    monkeypatch.setattr(updates.threading, "Thread", lambda **kw: SimpleNamespace(start=lambda: started.append(kw)))
    m = updates.UpdateManager()
    m.install_callback = lambda p: None
    assert m.check()["phase"] == "downloading"
    m.check()
    assert len(started) == 1
    m.phase, m.message, m.download_error = "error", "HTTP 404", "HTTP 404"
    assert m.check()["message"] == "HTTP 404"
    assert m.phase == "error" and len(started) == 1 and not m.allowed
    m.download()
    assert len(started) == 2 and m.download_error == ""
    m.phase, m.message, m.download_error = "error", "HTTP 404", "HTTP 404"
    row["url"] = "https://cdn.example/new-installer.exe"
    response_for(monkeypatch, {"release": row, "policy_protocol": 1, "client": {"allowed": True}})
    assert m.check()["phase"] == "downloading" and len(started) == 3


def test_browser_checks_without_auto_download(monkeypatch):
    response_for(monkeypatch, {"release": release(), "policy_protocol": 1, "client": {"allowed": True}})
    m = updates.UpdateManager()
    assert m.check()["phase"] == "available"
    assert m.auto_download_key is None


def test_launch_failure_keeps_ready_state_and_does_not_block_tasks(tmp_path, monkeypatch):
    monkeypatch.setattr(updates, "INSTALLING", False)
    m = updates.UpdateManager()
    m.release, m.phase = release(), "ready"
    m.installer = tmp_path / "installer.exe"
    m.installer.write_bytes(b"MZtest-installer")
    def fail(path):
        raise ValueError("launch failed")
    m.install_callback = fail
    with pytest.raises(ValueError, match="launch failed"):
        m.install(SimpleNamespace(active_count=lambda: 0))
    assert not updates.INSTALLING and m.phase == "ready"


def test_silent_handoff_preserves_directory_and_data(tmp_path, monkeypatch):
    import os
    import subprocess
    from core import desktop
    folder = tmp_path / "Studio 中文 & spaces"
    folder.mkdir()
    (folder / "Respect-Studio.exe").touch()
    data = tmp_path / "project data"
    monkeypatch.setattr(desktop.paths, "PROGRAM_DIR", str(folder))
    monkeypatch.setattr(desktop.paths, "data_dir", lambda: str(data))
    monkeypatch.setattr(desktop.sys, "frozen", True, raising=False)
    monkeypatch.setattr(subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    calls = []
    monkeypatch.setattr(desktop.subprocess, "Popen", lambda *args, **kwargs: calls.append((args, kwargs)))
    installer = tmp_path / "下载文件 setup.exe"
    desktop.launch_update(installer)
    args, kwargs = calls[0]
    assert args[0] == subprocess.list2cmdline([str(installer), "/S", f"/STUDIOUPDATE={os.getpid()}"]) + " /D=" + str(folder.resolve())
    assert kwargs["env"]["STV_DATA_DIR"] == str(data)
    assert kwargs["env"]["PYINSTALLER_RESET_ENVIRONMENT"] == "1"
    assert kwargs["creationflags"] == 0x08000000 and not kwargs.get("shell")
    monkeypatch.setattr(desktop.sys, "frozen", False)
    with pytest.raises(ValueError, match="已安装"):
        desktop.launch_update(installer)


def test_launch_update_error_is_actionable(tmp_path, monkeypatch):
    from core import desktop
    (tmp_path / "Respect-Studio.exe").touch()
    monkeypatch.setattr(desktop.paths, "PROGRAM_DIR", str(tmp_path))
    monkeypatch.setattr(desktop.sys, "frozen", True, raising=False)
    monkeypatch.setattr(desktop.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    def fail(*args, **kwargs):
        raise OSError("denied")
    monkeypatch.setattr(desktop.subprocess, "Popen", fail)
    with pytest.raises(ValueError, match="尚未退出"):
        desktop.launch_update(tmp_path / "setup.exe")
