# -*- coding: utf-8 -*-
"""本地文件 → 公网 URL。

为什么必须有这一层：越来越多接口的参考图**只收公网 HTTPS 链接**，不接受把图片
本身发过去（零视 SD2 新接口、seedance / 小裴 全家族都是）。而本程序的资产图和
故事板全是本机文件，没有这一层就只能用那几家肯收 data URI 的，URL-only 的整个
家族根本接不进来。

三级策略，从便宜到通用：
  1. 服务商自己的上传端点 —— POST {base}/v1/uploads（字段 image），/v1/upload 兜底。
     免费、key 已经有了、不用额外账号。**但跨站不通**：各家的上传端点只认自己
     签发的 key，拿 A 家的 key 去 B 家的端点上传会 401。所以要按服务商分别配。
  2. 自己的 S3 兼容对象存储（R2 / OSS / COS / S3 / MinIO）—— 需要 boto3 和一次
     性配置，但**对所有服务商都有效**，是唯一的通用解。
  3. 都没配 —— 明确报错说清去哪配，绝不悄悄退回 data URI 让服务商拒一遍。

内容哈希缓存是必需的，不是优化：40 集 466 段视频、每段 1-2 张参考图接近 900 次
上传，但参考的是反复出现的那几十张资产图。按文件内容哈希缓存并持久化到项目目录，
重跑、续跑都不会重复上传。
"""

from __future__ import annotations

import hashlib
import mimetypes
import os
import threading
from typing import Callable, Optional

from .apiutil import ApiError, TASK_FATAL, file_to_data_uri
from .store import LOCK, read_json, write_json

CACHE_NAME = "upload_cache.json"
_MEM: dict = {}
_MEM_LOCK = threading.RLock()
# 哪些服务商的上传端点已经证明不通（404/401）。零视就没有这个端点，
# 不记住的话 466 段视频会白试近 2000 次。只在进程内记，重启后重新探一次。
_NO_ENDPOINT: set = set()


def _sha(path: str, max_side: int) -> str:
    """按文件内容 + 压缩参数算 key。同一张图换了压缩尺寸要重新上传。"""
    h = hashlib.sha256()
    h.update(f"{max_side}|".encode())
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:32]


def _cache_path(project_root: str) -> str:
    return os.path.join(project_root, "07_检查与记录", CACHE_NAME)


def _cache_get(project_root: str, key: str) -> str:
    with _MEM_LOCK:
        if key in _MEM:
            return _MEM[key]
    if not project_root:
        return ""
    url = (read_json(_cache_path(project_root), {}) or {}).get(key, "")
    if url:
        with _MEM_LOCK:
            _MEM[key] = url
    return url


def _cache_put(project_root: str, key: str, url: str) -> None:
    with _MEM_LOCK:
        _MEM[key] = url
    if not project_root:
        return
    with LOCK:                      # 读-改-写整段加锁，多线程同时上传时不互相覆盖
        p = _cache_path(project_root)
        d = read_json(p, {}) or {}
        d[key] = url
        write_json(p, d)


# ---------------------------------------------------------------- 策略1：服务商端点
def via_provider(session, data: bytes, filename: str, log: Callable = print) -> str:
    """POST {base}/v1/uploads，字段名 image；/v1/upload 兜底。返回公网 URL。

    各家返回结构不统一（url / data.url / name / file_id），逐个字段找。
    """
    ct = mimetypes.guess_type(filename)[0] or "image/jpeg"
    last = ""
    for endpoint in ("/v1/uploads", "/v1/upload"):
        try:
            resp = session.request("POST", endpoint,
                                   files=[("image", (filename, data, ct))],
                                   retries=1, timeout=300)
        except ApiError as exc:
            last = str(exc)
            continue
        url = _pick_url(resp)
        if url:
            return url
        last = f"{endpoint} 返回里找不到可用的链接: {str(resp)[:200]}"
    raise ApiError(f"服务商的上传端点没能用上：{last}")


def _pick_url(data) -> str:
    """从上传返回里挑出公网链接。找不到 http 链接时，退而接受 name/file_id 这类引用。"""
    if isinstance(data, str):
        return data if data.startswith("http") else ""
    if isinstance(data, list):
        for x in data:
            u = _pick_url(x)
            if u:
                return u
        return ""
    if not isinstance(data, dict):
        return ""
    for k in ("url", "public_url", "download_url", "file_url", "image_url", "src", "link"):
        v = data.get(k)
        if isinstance(v, str) and v.startswith("http"):
            return v
    for k in ("data", "result", "file", "files", "output"):
        if k in data:
            u = _pick_url(data[k])
            if u:
                return u
    # 有些站返回的是内部引用名，提交生成任务时照样能用
    for k in ("name", "file_id", "id", "key"):
        v = data.get(k)
        if isinstance(v, str) and v and not v.isdigit():
            return v
    return ""


