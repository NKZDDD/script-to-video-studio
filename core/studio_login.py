"""Online login and in-memory account configuration; cloud secrets are never saved locally."""
from __future__ import annotations

from http.cookies import SimpleCookie, CookieError
from http.client import HTTPException
import json
import secrets
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from . import paths
from .store import read_json, write_json


AUTH_URL = "https://login.respect.chat"
DEVICE_FILE = "studio-device.json"
COOKIE = "respect_studio_login"


class LoginRequired(Exception):
    def __init__(self, code="login_required", message="请先登录 Respect Studio", status=401):
        super().__init__(message)
        self.code, self.status = code, status


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class LoginManager:
    def __init__(self):
        self.lock = threading.RLock()
        self.tokens = None
        self.user = None
        self.local_session = ""
        self.checked_until = 0
        self.retry_at = 0
        self.last_error = None
        self.opener = urllib.request.build_opener(NoRedirect())
        self._device = None

    def device_id(self):
        if self._device:
            return self._device
        path = Path(paths.data_dir()) / DEVICE_FILE
        data = read_json(str(path), {}) or {}
        value = data.get("device_id", "") if isinstance(data, dict) else ""
        try:
            value = str(uuid.UUID(value))
        except (ValueError, TypeError, AttributeError):
            value = str(uuid.uuid4())
            write_json(str(path), {"device_id": value})
        self._device = value
        return value

    def _post(self, action, body, access=None):
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if access:
            headers["Authorization"] = "Bearer " + access
        request = urllib.request.Request(AUTH_URL + "/api/v1/auth/" + action,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"), headers=headers, method="POST")
        try:
            with self.opener.open(request, timeout=15) as response:
                raw = response.read(4194305)
                if len(raw) > 4194304:
                    raise ValueError("oversized auth response")
                data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("invalid auth response")
            return data
        except urllib.error.HTTPError as exc:
            try:
                detail = json.loads(exc.read(16384)).get("error", {})
                code = str(detail.get("code", "auth_service_error"))
                message = str(detail.get("message", "登录服务返回错误"))[:300]
            except (ValueError, AttributeError):
                code, message = "auth_service_error", f"登录服务返回 HTTP {exc.code}，请稍后重试"
            if code == "https_required":
                message = "登录服务的 HTTPS 代理配置不完整，请联系管理员检查 X-Forwarded-Proto"
            raise LoginRequired(code, message, exc.code if exc.code in (401, 403, 429) else 503) from None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, HTTPException):
            raise LoginRequired("auth_unavailable", "无法连接登录服务，请检查网络后重试；已有任务不会重新提交", 503) from None

    def matches(self, header):
        try:
            cookie = SimpleCookie()
            cookie.load(header or "")
            value = cookie[COOKIE].value if COOKIE in cookie else ""
            return bool(self.local_session) and secrets.compare_digest(value, self.local_session)
        except (ValueError, TypeError, CookieError):
            return False

    def _clear(self):
        from . import cloud_config
        cloud_config.clear(self)
        self.tokens = self.user = None
        self.local_session = ""
        self.checked_until = self.retry_at = 0
        self.last_error = None

    def _set_tokens(self, data):
        if (not all(isinstance(data.get(k), str) and 40 <= len(data[k]) <= 128
                    for k in ("access_token", "refresh_token"))
                or not isinstance(data.get("user"), dict)):
            raise LoginRequired("invalid_auth_response", "登录服务没有返回有效凭证", 503)
        self.tokens = {k: data[k] for k in ("access_token", "refresh_token")}

    def login(self, body):
        name, password = body.get("username"), body.get("password")
        if not isinstance(name, str) or not 3 <= len(name.strip()) <= 64 or not isinstance(password, str) or not 1 <= len(password) <= 128:
            raise LoginRequired("invalid_credentials", "请填写账号和密码", 400)
        with self.lock:
            # A rejected attempt from a second tab must not revoke the running session.
            data = self._post("login", {"username": name, "password": password,
                               "device_id": self.device_id(), "device_name": "Respect Studio"})
            self._clear()
            self._set_tokens(data)
            self.local_session = secrets.token_urlsafe(32)
            self._verify()
            return {"authenticated": True, "user": self.user}, self.local_session

    def _verify(self):
        if not self.tokens:
            raise LoginRequired()
        for attempt in range(2):
            try:
                data = self._post("verify", {"device_id": self.device_id()}, self.tokens["access_token"])
                break
            except LoginRequired as exc:
                if exc.code == "access_expired" and attempt == 0:
                    old = self.tokens["refresh_token"]
                    self.tokens = None
                    try:
                        new = self._post("refresh", {"refresh_token": old, "device_id": self.device_id()})
                        self._set_tokens(new)
                    except LoginRequired:
                        self._clear()  # Timeout could mean rotation succeeded. Never retry old refresh.
                        raise LoginRequired("login_required", "登录续期未完成，请重新登录；已有任务不会重新提交") from None
                    continue
                if exc.status in (401, 403):
                    self._clear()
                else:
                    self.checked_until = 0
                    self.last_error, self.retry_at = exc, time.monotonic() + 5
                raise
        if data.get("valid") is not True or not isinstance(data.get("user"), dict):
            self.checked_until = 0
            raise LoginRequired("invalid_auth_response", "登录服务未确认授权，请重试", 503)
        from . import cloud_config, cloud_usage
        self.user = {k: data["user"].get(k) for k in ("id", "username", "expires_at")}
        try:
            cloud_config.install(self, self.user.get('id') or '', data.get('provider_config', {}))
        except ValueError:
            self._clear()
            raise LoginRequired('invalid_auth_response', '后台服务商配置格式不正确，请联系管理员', 503) from None
        self.checked_until = time.monotonic() + 60
        self.last_error, self.retry_at = None, 0
        if self.user.get('id'):
            cloud_usage.start_sender(self)

    def send_usage(self):
        from . import cloud_usage
        with self.lock:
            if not self.tokens or not self.user or not self.user.get('id'):
                return
            uid = self.user['id']
            events = cloud_usage.pending(uid)
            if not events:
                return
            body = {'device_id': self.device_id(), 'events': events}
            while len(json.dumps(body, ensure_ascii=False).encode('utf-8')) > 14000:
                events.pop()
            if time.monotonic() >= self.checked_until:
                self._verify()
            result = self._post('usage', body, self.tokens['access_token'])
            if result.get('accepted') == [e['event_id'] for e in events]:
                cloud_usage.acknowledge(uid, events)

    def require(self, cookie, force=False):
        with self.lock:
            if not self.matches(cookie) or not self.tokens:
                raise LoginRequired()
            if self.last_error and time.monotonic() < self.retry_at:
                raise self.last_error
            if force or time.monotonic() >= self.checked_until:
                self._verify()

    def status(self, cookie, force=False):
        with self.lock:
            try:
                self.require(cookie, force=force)
                from . import cloud_usage
                return {"authenticated": True, "user": self.user, "service": AUTH_URL,
                        "usage": cloud_usage.status(self.user.get('id'))}
            except LoginRequired as exc:
                return {"authenticated": False, "code": exc.code, "message": str(exc),
                        "can_retry": bool(self.tokens and self.matches(cookie)), "service": AUTH_URL}

    def logout(self, cookie):
        with self.lock:
            if not self.matches(cookie):
                raise LoginRequired()
            old = self.tokens
            self._clear()
            if old:
                self._post("logout", {"refresh_token": old["refresh_token"], "device_id": self.device_id()})
            return {"ok": True}
