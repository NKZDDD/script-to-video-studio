import hashlib
import json

import pytest

from core import client_build as B


def test_manifest_verifies_file_content_and_reports_actual_fingerprint(tmp_path, monkeypatch):
    files = {}
    for name in ("Respect-Studio.exe", "Respect-Studio-CLI.exe", "_internal/web/agent.js"):
        file = tmp_path / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(name.encode())
        files[name] = hashlib.sha256(file.read_bytes()).hexdigest()
    digest = B.fingerprint(files)
    (tmp_path / B.MANIFEST).write_text(json.dumps(
        {"version": B.VERSION, "files": files, "build_sha256": digest}), encoding="utf-8")
    monkeypatch.setattr(B.paths, "FROZEN", True)
    monkeypatch.setattr(B.paths, "PROGRAM_DIR", str(tmp_path))
    assert B.headers()["X-Studio-Build"] == digest
    (tmp_path / "_internal/web/agent.js").write_text("tampered")
    with pytest.raises(ValueError, match="校验失败"):
        B.headers()


@pytest.mark.parametrize("path", ["../outside", "/absolute", "C:/outside", r"..\outside"])
def test_manifest_rejects_outside_paths(tmp_path, monkeypatch, path):
    files = {"Respect-Studio.exe": "a"*64, "Respect-Studio-CLI.exe": "b"*64, path: "c"*64}
    (tmp_path / B.MANIFEST).write_text(json.dumps(
        {"version": B.VERSION, "files": files, "build_sha256": B.fingerprint(files)}), encoding="utf-8")
    monkeypatch.setattr(B.paths, "FROZEN", True)
    monkeypatch.setattr(B.paths, "PROGRAM_DIR", str(tmp_path))
    with pytest.raises(ValueError):
        B.identity()


def test_real_login_transport_blocks_before_credentials_leave_machine(monkeypatch):
    from core import studio_login, updates
    calls = []
    def denied():
        raise studio_login.LoginRequired("update_required", "update first", 426)
    monkeypatch.setattr(updates.MANAGER, "require_allowed", denied)
    manager = studio_login.LoginManager()
    monkeypatch.setattr(manager.opener, "open", lambda *a, **k: calls.append(a))
    for action in ("login", "verify", "refresh"):
        with pytest.raises(studio_login.LoginRequired) as exc:
            manager._post(action, {"password": "test-only"})
        assert exc.value.status == 426
    assert not calls
