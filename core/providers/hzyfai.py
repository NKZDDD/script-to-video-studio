# -*- coding: utf-8 -*-
"""HZYF 图片接口：2026-10-02 实测 OpenAI JSON generations / multipart edits。"""
from __future__ import annotations

import base64
import io
import sys
from pathlib import Path
from typing import Callable, Optional

import requests

from core.apiutil import ApiError, extract_image_items
from core.providers.base import ImageTask, Provider

PATCH_VERSION = "20261004.1"
IMAGE_MODELS = ["gpt-image-2", "gpt-image-2.5", "gpt-image-2.5-flare", "gpt-image-2.5-sunburst"]
IMAGE_SIZES = ["1024x1024", "1536x1024", "1024x1536"]
IMAGE_4K_MODEL = "gpt-image-2"
IMAGE_4K_SIZE = "4096x4096"


class HzyfaiProvider(Provider):
    id = "hzyfai"
    name = "HZYF hzyfai.com"
    default_base_url = "https://hzyfai.com"
    supports = ("image",)
    ref_mode = "bytes"

    def __init__(self, api_key: str = "", base_url: str = "", proxy: str = "", timeout: int = 900):
        # 用户给的是 /v1 基址；宿主固定拼 /v1/...，避免出现 /v1/v1。
        base = (base_url or self.default_base_url).strip().rstrip("/")
        if base.endswith("/v1"):
            base = base[:-3]
        super().__init__(api_key, base, proxy, timeout)

    def capabilities(self) -> dict:
        return {"id": self.id, "name": self.name, "default_base_url": self.default_base_url,
                "supports": list(self.supports), "image": {
                    "models": IMAGE_MODELS, "default_model": "gpt-image-2",
                    "sizes": IMAGE_SIZES, "default_size": "1024x1024",
                    "model_options": {IMAGE_4K_MODEL: {
                        "sizes": IMAGE_SIZES + [IMAGE_4K_SIZE], "resolutions": ["4K"]}},
                    "ref_mode": "bytes", "refs_unlimited": True, "max_refs": sys.maxsize,
                    "notes": "OpenAI 兼容图片接口，size 传像素尺寸。参考图使用 multipart image[]，"
                             "本地图片无需对象存储。参考图本地不截断，实际服务端上限以响应为准。"
                             "gpt-image-2 已实测 4096x4096 方图；选 4K 时转换为此像素尺寸，"
                             "请选方图。其余模型仍使用已验证尺寸。"},
                "notes": "仅接入图片；同步返回 URL/Base64。CPU 过载 503 属于服务商网关繁忙。"}

    def _image_file(self, ref: str, index: int) -> tuple:
        try:
            ref = str(ref)
            if ref.startswith("data:"):
                head, payload = ref.split(",", 1)
                if ";base64" not in head.lower():
                    raise ValueError("参考图不是 Base64 图片")
                raw = base64.b64decode("".join(payload.split()), validate=True)
            elif ref.startswith(("https://", "http://")):
                # 不把 API Key 发给参考图地址。
                with requests.get(ref, timeout=(20, 60), proxies=self.session._proxies()) as response:
                    response.raise_for_status()
                    raw = response.content
            else:
                raw = Path(ref).read_bytes()
            from PIL import Image
            with Image.open(io.BytesIO(raw)) as im:
                fmt = im.format
                im.verify()
            with Image.open(io.BytesIO(raw)) as im:
                for frame in range(getattr(im, "n_frames", 1)):
                    im.seek(frame)
                    im.load()
            ext, mime = {"PNG": ("png", "image/png"), "JPEG": ("jpg", "image/jpeg"),
                         "WEBP": ("webp", "image/webp")}[fmt]
        except requests.RequestException as exc:
            raise ApiError(f"HZYF 第 {index} 张参考图下载失败：{type(exc).__name__}") from exc
        except Exception as exc:
            raise ApiError(f"HZYF 第 {index} 张参考图无法完整解码为 PNG/JPEG/WebP；"
                           "请检查或替换，尚未提交生成。", kind="task_fatal") from exc
        return (f"reference-{index}.{ext}", raw, mime)

    def generate_image(self, task: ImageTask, dest: str, *, log: Callable = print,
                       cancel: Optional[Callable] = None,
                       poll_interval: int = 5, poll_timeout: int = 900) -> dict:
        model = task.model or "gpt-image-2"
        size = task.size or "1024x1024"
        if model not in IMAGE_MODELS:
            raise ApiError(f"HZYF 尚未接入图片模型 {model}，请选择已声明的图片模型", kind="task_fatal")
        allowed = IMAGE_SIZES + ([IMAGE_4K_SIZE] if model == IMAGE_4K_MODEL else [])
        if size not in allowed:
            raise ApiError(f"HZYF {model} 请选择已验证的像素尺寸：{'、'.join(allowed)}", kind="task_fatal")
        resolution = str(task.extra.get("resolution") or "").strip().upper()
        if resolution:
            if model != IMAGE_4K_MODEL or resolution != "4K":
                raise ApiError("HZYF 目前仅 gpt-image-2 已验证方图 4K；其他模型或档位请用像素尺寸，"
                               "清晰度选跟随任务", kind="task_fatal")
            if size not in ("1024x1024", IMAGE_4K_SIZE):
                raise ApiError("HZYF 已验证的 4K 是 4096x4096 方图，请在比例中选择 4096x4096；"
                               "不会自动改变横图或竖图比例", kind="task_fatal")
            size = IMAGE_4K_SIZE
        if not (task.prompt or "").strip() or task.n != 1:
            raise ApiError("HZYF 提示词不能为空；Studio 每个图片任务只能生成一张", kind="task_fatal")
        if cancel and cancel():
            raise ApiError("已取消", kind="task_fatal")
        body = {"model": model, "prompt": task.prompt, "size": size, "n": 1, "response_format": "url"}
        files = [("image[]", self._image_file(ref, i)) for i, ref in enumerate(task.refs, 1)]
        if cancel and cancel():
            raise ApiError("已取消", kind="task_fatal")
        log(f"HZYF 图片 {model}: size={size}，参考图 {len(files)} 张")
        # 同步生成未提供幂等协议，不在 HTTP 层自动重复付费提交。
        if files:
            fields = [(key, (None, str(value))) for key, value in body.items()]
            data = self.session.request("POST", "/v1/images/edits", files=fields + files,
                                        retries=1, timeout=self.session.timeout)
        else:
            data = self.session.request("POST", "/v1/images/generations", json_body=body,
                                        retries=1, timeout=self.session.timeout)
        items = extract_image_items(data)
        if not items:
            raise ApiError("HZYF 图片接口未返回可用 URL/Base64，请先核对服务商记录，避免重复生成。",
                           kind="task_fatal")
        log("HZYF 图片结果已返回，正在下载保存")
        self.session.save_item(items[0], dest)
        log("HZYF 图片已保存")
        # request_id 是请求追踪号，不是可轮询的任务 ID。
        return {"task_id": "", "source": str(items[0])[:200], "provider": self.id, "model": model}
