import json

from test_admin import env, login, post, PASSWORD
from test_releases import build, headers


DEVICE = "statistics-device-123456"


def stats(c, query=""):
    response = c.get("/admin/api/releases/statistics"+query)
    assert response.status_code == 200, response.text
    return response.json()


def client_login(c, h=None, **overrides):
    return c.post("/api/v1/auth/login", headers=h or {}, json={
        "username": "customer", "password": PASSWORD, "device_id": DEVICE,
        "device_name": "Workstation", **overrides})


def seed(auth, uid, device, version="2026.10.20.1", flavor="all", build_hash="b"*64, age=0):
    with auth.store.connect(write=True) as db:
        db.execute("INSERT INTO client_observations VALUES(?,?,?,?,?,?,?,?)",
                   (uid, device, flavor, version, build_hash, "Test PC", auth.now()-age, auth.now()-age))


def test_only_authenticated_reports_count_and_admin_only(env):
    c, auth, _ = env
    assert c.get("/admin/api/releases/statistics").status_code == 401
    login(c)
    assert post(c, "releases", build()).status_code == 200
    assert client_login(c, headers(), password="wrong-password").status_code == 401
    assert client_login(c, headers(digest="c")).status_code == 426
    for _ in range(3):
        c.get("/api/v1/updates/latest", headers=headers())
    assert stats(c)["summary"]["accounts"] == 0
    result = client_login(c, headers()).json()
    assert "access_token" in result
    for _ in range(3):
        assert c.post("/api/v1/auth/verify", headers={**headers(), "Authorization": "Bearer "+result["access_token"]},
                      json={"device_id": DEVICE}).status_code == 200
    assert c.post("/api/v1/auth/verify", headers={**headers(), "Authorization": "Bearer "+result["access_token"]},
                  json={"device_id": "wrong-device-123456"}).status_code == 401
    data = stats(c)
    assert data["summary"]["accounts"] == data["summary"]["installations"] == 1
    assert data["details"][0]["state"] == "current"
    text = json.dumps(data)
    for secret in (DEVICE, result["access_token"], result["refresh_token"], PASSWORD, "b"*64):
        assert secret not in text
    for query in ("?days=3", "?flavor=bogus", "?page=0", "?segment=bogus"):
        assert c.get("/admin/api/releases/statistics"+query).status_code == 422


def test_upgrade_refresh_and_relogin_replace_same_installation(env):
    c, auth, clock = env
    login(c)
    post(c, "releases", build())
    tokens = client_login(c, headers()).json()
    post(c, "releases", build("2026.10.20.2", "c", False))
    assert stats(c)["summary"]["old"] == 1
    clock[0] += 60
    refreshed = c.post("/api/v1/auth/refresh", headers=headers("2026.10.20.2", "c"), json={
        "refresh_token": tokens["refresh_token"], "device_id": DEVICE})
    assert refreshed.status_code == 200
    data = stats(c)
    assert data["summary"]["old"] == 0
    assert data["summary"]["installations"] == 1
    assert data["details"][0]["version"] == "2026.10.20.2"
    assert data["details"][0]["last_seen_at"] == clock[0]
    assert client_login(c, headers("2026.10.20.2", "c")).status_code == 200
    assert stats(c)["summary"]["installations"] == 1
    # Removing expired/revoked sessions does not erase the last version observation.
    with auth.store.connect(write=True) as db:
        db.execute("DELETE FROM used_refresh")
        db.execute("DELETE FROM sessions")
    assert stats(c)["summary"]["accounts"] == 1


def test_unique_accounts_windows_flavors_and_account_eligibility(env):
    c, auth, _ = env
    login(c)
    recent = auth.create_user("recent", PASSWORD)
    absent = auth.create_user("absent", PASSWORD)
    disabled = auth.create_user("disabled", PASSWORD)
    expired = auth.create_user("expired", PASSWORD, expires_at=auth.now())
    auth.manage_user("disabled", enabled=False)
    post(c, "releases", build())
    post(c, "releases", build("2026.10.20.2", "c"))
    seed(auth, recent, "old-pc")
    seed(auth, recent, "new-pc", "2026.10.20.2", build_hash="c"*64)
    seed(auth, recent, "other-flavor", flavor="v34")
    seed(auth, absent, "offline", age=31*86400)
    seed(auth, disabled, "off")
    seed(auth, expired, "expired")
    data = stats(c)
    assert data["summary"] == dict(accounts=1, old=1, required=1, attention=1, installations=3,
                                   inactive_accounts=1, eligible_accounts=3, unobserved_accounts=1, excluded_accounts=2)
    assert len(data["distribution"]) == 3
    assert sum(r["accounts"] for r in data["distribution"]) == 3  # same user in multiple versions
    assert stats(c, "?flavor=all")["summary"]["attention"] == 0
    assert stats(c, "?flavor=v34")["summary"]["old"] == 0
    assert stats(c, "?days=90")["summary"]["accounts"] == 2
    assert stats(c, "?days=0")["summary"]["inactive_accounts"] == 0
    assert stats(c, "?segment=required")["total"] == 1
    assert stats(c, "?version=2026.10.20.2")["total"] == 1
    assert stats(c, "?search=RECEN")["total"] == 3
    assert stats(c, "?search=absent")["total"] == 0


