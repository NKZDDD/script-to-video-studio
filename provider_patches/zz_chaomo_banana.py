# -*- coding: utf-8 -*-
"""超模·香蕉 —— 独立服务商外挂。

两个坑，一个文件修掉：

坑一（Key 槽）：exe 服务端把超模的 Key 槽硬编码成 llm / image_1k(TH) /
image_4k(Native) / video 四个（app.py `_CHAOMO_KEY_FIELDS`），插件**加不了
第五个槽**。所以香蕉(gemini)系模型要单独一把 Key，唯一干净的做法是注册一个
独立服务商：同一个超模网关、只挂香蕉模型、走通用 Key 框。

坑二（JPEG 撞 PNG 闸）：香蕉线返回的是公网 URL（`..._native.jpg`），
下载下来**是 JPEG**，而管线资产图是 .png 路径 —— apiutil 落盘时的
「文件头 vs 扩展名」对账会删文件报错「存下来的东西不是 .png」（和坤鸡
20261008.2 修的是同一种病）。本插件包住 `session._save_once`：先下载探
魔数，JPEG→PNG 无损转码再交回原落盘逻辑，对账自然通过。格式本来就对的
（比如 PNG URL）只借道已下载的字节，不多下一次。

用法：
  1. 本文件放进数据目录 providers/ → 设置页「重新扫描」或「导入服务商文件」
  2. 服务商列表出现「超模·香蕉」→ 在它的 Key 框填香蕉分组的 Key
     （支持一行一把的多 Key 池，和别家一样）
  3. 「服务商与并发」页里，想走香蕉的能力行（资产图/交联板等）
     服务商选「超模·香蕉」，模型填 gemini 系
  4. 同一行「清晰度」选 1K/2K/4K（留空=跟随任务/默认）。参数名是
     image_size，与 gpt-image 系的 size 不同，见 _generate_image_bare
  5. 原来的「超模」不受影响，TH/Native Key 继续各管各的 gpt-image 系

注意：
  · 只支持 image 能力——这个服务商不会出现在 LLM/视频的下拉里
  · 模型名单按 2026-10-08 实拉（香蕉分组 key 的 /v1/models）；
    要加新模型改 _BANANA_MODELS 即可
"""
import base64
import io
import os

import requests

from core.apiutil import RETRYABLE, ApiError, extract_task_id
from core.providers.chaomo import ChaomoProvider, _to_ratio

_BANANA_MODELS = [
    "gemini-3-pro-image",        # Nano Banana Pro（1K/2K/4K，¥0.09/0.10）
    "gemini-3.1-flash-image",    # Nano Banana 2（512/1K/2K/4K，¥0.07/0.08）
    "gemini-nano-banana-2.1",    # Nano Banana 2.1（512/1K/2K/4K，¥0.07/0.08）
]

# 清晰度档位（2026-10-08 香蕉分组 key 实拉 /v1/models 的 supported_image_sizes）：
# pro 没有 512。512 档不往声明里写 —— 界面的清晰度下拉只认 1K/2K/4K
# （web/agent.js 白名单硬编码），写进去也选不到，白添歧义。
_DEFAULT_SIZES = ["1K", "2K", "4K"]
_SIZES_BY_MODEL = {
    "gemini-3-pro-image": ["1K", "2K", "4K"],
    "gemini-3.1-flash-image": _DEFAULT_SIZES,
    "gemini-nano-banana-2.1": _DEFAULT_SIZES,
}

