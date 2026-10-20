import copy
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from http.cookiejar import CookieJar
from urllib.error import HTTPError
from urllib.request import Request, build_opener, HTTPCookieProcessor, urlopen

import pytest

from core import studio_login as L, paths
from server import app


class AuthStub:
    def __init__(self):
        self.calls = []
        self.expired = False
        self.disabled = False
        self.offline = False
        self.refresh_failure = False

    def __call__(self, action, body, access=None):
        self.calls.append((action, copy.deepcopy(body)))
        if self.offline:
            raise L.LoginRequired("auth_unavailable", "offline", 503)
        if action == "login" and body["password"] != "test-password":
            raise L.LoginRequired("invalid_credentials", "账号或密码不正确")
        if self.disabled and action != "logout":
            raise L.LoginRequired("account_disabled", "账号已停用", 403)
        if action == "verify":
            if self.expired:
                raise L.LoginRequired("access_expired", "expired")
            return {"valid": True, "user": {"username": "alice", "expires_at": None}}
        if action == "refresh":
            if self.refresh_failure:
                raise L.LoginRequired("auth_unavailable", "uncertain refresh", 503)
            self.expired = False
        if action in ("login", "refresh"):
            return {"access_token": "a"*43, "refresh_token": "b"*64, "user": {"username": "alice"}}
        return {"ok": True}


@pytest.fixture
def manager(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "data_dir", lambda: str(tmp_path))
    manager = L.LoginManager()
    stub = AuthStub()
    monkeypatch.setattr(manager, "_post", stub)
    return manager, stub


def logged_in(manager):
    _, token = manager.login({"username": "alice", "password": "test-password"})
    return f"{L.COOKIE}={token}"


def test_login_only_device_id_persisted(manager, tmp_path):
    mgr, stub = manager
    cookie = logged_in(mgr)
    mgr.require(cookie)
    assert mgr.status(cookie)["user"]["username"] == "alice"
    data = json.loads((tmp_path/L.DEVICE_FILE).read_text())
    assert set(data) == {"device_id"}
    assert L.LoginManager().device_id() == data["device_id"]
    assert L.LoginManager().status(cookie)["authenticated"] is False
    assert {action for action, _ in stub.calls} == {"login", "verify"}
    assert all("api_key" not in body for _, body in stub.calls)


def test_unknown_cookie_blocks_without_network(manager):
    mgr, stub = manager
    logged_in(mgr)
    before = len(stub.calls)
    with pytest.raises(L.LoginRequired):
        mgr.require("respect_studio_login=fake")
    assert len(stub.calls) == before


def test_force_check_disabled_account_does_not_use_cached_success(manager):
    mgr, stub = manager
    cookie = logged_in(mgr)
    stub.disabled = True
    with pytest.raises(L.LoginRequired) as exc:
        mgr.require(cookie, force=True)
    assert exc.value.code == "account_disabled"
    assert mgr.tokens is None and not mgr.local_session


def test_network_failure_blocks_until_check_succeeds(manager):
    mgr, stub = manager
    cookie = logged_in(mgr)
    stub.offline = True
    assert mgr.status(cookie, force=True)["can_retry"]
    assert not mgr.status(cookie)["authenticated"]
    stub.offline = False
    mgr.retry_at = 0
    mgr.require(cookie, force=True)


def test_concurrent_requests_only_refresh_once(manager):
    mgr, stub = manager
    cookie = logged_in(mgr)
    stub.expired = True
    mgr.checked_until = 0
    with ThreadPoolExecutor(max_workers=12) as pool:
        list(pool.map(lambda _: mgr.require(cookie), range(25)))
    assert sum(a == "refresh" for a, _ in stub.calls) == 1


def test_uncertain_refresh_requires_login_no_old_token_retry(manager):
    mgr, stub = manager
    cookie = logged_in(mgr)
    stub.expired = stub.refresh_failure = True
    with pytest.raises(L.LoginRequired):
        mgr.require(cookie, force=True)
    with pytest.raises(L.LoginRequired):
        mgr.require(cookie, force=True)
    assert mgr.tokens is None
    assert sum(a == "refresh" for a, _ in stub.calls) == 1


