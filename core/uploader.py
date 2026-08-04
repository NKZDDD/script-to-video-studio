# -*- coding: utf-8 -*-
"""本地文件 → 公网 URL（走你自己的对象存储，R2 / OSS / COS / S3 / MinIO 都是这一套）。

只有这一条通道，故意不做别的兜底：
  · 服务商自带的上传端点跨站不通（各家只认自己签发的 key），而且要靠 404/401
    才能发现某家没有这个端点 —— 几百段视频光探测就白发上千次请求。
  · data URI 只有部分家接受，而且请求体会膨胀到几 MB。

配好对象存储之后，参考图统一传上去换链接：URL-only 的接口能用了，
能吃 data URI 的那些家请求体也从几 MB 缩成一行链接。

按内容哈希缓存并持久化 —— 这不是优化是必需：40 集 466 段视频接近 900 次上传，
但参考的是反复出现的那几十张资产图，缓存后同一张全剧只传一次。
"""

from __future__ import annotations

import base64
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

# 配置写错了重试多少次都一样，别浪费时间。
# endpoint 打错的典型表现是 SSL 握手失败 / 域名解析不了，也算配置问题。
_FATAL_WORDS = ("NoSuchBucket", "InvalidAccessKeyId", "SignatureDoesNotMatch",
                "AccessDenied", "AllAccessDisabled", "InvalidBucketName",
                "Could not connect to the endpoint", "EndpointConnectionError",
                "InvalidArgument", "NoSuchKey",
                "SSL validation failed", "SSLError", "Name or service not known",
                "getaddrinfo failed", "nodename nor servname")


def configured(cfg: Optional[dict]) -> bool:
    cfg = cfg or {}
    return bool((cfg.get("bucket") or "").strip()
                and (cfg.get("access_key") or "").strip()
                and (cfg.get("secret_key") or "").strip())


# ---------------------------------------------------------------- 缓存
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


# ---------------------------------------------------------------- 上传
def _client(cfg: dict):
    try:
        import boto3
        from botocore.config import Config as BotoConfig
    except ImportError:
        raise ApiError("要用对象存储得先装 boto3：pip install boto3，装完重启程序",
                       kind=TASK_FATAL)
    return boto3.client(
        "s3",
        endpoint_url=(cfg.get("endpoint") or "").strip() or None,
        region_name=(cfg.get("region") or "auto").strip(),
        aws_access_key_id=(cfg.get("access_key") or "").strip(),
        aws_secret_access_key=(cfg.get("secret_key") or "").strip(),
        config=BotoConfig(signature_version="s3v4", retries={"max_attempts": 3}),
    )


def public_url(cfg: dict, key: str) -> str:
    """拼公网访问地址。

    强烈建议填 public_base_url：R2 的 *.r2.cloudflarestorage.com 是 S3 API 域名，
    **不对外公开**，拿它拼出来的链接服务商取不到图。要填 R2 的公开域名
    （自定义域，或 pub-xxx.r2.dev）。
    """
    base = (cfg.get("public_base_url") or "").strip().rstrip("/")
    if base:
        return f"{base}/{key}"
    ep = (cfg.get("endpoint") or "").strip().rstrip("/")
    bucket = (cfg.get("bucket") or "").strip()
    if ep:
        return f"{ep}/{bucket}/{key}"
    return f"https://{bucket}.s3.amazonaws.com/{key}"


def put(cfg: dict, data: bytes, key: str) -> str:
    """上传一份字节，返回公网 URL。"""
    bucket = (cfg.get("bucket") or "").strip()
    if not bucket:
        raise ApiError("对象存储没填 bucket（设置 → 参考图上传）", kind=TASK_FATAL)
    extra = {"ContentType": mimetypes.guess_type(key)[0] or "application/octet-stream"}
    if cfg.get("public_acl"):            # R2 不支持 ACL，默认关；靠公开桶+公开域名
        extra["ACL"] = "public-read"
    try:
        _client(cfg).put_object(Bucket=bucket, Key=key, Body=data, **extra)
    except ApiError:
        raise
    except Exception as exc:             # noqa: BLE001
        msg = str(exc)
        raise ApiError(f"上传到对象存储失败：{msg}",
                       kind=TASK_FATAL if any(w in msg for w in _FATAL_WORDS) else "") from exc
    return public_url(cfg, key)


