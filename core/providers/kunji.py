# -*- coding: utf-8 -*-
"""坤鸡 图片（img.yunfei.best）。对齐 respect_comfyui/kunji_nodes.py。

只做图片。这家的视频是另一个网关（ComfyUI 侧走「Grok-Video 坤鸡分支」节点），
base_url 不一样，所以这里不声明 video 能力 —— 硬凑只会让人选了发现不通。

- 有参考图 → `POST /v1/images/edits`，**multipart/form-data**，
  文档写单个 `image` 字段，多张按 OpenAI 惯例重复该字段
- 无参考图 → `POST /v1/images/generations`（JSON，同一套字段）
- 返回 `data[0].b64_json`

和别家的差别：它要的是**真的文件字节**，不是 data URI 字符串。
所以本程序传下来的 data URI 要先解回 bytes 再塞 multipart。
"""

from __future__ import annotations

import base64
from typing import Callable, Optional

from ..apiutil import ApiError, extract_image_items
from .base import ImageTask, Provider

IMAGE_MODELS = ["gpt-image-2", "gpt-image-1", "nano-banana"]
SIZES = ["1024x1536", "1024x1024", "1536x1024", "2048x2048", "1792x1024", "1024x1792"]


def _to_bytes(ref: str, idx: int) -> Optional[tuple]:
    """data URI → (文件名, 字节, MIME)。公网 URL 这家吃不了，跳过并告知。"""
    if ref.startswith("data:"):
        head, _, b64 = ref.partition(",")
        mime = head[5:].split(";")[0] or "image/png"
        ext = "png" if "png" in mime else "jpg"
        try:
            return (f"ref_{idx}.{ext}", base64.b64decode(b64), mime)
        except Exception:                       # noqa: BLE001
            return None
    return None


class KunjiProvider(Provider):
    id = "kunji"
    name = "坤鸡 img.yunfei.best（只出图）"
    default_base_url = "https://img.yunfei.best"
    supports = ("image",)

    def capabilities(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "default_base_url": self.default_base_url,
            "supports": list(self.supports),
            "image": {
                "models": IMAGE_MODELS,
                "default_model": "gpt-image-2",
                "sizes": SIZES,
                "default_size": "1024x1536",
                "max_refs": 9,
                "ref_mode": "data_uri",
                "notes": "有参考图走 multipart 的 /v1/images/edits（要真文件字节，"
                         "不是链接）；无参考图走 JSON 的 /v1/images/generations。"
                         "返回 b64_json。",
            },
            "notes": "这家只做图片。视频在另一个网关，本程序没接。",
        }

    def generate_image(self, task: ImageTask, dest: str, *, log: Callable = print,
                       cancel: Optional[Callable] = None,
                       poll_interval: int = 5, poll_timeout: int = 900) -> dict:
        model = task.model or "gpt-image-2"
        size = task.size or "1024x1536"
        if not (task.prompt or "").strip():
            raise ApiError("提示词是空的")

        files = []
        for i, r in enumerate(task.refs[:9], 1):
            got = _to_bytes(r, i)
            if got:
                files.append(("image", got))
            else:
                log(f"参考图{i} 不是本地图片（这家的 edits 只收文件字节，不收链接），已跳过")

        if files:
            form = [("model", (None, model)), ("prompt", (None, task.prompt)),
                    ("size", (None, size)), ("response_format", (None, "b64_json")),
                    ("n", (None, str(int(task.n or 1))))]
            data = self.session.request("POST", "/v1/images/edits", files=form + files,
                                        retries=2, timeout=600)
        else:
            body = {"model": model, "prompt": task.prompt, "size": size,
                    "response_format": "b64_json", "n": int(task.n or 1)}
            data = self.session.request("POST", "/v1/images/generations", json_body=body,
                                        retries=2, timeout=600)

        items = extract_image_items(data)
        if not items:
            raise ApiError(f"没返回图片：{str(data)[:300]}")
        self.session.save_item(items[0], dest)
        return {"task_id": "", "source": items[0][:200], "provider": self.id, "model": model}
