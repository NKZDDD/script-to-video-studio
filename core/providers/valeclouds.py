# -*- coding: utf-8 -*-
"""NY / ValeClouds（valeclouds.com）。

统一入口 `https://valeclouds.com/v1`。图片沿用标准 OpenAI 图片协议；
视频是「免费报价 → 带 quote_id 创建 → 轮询 → /content 取片」三段式，
创建必须带 `Idempotency-Key`，否则超时重试会重复扣费。

两个坑是文档明确写出来的，别绕开：
  1. 视频参数必须来自同一个 profile（时长/比例/清晰度是一组完整组合），
     把 480p 的档位和 720p 的时长拼起来，服务端会直接拒绝。
  2. 参考素材要么是本站 `/v1/video/assets/*` 上传后的 URL，要么是外部
     公网 HTTPS 链接；本机路径和 data URI 不会自己变 URL，必须先上传。
"""

from __future__ import annotations

import base64
import io
import uuid
from pathlib import Path
from typing import Callable, Optional

import requests

from ..apiutil import (ApiError, DONE_STATES, TASK_FATAL, extract_image_items,
                       extract_status, extract_task_id, extract_video_url)
from .base import ImageTask, Provider, VideoTask

# 目录里能出片的视频模型（超分/去字幕这类「编辑」模型不在生产链路里用）。
# 下面括号里依次是：清晰度档、比例、图片上限、音频上限、视频上限。
VIDEO_MODELS = [
    "seedance-len", "seedance-2.5-official", "seedance2.0-official",
    "seedance2.0-fast-official", "seedance2.0-mini-official", "seedance-2.0-len",
    "Seedance2.5ny2", "Seedance2.0ny2", "seedance20-720p-deal",
    "奥特曼h3-768p（特惠版）", "sd25pp-15s",
]
_SIX = ["16:9", "9:16", "1:1", "4:3", "3:4", "21:9"]
_VIDEO_OPTIONS = {
    "seedance-len": {"resolutions": ["480p", "720p"], "ratios": ["16:9", "9:16", "1:1"],
                     "max_refs": 30, "max_audio_refs": 10, "max_video_refs": 0},
    "seedance-2.5-official": {"resolutions": ["480p", "720p"], "ratios": _SIX,
                              "max_refs": 30, "max_audio_refs": 10, "max_video_refs": 0},
    "seedance2.0-official": {"resolutions": ["480p", "720p", "1080p", "4k"],
                             "ratios": _SIX + ["auto"],
                             "max_refs": 9, "max_audio_refs": 3, "max_video_refs": 0},
    "seedance2.0-fast-official": {"resolutions": ["480p", "720p"],
                                  "ratios": _SIX + ["auto"],
                                  "max_refs": 9, "max_audio_refs": 3, "max_video_refs": 3},
    "seedance2.0-mini-official": {"resolutions": ["480p", "720p"], "ratios": _SIX,
                                  "max_refs": 9, "max_audio_refs": 3, "max_video_refs": 0},
    "seedance-2.0-len": {"resolutions": ["480p", "720p"], "ratios": ["16:9", "9:16", "1:1"],
                         "max_refs": 9, "max_audio_refs": 3, "max_video_refs": 0},
    "Seedance2.5ny2": {"resolutions": ["480p", "720p", "1080p"], "ratios": _SIX,
                       "max_refs": 30, "max_audio_refs": 10, "max_video_refs": 10},
    "Seedance2.0ny2": {"resolutions": ["480p", "720p", "1080p"], "ratios": _SIX,
                       "max_refs": 9, "max_audio_refs": 3, "max_video_refs": 3},
    "seedance20-720p-deal": {"resolutions": ["720p"], "ratios": _SIX,
                             "max_refs": 9, "max_audio_refs": 3, "max_video_refs": 0},
    "奥特曼h3-768p（特惠版）": {"resolutions": ["768p"],
                                "ratios": ["16:9", "9:16", "1:1", "4:3", "3:4", "3:2", "2:3", "21:9"],
                                "max_refs": 9, "max_audio_refs": 3, "max_video_refs": 0},
    "sd25pp-15s": {"resolutions": ["1080p"], "ratios": _SIX,
                   "max_refs": 30, "max_audio_refs": 10, "max_video_refs": 10},
}