def test_logout_offline_clears_local_authorization(manager):
    mgr, stub = manager
    cookie = logged_in(mgr)
    stub.offline = True
    with pytest.raises(L.LoginRequired):
        mgr.logout(cookie)
    assert mgr.tokens is None and not mgr.local_session


@pytest.fixture
def http_server(manager, monkeypatch):
    from server import agent_api
    mgr, stub = manager
    monkeypatch.setattr(agent_api, "get", lambda *a: {"projects": [], "private": "test-data"})
    monkeypatch.setattr(agent_api, "post", lambda *a: {"ok": True})
    server = app.ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
    server.studio_login = mgr
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    opener = build_opener(HTTPCookieProcessor(CookieJar()))
    url = f"http://127.0.0.1:{server.server_port}"
    def request(path, body=None, **headers):
        if body is not None:
            headers.setdefault("Content-Type", "application/json")
        req = Request(url+path, data=None if body is None else json.dumps(body).encode(), headers=headers)
        return opener.open(req, timeout=5)
    yield request, mgr, stub, url
    server.shutdown(); server.server_close(); thread.join(3)


@pytest.mark.parametrize("path", ["/api/agent/bootstrap", "/api/agent/runtime", "/file?root=x&rel=y"])
def test_http_private_get_requires_auth(http_server, path):
    request, *_ = http_server
    with pytest.raises(HTTPError) as exc:
        request(path)
    assert exc.value.code == 401
    assert json.loads(exc.value.read())["login_required"]


def test_http_login_cookie_protected_posts_and_logout(http_server):
    request, _, stub, _ = http_server
    with request("/") as r:
        assert b"studio-locked" in r.read()
    with pytest.raises(HTTPError) as exc:
        request("/api/agent/start", {})
    assert exc.value.code == 401
    with request("/api/auth/login", {"username": "alice", "password": "test-password"}) as r:
        cookie = r.headers["Set-Cookie"]
        assert "HttpOnly" in cookie and "SameSite=Strict" in cookie
        assert "access_token" not in r.read().decode()
    with request("/api/agent/bootstrap") as r:
        assert json.load(r)["private"] == "test-data"
    stub.disabled = True
    with pytest.raises(HTTPError) as exc:
        request("/api/agent/start", {})
    assert exc.value.code == 403
    stub.disabled = False
    request("/api/auth/login", {"username": "alice", "password": "test-password"}).close()
    request("/api/auth/logout", {}).close()
    with request("/api/auth/status") as r:
        assert not json.load(r)["authenticated"]


def test_http_host_origin_and_plaintext_form_rejected(http_server):
    request, *_ = http_server
    for headers in ({"Origin": "https://evil.example"}, {"Host": "evil.example"}):
        with pytest.raises(HTTPError) as exc:
            request("/api/auth/login", {}, **headers)
        assert exc.value.code == 403
    with pytest.raises(HTTPError) as exc:
        request("/api/auth/login", {}, **{"Content-Type": "text/plain"})
    assert exc.value.code == 415


@pytest.mark.parametrize("path", ["/api/agent/start", "/api/agent/reroll", "/api/agent/assemble"])
def test_all_new_job_entries_force_online_check(http_server, path):
    request, _, stub, _ = http_server
    request("/api/auth/login", {"username": "alice", "password": "test-password"}).close()
    stub.offline = True
    with pytest.raises(HTTPError) as exc:
        request(path, {})
    assert exc.value.code == 503


def test_update_recovery_is_accessible_before_login_but_same_origin(http_server, monkeypatch):
    from core import updates
    request, *_ = http_server
    m = updates.UpdateManager()
    monkeypatch.setattr(updates, "MANAGER", m)
    monkeypatch.setattr(m, "check", lambda: dict(m.status(), code="update_required"))
    with request("/api/updates/check", {}) as r:
        assert json.load(r)["code"] == "update_required"
    with request("/api/updates/status") as r:
        assert not json.load(r)["allowed"]
    with pytest.raises(HTTPError) as exc:
        request("/api/updates/install", {}, Origin="https://evil.example")
    assert exc.value.code == 403
