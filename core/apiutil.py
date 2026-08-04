# -*- coding: utf-8 -*-
"""服务商共用的 HTTP / 解析 / 落盘工具。不依赖 ComfyUI、torch。"""

from __future__ import annotations

import base64
import io
import json
import mimetypes
import os
import time
from typing import Any, Optional

import requests

DONE_STATES = ("completed", "succeeded", "success", "done", "finished", "complete", "generated")
FAIL_STATES = ("failed", "cancelled", "canceled", "error", "fail")
MEDIA_VIDEO = (".mp4", ".mov", ".webm", ".m4v", ".mkv", ".avi")
MEDIA_IMAGE = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp")
URL_KEYS = ("url", "video_url", "download_url", "file_url", "result_url", "image_url")


# ---------------------------------------------------------------- 错误分级
#
# retryable   —— 网络抖动/限流/网关错误：值得同参重试
# task_fatal  —— 这一个任务本身的问题（提示词违规、参数不合法）：重试无意义，跳过它继续跑别的
# batch_fatal —— 账户级问题（余额不足、密钥失效、封禁）：重试和继续都无意义，立即熔断整批
RETRYABLE, TASK_FATAL, BATCH_FATAL = "retryable", "task_fatal", "batch_fatal"

# 余额/鉴权类关键词（各中转站措辞不一，做宽匹配）
_BATCH_FATAL_KW = (
    "insufficient", "quota", "balance", "billing", "payment", "credit",
    "exceeded your current", "no more credit", "out of credit",
    "invalid api key", "invalid_api_key", "incorrect api key", "unauthorized",
    "authentication", "token 不正确", "令牌", "余额", "额度", "欠费",
    "无可用渠道", "账户", "已封禁", "禁用", "过期",
)
_TASK_FATAL_KW = (
    "content policy", "safety", "violat", "prohibited", "sensitive",
    "invalid prompt", "prompt too long", "unsupported", "违规", "敏感", "审核",
)


def classify(status: int, text: str = "") -> str:
    """把 HTTP 状态 + 响应体分成三级。"""
    low = (text or "").lower()
    if status in (401, 402, 403):
        return BATCH_FATAL
    if any(k in low for k in _BATCH_FATAL_KW):
        return BATCH_FATAL
    if status == 429:
        return RETRYABLE
    if status in (408, 409, 425, 500, 502, 503, 504, 0):
        return RETRYABLE
    if status == 400 or status == 422:
        return TASK_FATAL if any(k in low for k in _TASK_FATAL_KW) else TASK_FATAL
    if any(k in low for k in _TASK_FATAL_KW):
        return TASK_FATAL
    return RETRYABLE


class ApiError(RuntimeError):
    def __init__(self, message: str, status: int = 0, kind: str = "", retry_after: float = 0):
        super().__init__(message)
        self.status = status
        self.kind = kind or classify(status, message)
        self.retry_after = retry_after
        # 服务商可以往这里塞「该查什么」的清单：有些家的 400 只回一句笼统话，
        # 通用错误码给不出具体指引，只有各家自己知道该逐条比对哪些约束。
        # diagnose.build() 会把它并进「怎么改」。
        self.extra_fix: list = []


# ---------------------------------------------------------------- 响应解析

def extract_task_id(data: Any) -> str:
    if not isinstance(data, dict):
        return ""
    scopes = [data]
    inner = data.get("data")
    if isinstance(inner, dict):
        scopes.append(inner)
    for scope in scopes:
        for k in ("id", "task_id", "video_id"):
            v = scope.get(k)
            if isinstance(v, (str, int)) and str(v):
                return str(v)
    return ""


def extract_status(data: Any) -> str:
    if not isinstance(data, dict):
        return ""
    scopes = [data]
    inner = data.get("data")
    if isinstance(inner, dict):
        scopes.append(inner)
    for scope in scopes:
        for k in ("status", "state", "task_status", "job_status"):
            v = scope.get(k)
            if isinstance(v, str) and v:
                return v.lower()
    return ""


def _collect_urls(node: Any, found: list, key: str = "") -> None:
    if isinstance(node, dict):
        for k, v in node.items():
            _collect_urls(v, found, str(k))
    elif isinstance(node, (list, tuple)):
        for v in node:
            _collect_urls(v, found, key)
    elif isinstance(node, str) and node.startswith("http"):
        found.append((key, node))


