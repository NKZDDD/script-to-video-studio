"""Account-authorized R2 PUT signatures. Long-lived credentials never leave this service."""
import ipaddress
import json
import re
import uuid
from urllib.parse import urlsplit

from fastapi import APIRouter, Header, Request
from fastapi.responses import FileResponse
from pydantic import Field, field_validator

from .admin import Body, WEB, current, record
from .provisioning import user_id
from .service import AuthError


router = APIRouter()
MAX_BYTES = 25 * 1024 * 1024
TYPES = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "image/gif": ".gif",
         "audio/mpeg": ".mp3", "audio/mp4": ".m4a", "audio/x-m4a": ".m4a",
         "audio/wav": ".wav", "audio/x-wav": ".wav", "audio/ogg": ".ogg",
         "audio/flac": ".flac", "audio/x-flac": ".flac", "audio/aac": ".aac", "text/plain": ".txt"}


class StorageSettings(Body):
    endpoint: str = Field(max_length=250)
    bucket: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$")
    public_base_url: str = Field(max_length=500)
    prefix: str = Field(default="studio-references", pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    access_key: str = Field(default="", max_length=128)
    secret_key: str = Field(default="", max_length=256)
    enabled: bool = True

    @field_validator("endpoint", "public_base_url")
    @classmethod
    def valid_url(cls, value, info):
        value = value.strip().rstrip("/")
        u = urlsplit(value)
        if (u.scheme != "https" or not u.hostname or u.username or u.password or u.port not in (None, 443)
                or u.query or u.fragment or u.path or not u.hostname.isascii() or "%" in u.netloc):
            raise ValueError("Use an HTTPS origin without path, credentials or query")
        host = u.hostname.lower()
        if info.field_name == "endpoint":
            if not host.endswith(".r2.cloudflarestorage.com"):
                raise ValueError("Use the Cloudflare R2 S3 endpoint")
        else:
            if "." not in host or host.endswith((".local", ".localhost", ".internal", ".r2.cloudflarestorage.com")):
                raise ValueError("Use a public R2 domain, not the S3 endpoint")
            try:
                ipaddress.ip_address(host)
            except ValueError:
                pass
            else:
                raise ValueError("Use a public domain")
        return value

    @field_validator("access_key", "secret_key")
    @classmethod
    def valid_key(cls, value):
        value = value.strip()
        if value and (not value.isascii() or any(c.isspace() for c in value)):
            raise ValueError("Invalid storage credential")
        return value


def _load(db):
    row = db.execute("SELECT * FROM r2_settings WHERE id=1").fetchone()
    return (json.loads(row["config"]), row["revision"], bool(row["enabled"])) if row else ({}, "", False)


def configuration(auth, uid):
    with auth.store.connect() as db:
        cfg, revision, enabled = _load(db)
        grant = db.execute("SELECT enabled FROM user_r2 WHERE user_id=?", (uid,)).fetchone()
    assigned = bool(grant and grant["enabled"])
    if not assigned:
        return {"assigned": False}
    return {"assigned": True, "available": enabled and bool(cfg), "revision": revision,
            "endpoint": cfg.get("endpoint", ""), "bucket": cfg.get("bucket", ""),
            "public_base_url": cfg.get("public_base_url", ""),
            "prefix": f"{cfg.get('prefix', 'studio-references')}/{uid}/{revision}", "max_bytes": MAX_BYTES}


@router.get("/admin/storage.js")
def javascript():
    return FileResponse(WEB / "storage.js", media_type="text/javascript")


@router.get("/admin/storage.css")
def stylesheet():
    return FileResponse(WEB / "storage.css", media_type="text/css")


@router.get("/admin/api/storage")
def get_settings(request: Request):
    current(request)
    with request.app.state.auth.store.connect() as db:
        cfg, revision, enabled = _load(db)
        assigned = db.execute("SELECT COUNT(*) FROM user_r2 WHERE enabled=1").fetchone()[0]
    return {"config": {k: v for k, v in cfg.items() if k not in ("access_key", "secret_key")},
            "access_key_set": bool(cfg.get("access_key")), "secret_key_set": bool(cfg.get("secret_key")),
            "revision": revision, "enabled": enabled, "assigned_accounts": assigned}


@router.post("/admin/api/storage")
def save_settings(body: StorageSettings, request: Request):
    actor, _ = current(request, csrf=True)
    auth = request.app.state.auth
    with auth.store.connect(write=True) as db:
        cfg, _, _ = _load(db)
        values = body.model_dump(exclude={"enabled"})
        for name in ("access_key", "secret_key"):
            values[name] = values[name] or cfg.get(name, "")
        if not all(values.get(k) for k in ("access_key", "secret_key")):
            raise AuthError(422, "storage_credentials_missing", "首次配置请填写 R2 Access Key 和 Secret Key")
        db.execute("INSERT INTO r2_settings VALUES(1,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                   "config=excluded.config,revision=excluded.revision,enabled=excluded.enabled",
                   (json.dumps(values), uuid.uuid4().hex, int(body.enabled)))
    record(request, actor, "save_r2", "enabled" if body.enabled else "disabled")
    return {"ok": True}


class Grant(Body):
    enabled: bool


@router.get("/admin/api/users/{name}/storage")
def get_grant(name: str, request: Request):
    current(request)
    auth = request.app.state.auth
    return configuration(auth, user_id(auth, name))


@router.post("/admin/api/users/{name}/storage")
def save_grant(name: str, body: Grant, request: Request):
    actor, _ = current(request, csrf=True)
    auth = request.app.state.auth
    uid = user_id(auth, name)
    with auth.store.connect(write=True) as db:
        if body.enabled and not _load(db)[0]:
            raise AuthError(422, "storage_not_configured", "请先在 R2 对象存储中保存配置")
        db.execute("INSERT INTO user_r2 VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET enabled=excluded.enabled",
                   (uid, int(body.enabled)))
    record(request, actor, "assign_r2" if body.enabled else "revoke_r2", name)
    return {"ok": True}


class Upload(Body):
    device_id: str = Field(min_length=16, max_length=128)
    revision: str = Field(pattern=r"^[a-f0-9]{32}$")
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    bytes: int = Field(ge=1, le=MAX_BYTES, strict=True)
    content_type: str = Field(max_length=80)


def sign(cfg, key, body, lifetime):
    import boto3
    from botocore.config import Config
    client = boto3.client("s3", endpoint_url=cfg["endpoint"], region_name="auto",
                          aws_access_key_id=cfg["access_key"], aws_secret_access_key=cfg["secret_key"],
                          config=Config(signature_version="s3v4", s3={"addressing_style": "path"}))
    return client.generate_presigned_url("put_object", Params={"Bucket": cfg["bucket"], "Key": key,
        "ContentType": body.content_type, "ContentLength": body.bytes}, ExpiresIn=lifetime, HttpMethod="PUT")


@router.post("/api/v1/auth/storage/sign")
def upload_ticket(body: Upload, request: Request, authorization: str = Header(default="", max_length=256)):
    from .releases import require_client
    require_client(request)
    auth = request.app.state.auth
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer" or not 40 <= len(parts[1]) <= 128:
        raise AuthError(401, "invalid_session", "请先登录")
    result = auth.verify(parts[1], body.device_id)
    uid, now = result["user"]["id"], auth.now()
    auth.rate([("r2-sign:"+uid, 3000)])
    if body.content_type not in TYPES or (body.content_type == "text/plain" and body.bytes > 1024):
        raise AuthError(422, "storage_type_invalid", "只支持参考图片、参考音频和小型连接测试")
    with auth.store.connect() as db:
        cfg, revision, enabled = _load(db)
        grant = db.execute("SELECT enabled FROM user_r2 WHERE user_id=?", (uid,)).fetchone()
        if not enabled or not grant or not grant["enabled"]:
            raise AuthError(403, "storage_denied", "账号未获 R2 上传授权或存储已停用，请联系管理员")
        if revision != body.revision:
            raise AuthError(409, "storage_changed", "后台 R2 配置已更新，请刷新后台配置后重试")
        session = db.execute("SELECT expires_at FROM sessions WHERE id=?", (result["session_id"],)).fetchone()
    lifetime = max(1, min(300, (result["user"]["expires_at"] or now+300)-now, session["expires_at"]-now))
    key = f"{cfg['prefix']}/{uid}/{revision}/{body.sha256}{TYPES[body.content_type]}"
    try:
        url = sign(cfg, key, body, lifetime)
    except Exception:
        raise AuthError(503, "storage_sign_failed", "R2 上传授权生成失败，请管理员检查服务端依赖和配置") from None
    return {"upload_url": url, "public_url": cfg["public_base_url"]+"/"+key,
            "headers": {"Content-Type": body.content_type}, "expires_at": now+lifetime,
            "bytes": body.bytes, "revision": revision}
