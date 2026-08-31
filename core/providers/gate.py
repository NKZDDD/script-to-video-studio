# -*- coding: utf-8 -*-
"""Gate（api-gate.astralmindai.com）图片、视频与模型清单接入。

依据用户提供的《Gate接入文档》和公开模型 Schema：
  · GET  /public/model_group/info       公开模型及参数 Schema
  · POST /v1/images/generations        图片生成/编辑
  · POST /api/multimodal/create_task   创建异步视频任务
  · GET  /v1/videos/{id}               查询视频任务
  · GET  /v1/videos/{id}/content       下载视频

视频 Schema 中 Seedance 2.5 支持 4–30 秒、30 图、10 视频、10 音频；2.0 系列
支持 4–15 秒、9 图、3 视频、3 音频。视频 reference 字段为纯字符串数组。
"""

from __future__ import annotations

from typing import Callable, Optional

from ..apiutil import (ApiError, extract_image_items, extract_task_id,
                       extract_video_url)
from .base import ImageTask, Provider, VideoTask

IMAGE_MODELS = [
    "nano-banana", "nano-banana-pro", "seedream-5-0-lite", "nano-banana-2",
    "seedream-5-0-pro", "gpt-image-2", "nano-banana-2-lite", "seedream-4-0",
    "kling-image-o3", "qwen-image-2.0-pro", "qwen-image-2.0", "seedream-4-5",
    "kling-image-v3", "gpt-image-1",
]
VIDEO_MODELS = [
    "seedance-2.5-official", "seedance-2.0-standard-official",
    "seedance-2.0-fast-official", "seedance-2.0-mini", "seedance-2.0-standard",
    "seedance-2.5", "seedance-2.0-fast",
]
IMAGE_SIZES = ["1024x1536", "1536x1024", "1024x1024"]
RATIOS = ["9:16", "16:9", "1:1", "4:3", "3:4", "21:9", "adaptive"]

# /public/model_group/info 的图片 Schema 不是一套字段硬套所有模型：GPT 不收
# reference image，Kling 用 num_images/resolution，Qwen 的尺寸分隔符还是“*”。
# 在适配层明确转换，不能把服务端会拒绝（或静默忽略）的字段照发出去。
IMAGE_REF_LIMITS = {
    "nano-banana": 14, "nano-banana-pro": 14, "seedream-5-0-lite": 14,
    "nano-banana-2": 14, "seedream-5-0-pro": 10,
    "nano-banana-2-lite": 14, "gpt-image-2": 0, "gpt-image-1": 0,
    "kling-image-v3": 1,
}
_NO_N = {"seedream-5-0-lite", "seedream-5-0-pro", "seedream-4-0",
         "seedream-4-5", "kling-image-o3", "kling-image-v3"}
_KLING = {"kling-image-o3", "kling-image-v3"}


def _is_25(model: str) -> bool:
    return str(model).startswith("seedance-2.5")


def _ratio_for_size(size: str) -> str:
    """把项目的 WIDTHxHEIGHT 转为 Kling 接口的宽高比字段。"""
    try:
        w, h = (int(x) for x in str(size).lower().split("x", 1))
    except (TypeError, ValueError):
        return "1:1"
    pairs = {(9, 16): "9:16", (16, 9): "16:9", (1, 1): "1:1",
             (4, 3): "4:3", (3, 4): "3:4", (3, 2): "3:2", (2, 3): "2:3"}
    return min(pairs.items(), key=lambda item: abs(w / h - item[0][0] / item[0][1]))[1]


def _image_shape(model: str, size: str) -> dict:
    """按 Gate 当前公开 Schema 生成每个图片模型能接受的尺寸字段。"""
    wanted = str(size or "1024x1536")
    if model in _KLING:
        return {"resolution": "1K", "aspect_ratio": _ratio_for_size(wanted)}
    if model.startswith("qwen-image-2.0"):
        return {"size": wanted.replace("x", "*").replace("X", "*")}
    if model == "seedream-4-5":
        # 该模型自定义尺寸下限高于项目的默认 1024x1536；2K 是文档默认安全值。
        return {"size": wanted if wanted in ("2K", "4K") else "2K"}
    return {"size": wanted}


