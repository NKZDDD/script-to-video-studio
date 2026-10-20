from test_admin import env, login, post


def release():
    return dict(version="2026.10.20.0", flavor="all",
                url="https://auth.example.test/downloads/Studio.exe",
                sha256="a" * 64, bytes=123456, notes="更新测试")


def test_release_admin_auth_csrf_and_public_discovery(env):
    c, auth, _ = env
    assert c.get("/api/v1/updates/latest").json() == {"release": None}
    assert c.get("/admin/api/releases").status_code == 401
    assert post(c, "releases", release(), csrf="no").status_code == 401
    login(c)
    assert post(c, "releases", release(), csrf="no").status_code == 403
    assert post(c, "releases", release()).status_code == 200
    assert c.get("/api/v1/updates/latest").json()["release"] == release()
    assert c.get("/api/v1/updates/latest?flavor=v34").json() == {"release": None}
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
