# -*- coding: utf-8 -*-
"""三胖（api.threefatai.com）。

一把密钥走图片 + 视频，都是异步：提交拿任务号，轮询到终态再取产物。
`https://api.threefatai.com/api` 是统一前缀，下面再拼 `/v1/...`。

几个必须照着做的点：
  1. 参考图有两条路：公网直链走 `image_url` / `image_urls`；
     本机文件或 data URI 先 POST /v1/files 换 `file_id` 再走 `image_ids`。
     两条混用会打乱「Image 1..N」的顺序，所以有一张不是链接就全部上传。
  2. `image_ids` 的顺序就是提示词里 Image 1..N 的顺序，不能乱序、不能丢图。
  3. 提交带 `external_id`（幂等键）：超时重发拿到同一个任务号，不会重复扣费。
"""

from __future__ import annotations

import base64
import io
import uuid
from pathlib import Path
from typing import Callable, Optional

import requests

from ..apiutil import ApiError, TASK_FATAL, extract_image_items, extract_task_id, extract_video_url
from .base import ImageTask, Provider, VideoTask

_SIX = ["16:9", "9:16", "1:1", "4:3", "3:4", "21:9"]
_VIDEO_OPTIONS = {
    "SD 2.5 · 官方直连（满血版）按秒": {
        "durations": list(range(4, 31)), "ratios": ["adaptive"] + _SIX,
        "resolutions": ["720p", "1080p"]},
    "SD 2.0 满血版 按秒": {
        "durations": list(range(4, 16)), "ratios": _SIX,
        "resolutions": ["720p", "1080p"]},
    "SD 2.5 · 特价按次": {
        "durations": list(range(5, 31)), "ratios": _SIX, "resolutions": ["1080p"]},
    "GK-video-3.5": {
        "durations": list(range(1, 16)), "ratios": ["16:9", "9:16", "1:1", "3:2", "2:3"],
        "resolutions": ["480p", "720p"]},
    "MiniMax H3 · 官方直连": {
        "durations": list(range(4, 16)), "ratios": ["adaptive"] + _SIX,
        "resolutions": ["768P", "2K"]},
    "Muse · 特价按秒": {
        "durations": list(range(4, 46)), "ratios": ["16:9", "9:16", "1:1", "2:3", "3:2"],
        "resolutions": ["720p"]},
    "Sd 2.0 · 特价按次 · 漫剧短剧专线": {
        "durations": [5, 10, 15], "ratios": ["16:9", "9:16", "1:1", "4:3", "3:4"],
        "resolutions": ["720p"]},
    "SD 2.0 · 特价按次 · 电商专线": {
        "durations": [5, 10, 15], "ratios": ["9:16", "16:9", "1:1"],
        "resolutions": ["720p"]},
    "SD 2.0 FAST · 特价按秒 · 稳定冲量": {
        "durations": [5, 10, 15], "ratios": _SIX, "resolutions": ["720p"]},
}
VIDEO_MODELS = list(_VIDEO_OPTIONS)

IMAGE_MODELS = ["GT Image 2.5", "Banana 2", "GT Image 2 · OpenAI"]
_IMAGE_OPTIONS = {
    "GT Image 2.5": {"ratios": ["auto", "1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3",
                                "5:4", "4:5", "2:1", "1:2", "21:9", "9:21"],
                     "resolutions": ["auto", "1K", "2K", "4K"]},
    "Banana 2": {"ratios": ["auto", "1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4",
                            "9:16", "16:9", "21:9"],
                 "resolutions": ["0.5K", "1K", "2K", "4K"]},
    "GT Image 2 · OpenAI": {"ratios": ["1:1", "3:2", "2:3", "4:3", "3:4", "5:4", "4:5",
                                       "16:9", "9:16", "21:9", "9:21"],
                            "resolutions": ["1k", "2k", "4k"]},
}
IMAGE_SIZES = ["1024x1536", "1024x1024", "1536x1024", "2048x2048"]
_MIME = {"mp4": "video/mp4", "mov": "video/quicktime", "webm": "video/webm",
         "mp3": "audio/mpeg", "wav": "audio/wav", "m4a": "audio/mp4"}


def _remote(ref: str) -> bool:
    return str(ref or "").startswith(("http://", "https://"))


def _ratio_value(name: str) -> float:
    try:
        a, b = name.replace("×", "x").split(":")[:2]
        return float(a) / float(b)
    except Exception:                                    # noqa: BLE001
        return 0.0


def _ratio_of(size: str, allowed: list) -> str:
    """像素尺寸 → 模型支持的最近比例名（不认识就返回空串，让服务端用默认档）。"""
    try:
        w, h = size.lower().replace("×", "x").split("x")[:2]
        want = float(w) / float(h)
    except Exception:                                    # noqa: BLE001
        return ""
    names = [x for x in allowed if x not in ("auto", "adaptive") and _ratio_value(x)]
    return min(names, key=lambda n: abs(_ratio_value(n) - want)) if names else ""


