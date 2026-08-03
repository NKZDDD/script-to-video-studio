# -*- coding: utf-8 -*-
"""派系 / SD2·Seedance 系（api.paisio.online 风格）。

视频：POST /v1/videos，body 带 metadata{modeType,ratio,enableSound} + images[]，提示词补 @图N。
图片：OpenAI 兼容 /v1/images/generations（同步或异步都兼容）。
"""

from __future__ import annotations

from typing import Callable, Optional

from ..apiutil import ApiError, extract_image_items, extract_task_id, extract_video_url
from .base import ImageTask, Provider, VideoTask


class PaisioProvider(Provider):
    id = "paisio"
    name = "派系 api.paisio.online（SD2/Seedance）"
    default_base_url = "https://api.paisio.online"
    supports = ("image", "video")

    def capabilities(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "default_base_url": self.default_base_url,
            "supports": list(self.supports),
            "image": {
                "models": ["gpt-image-2", "gpt-image-2-4k"],
                "default_model": "gpt-image-2",
                "sizes": ["1024x1536", "1024x1024", "1536x1024"],
                "default_size": "1024x1536",
                "max_refs": 9,
                "ref_mode": "data_uri",
                "notes": "模型清单以 /v1/models 实际返回为准。",
            },
            "video": {
                "models": ["sd2-pro-720p", "sd2-pro1-720p", "sd2-720p", "sd2-1080p",
                           "sd2-fast-720p", "sd2-fast-480p",
                           "seedance2.0-official2-720p", "seedance2.0-fast2-720p",
                           "video-pro-1080p", "video-pro-480p"],
                "default_model": "sd2-pro-720p",
                "ratios": ["9:16", "16:9", "1:1"],
                "durations": [4, 5, 8, 10, 12, 15],
                "default_duration": 15,
                "resolutions": [""],
                "max_refs": 9,
                "ref_mode": "data_uri",
                "notes": "✅ 实测稳定（17/17 一次通过）。分辨率含在模型名里；"
                         "参考图用压缩 data URI 直传（本站无上传端点）。",
            },
            "notes": "视频首选。也提供 chat 模型（claude/gpt 系）可作 LLM 分析引擎。",
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
                                    retries=2, timeout=600)
        items = extract_image_items(data)
        task_id = extract_task_id(data)
        if not items:
            if not task_id:
                raise ApiError("提交未返回 task_id 或图片")
            items = self.session.poll("/v1/images/{id}", task_id, picker=extract_image_items,
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
        refs = task.refs[:9]
        prompt = task.prompt or ""
        if refs and "@图" not in prompt:
            prompt = prompt.strip() + " " + " ".join(f"@图{i + 1}" for i in range(len(refs)))
        body = {
            "model": task.model or "sd2-pro-720p",
            "prompt": prompt,
            "duration": int(task.duration),
            "metadata": {
                "modeType": "image2video" if refs else "text2video",
                "ratio": task.ratio or "9:16",
                "enableSound": "on" if task.extra.get("enable_sound", True) else "off",
            },
        }
        if refs:
            body["images"] = refs
        data = self.session.request("POST", "/v1/videos", json_body=body, retries=2, timeout=300)
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
        return {"task_id": task_id, "source": url, "provider": self.id, "model": body["model"]}
