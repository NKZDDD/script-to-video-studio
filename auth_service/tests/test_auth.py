import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from studio_auth.app import create_app
from studio_auth.service import AuthError, AuthService, digest
from studio_auth.settings import Settings


PASSWORD = "sample-password-for-tests-only"
DEVICE = "test-device-1234567890"


@pytest.fixture
def env(tmp_path):
    clock = [1800000000]
    settings = Settings(tmp_path / "auth.sqlite3")
    with TestClient(create_app(settings, clock=lambda: clock[0]), base_url="https://auth.example.test") as client:
        auth = client.app.state.auth
        auth.create_user("alice", PASSWORD)
        yield client, auth, clock


def login(client, **kwargs):
    return client.post("/api/v1/auth/login", json={"username": "alice", "password": PASSWORD,
                       "device_id": DEVICE, "device_name": "办公电脑", **kwargs})


def verify(client, tokens, device=DEVICE):
    return client.post("/api/v1/auth/verify", json={"device_id": device},
                       headers={"Authorization": "Bearer " + tokens["access_token"]})


def refresh(client, tokens, device=DEVICE):
    return client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"],
                                                   "device_id": device})


def test_login_verify_no_plaintext_database(env):
    client, auth, _ = env
    response = login(client)
    assert response.status_code == 200
    tokens = response.json()
    assert verify(client, tokens).json()["valid"] is True
    assert response.headers["cache-control"] == "no-store"
    with auth.store.connect() as db:
        dump = "\n".join(db.iterdump())
        assert "$argon2id$" in dump
        assert PASSWORD not in dump
        assert tokens["access_token"] not in dump
        assert tokens["refresh_token"] not in dump
        assert DEVICE not in dump
    assert "password_hash" not in response.text


@pytest.mark.parametrize("name,password", [("alice", "wrong-password"), ("nobody", PASSWORD)])
def test_invalid_credentials_identical(env, name, password):
    response = login(env[0], username=name, password=password)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_credentials"


def test_rotation_invalidates_old_access_and_replay_revokes_family(env):
    client, _, _ = env
    old = login(client).json()
    new = refresh(client, old).json()
    assert new["refresh_token"] != old["refresh_token"]
    assert new["refresh_expires_at"] == old["refresh_expires_at"]
    assert verify(client, old).status_code == 401
    assert verify(client, new).status_code == 200
    replay = refresh(client, old)
    assert replay.status_code == 401
    assert replay.json()["error"]["code"] == "refresh_reused"
    assert verify(client, new).status_code == 401
    assert refresh(client, new).status_code == 401


def test_expired_access_refresh_then_absolute_session_expiry(env):
    client, _, clock = env
    tokens = login(client).json()
    clock[0] = tokens["access_expires_at"]
    assert verify(client, tokens).json()["error"]["code"] == "access_expired"
    tokens = refresh(client, tokens).json()
    assert verify(client, tokens).status_code == 200
    clock[0] = tokens["refresh_expires_at"]
    assert refresh(client, tokens).status_code == 401


def test_disable_revokes_and_enable_requires_fresh_login(env):
    client, auth, _ = env
    tokens = login(client).json()
    auth.manage_user("alice", enabled=False)
    assert verify(client, tokens).status_code == 401
    assert login(client).json()["error"]["code"] == "account_disabled"
    auth.manage_user("alice", enabled=True)
    assert verify(client, tokens).status_code == 401
    assert login(client).status_code == 200


def test_account_expiry_blocks_login_verify_refresh_and_can_extend(env):
    client, auth, clock = env
    tokens = login(client).json()
    auth.manage_user("alice", expires_at=clock[0])
    for response in (login(client), verify(client, tokens), refresh(client, tokens)):
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "account_expired"
    auth.manage_user("alice", expires_at=clock[0]+86400)
    assert verify(client, tokens).status_code == 200


def test_password_reset_revokes_every_session(env):
    client, auth, _ = env
    tokens = login(client).json()
    auth.manage_user("alice", password="new-password-for-tests")
    assert verify(client, tokens).status_code == 401
    assert refresh(client, tokens).status_code == 401
    assert login(client).status_code == 401
    assert login(client, password="new-password-for-tests").status_code == 200


def test_device_limit_relogin_and_logout(env):
    client, auth, _ = env
    auth.manage_user("alice", max_devices=1)
    tokens = login(client).json()
    assert login(client, device_id="another-device-123456").json()["error"]["code"] == "device_limit"
    current = login(client).json()
    assert verify(client, tokens).status_code == 401
    assert verify(client, current, "another-device-123456").status_code == 401
    assert refresh(client, current, "another-device-123456").status_code == 401
    assert verify(client, current).status_code == 200
    body = {"refresh_token": current["refresh_token"], "device_id": DEVICE}
    assert client.post("/api/v1/auth/logout", json=body).json() == {"ok": True}
    assert client.post("/api/v1/auth/logout", json=body).json() == {"ok": True}
    assert verify(client, current).status_code == 401
    assert login(client, device_id="another-device-123456").status_code == 200