def _tier_of(size: str, allowed: list) -> str:
    """像素尺寸 → 清晰度档；按长边向上取最接近的一档。"""
    try:
        w, h = size.lower().replace("×", "x").split("x")[:2]
        long_side = max(int(w), int(h))
    except Exception:                                    # noqa: BLE001
        return ""
    tiers = []
    for name in allowed:
        if name in ("auto", ""):
            continue
        try:
            tiers.append((float(name.lower().rstrip("k")) * 1024, name))
        except ValueError:
            continue
    if not tiers:
        return ""
    for value, name in sorted(tiers):
        if value >= long_side:
            return name
    return sorted(tiers)[-1][1]


class ThreeFatProvider(Provider):
    id = "threefatai"
    name = "三胖 api.threefatai.com"
    aliases = ("threefat", "三胖", "sf")
    default_base_url = "https://api.threefatai.com/api"
    supports = ("image", "video")
    ref_mode = "data_uri"

    def capabilities(self) -> dict:
        return {
            "id": self.id, "name": self.name, "default_base_url": self.default_base_url,
            "supports": list(self.supports),
            "image": {
                "models": IMAGE_MODELS, "default_model": "GT Image 2.5",
                "sizes": IMAGE_SIZES, "default_size": "1024x1536",
                "max_refs": 10, "ref_mode": "data_uri", "model_options": _IMAGE_OPTIONS,
                "notes": "比例与清晰度分开选；像素尺寸会换算成最近的比例 + 清晰度档。"
                         "参考图本机/data URI 先上传换 file_id，公网链接直接用 image_urls。",
            },
            "video": {
                "models": VIDEO_MODELS, "default_model": "SD 2.5 · 特价按次",
                "ratios": ["16:9", "9:16", "1:1", "4:3", "3:4", "21:9", "3:2", "2:3", "adaptive"],
                # 本地不夹秒数：档位以各模型 limits 为准，真超范围让服务端拒绝。
                "durations": list(range(1, 61)), "default_duration": 10,
                "resolutions": ["", "480p", "720p", "768P", "1080p", "2K", "4K"],
                "max_refs": 30, "ref_mode": "data_uri", "model_options": _VIDEO_OPTIONS,
                "notes": "提交带 external_id 幂等键。参考图公网链接走 image_urls，"
                         "本机/data URI 先 POST /v1/files 换 image_ids（顺序即 Image 1..N）。"
                         "GK-video-3.5 必须带首帧参考图；9:16 竖屏在部分线路需配 30 秒（以成片为准）。",
            },
            "notes": "模型与档位以 GET /v1/models 的实时 limits 为准。",
        }

    # ------------------------------------------------------------- 素材
    def _bytes(self, ref: str, index: int, kind: str) -> tuple:
        try:
            ref = str(ref or "")
            if ref.startswith("data:"):
                raw = base64.b64decode("".join(ref.split(",", 1)[1].split()), validate=True)
                mime = ref[5:].split(";")[0] or "application/octet-stream"
            elif _remote(ref):
                with requests.get(ref, timeout=(20, 120), proxies=self.session._proxies()) as response:
                    response.raise_for_status()
                    raw = response.content
                mime = response.headers.get("Content-Type", "").split(";")[0]
            else:
                path = Path(ref)
                raw = path.read_bytes()
                mime = _MIME.get(path.suffix.lower().lstrip("."), "application/octet-stream")
            if not raw:
                raise ValueError("文件为空")
            if kind == "image":
                from PIL import Image
                with Image.open(io.BytesIO(raw)) as im:
                    fmt = im.format
                    im.verify()
                ext, mime = {"PNG": ("png", "image/png"), "JPEG": ("jpg", "image/jpeg"),
                             "WEBP": ("webp", "image/webp")}[fmt]
            else:
                ext = {"video/mp4": "mp4", "video/quicktime": "mov", "video/webm": "webm",
                       "audio/mpeg": "mp3", "audio/wav": "wav", "audio/mp4": "m4a"}.get(mime, "bin")
        except requests.RequestException as exc:
            raise ApiError(f"三胖第 {index} 个参考素材下载失败：{type(exc).__name__}") from exc
        except Exception as exc:                         # noqa: BLE001
            raise ApiError(f"三胖第 {index} 个参考素材无法读取（data URI / 本机文件）；"
                           f"未提交生成：{exc}", kind=TASK_FATAL) from exc
        return raw, mime, f"{kind}-{index}.{ext}"

    def _file_id(self, ref: str, index: int, kind: str, log: Callable) -> str:
        raw, _mime, name = self._bytes(ref, index, kind)
        data = self.session.request(
            "POST", "/v1/files",
            json_body={"kind": kind, "data_b64": base64.b64encode(raw).decode(), "name": name},
            retries=2, timeout=180, submit=False)
        file_id = str((data or {}).get("file_id") or "")
        if not file_id:
            raise ApiError(f"三胖第 {index} 个参考素材上传后没有 file_id：{str(data)[:200]}",
                           kind=TASK_FATAL)
        log(f"三胖素材已上传：{name} → {file_id}")
        return file_id

    def _image_refs(self, refs: list, log: Callable) -> dict:
        """公网链接走 url 字段；有一张不是链接就全部上传，保住 Image 1..N 的顺序。"""
        refs = [str(r) for r in refs if str(r or "").strip()]
        if not refs:
            return {}
        if all(_remote(r) for r in refs):
            return {"image_url": refs[0]} if len(refs) == 1 else {"image_urls": refs}
        return {"image_ids": [self._file_id(r, i, "image", log) for i, r in enumerate(refs, 1)]}

    # ------------------------------------------------------------- 视频
    def generate_video(self, task: VideoTask, dest: str, *, log: Callable = print,
                       cancel: Optional[Callable] = None,
                       poll_interval: int = 10, poll_timeout: int = 2400) -> dict:
        model = (task.model or "SD 2.5 · 特价按次").strip()
        if not (task.prompt or "").strip():
            raise ApiError("三胖视频提示词为空", kind=TASK_FATAL)
        refs = list(task.refs or [])
        videos = list(task.extra.get("video_refs") or task.extra.get("videos") or [])
        audios = list(task.extra.get("audio_refs") or task.extra.get("audios") or [])
        if model == "GK-video-3.5" and not refs:
            raise ApiError("三胖 GK-video-3.5 必须带首帧参考图，不接受纯文生", kind=TASK_FATAL)
        if len(refs) > 30:
            raise ApiError(f"三胖 {model} 最多 30 张参考图，本任务有 {len(refs)} 张；"
                           "不能静默裁图，否则人物绑定会丢失", kind=TASK_FATAL)
        if cancel and cancel():
            raise ApiError("已取消", kind=TASK_FATAL)

        body: dict = {"model": model, "prompt": task.prompt,
                      "duration": max(1, int(task.duration or 10)),
                      "external_id": uuid.uuid4().hex}
        if task.ratio:
            body["ratio"] = task.ratio
        if task.resolution:
            body["resolution"] = task.resolution
        body.update(self._image_refs(refs, log))
        if videos:
            body["video_b64s"] = [base64.b64encode(self._bytes(r, i, "video")[0]).decode()
                                  for i, r in enumerate(videos, 1)]
        if audios:
            body["audio_b64s"] = [base64.b64encode(self._bytes(r, i, "audio")[0]).decode()
                                  for i, r in enumerate(audios, 1)]
        log(f"三胖 {model}: {body['duration']}s {body.get('ratio', '默认比例')} "
            f"{body.get('resolution') or '默认清晰度'} 图{len(refs)}/视频{len(videos)}/音频{len(audios)}")
        data = self.session.request("POST", "/v1/videos", json_body=body,
                                    retries=1, timeout=300)
        task_id = extract_task_id(data)
        url = extract_video_url(data)
        if not url:
            if not task_id:
                raise ApiError(f"三胖创建任务没有返回任务号：{str(data)[:300]}")
            url = self.session.poll("/v1/videos/{id}", task_id, picker=extract_video_url,
                                    interval=poll_interval, timeout=poll_timeout,
                                    log=log, cancel=cancel)
        self.session.save_item(url, dest, log=log)
        return {"task_id": task_id, "source": url, "provider": self.id, "model": model}

    # ------------------------------------------------------------- 图片
    def generate_image(self, task: ImageTask, dest: str, *, log: Callable = print,
                       cancel: Optional[Callable] = None,
                       poll_interval: int = 5, poll_timeout: int = 900) -> dict:
        model = task.model or "GT Image 2.5"
        options = _IMAGE_OPTIONS.get(model) or _IMAGE_OPTIONS["GT Image 2.5"]
        if not (task.prompt or "").strip():
            raise ApiError("三胖图片提示词为空", kind=TASK_FATAL)
        if task.n != 1:
            raise ApiError("Studio 一个图片任务对应一个输出文件，请每次生成 1 张", kind=TASK_FATAL)
        if cancel and cancel():
            raise ApiError("已取消", kind=TASK_FATAL)
        size = task.size or "1024x1536"
        body: dict = {"model": model, "prompt": task.prompt, "n": 1, "output_format": "png",
                      "external_id": uuid.uuid4().hex}
        ratio = task.extra.get("ratio") or _ratio_of(size, options["ratios"])
        tier = _tier_of(size, options["resolutions"])
        if ratio:
            body["ratio"] = ratio
        if tier:
            body["resolution"] = tier
        body.update(self._image_refs(list(task.refs or []), log))
        log(f"三胖 图片 {model}: size={size} → {ratio or '默认比例'} {tier or '默认清晰度'}，"
            f"参考图 {len(task.refs or [])} 张")
        data = self.session.request("POST", "/v1/images/generations", json_body=body,
                                    retries=1, timeout=600)
        items = extract_image_items(data)
        if not items:
            task_id = extract_task_id(data)
            if not task_id:
                raise ApiError(f"三胖图片接口没有返回可用图片：{str(data)[:300]}", kind=TASK_FATAL)
            items = self.session.poll("/v1/images/{id}", task_id, picker=extract_image_items,
                                      interval=poll_interval, timeout=poll_timeout,
                                      log=log, cancel=cancel)
        source = items[0] if isinstance(items, list) else items
        self.session.save_item(source, dest, log=log)
        return {"task_id": "", "source": str(source)[:200], "provider": self.id, "model": model}
