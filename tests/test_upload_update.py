import hashlib
import json
from types import SimpleNamespace

import pytest

from tools import upload_update as tool


RAW = b"MZtest-installer"


def manifest(tmp_path):
    row = dict(version="2026.10.21.0", flavor="all", bytes=len(RAW),
               sha256=hashlib.sha256(RAW).hexdigest(), build_sha256="a"*64,
               url="https://login.respect.chat/downloads/Studio.exe", notes="test",
               accidentally_added_secret="must-not-export")
    p = tmp_path / "release.json"
    p.write_text(json.dumps(row), encoding="utf-8")
    (tmp_path / "Respect-Studio-Setup-2026.10.21.0.exe").write_bytes(RAW)
    return p, row


def setup_upload(monkeypatch, existing=None, public=RAW, status=200):
    calls = []
    class Missing(Exception):
        response = {"Error": {"Code": "404"}}
    def head(**kw):
        if existing is None: raise Missing()
        return existing
    def put(**kw):
        calls.append({**kw, "Body": kw["Body"].read()})
    monkeypatch.setattr(tool.uploader, "_client", lambda _: SimpleNamespace(head_object=head, put_object=put))
    class Response:
        status_code = status
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def iter_content(self, n): yield public
    def get(url, **kw):
        assert kw["allow_redirects"] is False
        assert "Authorization" not in kw["headers"]
        return Response()
    monkeypatch.setattr(tool.requests, "get", get)
    return calls


CFG = dict(access_key="test", secret_key="test", bucket="test",
           public_base_url="https://pub-test.r2.dev")


def test_upload_verifies_public_file_and_exports_only_release_fields(tmp_path, monkeypatch):
    p, row = manifest(tmp_path)
    calls = setup_upload(monkeypatch)
    output, result = tool.upload_release(p, CFG)
    assert calls[0]["IfNoneMatch"] == "*" and calls[0]["Body"] == RAW
    assert result["url"] == "https://pub-test.r2.dev/studio-updates/all/Respect-Studio-Setup-2026.10.21.0.exe"
    assert json.loads(output.read_text(encoding="utf-8")) == result
    assert "accidentally_added_secret" not in result and "secret_key" not in result
    assert result["mandatory"] is False
    assert json.loads(p.read_text()) == row


@pytest.mark.parametrize("public,status", [(b"wrong", 200), (RAW+b"extra", 200), (RAW, 302)])
def test_failed_public_verification_does_not_export_manifest(tmp_path, monkeypatch, public, status):
    p, _ = manifest(tmp_path)
    setup_upload(monkeypatch, public=public, status=status)
    with pytest.raises(ValueError):
        tool.upload_release(p, CFG)
    assert not (tmp_path/"release.r2.json").exists()


def test_resume_same_upload_without_overwrite_and_reject_different_content(tmp_path, monkeypatch):
    p, row = manifest(tmp_path)
    calls = setup_upload(monkeypatch, existing={"ContentLength":len(RAW),"Metadata":{"sha256":row["sha256"]}})
    tool.upload_release(p, CFG)
    assert not calls
    calls = setup_upload(monkeypatch, existing={"ContentLength":len(RAW),"Metadata":{"sha256":"b"*64}})
    with pytest.raises(ValueError, match="同版本"):
        tool.upload_release(p, CFG)
    assert not calls


def test_modified_local_installer_never_uploads(tmp_path, monkeypatch):
    p, _ = manifest(tmp_path)
    (tmp_path/"Respect-Studio-Setup-2026.10.21.0.exe").write_bytes(b"bad")
    calls = setup_upload(monkeypatch)
    with pytest.raises(ValueError, match="大小"):
        tool.upload_release(p, CFG)
    assert not calls