class GateProvider(Provider):
    id = "gate"
    name = "Gate api-gate.astralmindai.com"
    aliases = ("astralmind", "astralmindai", "Gate", "盖特")
    default_base_url = "https://api-gate.astralmindai.com"
    supports = ("image", "video")
    # 图片 Schema 明确收公网 URL / Base64；视频 image_url 必须是网关能取到的 URL。
    ref_mode = "data_uri"

    def __init__(self, api_key: str = "", base_url: str = "", proxy: str = "",
                 timeout: int = 900):
        # 实测 Windows 系统代理可能令该域名在 TLS 握手时 EOF，直连正常。用户若在
        # config 里显式填了代理仍尊重其配置；没填则不偷偷继承系统代理。
        super().__init__(api_key, base_url, proxy or "direct", timeout)

    def needs_url(self, model: str = "", media: str = "image") -> bool:
        return media == "video"

    def capabilities(self) -> dict:
        video_options = {
            m: ({"durations": list(range(4, 31)), "max_refs": 30,
                 "resolutions": ["480p", "720p"]}
                if _is_25(m) else
                {"durations": list(range(4, 16)), "max_refs": 9,
                 "resolutions": ["480p", "720p"]})
            for m in VIDEO_MODELS
        }
        return {
            "id": self.id,
            "name": self.name,
            "default_base_url": self.default_base_url,
            "supports": list(self.supports),
            "image": {
                "models": IMAGE_MODELS,
                # 默认选能吃多参考图的模型；GPT 两个图片模型在当前 Schema 中只做文生图。
                "default_model": "seedream-5-0-pro",
                "sizes": IMAGE_SIZES,
                "default_size": "1024x1536",
                "max_refs": 14,
                "ref_mode": "data_uri",
                "model_options": {
                    model: {"max_refs": limit} for model, limit in IMAGE_REF_LIMITS.items()
                },
                "notes": "POST /v1/images/generations。参考图字段为 image，单张可传字符串，"
                         "多张传数组；支持公网 URL 或 Base64 Data URL。各模型精确尺寸/参考图"
                         "上限以公开 Schema 为准。",
            },
            "video": {
                "models": VIDEO_MODELS,
                "default_model": "seedance-2.5",
                "ratios": RATIOS,
                "durations": list(range(4, 31)),
                "default_duration": 15,
                "resolutions": ["480p", "720p"],
                "max_refs": 30,
                "max_video_refs": 10,
                "max_audio_refs": 10,
                "ref_mode": "url",
                "model_options": video_options,
                "notes": "Seedance 2.5：4–30 秒、30 图/10 视频/10 音频；Seedance 2.0："
                         "4–15 秒、9 图/3 视频/3 音频。字段为 duration、ratio、resolution、"
                         "image_url/video_url/audio_url，均为纯字符串数组。",
            },
            "notes": "公开 Schema 会持续变化，list_models() 每次从 /public/model_group/info "
                     "读取；静态列表仅用于界面首次打开。默认直连，不继承 Windows 系统代理；"
                     "Gate 同一个 Key 也可配置为 LLM Base URL。",
        }

    def list_models(self) -> list:
        try:
            data = self.session.request("GET", "/public/model_group/info",
                                        retries=2, timeout=30)
        except ApiError:
            return []
        return sorted({str(m.get("model_group")) for m in (data.get("data") or [])
                       if m.get("model_group")})

    def generate_image(self, task: ImageTask, dest: str, *, log: Callable = print,
                       cancel: Optional[Callable] = None,
                       poll_interval: int = 5, poll_timeout: int = 900) -> dict:
        model = (task.model or "gpt-image-2").strip()
        refs = list(task.refs or [])
        limit = IMAGE_REF_LIMITS.get(model, 14)
        if refs and not limit:
            raise ApiError(f"Gate {model} 当前 Schema 不支持参考图；请改用 Seedream、"
                           "Nano Banana、Qwen 或 Kling 模型。",
                           status=0, kind="task_fatal")
        if len(refs) > limit:
            raise ApiError(f"Gate {model} 最多 {limit} 张参考图，本任务有 {len(refs)} 张；"
                           "不能静默裁图。", status=0, kind="task_fatal")

        body: dict = {"model": model, "prompt": task.prompt or ""}
        body.update(_image_shape(model, task.size))
        if model in _KLING:
            body["num_images"] = int(task.n or 1)
        elif model not in _NO_N:
            body["n"] = int(task.n or 1)
        if refs:
            body["image"] = refs[0] if len(refs) == 1 else refs
        for key in ("quality", "image_size", "aspect_ratio", "output_format",
                    "background", "output_compression", "seed"):
            if key in task.extra:
                body[key] = task.extra[key]
        shape = body.get("size") or (f"{body.get('resolution')} {body.get('aspect_ratio')}")
        log(f"Gate 图片 {model}: size={shape} n={task.n or 1} 参考图{len(refs)}张")
        data = self.session.request("POST", "/v1/images/generations",
                                    json_body=body, retries=1, timeout=600)
        items = extract_image_items(data)
        if not items:
            raise ApiError(f"Gate 图片接口没返回可用图片: {str(data)[:300]}")
        self.session.save_item(items[0], dest)
        return {"task_id": extract_task_id(data), "source": items[0][:200],
                "provider": self.id, "model": model}

    def generate_video(self, task: VideoTask, dest: str, *, log: Callable = print,
                       cancel: Optional[Callable] = None,
                       poll_interval: int = 8, poll_timeout: int = 2400) -> dict:
        model = (task.model or "seedance-2.5").strip()
        is_25 = _is_25(model)
        sec, upper = int(task.duration or 15), 30 if is_25 else 15
        if not 4 <= sec <= upper:
            raise ApiError(f"Gate {model} 只支持 4–{upper} 秒，本任务是 {sec} 秒",
                           status=0, kind="task_fatal")

        images = list(task.refs or [])
        videos = list(task.extra.get("video_refs") or task.extra.get("videos") or [])
        audios = list(task.extra.get("audio_refs") or task.extra.get("audios") or [])
        max_i, max_va = (30, 10) if is_25 else (9, 3)
        if len(images) > max_i or len(videos) > max_va or len(audios) > max_va:
            raise ApiError(f"Gate {model} 素材超限：图 {len(images)}/{max_i}、"
                           f"视频 {len(videos)}/{max_va}、音频 {len(audios)}/{max_va}。"
                           "不能静默裁掉参考素材。", status=0, kind="task_fatal")
        bad = [r for r in images + videos + audios
               if not str(r).startswith(("http://", "https://"))]
        if bad:
            raise ApiError(f"Gate 视频参考素材只收公网 URL，本任务有 {len(bad)} 项不是。"
                           "请配置参考图对象存储，不能少素材后继续生成。",
                           status=0, kind="task_fatal")

        body: dict = {
            "model": model,
            "prompt": task.prompt or "",
            "duration": sec,
            "ratio": task.ratio or "9:16",
            "resolution": (task.resolution or "720p").lower(),
            "generate_audio": bool(task.extra.get("generate_audio", True)),
            "watermark": bool(task.extra.get("watermark", False)),
        }
        if images:
            body["image_url"] = images
        if videos:
            body["video_url"] = videos
        if audios:
            body["audio_url"] = audios
        for key in ("seed", "camera_fixed", "return_last_frame", "output_format",
                    "omni_reference_task_type", "priority"):
            if key in task.extra:
                body[key] = task.extra[key]

        log(f"Gate 视频 {model}: {sec}s {body['resolution']} {body['ratio']} "
            f"图{len(images)}/视频{len(videos)}/音频{len(audios)}")
        data = self.session.request("POST", "/api/multimodal/create_task",
                                    json_body=body, retries=1, timeout=300)
        task_id = extract_task_id(data)
        url = extract_video_url(data)
        if not url:
            if not task_id:
                raise ApiError(f"Gate 创建任务没返回 ID: {str(data)[:300]}")
            url = self.session.poll("/v1/videos/{id}", task_id, picker=extract_video_url,
                                    content_path_tpl="/v1/videos/{id}/content",
                                    interval=poll_interval, timeout=poll_timeout,
                                    log=log, cancel=cancel)
        self.session.save_item(url, dest)
        return {"task_id": task_id, "source": url, "provider": self.id, "model": model}
