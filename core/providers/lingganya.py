# -*- coding: utf-8 -*-
"""灵感鸭（www.lingganyaapi.com）。三步式异步：提交?async=true → 查询 → 取成品。"""

from __future__ import annotations

from typing import Callable, Optional

from ..apiutil import ApiError, extract_image_items, extract_task_id, extract_video_url
from .base import ImageTask, Provider, VideoTask


class LingganyaProvider(Provider):
    id = "lingganya"
    name = "灵感鸭 lingganyaapi.com"
    default_base_url = "https://www.lingganyaapi.com"
    supports = ("image", "video")

    def capabilities(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "default_base_url": self.default_base_url,
            "supports": list(self.supports),
            "image": {
                "models": ["gpt-image-2", "gpt-image-2-4k", "gpt-image-2-special"],
                "default_model": "gpt-image-2",
                "sizes": ["1024x1536", "1024x1024", "1536x1024"],
                "default_size": "1024x1536",
                "max_refs": 9,
                "ref_mode": "data_uri",
                "notes": "size 是像素。参考图官方要 URL，data URI 实测可用。",
            },
            "video": {
                "models": ["sora-2", "sora-2-pro", "sora-2-12s-special", "sd-2.0", "sd-fast"],
                "default_model": "sd-2.0",
                "ratios": ["9:16", "16:9", "1:1", "4:3", "3:4"],
                "durations": [4, 5, 8, 10, 12, 15],
                "default_duration": 12,
                "resolutions": ["", "1080p", "720p", "480p"],
                "max_refs": 9,
                "ref_mode": "data_uri",
                "notes": "size=宽高比 seconds=时长；SD系 resolution 必填放顶层。"
                         "⚠ sora-2 通道实测极不稳定（随机 task_failed），不建议用于批量。",
            },
            "notes": "图片生产实测稳定；视频建议改用其它服务商。",
        }

    # ---------------------------------------------------------------- image
    def generate_image(self, task: ImageTask, dest: str, *, log: Callable = print,
                       cancel: Optional[Callable] = None,
                       poll_interval: int = 5, poll_timeout: int = 900) -> dict:
        body = {
            "model": task.model or "gpt-image-2",
            "prompt": task.prompt,
            "size": task.size or "1024x1536",
            "n": int(task.n or 1),
        }
        if task.refs:
            body["images"] = task.refs[:9]
        data = self.session.request("POST", "/v1/images/generations", json_body=body,
                                    params={"async": "true"}, retries=2, timeout=300)
        items = extract_image_items(data)
        task_id = extract_task_id(data)
        if not items:
            if not task_id:
                raise ApiError("提交未返回 task_id 或图片")
            items = self.session.poll("/v1/images/{id}", task_id,
                                      picker=extract_image_items,
                                      interval=poll_interval, timeout=poll_timeout,
                                      content_path_tpl="/v1/images/{id}/content",
                                      log=log, cancel=cancel)
        self.session.save_item(items[0], dest)
        return {"task_id": task_id, "source": items[0][:200], "provider": self.id,
                "model": body["model"]}

    # ---------------------------------------------------------------- video
    def generate_video(self, task: VideoTask, dest: str, *, log: Callable = print,
                       cancel: Optional[Callable] = None,
                       poll_interval: int = 10, poll_timeout: int = 2400) -> dict:
        model = task.model or "sd-2.0"
        is_sd = model.lower().startswith("sd")
        body = {
            "model": model,
            "prompt": task.prompt,
            "size": task.ratio or "9:16",
            "seconds": int(task.duration) if is_sd else str(int(task.duration)),
        }
        if task.refs:
            body["images"] = task.refs[:9]
        res = (task.resolution or "").strip() or ("720p" if is_sd else "")
        if res:
            body["resolution"] = res
        data = self.session.request("POST", "/v1/videos", json_body=body,
                                    params={"async": "true"}, retries=2, timeout=300)
        url = extract_video_url(data)
        task_id = extract_task_id(data)
        if not url:
            if not task_id:
                raise ApiError("提交未返回 task_id 或视频URL")
            url = self.session.poll("/v1/videos/{id}", task_id, picker=extract_video_url,
                                    interval=poll_interval, timeout=poll_timeout,
                                    content_path_tpl="/v1/videos/{id}/content",
                                    log=log, cancel=cancel)
        self.session.save_item(url, dest)
        return {"task_id": task_id, "source": url, "provider": self.id, "model": model}
