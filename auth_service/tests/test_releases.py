from test_admin import env, login, post


def release():
    return dict(version="2026.10.20.0", flavor="all",
                url="https://auth.example.test/downloads/Studio.exe",
                sha256="a" * 64, bytes=123456, notes="更新测试")


def test_release_admin_auth_csrf_and_public_discovery(env):
    c, auth, _ = env
    assert c.get("/api/v1/updates/latest").json()["release"] is None
    assert c.get("/admin/api/releases").status_code == 401
    assert post(c, "releases", release(), csrf="no").status_code == 401
    login(c)
    assert post(c, "releases", release(), csrf="no").status_code == 403
    assert post(c, "releases", release()).status_code == 200
    assert all(c.get("/api/v1/updates/latest").json()["release"][k] == v for k,v in release().items())
    assert c.get("/api/v1/updates/latest?flavor=v34").json()["release"] is None
    assert c.get("/api/v1/updates/latest?flavor=other").status_code == 400


def test_release_accepts_r2_download_but_rejects_rollback(env):
    c, _, _ = env
    login(c)
    row = release()
    row["url"] = "https://pub-example.r2.dev/studio-updates/all/Studio.exe"
    assert post(c, "releases", row).status_code == 200
    row["url"] = "https://auth.example.test/downloads/Studio.exe"
    assert post(c, "releases", row).status_code == 200
    row["version"] = "2026.10.19.0"
    assert post(c, "releases", row).status_code == 400
    row["url"] = "file:///tmp/evil.exe"
    assert post(c, "releases", row).status_code == 422


def test_release_rejects_non_public_downloads_and_temporary_urls(env):
    c, _, _ = env
    login(c)
    for url in ("http://cdn.example/Studio.exe", "https://127.0.0.1/Studio.exe",
                "https://localhost/Studio.exe", "https://box.local/Studio.exe",
                "https://cdn.example:8080/Studio.exe", "https://cdn.example/../Studio.exe",
                "https://bucket.r2.cloudflarestorage.com/Studio.exe",
                "https://cdn.example/Studio.exe?signature=secret", "https://user:pass@cdn.example/Studio.exe"):
        row = release()
        row["url"] = url
        assert post(c, "releases", row).status_code == 422, url


def build(version="2026.10.20.1", digest="b", mandatory=True):
    row = release()
    row.update(version=version, build_sha256=digest*64, mandatory=mandatory)
    return row


def headers(version="2026.10.20.1", digest="b", flavor="all"):
    return {"X-Studio-Version": version, "X-Studio-Build": digest*64, "X-Studio-Flavor": flavor}


def test_enforced_policy_rejects_old_unknown_and_spoofed_flavors(env):
    from test_admin import PASSWORD
    c, auth, _ = env
    login(c)
    # A pre-existing session cannot fetch keys after the version is revoked.
    tokens = auth.login("customer", PASSWORD, "device-1234567890", "Studio", "test")
    assert post(c, "releases", build()).status_code == 200
    for h in ({}, headers("2020.1.1.0"), headers(digest="c"), headers(flavor="v34"),
              headers("2099.1.1.0"), {"X-Studio-Version": "anything"}):
        assert c.post("/api/v1/auth/login", headers=h, json={
            "username": "customer", "password": PASSWORD, "device_id": "device-1234567890"}).status_code == 426
        assert c.post("/api/v1/auth/verify", headers={**h, "Authorization": "Bearer "+tokens["access_token"]},
                      json={"device_id": "device-1234567890"}).status_code == 426
        assert c.post("/api/v1/auth/refresh", headers=h, json={
            "refresh_token": tokens["refresh_token"], "device_id": "device-1234567890"}).status_code == 426
    assert c.post("/api/v1/auth/verify", headers={**headers(), "Authorization": "Bearer "+tokens["access_token"]},
                  json={"device_id": "device-1234567890"}).status_code == 200
    data = c.get("/api/v1/updates/latest", headers=headers()).json()
    assert data["policy_protocol"] == 1 and data["client"]["allowed"]
    assert not c.get("/api/v1/updates/latest?flavor=v34", headers=headers(flavor="v34")).json()["client"]["allowed"]
    # Logout/usage don't require a new version: allow revocation and reconciliation.
    assert c.post("/api/v1/auth/logout", json={"refresh_token": tokens["refresh_token"],
                  "device_id": "device-1234567890"}).status_code == 200


