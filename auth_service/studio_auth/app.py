from contextlib import asynccontextmanager
from typing import Annotated
import logging

from fastapi import FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from . import __version__
from .service import AuthError, AuthService, username
from .settings import Settings


DeviceId = Annotated[str, Field(min_length=16, max_length=128, pattern=r"^[a-zA-Z0-9_-]+$")]


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Login(Body):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=1, max_length=128)
    device_id: DeviceId
    device_name: str = Field(default="Studio", max_length=80)

    @field_validator("username")
    @classmethod
    def normalize_username(cls, value):
        return username(value)


class Refresh(Body):
    refresh_token: str = Field(min_length=40, max_length=128, pattern=r"^[a-zA-Z0-9_-]+$")
    device_id: DeviceId


class Verify(Body):
    device_id: DeviceId


class Guard:
    """Limit bodies even without Content-Length; do not log credentials or bodies."""
    def __init__(self, app, require_https=True):
        self.app, self.require_https = app, require_https

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope["headers"])

        async def respond(status, code, message):
            response = JSONResponse({"error": {"code": code, "message": message}}, status_code=status,
                                    headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})
            await response(scope, receive, send)

        if self.require_https and scope["scheme"] != "https" and scope["path"] != "/health":
            return await respond(400, "https_required", "请使用 HTTPS 地址访问")
        admin_path = scope['path'] == '/admin' or scope['path'].startswith('/admin/')
        if admin_path:
            expected = (scope['scheme'] + '://').encode() + headers.get(b'host', b'')
            origin = headers.get(b'origin')
            if (headers.get(b'sec-fetch-site') == b'cross-site' or
                    (origin and origin != expected) or (scope['method'] == 'POST' and not origin)):
                return await respond(403, 'origin_forbidden', '请从本站管理页面操作')
        elif headers.get(b"origin"):
            # This API is for Studio's Python backend, not cross-site browser JS.
            return await respond(403, "browser_origin_forbidden", "请由 Studio 后端调用登录接口")
        if scope["method"] == "POST" and headers.get(b"content-type", b"").split(b";")[0].lower() != b"application/json":
            return await respond(415, "json_required", "请求必须使用 application/json")
        if headers.get(b"content-encoding", b"identity").lower() != b"identity":
            return await respond(415, "encoding_unsupported", "不接受压缩请求体")
        chunks, length = [], 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            length += len(message.get("body", b""))
            if length > 16384:
                return await respond(413, "body_too_large", "请求体过大")
            chunks.append(message.get("body", b""))
            if not message.get("more_body", False):
                break
        delivered = False

        async def bounded_receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": b"".join(chunks), "more_body": False}
            return await receive()

        async def no_cache(message):
            if message["type"] == "http.response.start":
                message["headers"] = list(message.get("headers", [])) + [
                    (b"cache-control", b"no-store"), (b"pragma", b"no-cache"),
                    (b"x-content-type-options", b"nosniff")]
                if admin_path:
                    message['headers'] += [
                        (b'content-security-policy', b"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"),
                        (b'x-frame-options', b'DENY'), (b'referrer-policy', b'no-referrer')]
            await send(message)

        await self.app(scope, bounded_receive, no_cache)


def create_app(settings=None, *, clock=None):
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app):
        kwargs = {"clock": clock} if clock is not None else {}
        app.state.auth = AuthService(settings, **kwargs)
        yield

    app = FastAPI(title="Respect Studio 登录服务", version=__version__, lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(Guard, require_https=settings.require_https)
    from .admin import router
    app.include_router(router)
    from .provisioning import router as provisioning_router
    app.include_router(provisioning_router)

    @app.exception_handler(AuthError)
    async def auth_error(request, exc):
        headers = {"WWW-Authenticate": "Bearer"} if exc.status == 401 else {}
        if exc.retry_after:
            headers["Retry-After"] = str(exc.retry_after)
        return JSONResponse({"error": {"code": exc.code, "message": exc.message}},
                            status_code=exc.status, headers=headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # FastAPI's normal validation error includes input (possibly passwords/tokens).
        fields = [".".join(str(p) for p in e["loc"]) for e in exc.errors()]
        return JSONResponse({"error": {"code": "invalid_request", "message": "参数格式不正确",
                                       "fields": fields}}, status_code=422)

    @app.exception_handler(Exception)
    async def unexpected_error(request, exc):
        # Avoid repr(exc): database/validation exceptions can contain secrets.
        logging.getLogger("studio_auth").error("Request failed: %s", type(exc).__name__)
        return JSONResponse({"error": {"code": "server_error", "message": "登录服务暂不可用，请稍后重试"}},
                            status_code=500, headers={"Cache-Control": "no-store"})

    @app.get("/health")
    def health(request: Request):
        with request.app.state.auth.store.connect() as db:
            db.execute("SELECT 1").fetchone()
        return {"ok": True, "service": "respect-studio-auth", "version": __version__}

    @app.post("/api/v1/auth/login")
    def login(body: Login, request: Request):
        return request.app.state.auth.login(body.username, body.password, body.device_id,
                                            body.device_name, request.client.host if request.client else "unknown")

    @app.post("/api/v1/auth/verify")
    def verify(body: Verify, request: Request, authorization: str = Header(default="", max_length=256)):
        parts = authorization.split()
        if len(parts) != 2 or parts[0].lower() != "bearer" or not 40 <= len(parts[1]) <= 128:
            raise AuthError(401, "invalid_session", "请先登录")
        result = request.app.state.auth.verify(parts[1], body.device_id)
        from .provisioning import configuration
        result['provider_config'] = configuration(request.app.state.auth, result['user']['id'])
        return result

    @app.post("/api/v1/auth/refresh")
    def refresh(body: Refresh, request: Request):
        return request.app.state.auth.refresh(body.refresh_token, body.device_id,
                                              request.client.host if request.client else "unknown")

    @app.post("/api/v1/auth/logout")
    def logout(body: Refresh, request: Request):
        return request.app.state.auth.logout(body.refresh_token, body.device_id)

    return app
