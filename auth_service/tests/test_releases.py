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


def test_release_rejects_remote_download_and_rollback(env):
    c, _, _ = env
    login(c)
    row = release()
    row["url"] = "https://other.example/downloads/Studio.exe"
    assert post(c, "releases", row).status_code == 400
    row["url"] = "https://auth.example.test/downloads/Studio.exe"
    assert post(c, "releases", row).status_code == 200
    row["version"] = "2026.10.19.0"
    assert post(c, "releases", row).status_code == 400
    row["url"] = "file:///tmp/evil.exe"
    assert post(c, "releases", row).status_code == 422


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