_MIME = {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp"}
_FMT = {"png": "PNG", "jpg": "JPEG", "webp": "WEBP"}


def _sniff(raw: bytes) -> str:
    """探图片真实格式（返回 png/jpg/webp/gif，认不出给空串）。"""
    if raw[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if raw[:3] == b"\xff\xd8\xff":
        return "jpg"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "webp"
    if raw[:4] == b"GIF8":
        return "gif"
    return ""


def _fetch_raw(item: str) -> bytes:
    """URL / 裸 base64 → 原始字节。

    空 body 按 retryable 报（错误串带「字节」字样，外层 save_item 的重取
    循环就认这个口径），和原版 http 下载的空 body 重取行为对齐。
    """
    if item.startswith("http"):
        r = requests.get(item, timeout=(30, 300),
                         headers={"User-Agent": "ScriptToVideoRunner/2.0"})
        if r.status_code >= 400:
            raise ApiError(f"结果地址返回 HTTP {r.status_code}，"
                           f"字节没取到。来源：{item}", 0, RETRYABLE)
        if not r.content:
            raise ApiError(f"结果地址返回 200 但 body 是空的（0 字节），"
                           f"将重取同一地址。来源：{item}", 0, RETRYABLE)
        return r.content
    return base64.b64decode(item)          # 裸 base64；解不动会抛给上层兜底


def _to_data_uri(raw: bytes, want: str) -> str:
    """原始字节 → 无损转码成目标格式 → data URI。"""
    from PIL import Image
    with Image.open(io.BytesIO(raw)) as im:
        im.load()
        if _FMT[want] == "JPEG" and im.mode not in ("RGB", "L"):
            im = im.convert("RGB")          # JPEG 没有 alpha
        buf = io.BytesIO()
        im.save(buf, format=_FMT[want])
    return f"data:{_MIME[want]};base64,{base64.b64encode(buf.getvalue()).decode()}"


class ChaomoBananaProvider(ChaomoProvider):
    id = "chaomo_banana"
    name = "超模·香蕉（独立Key）"
    aliases = ("chaomobanana", "超模香蕉", "cmbanana")
    supports = ("image",)

    # 只暴露香蕉模型；PINNED 保证不依赖 list_models 也能出现在模型下拉里
    IMAGE_MODELS = list(_BANANA_MODELS)
    PINNED_IMAGE_MODELS = list(_BANANA_MODELS)

    def capabilities(self) -> dict:
        """在父类能力上换掉 model_options：香蕉模型各报各的清晰度档位。

        界面「清晰度」下拉读 model_options[模型].resolutions（没拉过模型
        列表时回落 image.resolutions），两处都补上 —— 不点「拉取模型」也能选。
        不覆盖的话父类 IMAGE_MODEL_OPTIONS 里没有香蕉条目，
        agent_runtime 的「模型不支持所选清晰度」会把任何清晰度都拦下来。
        """
        cap = super().capabilities()
        img = cap.get("image", {})
        # 父类 capabilities 里 models/default_model 用的是 chaomo 模块级常量，
        # 类属性覆盖不到 —— 这里必须整个换掉，否则模型下拉还是 gpt-image
        # 全家桶，agent_runtime 的「模型不在当前能力清单」会把香蕉模型拒掉。
        img["models"] = list(_BANANA_MODELS)
        img["default_model"] = "gemini-nano-banana-2.1"
        img["model_options"] = {
            m: {"resolutions": list(_SIZES_BY_MODEL.get(m, _DEFAULT_SIZES)),
                "default_resolution": "1K"}
            for m in _BANANA_MODELS
        }
        img["resolutions"] = list(_DEFAULT_SIZES)   # 兜底：供未声明模型的回落
        return cap

    def generate_image(self, task, dest, **kw):
        """出图入口：先装转码闸，再走 _generate_image_bare，走完还原。"""
        s = self.session
        real = s._save_once

        def _transcoding_save_once(item, dest_, *, log=None):
            want = os.path.splitext(dest_)[1].lower().lstrip(".")
            if want == "jpeg":
                want = "jpg"
            if want not in _MIME:
                return real(item, dest_, log=log)
            try:
                if item.startswith("data:"):
                    raw = base64.b64decode(item.partition(",")[2])
                else:
                    raw = _fetch_raw(item)
                have = _sniff(raw)
            except Exception:
                return real(item, dest_, log=log)   # 取不动/认不出：交回原逻辑
            if not have or have == want:
                return real(item, dest_, log=log)   # 格式本来就对：原样走
            if log:
                log(f"结果实际是 {have}（{item[:80]}），"
                    f"按目标 .{want} 无损转码后落盘")
            try:
                item = _to_data_uri(raw, want)
            except Exception:
                return real(item, dest_, log=log)   # 转码不动：交回原逻辑报错
            return real(item, dest_, log=log)

        s._save_once = _transcoding_save_once
        try:
            return self._generate_image_bare(task, dest, **kw)
        finally:
            s._save_once = real

    def _generate_image_bare(self, task, dest, **kw):
        """香蕉线出图本体：请求带 image_size 清晰度。

        不能直接 super()：父类只在 TH_SIZED_MODELS 才发清晰度且参数名叫
        size，香蕉线的参数名是 image_size（2026-10-08 实拉
        generation_parameters：model/prompt/image_size/ratio/n/response_format）；
        且父类的档位校验表（IMAGE_MODEL_OPTIONS）里没有香蕉条目，
        用户一选清晰度就会在本地被拦死。请求结构照抄父类（async / URL
        轮询 / 参考图 multipart image[] 那套在香蕉线上实测是通的），
        只把校验表换成香蕉自己的、补上 image_size 字段。
        """
        log = kw.get("log", print)
        model = task.model or _BANANA_MODELS[0]
        ratio = _to_ratio(task.size)
        resolution = str(task.extra.get("resolution") or "").strip().upper()
        allowed = _SIZES_BY_MODEL.get(model, _DEFAULT_SIZES)
        if resolution and resolution not in allowed:
            raise ApiError(f"{model} 支持的图片清晰度：{'/'.join(allowed)}，"
                           f"不支持 {resolution}。", kind="task_fatal")
        size_kv = [("image_size", (None, resolution))] if resolution else []
        refs = list(task.refs or [])

        if refs:
            files = [("model", (None, model)), ("prompt", (None, task.prompt or "")),
                     ("ratio", (None, ratio)), ("n", (None, "1")),
                     ("response_format", (None, "url")),
                     ("async", (None, "true")),
                     ("include_metadata", (None, "true")),
                     *size_kv]
            attached = 0
            for i, ref in enumerate(refs, start=1):
                got = self._ref_bytes(ref, i, log=log)
                if got:
                    files.append(("image[]", (got[1], got[0], got[2])))
                    attached += 1
            if not attached:
                raise ApiError(f"超模·香蕉图生图：{len(refs)} 张参考图一张都没转成文件，"
                               "不出这张图 —— 少了参考图出来的就不是同一个人。")
            log(f"超模·香蕉 图生图 {model}: ratio={ratio} "
                f"image_size={resolution or '默认'} 参考图{attached}张")
            data = self.session.request("POST", "/v1/images/edits", files=files,
                                        retries=2, timeout=600)
        else:
            body = {"model": model, "prompt": task.prompt, "ratio": ratio,
                    "n": 1, "response_format": "url", "async": True,
                    "include_metadata": True}
            if resolution:
                body["image_size"] = resolution
            log(f"超模·香蕉 文生图 {model}: ratio={ratio} "
                f"image_size={resolution or '默认'}")
            data = self.session.request("POST", "/v1/images/generations",
                                        json_body=body, retries=2, timeout=600)

        items, final = self._pick_images(
            data, "", log=log,
            poll_interval=kw.get("poll_interval", 5),
            poll_timeout=kw.get("poll_timeout", 900))
        if not items:
            raise ApiError(f"出图没返回可用结果: {str(data)[:300]}")
        self.session.save_item(items[0], dest, log=log)
        log("超模·香蕉 图片已下载，正在核验图片信息")
        self.check_meta(self.meta_of(final), items, dest=dest, log=log)
        log("超模·香蕉 图片结果已就绪")
        return {"task_id": extract_task_id(data), "source": items[0][:200],
                "provider": self.id, "model": model}
