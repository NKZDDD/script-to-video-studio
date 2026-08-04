# -*- coding: utf-8 -*-
"""小裴 aicopy（api.aicopy.top）。对齐 respect_comfyui 的 image_nodes / video_nodes。

值钱的地方是**它有一堆 gpt-image-2 的备用通道**：`gpt-image-2应急通道01..06` 和
`GPT本地版*` 系列。别家 image-2 出问题时，这里能换通道继续 —— 同一个模型、
不同线路，比换模型对画风的影响小得多。

- 图片：`POST /v1/images/generations`（JSON）。参考图放 `image` 字段，
  **要裸 base64，不带 `data:` 前缀**（这点和别家都不一样）。
  返回 b64_json 或 url。
- 视频：`POST /v1/videos` 提交 + `GET /v1/videos/{id}` 轮询。
"""

from __future__ import annotations

from typing import Callable, Optional

from ..apiutil import (ApiError, extract_image_items, extract_task_id,
                       extract_video_url)
from .base import ImageTask, Provider, VideoTask

# gpt-image-2 的多条线路 —— 这家的核心价值
IMAGE_MODELS = [
    "gpt-image-2应急通道",
    "gpt-image-2应急通道01", "gpt-image-2应急通道02", "gpt-image-2应急通道03",
    "gpt-image-2应急通道04", "gpt-image-2应急通道05", "gpt-image-2应急通道06",
    "GPT本地版", "GPT本地版1k", "GPT本地版2k", "GPT本地版4k",
    "GPT本地版-通道1", "GPT本地版1k-通道1", "GPT本地版2k-通道1", "GPT本地版4k-通道1",
    "GPT本地版-通道2", "GPT本地版1k-通道2", "GPT本地版2k-通道2", "GPT本地版4k-通道2",
    "GPT本地版-通道3", "GPT本地版1k-通道3", "GPT本地版2k-通道3", "GPT本地版4k-通道3",
]
VIDEO_MODELS = [
    "sd2-pro-720p", "sd2-720p", "sd2-fast-720p", "sd2-fast-480p",
    "seedance-2-fast", "seedance-2-pro-1080p",
    "veo_3_1-fast", "sora-2", "sora-2-pro",
    "grok-imagine-video-1.5", "grok-imagine-video-1.5-fast",
]


def _bare_b64(ref: str) -> str:
    """这家的 image 字段要裸 base64，不带 data: 前缀。"""
    return ref.split(",", 1)[1] if ref.startswith("data:") else ref


class AicopyProvider(Provider):
    id = "aicopy"
    name = "小裴 api.aicopy.top"
    default_base_url = "https://api.aicopy.top"
    supports = ("image", "video")

    def capabilities(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "default_base_url": self.default_base_url,
            "supports": list(self.supports),
            "image": {
                "models": IMAGE_MODELS,
                "default_model": "gpt-image-2应急通道",
                "sizes": ["1024x1536", "1024x1024", "1536x1024"],
                "default_size": "1024x1536",
                "max_refs": 1,
                "ref_mode": "data_uri",
                "notes": "★ 这家有 6 条 gpt-image-2 应急通道 + 12 个 GPT本地版通道，"
                         "别家 image-2 挂了可以在这里换线路继续（同一个模型不同线路，"
                         "比换模型对画风影响小）。"
                         "⚠ 参考图只收 1 张，且要裸 base64（本程序已自动处理）。"
                         "模型名带中文，是这家自己的叫法，别改。",
            },
            "video": {
                "models": VIDEO_MODELS,
                "default_model": "sd2-pro-720p",
                "ratios": ["9:16", "16:9", "1:1"],
                "durations": [4, 5, 8, 10, 12, 15],
                "default_duration": 10,
                "resolutions": [""],
                "max_refs": 9,
                "ref_mode": "data_uri",
                "notes": "模型清单以 /v1/models 实际返回为准（这家上下线较勤）。"
                         "分辨率含在模型名里。",
            },
            "notes": "接了很多线路，主要当 image-2 的备份来用。",
        }

    # ---------------------------------------------------------------- image
    def generate_image(self, task: ImageTask, dest: str, *, log: Callable = print,
                       cancel: Optional[Callable] = None,
                       poll_interval: int = 5, poll_timeout: int = 900) -> dict:
        model = task.model or "gpt-image-2应急通道"
        body = {"model": model, "prompt": task.prompt, "n": int(task.n or 1),
                "size": task.size or "1024x1536", "response_format": "b64_json"}
        if task.refs:
            if len(task.refs) > 1:
                log(f"这家的图片接口只收 1 张参考图，给了 {len(task.refs)} 张，只用第 1 张")
            body["image"] = _bare_b64(task.refs[0])
        data = self.session.request("POST", "/v1/images/generations", json_body=body,
                                    retries=2, timeout=600)
        items = extract_image_items(data)
        task_id = extract_task_id(data)
        if not items:
            if not task_id:
                raise ApiError(f"没返回图片也没返回 task_id：{str(data)[:300]}")
            items = self.session.poll("/v1/images/{id}", task_id, picker=extract_image_items,
                                      interval=poll_interval, timeout=poll_timeout,
                                      content_path_tpl="/v1/images/{id}/content",
                                      log=log, cancel=cancel)
        self.session.save_item(items[0], dest)
        return {"task_id": task_id, "source": items[0][:200], "provider": self.id,
                "model": model}

    # ---------------------------------------------------------------- video
    def generate_video(self, task: VideoTask, dest: str, *, log: Callable = print,
                       cancel: Optional[Callable] = None,
                       poll_interval: int = 10, poll_timeout: int = 2400) -> dict:
        model = task.model or "sd2-pro-720p"
        body = {"model": model, "prompt": task.prompt,
                "duration": int(task.duration or 10),
                "metadata": {"modeType": "image2video" if task.refs else "text2video",
                             "ratio": task.ratio or "9:16", "enableSound": "on"}}
        if task.refs:
            body["images"] = task.refs[:9]
        data = self.session.request("POST", "/v1/videos", json_body=body,
                                    retries=2, timeout=300)
        url = extract_video_url(data)
        task_id = extract_task_id(data)
        if not url:
            if not task_id:
                raise ApiError("提交没返回视频地址也没返回 task_id")
            url = self.session.poll("/v1/videos/{id}", task_id, picker=extract_video_url,
                                    interval=poll_interval, timeout=poll_timeout,
                                    content_path_tpl="/v1/videos/{id}/content",
                                    log=log, cancel=cancel)
        self.session.save_item(url, dest)
        return {"task_id": task_id, "source": url, "provider": self.id, "model": model}