def to_url(path: str, cfg: dict, *, project_root: str = "", max_side: int = 1536,
           log: Callable = print) -> str:
    """本地图片 → 公网 URL。命中缓存就不重复上传。"""
    if not os.path.isfile(path):
        raise ApiError(f"参考图文件不存在: {path}")
    if not configured(cfg):
        raise ApiError(
            "这个模型的参考图只收公网链接，但还没配对象存储，本机的故事板传不上去。"
            "去「设置 → 参考图上传」填 R2（或 OSS/COS/MinIO）的 endpoint、bucket、"
            "密钥和公开访问域名；或者把这一类任务换成能直接收本地图的服务商。",
            kind=TASK_FATAL)

    h = _sha(path, max_side)
    hit = _cache_get(project_root, h)
    if hit:
        return hit

    # 统一压成 JPEG：省流量，也避开各家对 png 透明通道的处理差异
    data = base64.b64decode(file_to_data_uri(path, max_side=max_side).split(",", 1)[1])
    prefix = (cfg.get("prefix") or "respect").strip().strip("/")
    name = f"{os.path.splitext(os.path.basename(path))[0]}_{h[:12]}.jpg"
    key = f"{prefix}/{name}" if prefix else name

    url = put(cfg, data, key)
    log(f"已上传 {os.path.basename(path)}（{len(data)//1024}KB）→ {url}")
    _cache_put(project_root, h, url)
    return url


# ---------------------------------------------------------------- 自检
def selftest(cfg: dict, log: Callable = print) -> dict:
    """传一个小文件再用普通 HTTP 取回来，确认**服务商真的能读到**。

    只测「上传成功」是不够的：最常见的坑是桶不公开、或 public_base_url 填了
    R2 的 S3 API 域名（那个域名不对外）。上传都会成功，但服务商取图 403，
    到时候是一批任务失败，不如现在花两秒测出来。
    """
    if not configured(cfg):
        return {"ok": False, "step": "配置", "msg": "bucket / access_key / secret_key 没填全"}
    payload = b"respect-studio-uploader-selftest"
    prefix = (cfg.get("prefix") or "respect").strip().strip("/")
    key = f"{prefix}/_selftest.txt" if prefix else "_selftest.txt"
    try:
        url = put(cfg, payload, key)
    except ApiError as exc:
        return {"ok": False, "step": "上传", "msg": str(exc)}

    try:
        import requests
        r = requests.get(url, timeout=20)
    except Exception as exc:            # noqa: BLE001
        return {"ok": False, "step": "读回", "url": url,
                "msg": f"上传成功但链接打不开：{exc}。"
                       f"检查「公开访问域名」填得对不对。"}
    if r.status_code != 200:
        return {"ok": False, "step": "读回", "url": url,
                "msg": f"上传成功，但公开访问返回 {r.status_code}。"
                       f"要么桶没开公开读，要么「公开访问域名」填错了 —— "
                       f"R2 别填 *.r2.cloudflarestorage.com（那是 S3 API 域名，不对外），"
                       f"要填自定义域或 pub-xxx.r2.dev。"}
    if r.content.strip() != payload:
        return {"ok": False, "step": "读回", "url": url,
                "msg": "链接能打开但内容不对，可能是域名指到了别的桶"}
    return {"ok": True, "step": "完成", "url": url,
            "msg": "上传和公开读取都正常，可以开工"}


def cache_stats(project_root: str) -> dict:
    return {"cached": len(read_json(_cache_path(project_root), {}) or {})}