def test_paused_legacy_missing_invalid_and_mismatched_metadata(env):
    c, auth, _ = env
    login(c)
    post(c, "releases", build())
    post(c, "releases/enforcement", {"enabled": False})
    assert client_login(c).status_code == 200
    h = {"X-Studio-Flavor": "bogus", "X-Studio-Version": "<script>evil</script>", "X-Studio-Build": "secret-value"}
    assert client_login(c, h).status_code == 200
    data = stats(c)
    assert not data["enforcement_enabled"]
    assert data["summary"]["accounts"] == data["summary"]["attention"] == 1
    assert data["summary"]["old"] == data["summary"]["required"] == 0
    assert data["details"][0]["version"] == ""
    assert data["details"][0]["flavor"] == "unknown"
    assert "secret-value" not in json.dumps(data)
    assert client_login(c, headers(digest="c"), device_id="another-device-123456").status_code == 200
    assert stats(c, "?flavor=all")["details"][0]["state"] == "mismatch"
    assert client_login(c, headers("2026.10.20.9"), device_id="new-unknown-device").status_code == 200
    assert {r["state"] for r in stats(c)["distribution"]} == {"unknown", "mismatch", "unregistered"}


def test_policy_changes_reclassify_without_altering_enforcement_or_catalog(env):
    c, auth, _ = env
    login(c)
    post(c, "releases", build())
    client_login(c, headers())
    post(c, "releases/builds", dict(flavor="all", version="2026.10.20.3", build_sha256="d"*64))
    client_login(c, headers("2026.10.20.3", "d"), device_id="tester-device-123456")
    assert {r["state"] for r in stats(c)["distribution"]} == {"current", "ahead"}
    post(c, "releases", build("2026.10.20.2", "c"))
    before = c.get("/admin/api/releases").json()
    assert stats(c)["summary"]["required"] == 1
    assert client_login(c, headers()).status_code == 426
    post(c, "releases/enforcement", {"enabled": False})
    data = stats(c)
    assert data["summary"]["required"] == 1 and not data["enforcement_enabled"]
    after = c.get("/admin/api/releases").json()
    assert before["builds"] == after["builds"] and before["releases"] == after["releases"]


def test_details_pagination_and_cutoff_boundary(env):
    c, auth, _ = env
    login(c)
    uid = auth.create_user("manydevices", PASSWORD)
    for i in range(56):
        seed(auth, uid, str(i), age=7*86400)
    seed(auth, uid, "outside", age=7*86400+1)
    data = stats(c, "?days=7")
    assert data["summary"]["accounts"] == 1 and data["total"] == 56
    assert len(data["details"]) == 50
    tail = stats(c, "?days=7&page=999")
    assert tail["page"] == 2 and len(tail["details"]) == 6
    assert not {r["device_label"] for r in data["details"]} & {r["device_label"] for r in tail["details"]}


def test_additive_schema_upgrade_preserves_existing_users_policy_and_keys(env):
    c, auth, _ = env
    login(c)
    key = post(c, "keys", {"provider": "julun", "name": "Migration fixture", "api_key": "example-stat-key"})
    assert key.status_code == 200
    assert post(c, "users/customer/keys", {"key_ids": [key.json()["id"]]}).status_code == 200
    post(c, "releases", build())
    post(c, "releases/enforcement", {"enabled": False})
    before = c.get("/admin/api/releases").json()
    with auth.store.connect(write=True) as db:
        db.execute("DROP TABLE client_observations")  # schema before 1.6.4
        users = [dict(r) for r in db.execute("SELECT * FROM users")]
        keys = [dict(r) for r in db.execute("SELECT * FROM key_library")]
        assignments = [dict(r) for r in db.execute("SELECT * FROM user_keys")]
    auth.store.initialize()
    auth.store.initialize()
    assert c.get("/admin/api/releases").json() == before
    with auth.store.connect() as db:
        assert [dict(r) for r in db.execute("SELECT * FROM users")] == users
        assert [dict(r) for r in db.execute("SELECT * FROM key_library")] == keys
        assert [dict(r) for r in db.execute("SELECT * FROM user_keys")] == assignments
    assert stats(c)["summary"]["unobserved_accounts"] == 1
    assert client_login(c).status_code == 200
    assert stats(c)["summary"]["accounts"] == 1
    assert "example-stat-key" not in json.dumps(stats(c))