def test_optional_release_keeps_minimum_and_known_builds(env):
    c, _, _ = env
    login(c)
    post(c, "releases", build())
    assert post(c, "releases", build("2026.10.21.0", "c", False)).status_code == 200
    data = c.get("/api/v1/updates/latest", headers=headers()).json()
    assert data["release"]["minimum_version"] == "2026.10.20.1"
    assert data["client"]["allowed"]  # optional does not revoke the previous valid build
    assert not c.get("/api/v1/updates/latest", headers=headers("2026.10.19.0")).json()["client"]["allowed"]
    assert post(c, "releases", build("2026.10.21.0", "d", False)).status_code == 400
    row = build("2026.10.22.0", "d", False)
    row["build_sha256"] = ""
    assert post(c, "releases", row).status_code == 400


def test_new_client_must_wait_for_build_registration(env):
    c, _, _ = env
    assert not c.get("/api/v1/updates/latest", headers=headers()).json()["client"]["allowed"]


def test_recommended_and_minimum_versions_are_independent_and_can_be_raised_later(env):
    c, _, _ = env
    login(c)
    for v, digest in (("2026.10.20.1", "b"), ("2026.10.20.2", "c")):
        assert post(c, "releases", build(v, digest, False)).status_code == 200
    row = build("2026.10.20.3", "d", False)
    row["minimum_version"] = "2026.10.20.2"
    assert post(c, "releases", row).status_code == 200
    assert not c.get("/api/v1/updates/latest", headers=headers()).json()["client"]["allowed"]
    data = c.get("/api/v1/updates/latest", headers=headers("2026.10.20.2", "c")).json()
    assert data["client"]["allowed"] and data["release"]["version"] == "2026.10.20.3"
    assert data["release"]["minimum_version"] == "2026.10.20.2"
    assert len(c.get("/admin/api/releases").json()["builds"]) == 3
    # Promote the minimum without building/uploading another installer.
    row["minimum_version"] = row["version"]
    assert post(c, "releases", row).status_code == 200
    assert not c.get("/api/v1/updates/latest", headers=headers("2026.10.20.2", "c")).json()["client"]["allowed"]
    assert c.get("/api/v1/updates/latest", headers=headers("2026.10.20.3", "d")).json()["client"]["allowed"]


def test_invalid_minimum_never_publishes_or_registers_a_build(env):
    c, _, _ = env
    login(c)
    assert post(c, "releases", build()).status_code == 200
    row = build("2026.10.20.3", "d", False)
    for minimum in ("2026.10.20.0", "2026.10.20.2", "2026.10.20.4"):
        row["minimum_version"] = minimum
        assert post(c, "releases", row).status_code == 400
    row["mandatory"] = True
    row["minimum_version"] = "2026.10.20.1"
    assert post(c, "releases", row).status_code == 400
    data = c.get("/admin/api/releases").json()
    assert len(data["builds"]) == 1 and data["releases"][0]["version"] == "2026.10.20.1"


def test_pause_requires_admin_csrf_and_strict_boolean(env):
    c, _, _ = env
    assert post(c, "releases/enforcement", {"enabled":False}, csrf="bad").status_code == 401
    login(c)
    assert post(c, "releases/enforcement", {"enabled":False}, csrf="bad").status_code == 403
    for value in ("false", 0, None):
        assert post(c, "releases/enforcement", {"enabled":value}).status_code == 422
    assert c.get("/admin/api/releases").json()["enforcement_enabled"] is True


def test_pause_allows_legacy_auth_but_preserves_credentials_account_checks_and_config_scope(env):
    from test_admin import PASSWORD
    c, auth, clock = env
    login(c)
    assert post(c, "users/customer/providers", dict(
        provider="julun", api_key="test-key-only", base_url="https://example.test")).status_code == 200
    assert post(c, "releases", build()).status_code == 200
    request = dict(username="customer", password=PASSWORD, device_id="legacy-device-123456")
    assert c.post("/api/v1/auth/login", json=request).status_code == 426
    assert post(c, "releases/enforcement", {"enabled":False}).status_code == 200
    data = c.get("/api/v1/updates/latest").json()
    assert data["client"]["allowed"] and data["release"] is None
    assert data["client"]["code"] == "version_check_paused" and data["policy_protocol"] == 1
    assert c.post("/api/v1/auth/login", json={**request,"password":"incorrect"}).status_code == 401
    assert c.post("/api/v1/auth/verify", json={"device_id":request["device_id"]}).status_code == 401
    tokens = c.post("/api/v1/auth/login", json=request).json()
    verified = c.post("/api/v1/auth/verify", json={"device_id":request["device_id"]},
        headers={"Authorization":"Bearer "+tokens["access_token"]})
    assert verified.status_code == 200
    assert verified.json()["provider_config"]["julun"]["api_key"] == "test-key-only"
    renewed = c.post("/api/v1/auth/refresh", json={"device_id":request["device_id"],
                                                 "refresh_token":tokens["refresh_token"]})
    assert renewed.status_code == 200
    auth.create_user("another", PASSWORD)
    other = c.post("/api/v1/auth/login", json={**request, "username":"another"}).json()
    assert c.post("/api/v1/auth/verify", json={"device_id":request["device_id"]},
        headers={"Authorization":"Bearer "+other["access_token"]}).json()["provider_config"] == {}
    auth.manage_user("customer", expires_at=clock[0])
    assert c.post("/api/v1/auth/login", json=request).status_code == 403
    auth.manage_user("another", enabled=False)
    assert c.post("/api/v1/auth/login", json={**request,"username":"another"}).status_code == 403