IMAGE_MODELS = ["gpt-image-2.5-sunburst", "gpt-image-2.5-flare",
                "image2_4k", "image2_1k", "grok-imagine-image-2.0"]
IMAGE_SIZES = ["1024x1536", "1024x1024", "1536x1024", "2048x2048"]

_MIME_BY_EXT = {"mp4": "video/mp4", "mov": "video/quicktime", "webm": "video/webm",
                "mp3": "audio/mpeg", "wav": "audio/wav", "m4a": "audio/mp4"}


def _remote(ref: str) -> bool:
    return str(ref or "").startswith(("http://", "https://"))


def _payload(ref: str, index: int, kind: str = "image") -> tuple:
    """把 data URI / 本机路径读成 (字节, MIME, 文件名)。图片顺带验一遍能否解码。"""
    ref = str(ref or "")
    try:
        if ref.startswith("data:"):
            head, body = ref.split(",", 1)
            if ";base64" not in head.lower():
                raise ValueError("不是 Base64")
            raw = base64.b64decode("".join(body.split()), validate=True)
            mime = head[5:].split(";")[0] or "application/octet-stream"
        else:
            path = Path(ref)
            raw = path.read_bytes()
            mime = _MIME_BY_EXT.get(path.suffix.lower().lstrip("."), "application/octet-stream")
        if not raw:
            raise ValueError("文件为空")
        if kind == "image":
            from PIL import Image
            with Image.open(io.BytesIO(raw)) as im:
                fmt = im.format
                im.verify()
            with Image.open(io.BytesIO(raw)) as im:
                im.load()
            ext, mime = {"PNG": ("png", "image/png"), "JPEG": ("jpg", "image/jpeg"),
                         "WEBP": ("webp", "image/webp")}[fmt]
        else:
            ext = {"video/mp4": "mp4", "video/quicktime": "mov", "video/webm": "webm",
                   "audio/mpeg": "mp3", "audio/wav": "wav", "audio/mp4": "m4a"}.get(mime, "bin")
        label = {"image": "参考图", "video": "参考视频", "audio": "参考音频"}[kind]
    except requests.RequestException as exc:
        raise ApiError(f"NY 第 {index} 个参考素材下载失败：{type(exc).__name__}") from exc
    except Exception as exc:                              # noqa: BLE001
        raise ApiError(f"NY 第 {index} 个参考素材无法读取（data URI / 本机文件）；"
                       f"未提交生成：{exc}", kind=TASK_FATAL) from exc
    return raw, mime, f"{kind}-{index}.{ext}"


