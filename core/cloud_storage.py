"""Account-scoped R2 descriptors in memory; upload grants never go into job/config files."""
import copy
import hashlib
import re
import threading
from urllib.parse import urlsplit

from .apiutil import ApiError, TASK_FATAL

_lock = threading.RLock()
_manager = None
_owner = ""
_config = {}


def install(manager, owner, config):
    global _manager, _owner, _config
    config = config or {"assigned": False}  # Servers before 1.7 do not send R2 assignments.
    if not isinstance(config, dict) or not isinstance(config.get("assigned"), bool):
        raise ValueError("invalid storage configuration")
    clean = {"assigned": config["assigned"]}
    if clean["assigned"]:
        if not isinstance(config.get("available"), bool):
            raise ValueError("invalid storage configuration")
        for field in ("revision", "endpoint", "bucket", "public_base_url", "prefix"):
            if not isinstance(config.get(field), str) or len(config[field]) > 600:
                raise ValueError("invalid storage configuration")
            clean[field] = config[field]
        clean["available"] = config["available"]
        if clean["available"] and (not re.fullmatch(r"[a-f0-9]{32}", clean["revision"])
                or urlsplit(clean["endpoint"]).scheme != "https"
                or not (urlsplit(clean["endpoint"]).hostname or "").endswith(".r2.cloudflarestorage.com")
                or urlsplit(clean["public_base_url"]).scheme != "https"):
            raise ValueError("invalid storage route")
    with _lock:
        _manager, _owner, _config = manager, owner, clean


def clear(manager):
    global _manager, _owner, _config
    with _lock:
        if _manager is manager:
            _manager, _owner, _config = None, "", {}


def status():
    with _lock:
        return copy.deepcopy(_config)


def effective(local, *, allow_unavailable=False):
    local = dict(local or {})
    with _lock:
        config, owner = copy.deepcopy(_config), _owner
    if local.get("_storage_source") == "managed" and (not config.get("assigned") or local.get("_storage_owner") != owner):
        raise ApiError("此批次的后台 R2 授权已失效或账号已切换，请用原账号刷新配置后重试", kind=TASK_FATAL)
    if not config.get("assigned"):
        return local
    if not config.get("available") and not allow_unavailable:
        raise ApiError("后台 R2 已停用或配置不完整，请联系管理员后刷新后台配置", kind=TASK_FATAL)
    return {k: config[k] for k in ("endpoint", "bucket", "public_base_url", "prefix", "revision")} | {
        "_storage_source": "managed", "_storage_owner": owner, "mode": "always"}


def upload(config, data, content_type):
    config = effective(config)
    with _lock:
        manager, owner = _manager, _owner
    if not manager or config.get("_storage_owner") != owner:
        raise ApiError("请登录并刷新后台 R2 配置", kind=TASK_FATAL)
    body = {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data),
            "content_type": content_type, "revision": config["revision"]}
    try:
        ticket = manager.storage_ticket(body, owner)
    except Exception as exc:
        from .studio_login import LoginRequired
        if isinstance(exc, LoginRequired):
            raise ApiError(str(exc), kind=TASK_FATAL if exc.status in (401, 403, 426) else "") from None
        raise ApiError("无法获取后台 R2 上传授权，请刷新后台配置后重试") from None
    try:
        signed, public = urlsplit(ticket["upload_url"]), urlsplit(ticket["public_url"])
        endpoint = urlsplit(config["endpoint"])
        base = config["public_base_url"].rstrip("/")+"/"+config["prefix"].strip("/")+"/"
        if (signed.scheme != "https" or signed.netloc != endpoint.netloc or signed.username or signed.password
                or signed.fragment or not signed.query or not ticket["public_url"].startswith(base)
                or public.query or public.fragment or ticket["revision"] != config["revision"]
                or ticket["bytes"] != len(data) or ticket["headers"] != {"Content-Type": content_type}):
            raise ValueError()
    except (KeyError, TypeError, ValueError):
        raise ApiError("后台返回了无效的 R2 上传地址，请联系管理员", kind=TASK_FATAL) from None
    # Recheck account state before sending bytes; never attach the login token to R2.
    with _lock:
        if _manager is not manager or _owner != owner or _config.get("revision") != config["revision"]:
            raise ApiError("上传期间账号或 R2 配置已改变，请重新操作", kind=TASK_FATAL)
    import requests
    try:
        response = requests.put(ticket["upload_url"], data=data, headers=ticket["headers"],
                                timeout=(15, 90), allow_redirects=False)
        code = response.status_code
        response.close()
    except Exception:
        # requests exceptions include presigned URLs: do not log them or persist them in failures.
        raise ApiError("直传 R2 连接中断，请重试；已有生成任务不会因此重新提交") from None
    if not 200 <= code < 300:
        raise ApiError(f"R2 上传返回 HTTP {code}，请刷新后台配置并检查公开访问设置",
                       kind=TASK_FATAL if code in (400, 403, 404) else "")
    return ticket["public_url"]
