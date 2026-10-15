"""Reference integration for Studio's Python backend, not a browser JS client.

Tokens stay in memory here. Production 'remember login' must use OS credential storage.
No provider Key, prompt, project path, or media is sent to this service.
"""
import json
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid


class LoginError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class StudioAuthClient:
    def __init__(self, base_url, device_id, device_name="Studio"):
        url = urllib.parse.urlsplit(base_url)
        if url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError("Use an HTTPS auth service URL without embedded credentials/query")
        if url.path not in ("", "/"):
            raise ValueError("Use the dedicated auth domain root, without an /api suffix")
        self.base_url = base_url.rstrip("/")
        self.device_id, self.device_name = device_id, device_name
        self.tokens = None
        self.lock = threading.RLock()
        self.opener = urllib.request.build_opener(NoRedirect())

    @staticmethod
    def new_device_id():
        """Generate ONCE per installation and persist this non-secret ID locally."""
        return str(uuid.uuid4())

    def _post(self, action, payload, access=None):
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if access:
            headers["Authorization"] = "Bearer " + access
        request = urllib.request.Request(self.base_url + "/api/v1/auth/" + action,
                                         json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
        try:
            with self.opener.open(request, timeout=15) as response:
                data = json.load(response)
        except urllib.error.HTTPError as exc:
            try:
                error = json.loads(exc.read(16384)).get("error", {})
            except (ValueError, AttributeError):
                error = {}
            raise LoginError(error.get("code", "http_error"),
                             error.get("message", f"登录服务返回 HTTP {exc.code}")) from None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            raise LoginError("network_error", "无法连接登录服务，请检查网络后重试") from None
        if not isinstance(data, dict):
            raise LoginError("invalid_response", "登录服务响应格式不正确")
        return data

    def login(self, username, password):
        with self.lock:
            self.tokens = None
            data = self._post("login", {"username": username, "password": password,
                              "device_id": self.device_id, "device_name": self.device_name})
            self._set_tokens(data)
            return data["user"]

    def _set_tokens(self, data):
        if (not all(isinstance(data.get(k), str) and data[k] for k in ("access_token", "refresh_token"))
                or not isinstance(data.get("user"), dict)):
            raise LoginError("invalid_response", "登录服务未返回有效凭证")
        self.tokens = data

    def verify(self):
        """Success returns user; any failure raises. Never treats network errors as permission."""
        with self.lock:
            if not self.tokens:
                raise LoginError("login_required", "请先登录")
            for attempt in range(2):
                try:
                    result = self._post("verify", {"device_id": self.device_id}, self.tokens["access_token"])
                    break
                except LoginError as exc:
                    if exc.code == "access_expired" and attempt == 0:
                        self.refresh()
                        continue
                    if exc.code in ("invalid_session", "account_disabled", "account_expired", "access_expired"):
                        self.tokens = None
                    raise
            if result.get("valid") is not True or not isinstance(result.get("user"), dict):
                raise LoginError("invalid_response", "登录服务未确认授权")
            return result["user"]

    def refresh(self):
        # All application threads must share ONE client and this lock.
        with self.lock:
            if not self.tokens:
                raise LoginError("login_required", "请先登录")
            old = self.tokens
            # A timeout may mean the server rotated already: do not retry the old refresh token.
            self.tokens = None
            data = self._post("refresh", {"refresh_token": old["refresh_token"], "device_id": self.device_id})
            self._set_tokens(data)

    def logout(self):
        with self.lock:
            old, self.tokens = self.tokens, None
            if old:
                return self._post("logout", {"refresh_token": old["refresh_token"], "device_id": self.device_id})
            return {"ok": True}