class ValeCloudsProvider(Provider):
    id = "valeclouds"
    name = "NY valeclouds.com"
    aliases = ("ny", "NY", "vale", "valeclouds")
    default_base_url = "https://valeclouds.com"
    supports = ("image", "video")
    ref_mode = "data_uri"

    def __init__(self, api_key: str = "", base_url: str = "", proxy: str = "", timeout: int = 900):
        base = (base_url or self.default_base_url).strip().rstrip("/")
        if base.endswith("/v1"):
            base = base[:-3]
        super().__init__(api_key, base, proxy, timeout)

    def capabilities(self) -> dict:
        return {
            "id": self.id, "name": self.name, "default_base_url": self.default_base_url,
            "supports": list(self.supports),
            "image": {
                "models": IMAGE_MODELS, "default_model": "gpt-image-2.5-sunburst",
                "sizes": IMAGE_SIZES, "default_size": "1024x1536",
                "max_refs": 9, "ref_mode": "data_uri",
                "notes": "标准 OpenAI 图片协议（size 传像素尺寸）；带参考图走 multipart /v1/images/edits。"
                         "图片模型与可用尺寸以实时目录为准。",
            },
            "video": {
                "models": VIDEO_MODELS, "default_model": "seedance-2.5-official",
                "ratios": _SIX + ["3:2", "2:3", "auto"],
                # 不在本地收敛秒数：档位随模型和渠道变化，按模型名猜只会把能用的秒数改坏。
                "durations": list(range(1, 61)), "default_duration": 10,
                "resolutions": ["", "480p", "720p", "768p", "1080p", "4k"],
                "max_refs": 30, "ref_mode": "data_uri",
                "model_options": _VIDEO_OPTIONS,
                "notes": "先 POST /v1/video/quotes 报价，再用同一份参数 + quote_id 创建；"
                         "参数必须来自同一档位（时长/比例/清晰度不能跨档拼）。"
                         "参考素材先上传 /v1/video/assets/* 换 URL，或直接给公网 HTTPS 链接。",
            },
            "notes": "本站基于 New API；模型可用性以 GET /v1/models 和 /v1/video/catalog 为准。",
        }

    # ------------------------------------------------------------- 素材
    def _asset(self, ref: str, kind: str, index: int, log: Callable) -> str:
        """把本机 / data URI 素材换成本站 URL；本来就是公网链接的原样用。"""
        if _remote(ref):
            return ref
        raw, mime, name = _payload(ref, index, kind)
        data = self.session.request("POST", f"/v1/video/assets/{kind}", raw_body=raw,
                                    headers={"Content-Type": mime}, retries=2,
                                    timeout=180, submit=False)
        url = str((data or {}).get("url") or "")
        if not url:
            raise ApiError(f"NY 第 {index} 个{kind}素材上传后没有拿到 URL：{str(data)[:200]}",
                           kind=TASK_FATAL)
        log(f"NY 素材已上传：{name} → {url[:60]}…")
        return url

    # ------------------------------------------------------------- 视频
    def _result_url(self, data, task_id: str) -> str:
        """成片的地址。

        2026-11-01 用真任务（vcr1_98c62f87…）实查：查询响应里**只有**
        status/progress/billing 这些字段，一个结果 URL 都没有；成片在
        `GET /v1/videos/{id}/content`（直接回 mp4 二进制）。
        以前这里只等 extract_video_url，于是状态已经 completed 也取不到地址，
        报「任务完成但未取到结果」，而任务 ID 被一直保留、续查永远失败。
        """
        url = extract_video_url(data)
        if url:
            return url
        if extract_status(data) in DONE_STATES:
            return f"{self.session.base_url}/v1/videos/{extract_task_id(data) or task_id}/content"
        return ""

    def generate_video(self, task: VideoTask, dest: str, *, log: Callable = print,
                       cancel: Optional[Callable] = None,
                       poll_interval: int = 10, poll_timeout: int = 2400) -> dict:
        model = (task.model or "seedance-2.5-official").strip()
        if not (task.prompt or "").strip():
            raise ApiError("NY 视频提示词为空", kind=TASK_FATAL)
        if cancel and cancel():
            raise ApiError("已取消", kind=TASK_FATAL)
        refs = list(task.refs or [])
        videos = list(task.extra.get("video_refs") or task.extra.get("videos") or [])
        audios = list(task.extra.get("audio_refs") or task.extra.get("audios") or [])
        limit = _VIDEO_OPTIONS.get(model) or {}
        for got, cap, label in ((refs, limit.get("max_refs"), "参考图"),
                                (videos, limit.get("max_video_refs"), "参考视频"),
                                (audios, limit.get("max_audio_refs"), "参考音频")):
            if cap is not None and len(got) > cap:
                raise ApiError(f"NY {model} 最多 {cap} 个{label}，本任务有 {len(got)} 个；"
                               "不能静默丢素材，否则人物/运镜绑定会丢失", kind=TASK_FATAL)

        body: dict = {"model": model, "prompt": task.prompt,
                      "duration": max(1, int(task.duration or 10))}
        if task.ratio:
            body["ratio"] = task.ratio
        if task.resolution:
            body["resolution"] = task.resolution.lower()
        if refs:
            body["referenceImages"] = [self._asset(r, "image", i, log) for i, r in enumerate(refs, 1)]
        if videos:
            body["referenceVideos"] = [self._asset(r, "video", i, log) for i, r in enumerate(videos, 1)]
        if audios:
            body["referenceAudios"] = [self._asset(r, "audio", i, log) for i, r in enumerate(audios, 1)]
        if cancel and cancel():
            raise ApiError("已取消", kind=TASK_FATAL)

        log(f"NY {model}: {body['duration']}s {body.get('ratio', '默认比例')} "
            f"{body.get('resolution', '默认清晰度')} 图{len(refs)}/视频{len(videos)}/音频{len(audios)}")
        # 报价是免费的；创建必须带报价单号，参数与报价时完全一致。
        quote = self.session.request("POST", "/v1/video/quotes", json_body=body,
                                     retries=1, timeout=120)
        quote_id = str((quote or {}).get("id") or "")
        if not quote_id:
            raise ApiError(f"NY 报价没有返回 quote_id：{str(quote)[:300]}", kind=TASK_FATAL)
        create = dict(body, quote_id=quote_id)
        data = self.session.request("POST", "/v1/videos", json_body=create,
                                    headers={"Idempotency-Key": uuid.uuid4().hex},
                                    retries=1, timeout=300)
        task_id = extract_task_id(data)
        url = extract_video_url(data)
        if not url:
            if not task_id:
                raise ApiError(f"NY 创建任务没有返回任务号：{str(data)[:300]}")
            url = self.session.poll("/v1/videos/{id}", task_id,
                                    picker=lambda d: self._result_url(d, task_id),
                                    interval=poll_interval, timeout=poll_timeout,
                                    log=log, cancel=cancel)
        self.session.save_item(url, dest, log=log)
        return {"task_id": task_id, "source": url, "provider": self.id, "model": model}

    def resume(self, record, dest: str, *, log: Callable = print,
               cancel: Optional[Callable] = None,
               poll_interval: int = 10, poll_timeout: int = 2400) -> dict:
        """续查已提交的任务。

        成片只能从 `/content` 取（见 `_result_url`），所以不能走基类的
        「重放 extract_video_url」那条路 —— 那条对这家永远取不到结果。
        """
        task_id = str(record.get("task_id") or "")
        url = self.session.poll("/v1/videos/{id}", task_id,
                                picker=lambda d: self._result_url(d, task_id),
                                interval=poll_interval, timeout=poll_timeout,
                                log=log, cancel=cancel)
        self.session.save_item(url, dest, log=log)
        return {"task_id": task_id, "source": str(url)[:200], "provider": self.id,
                "model": record.get("model", ""), "resumed": True}

    # ------------------------------------------------------------- 图片
    def _image_bytes(self, ref: str, index: int) -> tuple:
        if _remote(ref):
            try:
                with requests.get(ref, timeout=(20, 60), proxies=self.session._proxies()) as response:
                    response.raise_for_status()
                    raw = response.content
            except requests.RequestException as exc:
                raise ApiError(f"NY 第 {index} 张参考图下载失败：{type(exc).__name__}") from exc
            return _payload("data:image/png;base64," + base64.b64encode(raw).decode(), index, "image")
        return _payload(ref, index, "image")

    def generate_image(self, task: ImageTask, dest: str, *, log: Callable = print,
                       cancel: Optional[Callable] = None,
                       poll_interval: int = 5, poll_timeout: int = 900) -> dict:
        model = task.model or "gpt-image-2.5-sunburst"
        if not (task.prompt or "").strip():
            raise ApiError("NY 图片提示词为空", kind=TASK_FATAL)
        if task.n != 1:
            raise ApiError("Studio 一个图片任务对应一个输出文件，请每次生成 1 张", kind=TASK_FATAL)
        if cancel and cancel():
            raise ApiError("已取消", kind=TASK_FATAL)
        size = task.size or "1024x1536"
        files = []
        for index, ref in enumerate(task.refs or [], 1):
            raw, mime, name = self._image_bytes(ref, index)
            files.append(("image", (name, raw, mime)))
        log(f"NY 图片 {model}: size={size}，参考图 {len(files)} 张")
        if files:
            form = [("model", model), ("prompt", task.prompt), ("n", "1"), ("size", size)]
            data = self.session.request("POST", "/v1/images/edits", files=form + files,
                                        retries=1, timeout=600)
        else:
            data = self.session.request("POST", "/v1/images/generations",
                                        json_body={"model": model, "prompt": task.prompt,
                                                   "n": 1, "size": size},
                                        retries=1, timeout=600)
        items = extract_image_items(data)
        if not items:
            task_id = extract_task_id(data)
            if not task_id:
                raise ApiError(f"NY 图片接口没有返回可用图片：{str(data)[:300]}", kind=TASK_FATAL)
            items = self.session.poll("/v1/images/{id}", task_id, picker=extract_image_items,
                                      interval=poll_interval, timeout=poll_timeout,
                                      log=log, cancel=cancel)
        source = items[0] if isinstance(items, list) else items
        self.session.save_item(source, dest, log=log)
        return {"task_id": "", "source": str(source)[:200], "provider": self.id, "model": model}