def test_pause_persists_across_restart_and_publication_then_resume_restores_catalog(env):
    c, auth, _ = env
    login(c)
    row = build()
    row["url"] = "https://pub-test.r2.dev/studio-updates/all/Studio.exe"
    assert post(c, "releases", row).status_code == 200
    assert post(c, "releases/enforcement", {"enabled":False}).status_code == 200
    auth.store.initialize()
    # A paused deployment may still prepare/publish the next release.
    row.update(version="2026.10.20.2", build_sha256="c"*64)
    assert post(c, "releases", row).status_code == 200
    for flavor in ("all", "v34", "v61"):
        public = c.get("/api/v1/updates/latest?flavor="+flavor, headers=headers(flavor=flavor)).json()
        assert public["release"] is None and public["client"]["allowed"]
    admin = c.get("/admin/api/releases").json()
    assert admin["enforcement_enabled"] is False
    assert admin["releases"][0]["version"] == row["version"] and len(admin["builds"]) == 2
    assert post(c, "releases/enforcement", {"enabled":True}).status_code == 200
    public = c.get("/api/v1/updates/latest").json()
    assert not public["client"]["allowed"] and public["release"]["url"] == row["url"]
    assert not c.get("/api/v1/updates/latest", headers=headers()).json()["client"]["allowed"]
    assert c.get("/api/v1/updates/latest", headers=headers("2026.10.20.2","c")).json()["client"]["allowed"]
    with auth.store.connect() as db:
        assert [r["target"] for r in db.execute(
            "SELECT target FROM admin_audit WHERE action='client_version_check' ORDER BY id")] == ["studio:paused","studio:enabled"]


def test_existing_database_migrates_with_enforcement_enabled(env):
    c, auth, _ = env
    login(c)
    post(c, "releases", build())
    with auth.store.connect(write=True) as db:
        db.execute("DROP TABLE client_version_policy")
    auth.store.initialize()
    assert c.get("/admin/api/releases").json()["enforcement_enabled"] is True
    assert not c.get("/api/v1/updates/latest").json()["client"]["allowed"]


def test_cli_pause_resume_and_status_preserve_database(env, monkeypatch, capsys):
    from studio_auth import manage
    c, auth, _ = env
    login(c)
    post(c, "releases", build())
    monkeypatch.setenv("STUDIO_AUTH_DB", str(auth.store.path))
    for action, enabled in (("pause",False), ("status",False), ("resume",True)):
        monkeypatch.setattr("sys.argv", ["manage", "client-version-check", action])
        manage.main()
        assert c.get("/admin/api/releases").json()["enforcement_enabled"] is enabled
    assert c.get("/admin/api/releases").json()["releases"][0]["version"] == build()["version"]
    assert "数据库" in capsys.readouterr().out


def test_cli_pause_refuses_wrong_database_path(tmp_path, monkeypatch):
    import pytest
    from studio_auth import manage
    missing = tmp_path/"missing.sqlite3"
    monkeypatch.setenv("STUDIO_AUTH_DB", str(missing))
    monkeypatch.setattr("sys.argv", ["manage", "client-version-check", "pause"])
    with pytest.raises(ValueError, match="数据库不存在"):
        manage.main()
    assert not missing.exists()


def registration(version="2026.10.20.3", digest="d", flavor="all"):
    return dict(version=version, build_sha256=digest*64, flavor=flavor)


def test_build_registration_requires_admin_csrf_and_valid_identity(env):
    c, _, _ = env
    assert post(c, "releases/builds", registration(), csrf="bad").status_code == 401
    login(c)
    assert post(c, "releases/builds", registration(), csrf="bad").status_code == 403
    for changes in ({"version":"latest"}, {"flavor":"unknown"}, {"build_sha256":""},
                    {"build_sha256":"source"}, {"minimum_version":"0.0.0.0"}, {"mandatory":True}):
        assert post(c, "releases/builds", {**registration(), **changes}).status_code == 422
    assert c.get("/admin/api/releases").json()["builds"] == []


