# -*- coding: utf-8 -*-
"""无限画布（videogogo.top）统一视频 API。只做视频。

这不是阿珂。文档页面给出的两个模型：
  · seedance-2.5-hf-720p：720p，4–30 秒
  · seedance-2.5-hf：480p，4–30 秒

两者均为混合参考：最多 30 图、10 视频、10 音频，默认音频开、水印关。
本地图片/data URI 先 POST /v1/assets 上传，随后把 asset_id 放进字符串数组。
"""

from __future__ import annotations

import base64
import mimetypes
import os
import re
import uuid
from typing import Callable, Optional
from urllib.parse import unquote_to_bytes

from ..apiutil import ApiError, extract_task_id, extract_video_url
from .base import Provider, VideoTask

VIDEO_MODELS = ["seedance-2.5-hf-720p", "seedance-2.5-hf"]
MODEL_RESOLUTION = {"seedance-2.5-hf-720p": "720p", "seedance-2.5-hf": "480p"}
RATIOS = ["9:16", "16:9", "1:1", "4:3", "3:4", "21:9"]
MAX_IMAGES, MAX_VIDEOS, MAX_AUDIOS = 30, 10, 10


def _data_uri(value: str) -> tuple[bytes, str] | None:
    match = re.match(r"^data:([^;,]+)?(;base64)?,(.*)$", str(value), re.I | re.S)
    if not match:
        return None
    mime = match.group(1) or "application/octet-stream"
    try:
        blob = (base64.b64decode(match.group(3), validate=False)
                if match.group(2) else unquote_to_bytes(match.group(3)))
    except (TypeError, ValueError) as exc:
        raise ApiError("无限画布参考素材的 data URI 无法解码") from exc
    return blob, mime


