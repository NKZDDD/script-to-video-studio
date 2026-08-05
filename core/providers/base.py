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

    # 参考图怎么给：
    #   data_uri —— 能直接吃图片内容（省事，默认）
    #   url      —— 只收公网链接，本机文件必须先上传（core/uploader.py）
    # 同一家不同模型可能不一样（零视图生视频吃 base64、SD2 只收 URL），
    # 所以还有 url_only_models 这个逃生口，按模型名细分。
    #   bytes    —— 走 multipart，只收真的文件字节，**给链接会被丢掉**
    ref_mode: str = "data_uri"
    url_only_models: tuple = ()

    def needs_url(self, model: str = "") -> bool:
        return self.ref_mode == "url" or (model or "") in self.url_only_models

    def needs_bytes(self, model: str = "") -> bool:
        """这家的参考图只收文件字节。

        为什么必须单独声明：配了对象存储之后，解析器默认把本机文件一律上传换成
        公网链接（体积小、通用）。可 multipart 的接口拿到链接只能丢掉 ——
        图照样出，但没有父资产参考，状态资产的脸就飘了，而且**不报错**。
        这种静默降级比直接失败危险得多，所以解析器要按这个声明给对形式。
        """
        return self.ref_mode == "bytes"

    def accepts_url(self, model: str = "", media: str = "image") -> bool:
        """给这家公网链接它也能用吗。

        默认能 —— 大多数网关的 `images` 数组既吃链接也吃 data URI，链接还更省
        体积。但有两类不行，必须各自声明清楚，否则解析器一上传就把参考图作废了：
          · multipart 接口（只收文件字节）
          · 把参考图内联进某个字段、只认裸 base64 的（给链接会被原样发出去）
        """
        return not self.needs_bytes(model)

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