def test_test_registration_preserves_public_release_floor_and_is_persistent(env):
    c, auth, _ = env
    login(c)
    assert post(c, "releases", build()).status_code == 200
    public_before = c.get("/api/v1/updates/latest", headers=headers()).json()
    for _ in range(2):  # Re-importing the same build is idempotent.
        assert post(c, "releases/builds", registration()).status_code == 200
    auth.store.initialize()
    assert c.get("/api/v1/updates/latest", headers=headers()).json() == public_before
    test = c.get("/api/v1/updates/latest", headers=headers("2026.10.20.3", "d")).json()
    assert test["client"]["allowed"] and test["release"] == public_before["release"]
    assert len(c.get("/admin/api/releases").json()["builds"]) == 2
    for h in (headers("2026.10.20.3", "e"), headers("2026.10.20.4", "d"),
              headers("2026.10.20.3", "d", "v34")):
        assert not c.get("/api/v1/updates/latest", headers=h).json()["client"]["allowed"]
    with auth.store.connect() as db:
        assert db.execute("SELECT count(*) FROM admin_audit WHERE action='register_client_build' "
                          "AND target='all:2026.10.20.3'").fetchone()[0] == 2


def test_test_build_can_authenticate_before_any_publication_without_affecting_legacy(env):
    from test_admin import PASSWORD
    c, auth, _ = env
    login(c)
    legacy = c.get("/api/v1/updates/latest").json()
    for flavor in ("all", "v34", "v61"):
        assert post(c, "releases/builds", registration(flavor=flavor)).status_code == 200
        h = headers("2026.10.20.3", "d", flavor)
        data = c.get("/api/v1/updates/latest?flavor="+flavor, headers=h).json()
        assert data["release"] is None and data["client"]["allowed"]
        request = dict(username="customer", password=PASSWORD, device_id="test-device-123456")
        assert c.post("/api/v1/auth/login", headers=h, json={**request,"password":"incorrect"}).status_code == 401
        response = c.post("/api/v1/auth/login", headers=h, json=request)
        assert response.status_code == 200
        tokens = response.json()
        assert c.post("/api/v1/auth/verify", headers={**h,"Authorization":"Bearer "+tokens["access_token"]},
                      json={"device_id":request["device_id"]}).status_code == 200
        assert c.post("/api/v1/auth/refresh", headers=h, json={"refresh_token":tokens["refresh_token"],
                      "device_id":request["device_id"]}).status_code == 200
    assert c.get("/api/v1/updates/latest").json() == legacy
    assert c.get("/admin/api/releases").json()["releases"] == []
    auth.manage_user("customer", enabled=False)
    assert c.post("/api/v1/auth/login", headers=h, json=request).status_code == 403


def test_registration_does_not_bypass_floor_or_change_paused_state(env):
    c, _, _ = env
    login(c)
    assert post(c, "releases", build()).status_code == 200
    assert post(c, "releases/builds", registration("2026.10.19.0")).status_code == 200
    old = c.get("/api/v1/updates/latest", headers=headers("2026.10.19.0", "d")).json()
    assert not old["client"]["allowed"] and old["client"]["code"] == "update_required"
    assert post(c, "releases/enforcement", {"enabled":False}).status_code == 200
    before = c.get("/api/v1/updates/latest").json()
    assert post(c, "releases/builds", registration()).status_code == 200
    assert c.get("/api/v1/updates/latest").json() == before
    assert c.get("/admin/api/releases").json()["enforcement_enabled"] is False
    assert post(c, "releases/enforcement", {"enabled":True}).status_code == 200
    test = c.get("/api/v1/updates/latest", headers=headers("2026.10.20.3", "d")).json()
    assert test["client"]["allowed"] and test["release"]["version"] == "2026.10.20.1"


def test_registered_test_can_be_published_but_never_replaced_by_another_build(env):
    c, _, _ = env
    login(c)
    assert post(c, "releases", build()).status_code == 200
    assert post(c, "releases/builds", registration()).status_code == 200
    before = c.get("/admin/api/releases").json()
    for path, row in (("releases/builds", registration(digest="e")),
                      ("releases", build("2026.10.20.3", "e", False)),
                      ("releases/builds", registration("2026.10.20.1", "e"))):
        response = post(c, path, row)
        assert response.status_code == 400 and response.json()["error"]["code"] == "build_conflict"
        assert c.get("/admin/api/releases").json() == before
    assert post(c, "releases", build("2026.10.20.3", "d", False)).status_code == 200
    data = c.get("/api/v1/updates/latest", headers=headers()).json()
    assert data["client"]["allowed"] and data["release"]["version"] == "2026.10.20.3"
    assert data["release"]["minimum_version"] == "2026.10.20.1"