def extract_video_url(data: Any) -> str:
    """媒体后缀优先，API /content 端点垫底。"""
    found: list = []
    _collect_urls(data, found)
    if not found:
        return ""

    def rank(item):
        key, url = item
        base = url.split("?", 1)[0].lower()
        score = 0
        if base.endswith(MEDIA_VIDEO):
            score -= 100
        if key in URL_KEYS:
            score -= 10
        if base.rstrip("/").endswith("/content") and not base.endswith(MEDIA_VIDEO):
            score += 50
        return score

    found.sort(key=rank)
    return found[0][1]


def extract_image_items(data: Any) -> list:
    """http URL / data URI / b64_json。"""
    items: list = []

    def walk(node: Any):
        if isinstance(node, dict):
            b64 = node.get("b64_json")
            if isinstance(b64, str) and b64:
                items.append("data:image/png;base64," + b64)
            for v in node.values():
                walk(v)
        elif isinstance(node, (list, tuple)):
            for v in node:
                walk(v)
        elif isinstance(node, str):
            if node.startswith("data:image"):
                items.append(node)
            elif node.startswith("http"):
                base = node.split("?", 1)[0].lower()
                if base.endswith(MEDIA_IMAGE) or "image" in base:
                    items.append(node)

    walk(data)
    if items:
        seen, out = set(), []
        for it in items:
            if it not in seen:
                seen.add(it)
                out.append(it)
        return out
    found: list = []
    _collect_urls(data, found)
    return [u for _, u in found]


# ---------------------------------------------------------------- 参考图

def file_to_data_uri(path: str, max_side: int = 1024, quality: int = 80) -> str:
    """本地图片 → base64 data URI（装了 Pillow 会先压缩）。"""
    try:
        from PIL import Image

        img = Image.open(path)
        if img.mode != "RGB":
            img = img.convert("RGB")
        w, h = img.size
        if max_side > 0 and max(w, h) > max_side:
            scale = max_side / float(max(w, h))
            img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality)
        raw, mime = buf.getvalue(), "image/jpeg"
    except ImportError:
        with open(path, "rb") as f:
            raw = f.read()
        mime = mimetypes.guess_type(path)[0] or "image/png"
    return f"data:{mime};base64," + base64.b64encode(raw).decode("ascii")


def resolve_ref(ref: str, project_root: str, max_side: int = 1024) -> str:
    """参考图引用 → 可入 images[] 的值。http/data 原样；本地路径转 data URI。"""
    ref = (ref or "").strip()
    if not ref:
        return ""
    if ref.startswith("http") or ref.startswith("data:"):
        return ref
    path = ref if os.path.isabs(ref) else os.path.join(project_root, ref)
    if not os.path.isfile(path):
        raise ApiError(f"参考图文件不存在: {path}")
    return file_to_data_uri(path, max_side=max_side)


# ---------------------------------------------------------------- HTTP

def _retry_after(resp) -> float:
    """读 Retry-After 头（秒或 HTTP 日期，只处理秒）。"""
    v = resp.headers.get("Retry-After") or resp.headers.get("retry-after")
    try:
        return max(0.0, float(v))
    except (TypeError, ValueError):
        return 0.0


def _body_error(data: Any) -> str:
    """HTTP 200 但 body 里藏错误的情况：提取错误文案，没有则返回空串。"""
    if not isinstance(data, dict):
        return ""
    err = data.get("error")
    if isinstance(err, dict):
        return str(err.get("message") or err.get("code") or "")
    if isinstance(err, str) and err:
        return err
    if data.get("code") not in (None, 0, "0", "success", "ok") and data.get("message"):
        return str(data.get("message"))
    return ""