class WuxianhuabuProvider(Provider):
    id = "wuxianhuabu"
    name = "无限画布 videogogo.top（Seedance 2.5）"
    aliases = ("videogogo", "无限画布", "wxhb")
    default_base_url = "https://videogogo.top/api"
    supports = ("video",)
    ref_mode = "data_uri"

    def capabilities(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "default_base_url": self.default_base_url,
            "supports": list(self.supports),
            "video": {
                "models": VIDEO_MODELS,
                "default_model": "seedance-2.5-hf-720p",
                "ratios": RATIOS,
                "durations": list(range(4, 31)),
                "default_duration": 15,
                "resolutions": ["480p", "720p"],
                "max_refs": MAX_IMAGES,
                "max_video_refs": MAX_VIDEOS,
                "max_audio_refs": MAX_AUDIOS,
                "ref_mode": "data_uri",
                "model_options": {
                    "seedance-2.5-hf-720p": {"resolutions": ["720p"]},
                    "seedance-2.5-hf": {"resolutions": ["480p"]},
                },
                "notes": "两个模型均为 4–30 秒混合参考，最多 30 图/10 视频/10 音频。"
                         "720p 选 seedance-2.5-hf-720p，480p 选 seedance-2.5-hf；"
                         "图片、视频、音频数组都是 URL 或 /v1/assets 返回的 asset_id 字符串。",
            },
            "notes": "独立于阿珂。素材与结果最多保留 24 小时；创建请求带幂等键，"
                     "同一次网络重试不会重复建单。",
        }

    def _asset(self, ref: str, kind: str, *, log: Callable) -> str:
        value = str(ref or "").strip()
        if not value:
            return ""
        if value.startswith(("http://", "https://")):
            return value
        parsed = _data_uri(value)
        if parsed:
            blob, mime = parsed
            name = f"reference-{uuid.uuid4().hex[:12]}{mimetypes.guess_extension(mime) or '.bin'}"
        elif os.path.isfile(value):
            name = os.path.basename(value)
            mime = mimetypes.guess_type(value)[0] or "application/octet-stream"
            with open(value, "rb") as fh:
                blob = fh.read()
        elif re.fullmatch(r"[A-Za-z0-9_\-]{8,128}", value):
            # 已有 asset_id 是普通字符串，原样使用。
            return value
        else:
            # 到这儿说明:不是链接、不是 data URI、本机也没这个文件、
            # 形状还不像 asset_id。以前这里原样返回 —— 一个写错的路径
            # 就变成一个假 asset_id 发出去,服务商认不出就当没有这张参考图,
            # **片子照出、照计费,脸不对而且一处都不报错**。
            raise ApiError(
                f"无限画布{kind}参考素材认不出:{value[:120]!r}。"
                f"既不是 http 链接、不是 data URI,本机也没有这个文件,"
                f"形状也不像 asset_id。少一张参考素材出来的就不是同一个人,"
                f"所以这一条不出。",
                status=0, kind="task_fatal")
        if not blob:
            raise ApiError(f"无限画布{kind}参考素材为空")
        data = self.session.request(
            "POST", "/v1/assets", raw_body=blob,
            headers={"Content-Type": mime, "X-File-Name": name},
            retries=2, timeout=600)
        asset_id = data.get("asset_id", "") if isinstance(data, dict) else ""
        if not asset_id:
            raise ApiError(f"无限画布上传{kind}素材没返回 asset_id: {str(data)[:300]}")
        log(f"无限画布 {kind}参考素材已上传")
        return str(asset_id)

    def generate_video(self, task: VideoTask, dest: str, *, log: Callable = print,
                       cancel: Optional[Callable] = None,
                       poll_interval: int = 5, poll_timeout: int = 2400) -> dict:
        model = (task.model or "seedance-2.5-hf-720p").strip()
        if model not in MODEL_RESOLUTION:
            raise ApiError(f"无限画布不认识模型 {model!r}，只能选 {' / '.join(VIDEO_MODELS)}",
                           status=0, kind="task_fatal")
        sec = int(task.duration or 15)
        if not 4 <= sec <= 30:
            raise ApiError(f"无限画布 Seedance 2.5 只支持 4–30 秒，本任务是 {sec} 秒",
                           status=0, kind="task_fatal")

        image_src = list(task.refs or [])
        video_src = list(task.extra.get("video_refs") or task.extra.get("videos") or [])
        audio_src = list(task.extra.get("audio_refs") or task.extra.get("audios") or [])
        if len(image_src) > MAX_IMAGES or len(video_src) > MAX_VIDEOS or len(audio_src) > MAX_AUDIOS:
            raise ApiError(
                f"无限画布素材超限：图片 {len(image_src)}/{MAX_IMAGES}、"
                f"视频 {len(video_src)}/{MAX_VIDEOS}、音频 {len(audio_src)}/{MAX_AUDIOS}。"
                "不能静默裁掉参考素材。", status=0, kind="task_fatal")

        images = [self._asset(r, "图片", log=log) for r in image_src]
        videos = [self._asset(r, "视频", log=log) for r in video_src]
        audios = [self._asset(r, "音频", log=log) for r in audio_src]
        body: dict = {
            "model": model,
            "prompt": task.prompt or "",
            "seconds": sec,
            "resolution": MODEL_RESOLUTION[model],
            "ratio": task.ratio or "9:16",
            "generate_audio": bool(task.extra.get("generate_audio", True)),
            "watermark": bool(task.extra.get("watermark", False)),
        }
        if images:
            body["reference_images"] = images
        if videos:
            body["reference_videos"] = videos
        if audios:
            body["reference_audios"] = audios

        idem = str(task.extra.get("idempotency_key") or uuid.uuid4())
        log(f"无限画布 {model}: {sec}s {body['resolution']} {body['ratio']} "
            f"图{len(images)}/视频{len(videos)}/音频{len(audios)}")
        data = self.session.request(
            "POST", "/v1/videos", json_body=body,
            headers={"Idempotency-Key": idem}, retries=2, timeout=300)
        task_id = extract_task_id(data)
        url = extract_video_url(data)
        if not url:
            if not task_id:
                raise ApiError(f"提交没返回任务 ID: {str(data)[:300]}")
            url = self.session.poll("/v1/videos/{id}", task_id, picker=extract_video_url,
                                    interval=poll_interval, timeout=poll_timeout,
                                    log=log, cancel=cancel)
        self.session.save_item(url, dest)
        return {"task_id": task_id, "source": url, "provider": self.id, "model": model}
