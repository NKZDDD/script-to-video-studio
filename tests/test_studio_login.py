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


def test_network_failure_blocks_production_but_preserves_local_access(manager):
    mgr, stub = manager
    cookie = logged_in(mgr)
    stub.offline = True
    assert mgr.status(cookie, force=True)["can_retry"]
    assert mgr.status(cookie)["authenticated"]
    stub.offline = False
    mgr.retry_at = 0
    mgr.require(cookie, force=True)


def test_concurrent_requests_only_refresh_once(manager):
    mgr, stub = manager
    cookie = logged_in(mgr)
    stub.expired = True
    mgr.checked_until = 0
    with ThreadPoolExecutor(max_workers=12) as pool:
        list(pool.map(lambda _: mgr.require(cookie, force=True), range(25)))
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


def test_file_preview_range_and_media_share_login_without_online_check(http_server, tmp_path, monkeypatch):
    from types import SimpleNamespace
    from urllib.parse import urlencode
    from server import agent_api
    request, _, stub, base = http_server
    monkeypatch.setattr(agent_api, 'project', lambda *a: SimpleNamespace(root=str(tmp_path)))
    (tmp_path/'preview.png').write_bytes(b'\x89PNG\r\n\x1a\nfixture')
    path = '/file?' + urlencode({'root': str(tmp_path), 'rel': 'preview.png'})
    request('/api/auth/login', {'username': 'alice', 'password': 'test-password'}).close()
    before = len(stub.calls)
    stub.offline = True
    with request(path, **{'Range': 'bytes=0-0'}) as r:
        assert r.status == 206 and r.read() == b'\x89'
        assert r.headers['Content-Type'] == 'image/png'
    with request(path) as r:
        assert r.read() == (tmp_path/'preview.png').read_bytes()
    # The separate external browser still has no credential; we do not make files public.
    with pytest.raises(HTTPError) as exc:
        urlopen(base+path)
    assert exc.value.code == 401
    assert len(stub.calls) == before


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


def test_local_access_never_renews_and_known_expiry_is_local(manager):
    import time
    mgr, stub = manager
    cookie = logged_in(mgr)
    before = len(stub.calls)
    mgr.checked_until = 0
    stub.offline = True
    for _ in range(5):
        mgr.require(cookie)
        assert mgr.status(cookie)["authenticated"]
    assert len(stub.calls) == before
    mgr.user['expires_at'] = time.time() - 1
    assert mgr.status(cookie)['code'] == 'account_expired'
    assert len(stub.calls) == before
    stub.offline = False
    mgr.require(cookie, force=True)  # Renewal on the server can restore access.


def test_slow_verify_does_not_block_dependency_or_plan_requests(http_server, monkeypatch):
    request, mgr, stub, url = http_server
    with request('/api/auth/login', {'username':'alice', 'password':'test-password'}) as response:
        cookie = response.headers['Set-Cookie'].split(';')[0]
    started, release = threading.Event(), threading.Event()
    def slow(action, body, access=None):
        if action == 'verify':
            started.set()
            assert release.wait(5)
        return stub(action, body, access)
    monkeypatch.setattr(mgr, '_post', slow)
    with ThreadPoolExecutor(2) as pool:
        future = pool.submit(mgr.require, cookie, True)
        try:
            assert started.wait(2)
            for path in ('/api/agent/dependencies', '/api/agent/preview'):
                req = Request(url+path, data=b'{}', headers={'Content-Type':'application/json', 'Cookie':cookie})
                with urlopen(req, timeout=1) as r:
                    assert json.load(r)['ok']
            assert mgr.status(cookie)['authenticated']
        finally:
            release.set()
        future.result(timeout=2)


def test_usage_upload_does_not_lock_pages_or_verify(manager, monkeypatch):
    from core import cloud_usage
    mgr, stub = manager
    cookie = logged_in(mgr)
    mgr.user['id'] = 'user-a'
    mgr.checked_until = 0
    monkeypatch.setattr(cloud_usage, 'pending', lambda uid: [{'event_id':'event-a'}])
    ack = []
    monkeypatch.setattr(cloud_usage, 'acknowledge', lambda *args: ack.append(args))
    started, release = threading.Event(), threading.Event()
    def slow(action, body, access=None):
        assert action == 'usage'
        started.set()
        assert release.wait(5)
        return {'accepted':['event-a']}
    monkeypatch.setattr(mgr, '_post', slow)
    with ThreadPoolExecutor(2) as pool:
        future = pool.submit(mgr.send_usage)
        try:
            assert started.wait(2)
            pool.submit(mgr.require, cookie).result(timeout=1)
        finally:
            release.set()
        future.result(timeout=2)
    assert ack == [('user-a', [{'event_id':'event-a'}])]