class HttpSession:
    """带鉴权/分级重试的轻量会话。"""

    def __init__(self, api_key: str, base_url: str, timeout: int = 600, proxy: str = ""):
        self.api_key = (api_key or "").strip()
        self.base_url = (base_url or "").strip().rstrip("/")
        self.timeout = timeout
        self.proxy = (proxy or "").strip()

    def _headers(self, multipart: bool = False) -> dict:
        h = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
            "User-Agent": "ScriptToVideoRunner/2.0",
        }
        # 传文件时不能自己写 Content-Type：requests 要在里面填 multipart 的 boundary，
        # 手写死 application/json 会让服务端解不出 form 字段。
        if not multipart:
            h["Content-Type"] = "application/json"
        return h

    def _proxies(self) -> Optional[dict]:
        return {"http": self.proxy, "https": self.proxy} if self.proxy else None

    def request(self, method: str, path: str, *, json_body: Any = None, params: Any = None,
                files: Any = None, retries: int = 3, timeout: Optional[int] = None) -> Any:
        url = path if path.startswith("http") else self.base_url + path
        last: Optional[Exception] = None
        for attempt in range(max(1, retries)):
            try:
                resp = requests.request(
                    method, url, headers=self._headers(multipart=bool(files)),
                    json=json_body, params=params, files=files,
                    timeout=timeout or self.timeout, proxies=self._proxies(),
                )
                if resp.status_code >= 400:
                    if not resp.encoding or resp.encoding.lower() in ("iso-8859-1", "latin-1"):
                        resp.encoding = "utf-8"
                    body = resp.text[:400]
                    kind = classify(resp.status_code, body)
                    # 账户级问题（余额/密钥）立刻抛，不浪费重试次数
                    if kind == BATCH_FATAL:
                        raise ApiError(f"HTTP {resp.status_code}: {body}", resp.status_code, kind)
                    if kind == RETRYABLE and attempt < retries - 1:
                        wait = _retry_after(resp) or (2 ** attempt)
                        time.sleep(min(wait, 30))
                        continue
                    raise ApiError(f"HTTP {resp.status_code}: {body}", resp.status_code, kind,
                                   _retry_after(resp))
                data = resp.json() if resp.content else {}
                # 有的中转站 HTTP 200 但 body 里带业务错误（余额不足最常见）
                err = _body_error(data)
                if err:
                    kind = classify(200, err)
                    if kind == BATCH_FATAL:
                        raise ApiError(f"接口返回错误: {err[:400]}", 200, kind)
                return data
            except ApiError:
                raise
            except requests.RequestException as exc:
                last = exc
                if attempt < retries - 1:
                    time.sleep(2 ** attempt)
                    continue
                raise ApiError(f"网络错误: {exc}", 0, RETRYABLE) from exc
        raise ApiError(f"网络错误: {last}", 0, RETRYABLE)

    def poll(self, path_tpl: str, task_id: str, *, picker, interval: int = 8,
             timeout: int = 1800, content_path_tpl: str = "", log=print, cancel=None) -> Any:
        """轮询直到完成。picker(data) 提取结果；cancel() 返回 True 时中止。"""
        start, last_status = time.time(), ""
        while time.time() - start < timeout:
            if cancel and cancel():
                raise ApiError("用户已取消")
            try:
                data = self.request("GET", path_tpl.format(id=task_id), retries=1, timeout=60)
            except ApiError as exc:
                log(f"轮询错误(继续): {exc}")
                time.sleep(interval)
                continue
            status = extract_status(data)
            got = picker(data)
            if status and status != last_status:
                log(f"状态: {status}")
                last_status = status
            if status in FAIL_STATES:
                raise ApiError(f"任务失败: {json.dumps(data, ensure_ascii=False)[:500]}")
            done = status in DONE_STATES
            if got and (not status or done):
                return got
            if done and content_path_tpl:
                got = picker(self.request("GET", content_path_tpl.format(id=task_id),
                                          retries=1, timeout=120))
                if got:
                    return got
                raise ApiError(f"任务完成但未取到结果: {task_id}")
            if done:
                raise ApiError(f"任务完成但未取到结果: {task_id}")
            time.sleep(interval)
        raise ApiError(f"任务超时({timeout}s): {task_id}")

    def save_item(self, item: str, dest: str) -> str:
        """结果（http / data URI / 裸base64）落盘。"""
        os.makedirs(os.path.dirname(os.path.abspath(dest)) or ".", exist_ok=True)
        if item.startswith("data:"):
            with open(dest, "wb") as f:
                f.write(base64.b64decode(item.split(",", 1)[1]))
            return dest
        if item.startswith("http"):
            headers = self._headers() if item.startswith(self.base_url) else None
            r = requests.get(item, headers=headers, timeout=self.timeout,
                             proxies=self._proxies(), stream=True)
            if r.status_code >= 400 and headers is None:
                r = requests.get(item, headers=self._headers(), timeout=self.timeout,
                                 proxies=self._proxies(), stream=True)
            r.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in r.iter_content(chunk_size=1 << 20):
                    f.write(chunk)
            return dest
        with open(dest, "wb") as f:
            f.write(base64.b64decode(item))
        return dest