# ---------------------------------------------------------------- 策略2：自己的对象存储
def via_s3(cfg: dict, data: bytes, filename: str, log: Callable = print) -> str:
    """上传到 S3 兼容对象存储，返回公网 URL。对所有服务商都有效。"""
    try:
        import boto3
        from botocore.config import Config as BotoConfig
    except ImportError:
        raise ApiError("要用对象存储上传得先装 boto3：pip install boto3", kind=TASK_FATAL)

    bucket = (cfg.get("bucket") or "").strip()
    if not bucket:
        raise ApiError("对象存储没填 bucket", kind=TASK_FATAL)
    prefix = (cfg.get("prefix") or "respect").strip().strip("/")
    key = f"{prefix}/{filename}" if prefix else filename

    client = boto3.client(
        "s3",
        endpoint_url=(cfg.get("endpoint") or "").strip() or None,
        region_name=(cfg.get("region") or "auto").strip(),
        aws_access_key_id=(cfg.get("access_key") or "").strip(),
        aws_secret_access_key=(cfg.get("secret_key") or "").strip(),
        config=BotoConfig(signature_version="s3v4", retries={"max_attempts": 3}),
    )
    extra = {"ContentType": mimetypes.guess_type(filename)[0] or "image/jpeg"}
    if cfg.get("public_acl"):            # R2 不支持 ACL，默认关
        extra["ACL"] = "public-read"
    try:
        client.put_object(Bucket=bucket, Key=key, Body=data, **extra)
    except Exception as exc:             # noqa: BLE001
        # 配置写错了（桶不存在、密钥不对、域名打错）重试多少次都一样，
        # 直接判死；只有真的网络抖动才值得重试。
        msg = str(exc)
        fatal = any(w in msg for w in (
            "NoSuchBucket", "InvalidAccessKeyId", "SignatureDoesNotMatch",
            "AccessDenied", "AllAccessDisabled", "InvalidBucketName",
            "Could not connect to the endpoint", "EndpointConnectionError"))
        raise ApiError(f"上传到对象存储失败：{msg}",
                       kind=TASK_FATAL if fatal else "") from exc

    base = (cfg.get("public_base_url") or "").strip().rstrip("/")
    if base:
        return f"{base}/{key}"
    ep = (cfg.get("endpoint") or "").strip().rstrip("/")
    if ep:
        return f"{ep}/{bucket}/{key}"
    return f"https://{bucket}.s3.amazonaws.com/{key}"


# ---------------------------------------------------------------- 对外入口
def to_url(path: str, *, project_root: str = "", session=None, s3_cfg: Optional[dict] = None,
           max_side: int = 1536, provider_id: str = "", log: Callable = print) -> str:
    """本地图片 → 公网 URL。命中缓存就不重复上传。

    先试服务商自带端点（免费），失败再走自己的对象存储。两个都不行就明确报错，
    不会悄悄退回 data URI —— 那样只会让服务商拒一遍，白等一轮还看不出原因。
    """
    if not os.path.isfile(path):
        raise ApiError(f"参考图文件不存在: {path}")
    key = f"{provider_id}|{_sha(path, max_side)}"
    hit = _cache_get(project_root, key)
    if hit:
        return hit

    # 统一压成 JPEG 再传：省流量，也避免各家对 png 透明通道的处理差异
    raw = file_to_data_uri(path, max_side=max_side)
    import base64
    data = base64.b64decode(raw.split(",", 1)[1])
    filename = f"{os.path.splitext(os.path.basename(path))[0]}_{key[-12:]}.jpg"

    errs = []
    if session is not None and provider_id not in _NO_ENDPOINT:
        try:
            url = via_provider(session, data, filename, log=log)
            log(f"参考图已上传到服务商：{os.path.basename(path)} → {url[:70]}")
            _cache_put(project_root, key, url)
            return url
        except ApiError as exc:
            errs.append(f"服务商端点：{exc}")
            # 记住这家没有可用端点，后面几百张图不再白试
            with _MEM_LOCK:
                _NO_ENDPOINT.add(provider_id)
            log(f"这家没有可用的上传端点（{str(exc)[:70]}），改用自己的对象存储；"
                f"本次运行不再重试它")

    if s3_cfg and (s3_cfg.get("bucket") or "").strip():
        try:
            url = via_s3(s3_cfg, data, filename, log=log)
            log(f"参考图已上传到对象存储：{os.path.basename(path)} → {url[:70]}")
            _cache_put(project_root, key, url)
            return url
        except ApiError as exc:
            errs.append(f"对象存储：{exc}")

    raise ApiError(
        "这个模型的参考图只收公网链接，但本机图片没能传上去，"
        "所以没法把故事板交给它。" + ("原因：" + "；".join(errs) if errs else
        "目前既没有可用的服务商上传端点，也没配自己的对象存储。") +
        " 去「设置 → 参考图上传」配一个 S3 兼容的对象存储（R2/OSS/COS/MinIO 都行），"
        "或者把这一类任务换成能直接收本地图的服务商。",
        kind=TASK_FATAL)


def cache_stats(project_root: str) -> dict:
    d = read_json(_cache_path(project_root), {}) or {}
    return {"cached": len(d)}
