# -*- coding: utf-8 -*-
"""XMD 视频：用户提供的《视频生成 API 接入说明》（2026-09-18）。

使用绝对导入，同一文件也能放进数据目录/providers，供已打包的 exe 加载。
10 MB 是这家上传接口的限制，只处理上传副本，不改原图或其它服务商。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import math
import re
import secrets
import time
from urllib.parse import quote, urlsplit

import requests
from PIL import Image, ImageOps

from core.apiutil import ApiError, BATCH_FATAL, HttpSession, RETRYABLE, TASK_FATAL
from core.providers.base import Provider, VideoTask

MODEL = "seedance_2.5"
RATIOS = ["1:1", "3:4", "4:3", "9:16", "16:9", "21:9"]
# 文档没有说明 MB/MiB，按较小的十进制上限保证不超过任一种解释。
MAX_IMAGE_BYTES = 10_000_000
MAX_REFS = 10
_FORMATS = {"JPEG": ("jpg", "image/jpeg"), "PNG": ("png", "image/png"),
            "WEBP": ("webp", "image/webp")}


def parse_credentials(raw: str) -> dict:
    raw = (raw or "").strip()
    if raw.startswith("{"):
        try:
            obj = json.loads(raw)
        except ValueError:
            obj = {}
        return {k: str(obj.get(k) or "").strip() for k in ("api_key", "api_secret")}
    parts = (p.partition("=") for p in re.split(r"[;\r\n]+", raw))
    return {k.strip(): v.strip() for k, sep, v in parts
            if sep and k.strip() in ("api_key", "api_secret")}


def prepare_image(raw: bytes, index: int, log=print) -> tuple:
    """返回 requests 的 file 元组；合规图字节不变，超限图保留格式和透明度。"""
    try:
        with Image.open(io.BytesIO(raw)) as opened:
            fmt = opened.format
            if fmt not in _FORMATS:
                raise ValueError("只支持 JPEG、PNG、WebP，请先把参考图另存为这些格式")
            if getattr(opened, "n_frames", 1) != 1:
                raise ValueError("参考图必须是静态图片，请先导出需要的一帧")
            opened.load()
            ext, mime = _FORMATS[fmt]
            if len(raw) <= MAX_IMAGE_BYTES:
                return f"ref_{index}.{ext}", raw, mime
            original_size = opened.size
            # 重编码会去掉 EXIF，先应用方向，防止竖图变横图。
            img = ImageOps.exif_transpose(opened)
            has_alpha = "A" in img.getbands() or "transparency" in img.info
            img = img.convert("RGBA" if has_alpha and fmt != "JPEG" else "RGB")
        while True:
            qualities = (95, 90, 85, 80) if fmt != "PNG" else (None,)
            for quality in qualities:
                buf = io.BytesIO()
                opts = {"optimize": True} if fmt == "PNG" else {"quality": quality}
                if fmt == "JPEG":
                    opts["optimize"] = True
                img.save(buf, format=fmt, **opts)
                data = buf.getvalue()
                if len(data) <= MAX_IMAGE_BYTES:
                    log(f"参考图 {index} 超过 10MB：上传副本 {len(raw) / 1e6:.2f}MB → "
                        f"{len(data) / 1e6:.2f}MB，{original_size[0]}×{original_size[1]} → "
                        f"{img.width}×{img.height}；原图未修改")
                    return f"ref_{index}.{ext}", data, mime
            if img.size == (1, 1):
                raise ValueError("压缩后的图片仍超过上传上限，请手动另存为更小的参考图")
            scale = min(0.85, math.sqrt(MAX_IMAGE_BYTES / len(data)) * 0.9)
            img = img.resize((max(1, int(img.width * scale)),
                              max(1, int(img.height * scale))), Image.Resampling.LANCZOS)
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise ApiError(f"XMD 参考图 {index} 无法上传：{exc}", kind=TASK_FATAL) from exc


def _check_cancel(cancel):
    if cancel and cancel():
        raise ApiError("用户已取消", kind=TASK_FATAL)


def _wait(seconds, cancel=None):
    end = time.monotonic() + max(0, seconds)
    while time.monotonic() < end:
        _check_cancel(cancel)
        time.sleep(min(0.5, max(0, end - time.monotonic())))


class _TransferSession(HttpSession):
    def _headers(self, multipart=False):
        # 老 exe 的模型刷新直接取 session 的头；禁止把整段 Key+Secret 当 Bearer 发出。
        raise ApiError("XMD 使用文档中的固定模型 seedance_2.5，没有 /v1/models 接口；"
                       "请使用测试连通性检查凭据", kind=TASK_FATAL)

    def _download_headers(self):
        return {"Accept": "*/*", "User-Agent": "ScriptToVideoRunner/2.0"}


class XmdProvider(Provider):
    id = "xmd"
    name = "XMD 视频（ai.xmdomain.dpdns.org）"
    default_base_url = "https://ai.xmdomain.dpdns.org"
    supports = ("video",)
    ref_mode = "bytes"

    def __init__(self, api_key="", base_url="", proxy="", timeout=900):
        # 下载公共素材/成片的 session 不持有签名凭据，防止将 Secret 发给 CDN。
        self.session = _TransferSession("", base_url or self.default_base_url, timeout, proxy)
        self.creds = parse_credentials(api_key)

    def capabilities(self):
        return {
            "id": self.id, "name": self.name, "supports": list(self.supports),
            "default_base_url": self.default_base_url,
            "video": {
                "models": [MODEL], "default_model": MODEL,
                "durations": [30], "default_duration": 30, "ratios": RATIOS,
                "max_refs": MAX_REFS, "max_prompt_chars": 3000, "ref_mode": "bytes",
                "poll_interval": 15, "poll_timeout": 2400,
                "model_options": {MODEL: {"durations": [30], "ratios": RATIOS,
                                          "max_refs": MAX_REFS}},
                "notes": "仅支持 30 秒视频，提示词最多 3000 字符，参考图最多 10 张。"
                         "单图超过 10MB 时压缩上传副本，保留本地原图。"
                         "真人照片需先在服务商网页图库过审；普通参考图入口不代办过审。",
            },
            "notes": "API Key 框填一整行：api_key=你的Key;api_secret=你的Secret。"
                     "两项都必填；测试连通性只查剩余次数，不生成视频。",
        }

    def list_models(self):
        return [MODEL]

    def _headers(self, method, path, body=""):
        if not all(self.creds.get(k) for k in ("api_key", "api_secret")):
            raise ApiError("XMD 凭据不全：请在设置的 API Key 框填写 "
                           "api_key=你的Key;api_secret=你的Secret", kind=BATCH_FATAL,
                           err_code="invalid_api_key")
        nonce, ts = secrets.token_hex(8), str(int(time.time()))
        message = method.upper() + urlsplit(path).path + body + nonce + ts
        sig = hmac.new(self.creds["api_secret"].encode("utf-8"),
                       message.encode("utf-8"), hashlib.sha256).hexdigest()
        return {"X-Api-Key": self.creds["api_key"], "X-Timestamp": ts,
                "X-Nonce": nonce, "X-Sign": sig, "Accept": "application/json"}

    def _error(self, data, status=0, *, task=False):
        code = str(data.get("code") or "")
        msg = str(data.get("message") or "服务商未给出错误说明")
        # 上游意外回显鉴权字段时也不写进日志。
        for secret in self.creds.values():
            if secret:
                msg = msg.replace(secret, "[已隐藏]")
        kind, tag = TASK_FATAL, code
        if task:
            if code == "40001":
                tag = "content_policy_violation"
            elif code in ("40002", "40003"):
                msg += "；参考图不可用，真人照片请先到服务商网页图库过审后使用 gallery_ids"
            elif code in ("50000", "50001"):
                kind = RETRYABLE
        elif code.startswith("401") or status in (401, 403):
            kind, tag = BATCH_FATAL, "invalid_api_key"
            if code == "40101":
                msg += "；请校准电脑时间（与服务器误差不能超过 300 秒）"
            elif code == "40106":
                msg += "；同一 IP 鉴权失败过多，请等待 15 分钟再试"
        elif code == "40201" or status == 402:
            kind, tag = BATCH_FATAL, "insufficient_quota"
        elif code in ("42901", "42902", "50311", "50500") or status == 429 or status >= 500:
            kind, tag = RETRYABLE, "rate_limit_exceeded" if code != "50500" else "server_error"
        elif code == "40003":
            msg += "；参考图上传地址已过期，请重新上传"
        elif code in ("40004", "40402"):
            msg += "；请在服务商网页图库核对编号和过审状态"
        try:
            retry_after = max(0, float(data.get("retry_after") or 0))
        except (TypeError, ValueError):
            retry_after = 0
        return ApiError(f"XMD {'任务失败' if task else '接口错误'} {code}: {msg}",
                        status=status, kind=kind, retry_after=retry_after, err_code=tag)

    def _request(self, method, path, *, obj=None, files=None, retries=3, cancel=None):
        # 序列化一次：签名和发送必须是同一份 UTF-8 字节。
        body = json.dumps(obj, ensure_ascii=False, separators=(",", ":")) if obj is not None else ""
        for attempt in range(max(1, retries)):
            _check_cancel(cancel)
            headers = self._headers(method, path, "" if files else body)
            if obj is not None:
                headers["Content-Type"] = "application/json"
            try:
                with requests.request(method, self.session.base_url + path, headers=headers,
                                      data=body.encode("utf-8") if obj is not None else None,
                                      files=files, timeout=(15, 120 if files else 60),
                                      proxies=self.session._proxies(), allow_redirects=False) as resp:
                    try:
                        data = resp.json()
                    except ValueError:
                        data = {"message": "服务商没有返回 JSON"}
                    if not isinstance(data, dict):
                        data = {"message": "服务商返回格式错误"}
                    if resp.status_code == 200 and data.get("code") in (0, "0"):
                        return data
                    err = self._error(data, resp.status_code)
            except requests.RequestException as exc:
                # 不输出请求内容/鉴权头。
                err = ApiError(f"XMD 接口网络错误：{type(exc).__name__}", kind=RETRYABLE)
            if err.kind != RETRYABLE or attempt >= retries - 1:
                raise err
            _wait(err.retry_after or 2 ** attempt, cancel)
        raise RuntimeError("unreachable")

    def selftest(self):
        try:
            data = self._request("GET", "/v1/me", retries=1)
            return {"ok": True, "msg": f"XMD 鉴权通过，剩余次数：{data.get('quota_remaining', '未返回')}"}
        except ApiError as exc:
            return {"ok": False, "msg": str(exc)}

    def _upload(self, ref, index, *, log, cancel):
        _check_cancel(cancel)
        try:
            if ref.startswith(("http://", "https://")):
                # 公网图也经过同一个大小检查，不靠扩展名或 Content-Length 猜大小。
                with requests.get(ref, timeout=(15, 30), proxies=self.session._proxies()) as resp:
                    resp.raise_for_status()
                    raw = resp.content
            elif ref.startswith("data:"):
                head, sep, payload = ref.partition(",")
                if not sep or ";base64" not in head:
                    raise ValueError("参考图 data URI 必须使用 base64")
                raw = base64.b64decode(payload, validate=True)
            else:
                with open(ref, "rb") as f:
                    raw = f.read()
        except (OSError, ValueError, requests.RequestException) as exc:
            raise ApiError(f"XMD 参考图 {index} 读取失败（{type(exc).__name__}），请检查来源",
                           kind=TASK_FATAL) from exc
        item = prepare_image(raw, index, log)
        data = self._request("POST", "/v1/images", files={"file": item}, cancel=cancel)
        url = data.get("url")
        if not isinstance(url, str) or not url.startswith(("https://", "http://")):
            raise ApiError(f"XMD 参考图 {index} 上传成功但没有返回有效地址", kind=TASK_FATAL)
        log(f"参考图 {index} 已上传（{len(item[1]) / 1e6:.2f}MB）")
        return url

    def generate_video(self, task: VideoTask, dest: str, *, log=print, cancel=None,
                       poll_interval=15, poll_timeout=2400):
        _check_cancel(cancel)
        self._headers("GET", "/v1/me")  # 本地检查凭据，验证完成前不上传/建单。
        if not task.prompt.strip() or len(task.prompt) > 3000:
            raise ApiError("XMD 提示词必须为 1–3000 字符，请缩短本段提示词后重试", kind=TASK_FATAL)
        if (task.model or MODEL) != MODEL or task.duration != 30:
            raise ApiError("XMD 仅支持 seedance_2.5、30 秒；请将分段视频时长设为 30 秒后重试",
                           kind=TASK_FATAL)
        if task.ratio and task.ratio not in RATIOS:
            raise ApiError(f"XMD 不支持该比例，请改为：{' / '.join(RATIOS)}", kind=TASK_FATAL)
        extra = task.extra or {}
        gallery = extra.get("gallery_ids") or []
        if not isinstance(gallery, list) or any(type(g) is not int or g <= 0 for g in gallery):
            raise ApiError("XMD gallery_ids 必须为正整数数组", kind=TASK_FATAL)
        if len(task.refs) + len(gallery) > MAX_REFS:
            raise ApiError("XMD 图片和图库参考合计最多 10 张，请减少参考图后重试", kind=TASK_FATAL)
        if "shield" in extra and type(extra["shield"]) is not bool:
            raise ApiError("XMD shield 必须为 true 或 false", kind=TASK_FATAL)
        if extra.get("video_refs") or extra.get("audio_refs"):
            raise ApiError("XMD 接口只支持图片参考，请移除视频/音频参考", kind=TASK_FATAL)
        obj = {"prompt": task.prompt, "model": MODEL, "duration": 30,
               "client_job_id": "stv-" + secrets.token_hex(16)}
        if task.ratio:
            obj["ratio"] = task.ratio
        if gallery:
            obj["gallery_ids"] = gallery
        if "shield" in extra:
            obj["shield"] = extra["shield"]
        if task.refs:
            obj["images"] = [self._upload(ref, i, log=log, cancel=cancel)
                             for i, ref in enumerate(task.refs, 1)]
        try:
            data = self._request("POST", "/v1/videos", obj=obj, cancel=cancel)
        except ApiError as exc:
            # 网络重试已用同一个订单号发过，仍无回执时不能立即创建另一个新订单。
            if exc.kind == RETRYABLE and not exc.status:
                raise ApiError(f"XMD 提交回执未取得，订单号 {obj['client_job_id']}；"
                               "请先让服务商核对是否已建单，再决定是否重新提交",
                               kind=TASK_FATAL) from exc
            raise
        task_id = data.get("job_id")
        if not isinstance(task_id, str) or not task_id:
            raise ApiError("XMD 提交后未返回 job_id，请先到服务商网页核对是否已建单", kind=TASK_FATAL)
        log(f"XMD 视频任务 {task_id} 已提交，开始轮询")
        deadline, last_status = time.monotonic() + poll_timeout, ""
        while time.monotonic() < deadline:
            _check_cancel(cancel)
            try:
                data = self._request("GET", "/v1/videos/" + quote(task_id, safe=""),
                                     retries=1, cancel=cancel)
            except ApiError as exc:
                if exc.kind != RETRYABLE:
                    raise
                log(f"XMD 任务 {task_id} 查询暂时失败，继续查询同一任务：{exc}")
                _wait(min(max(10, poll_interval, exc.retry_after),
                          max(0, deadline - time.monotonic())), cancel)
                continue
            status = data.get("status")
            if status != last_status:
                log(f"XMD 状态: {status}")
                last_status = status
            if status == "failed":
                error = data.get("error") or {}
                raise self._error(error if isinstance(error, dict) else {"message": str(error)}, task=True)
            if status == "success":
                url = data.get("video_url")
                if not isinstance(url, str) or not url.startswith(("https://", "http://")):
                    raise ApiError(f"XMD 任务 {task_id} 已成功，但没有有效视频地址，请到服务商网页取结果",
                                   kind=TASK_FATAL, err_code="result_download_failed")
                _check_cancel(cancel)
                log("XMD 生成已完成，开始下载视频")
                self.session.save_item(url, dest, log=log)
                return {"task_id": task_id, "source": url}
            if status not in ("queued", "running"):
                raise ApiError(f"XMD 任务 {task_id} 返回未识别状态 {status!r}，请核对服务商任务页",
                               kind=TASK_FATAL)
            _wait(min(max(10, poll_interval), max(0, deadline - time.monotonic())), cancel)
        raise ApiError(f"XMD 任务 {task_id} 等待结果超过 {poll_timeout} 秒；"
                       "请先到服务商网页核对结果，再决定是否重新提交", kind=TASK_FATAL)
