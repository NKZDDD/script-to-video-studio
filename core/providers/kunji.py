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
import os
from typing import Callable, Optional

from ..apiutil import ApiError, extract_image_items
from .base import ImageTask, Provider

IMAGE_MODELS = ["gpt-image-2", "gpt-image-1", "nano-banana"]
SIZES = ["1024x1536", "1024x1024", "1536x1024", "2048x2048", "1792x1024", "1024x1792"]


def _to_bytes(ref: str, idx: int) -> Optional[tuple]:
    """data URI / 本机路径 → (文件名, 字节, MIME)。公网 URL 这家吃不了。"""
    if ref.startswith("data:"):
        head, _, b64 = ref.partition(",")
        mime = head[5:].split(";")[0] or "image/png"
        ext = "png" if "png" in mime else "jpg"
        try:
            return (f"ref_{idx}.{ext}", base64.b64decode(b64), mime)
        except Exception:                       # noqa: BLE001
            return None
    if not ref.startswith("http") and os.path.isfile(ref):
        ext = os.path.splitext(ref)[1].lstrip(".").lower() or "png"
        mime = "image/png" if ext == "png" else "image/jpeg"
        with open(ref, "rb") as f:
            return (f"ref_{idx}.{ext}", f.read(), mime)
    return None


class KunjiProvider(Provider):
    id = "kunji"
    name = "坤鸡 img.yunfei.best（只出图）"
    default_base_url = "https://img.yunfei.best"
    supports = ("image",)
    # multipart 接口：只收文件字节。声明出来，参考图解析器才不会先上传成链接
    # 再被这里丢掉（那是静默降级，脸会飘）。
    ref_mode = "bytes"

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

        files, dropped = [], []
        for i, r in enumerate(task.refs[:9], 1):
            got = _to_bytes(r, i)
            if got:
                files.append(("image", got))
            else:
                dropped.append(i)
        if dropped:
            # 以前是 log 一句然后照着出图 —— 那是静默降级：图有了，但没有来源
            # 参考，连续性锚点的脸不认识本人，而且任务标 ok 没人知道。
            # 报错走失败路径，优先级链会自动换下一家（收链接的那种）补上。
            raise ApiError(
                f"这家的 edits 只收文件字节，不收链接，第 {dropped} 张参考图给的是链接。"
                f"本该有 {len(task.refs)} 张参考图，能用的只有 {len(files)} 张 —— "
                f"少了参考图出来的就不是同一个人/同一个东西，所以不出这张图。"
                f"要么把这一类活排给收链接的服务商，要么关掉对象存储让参考图走本机文件。",
                status=0, kind="task_fatal")

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