def test_logout_during_verify_cannot_restore_session(manager, monkeypatch):
    mgr, stub = manager
    cookie = logged_in(mgr)
    started, release = threading.Event(), threading.Event()
    def slow(action, body, access=None):
        if action == 'verify':
            started.set()
            assert release.wait(5)
        return stub(action, body, access)
    monkeypatch.setattr(mgr, '_post', slow)
    invalidated = threading.Event()
    original = mgr._current
    def current(generation):
        if mgr.generation != generation:
            invalidated.set()
        return original(generation)
    monkeypatch.setattr(mgr, '_current', current)
    with ThreadPoolExecutor(3) as pool:
        verification = pool.submit(mgr.require, cookie, True)
        try:
            assert started.wait(2)
            logout = pool.submit(mgr.logout, cookie)
            # Wait for local invalidation without waiting on the remote operation.
            import time
            deadline = time.monotonic() + 1
            while mgr.local_session and time.monotonic() < deadline:
                time.sleep(.005)
            assert not mgr.status(cookie)['authenticated']
        finally:
            release.set()
        logout.result(timeout=2)
        with pytest.raises(L.LoginRequired):
            verification.result(timeout=2)
    assert invalidated.is_set()
    assert not mgr.status(cookie)['authenticated']


def test_local_routes_do_not_force_cloud_refresh(http_server):
    request, mgr, stub, _ = http_server
    request('/api/auth/login', {'username':'alice','password':'test-password'}).close()
    mgr.checked_until = 0
    before = len(stub.calls)
    for path in ('/api/agent/dependencies','/api/agent/preview','/api/agent/settings',
                 '/api/agent/models/refresh','/api/agent/providers/reload'):
        request(path, {}).close()
    assert len(stub.calls) == before
    request('/api/agent/config/refresh', {}).close()
    assert len(stub.calls) == before + 1


def test_failed_production_check_does_not_lock_local_ui(http_server):
    request, _, stub, _ = http_server
    request('/api/auth/login', {'username':'alice','password':'test-password'}).close()
    stub.offline = True
    with pytest.raises(HTTPError) as exc:
        request('/api/agent/start', {})
    assert exc.value.code == 503
    assert json.loads(exc.value.read())['login_required'] is False
    with request('/api/agent/preview', {}) as r:
        assert json.load(r)['ok']


def test_slow_model_refresh_does_not_serialize_local_posts(http_server, monkeypatch):
    from server import agent_api
    request, mgr, stub, url = http_server
    with request('/api/auth/login', {'username':'alice','password':'test-password'}) as r:
        cookie = r.headers['Set-Cookie'].split(';')[0]
    entered, release = threading.Event(), threading.Event()
    def post(app, path, body):
        if path == '/api/agent/models/refresh':
            entered.set()
            assert release.wait(5)
        return {'ok':True}
    monkeypatch.setattr(agent_api, 'post', post)
    def call(path):
        req = Request(url+path, data=b'{}', headers={'Content-Type':'application/json','Cookie':cookie})
        with urlopen(req, timeout=2) as r:
            return json.load(r)
    with ThreadPoolExecutor(2) as pool:
        pending = pool.submit(call, '/api/agent/models/refresh')
        try:
            assert entered.wait(1)
            assert call('/api/agent/dependencies')['ok']
            assert call('/api/agent/preview')['ok']
        finally:
            release.set()
        assert pending.result(timeout=2)['ok']


def test_version_network_failure_preserves_local_access_but_enforced_update_locks(http_server, monkeypatch):
    request, mgr, _, _ = http_server
    request('/api/auth/login', {'username':'alice','password':'test-password'}).close()
    code = ['update_check_failed']
    def failure(*a, **k):
        raise L.LoginRequired(code[0], 'version check', 426)
    monkeypatch.setattr(mgr, '_post', failure)
    for reason, locks in [('update_check_failed',False), ('update_required',True)]:
        code[0] = reason
        mgr.retry_at = 0
        with pytest.raises(HTTPError) as exc:
            request('/api/agent/start', {})
        assert exc.value.code == 426
        assert json.loads(exc.value.read())['login_required'] is locks


def test_local_posts_do_not_wait_for_job_creation_lock(http_server):
    from core import updates
    request, _, _, url = http_server
    with request('/api/auth/login', {'username':'alice','password':'test-password'}) as r:
        cookie = r.headers['Set-Cookie'].split(';')[0]
    with updates.LIFECYCLE:
        req = Request(url+'/api/agent/preview', data=b'{}',
                      headers={'Content-Type':'application/json','Cookie':cookie})
        with urlopen(req, timeout=1) as r:
            assert json.load(r)['ok']