def test_unlimited_devices_default_and_admin_revoke(env):
    client, auth, _ = env
    a = login(client).json()
    b = login(client, device_id="another-device-123456").json()
    assert verify(client, a).status_code == 200
    assert verify(client, b, "another-device-123456").status_code == 200
    auth.manage_user("alice", revoke=True)
    assert verify(client, a).status_code == 401
    assert verify(client, b, "another-device-123456").status_code == 401


def test_user_rate_survives_restart_and_expires(env):
    client, auth, clock = env
    for _ in range(10):
        assert login(client, password="bad").status_code == 401
    response = login(client)
    assert response.status_code == 429
    assert int(response.headers["retry-after"]) > 0
    restarted = AuthService(auth.settings, clock=lambda: clock[0])
    with pytest.raises(AuthError) as exc:
        restarted.login("alice", PASSWORD, DEVICE, "PC", "different-ip")
    assert exc.value.code == "rate_limited"
    clock[0] += auth.settings.rate_window
    assert login(client).status_code == 200


def test_rate_atomic_across_connections(env):
    _, auth, _ = env
    def attempt(_):
        try:
            auth.rate([("shared-test", 5)])
            return True
        except AuthError:
            return False
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(attempt, range(20))) == 5


def test_refresh_race_only_one_rotation_then_revocation(env):
    client, auth, _ = env
    tokens = login(client).json()
    def attempt(_):
        try:
            return auth.refresh(tokens["refresh_token"], DEVICE, "race-ip")
        except AuthError as exc:
            return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, range(2)))
    successes = [r for r in results if isinstance(r, dict)]
    assert len(successes) == 1
    assert "refresh_reused" in results
    assert verify(client, successes[0]).status_code == 401


def test_persist_session_and_backup(env, tmp_path):
    client, auth, clock = env
    tokens = login(client).json()
    restarted = AuthService(auth.settings, clock=lambda: clock[0])
    assert restarted.verify(tokens["access_token"], DEVICE)["valid"]
    backup = tmp_path / "backup.sqlite3"
    auth.store.backup(backup)
    copied = AuthService(Settings(backup), clock=lambda: clock[0])
    assert copied.verify(tokens["access_token"], DEVICE)["valid"]
    with pytest.raises(FileExistsError):
        auth.store.backup(backup)
    with pytest.raises(FileExistsError):
        auth.store.backup(auth.store.path)


def test_prune_removes_rotated_hashes_after_session_expiry(env):
    client, auth, clock = env
    tokens = login(client).json()
    tokens = refresh(client, tokens).json()
    clock[0] = tokens["refresh_expires_at"] + 1
    auth.prune()
    with auth.store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM used_refresh").fetchone()[0] == 0


@pytest.mark.parametrize("change", [
    {"password": {"secret": "do-not-echo"}}, {"username": "'; DROP TABLE users;--"},
    {"api_key": "provider-key-must-never-be-accepted"}, {"device_id": "short"},
    {"password": "a"*129}, {"device_name": "a"*81},
])
def test_validation_never_echoes_inputs(env, change):
    response = login(env[0], **change)
    assert response.status_code == 422
    assert "do-not-echo" not in response.text
    assert "provider-key-must-never-be-accepted" not in response.text
    assert "DROP TABLE" not in response.text
    assert "sample-password" not in response.text


def test_https_proxy_spoof_and_no_browser_origin(env):
    client = env[0]
    assert client.get("http://auth.example.test/health").status_code == 200
    response = client.post("http://auth.example.test/api/v1/auth/login", json={},
                           headers={"X-Forwarded-Proto": "https"})
    assert response.status_code == 400  # App doesn't trust arbitrary forwarded headers.
    assert client.post("/api/v1/auth/login", json={}, headers={"Origin": "https://evil.test"}).status_code == 403


def test_body_bound_and_content_type(env):
    client = env[0]
    assert client.post("/api/v1/auth/login", content=b"x"*16385,
                       headers={"Content-Type": "application/json"}).status_code == 413
    assert client.post("/api/v1/auth/login", content="username=alice").status_code == 415
    assert client.post("/api/v1/auth/login", json={}, headers={"Content-Encoding": "gzip"}).status_code == 415
    assert client.post("/api/v1/auth/login", content='{"password":"secret",',
                       headers={"Content-Type": "application/json"}).status_code == 422


def test_no_admin_or_provider_proxy_endpoints(env):
    client = env[0]
    for path in ("/docs", "/openapi.json", "/admin", "/api/v1/users", "/v1/images/generations"):
        assert client.get(path).status_code == 404


def test_new_database_version_rejected(tmp_path):
    path = tmp_path / "future.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA user_version=99")
    with pytest.raises(RuntimeError):
        AuthService(Settings(path))


def test_cli_dates_are_explicit_and_weak_password_rejected(env):
    from studio_auth.manage import expiry
    assert expiry("never") is None
    assert expiry("2027-01-01T00:00:00+08:00") == expiry("2026-12-31T16:00:00Z")
    with pytest.raises(ValueError):
        expiry("2027-01-01")
    with pytest.raises(ValueError):
        env[1].create_user("bobby", "short")
