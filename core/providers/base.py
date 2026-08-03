# -*- coding: utf-8 -*-
"""服务商抽象层。

新增一个服务商 = 写一个继承 Provider 的类 + 在 __init__.py 注册。
`capabilities()` 返回的结构直接驱动前端表单渲染，不需要改前端代码。
"""

from __future__ import annotations

from typing import Callable, Optional

from ..apiutil import ApiError, HttpSession


class ImageTask:
    """一次出图请求（资产图 / 故事板通用）。"""

    def __init__(self, prompt: str, refs: Optional[list] = None, size: str = "1024x1536",
                 model: str = "", n: int = 1, extra: Optional[dict] = None):
        self.prompt = prompt
        self.refs = refs or []          # 已解析为 URL 或 data URI，顺序即 Image 1..N
        self.size = size
        self.model = model
        self.n = n
        self.extra = extra or {}


class VideoTask:
    def __init__(self, prompt: str, refs: Optional[list] = None, duration: int = 15,
                 ratio: str = "9:16", model: str = "", resolution: str = "",
                 extra: Optional[dict] = None):
        self.prompt = prompt
        self.refs = refs or []
        self.duration = duration
        self.ratio = ratio
        self.model = model
        self.resolution = resolution
        self.extra = extra or {}


class Provider:
    """服务商基类。"""

    id: str = ""
    name: str = ""
    default_base_url: str = ""
    supports: tuple = ()   # ("image",) / ("video",) / ("image", "video")

    def __init__(self, api_key: str = "", base_url: str = "", proxy: str = "", timeout: int = 900):
        self.session = HttpSession(api_key, base_url or self.default_base_url, timeout, proxy)

    # -- 能力声明（驱动前端） --------------------------------------------
    def capabilities(self) -> dict:
        """返回 {id,name,default_base_url,supports,image:{...},video:{...},notes}"""
        raise NotImplementedError

    # -- 探活 -------------------------------------------------------------
    def list_models(self) -> list:
        try:
            data = self.session.request("GET", "/v1/models", retries=1, timeout=30)
            return sorted(m.get("id", "") for m in (data.get("data") or []) if m.get("id"))
        except ApiError:
            return []

    # -- 生成 -------------------------------------------------------------
    def generate_image(self, task: ImageTask, dest: str, *, log: Callable = print,
                       cancel: Optional[Callable] = None,
                       poll_interval: int = 5, poll_timeout: int = 900) -> dict:
        """生成并落盘到 dest。返回 {task_id, source, ...} 元信息。"""
        raise NotImplementedError("该服务商不支持出图")

    def generate_video(self, task: VideoTask, dest: str, *, log: Callable = print,
                       cancel: Optional[Callable] = None,
                       poll_interval: int = 10, poll_timeout: int = 2400) -> dict:
        raise NotImplementedError("该服务商不支持出视频")
