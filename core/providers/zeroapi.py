# -*- coding: utf-8 -*-
"""零视工坊（zeroapi.ai-ren.cn）。

对齐 respect_comfyui/zeroapi_nodes.py 的四个节点：
  · 图片        POST /v1/images/generations（异步，轮询）；接参考图走 /v1/images/edits
  · Sora/VEO    POST /v1/videos，参考图进 input_reference（多张用 | 分隔）
  · 图生视频    POST /v1/videos，参考图进 image / images[]
  · SD2 新接口  POST /v1/videos，duration+aspect_ratio 必填，**参考素材只收 HTTPS URL**

两个坑是实测踩出来的，别去掉：
  1. 比例必须**显式**发 aspect_ratio + ratio。只给 size 让服务端推断，
     推断失败会静默回落成横屏 16:9 —— 接口不报错，就是画面躺倒了。
  2. seconds 这个字段该站要**字符串**，发数字会 400 invalid_json。
     而 SD2 新接口用的是 duration，且要 int。同名不同型，别混。
"""

from __future__ import annotations

from typing import Callable, Optional

from ..apiutil import (ApiError, TASK_FATAL, extract_image_items, extract_task_id,
                       extract_video_url)
from .base import ImageTask, Provider, VideoTask

# 文档允许的六种比例
RATIOS = ["9:16", "16:9", "1:1", "4:3", "3:4", "21:9"]
_RATIO_VALUES = {"21:9": 21 / 9, "16:9": 16 / 9, "4:3": 4 / 3, "1:1": 1.0,
                 "3:4": 3 / 4, "9:16": 9 / 16}

# Sora/VEO 系：参考图走 input_reference（| 分隔）
SORA_MODELS = ["veo_3_1-fast", "veo_3_1-fast-fl", "sora-2", "sora-2-pro"]
# 图生视频系：参考图走 image / images[]，接受 base64
I2V_MODELS = ["seedance_2_fast_480p", "vad3", "omni_flash", "grok-1.5"]
# SD2 新接口：duration 只能 5/10/15，aspect_ratio 必填，720P 固定，只收 HTTPS URL
SD2_MODELS = ["sd2-fast"]
SD2_DURATIONS = [5, 10, 15]

I2V_DURATIONS = [4, 5, 8, 10, 12, 15, 20]


def _ratio_of(want: str) -> str:
    """把 '9:16' 或 '1080x1920' 都归一成官方比例名。认不出返回空串。"""
    s = (want or "").strip().lower().replace("×", "x")
    if s in _RATIO_VALUES:
        return s
    try:
        w, h = s.split("x")[:2]
        val = int(w) / int(h)
    except Exception:                                   # noqa: BLE001
        return ""
    return min(_RATIO_VALUES.items(), key=lambda kv: abs(kv[1] - val))[0]


def _snap(val: int, allowed: list, log: Callable, model: str, field: str) -> int:
    if val in allowed:
        return val
    near = min(allowed, key=lambda a: abs(a - val))
    log(f"零视 {model} 的 {field} 只能是 {allowed}，已把 {val} 纠正为 {near}")
    return near


class ZeroApiProvider(Provider):
    id = "zeroapi"
    name = "零视工坊 zeroapi.ai-ren.cn"
    default_base_url = "https://zeroapi.ai-ren.cn"
    supports = ("image", "video")
    # 只有 SD2 新接口挑食：只收公网链接。其余模型直接吃图片内容。
    url_only_models = tuple(SD2_MODELS)

    def capabilities(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "default_base_url": self.default_base_url,
            "supports": list(self.supports),
            "image": {
                "models": ["gpt-image-2", "gpt-image-2-4k", "nano_banana_2", "nano_banana_pro"],
                "default_model": "gpt-image-2",
                "sizes": ["1024x1536", "1024x1024", "1536x1024", "2048x2048"],
                "default_size": "1024x1536",
                "max_refs": 9,
                "ref_mode": "data_uri",
                "notes": "异步出图，自动轮询。接了参考图会改走 /v1/images/edits。",
            },
            "video": {
                "models": I2V_MODELS + SORA_MODELS + SD2_MODELS,
                "default_model": "seedance_2_fast_480p",
                "ratios": RATIOS,
                "durations": I2V_DURATIONS,
                "default_duration": 10,
                "resolutions": [""],
                "max_refs": 9,
                "ref_mode": "data_uri",
                "notes": "比例会显式发 aspect_ratio+ratio（只给尺寸让它猜，会静默变成横屏）。"
                         "⚠ sd2-fast 是新接口，参考素材只收公网 HTTPS 链接，"
                         "本程序的故事板是本地文件、走 data URI，**用不了 sd2-fast**；"
                         "做图生视频请用 seedance_2_fast_480p / vad3 / omni_flash / grok-1.5。",
            },
            "notes": "和 ComfyUI 里的「零视工坊」四个节点同源。",
        }

    # ---------------------------------------------------------------- image
    def generate_image(self, task: ImageTask, dest: str, *, log: Callable = print,
                       cancel: Optional[Callable] = None,
                       poll_interval: int = 5, poll_timeout: int = 900) -> dict:
        model = task.model or "gpt-image-2"
        body = {
            "model": model,
            "prompt": task.prompt,
            "size": task.size or "1024x1536",
            "n": int(task.n or 1),
            "response_format": "url",
        }
        if task.refs:
            # 有参考图 → edits。该站没在文档里列，但按 OpenAI 兼容惯例可用；
            # 这里仍走 json 而非 multipart，因为参考图已经是 data URI 了。
            body["images"] = task.refs[:9]
            path = "/v1/images/edits"
        else:
            path = "/v1/images/generations"
        data = self.session.request("POST", path, json_body=body, retries=2, timeout=600)
        items = extract_image_items(data)
        task_id = extract_task_id(data)
        if not items:
            if not task_id:
                raise ApiError("提交没返回图片也没返回 task_id")
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
        model = task.model or "seedance_2_fast_480p"
        refs = list(task.refs or [])[:9]
        ratio = _ratio_of(task.ratio) or "9:16"
        dur = int(task.duration or 10)

        if model in SD2_MODELS:
            body = self._sd2_body(model, task.prompt, dur, ratio, refs, log)
        elif model in SORA_MODELS:
            body = self._sora_body(model, task.prompt, dur, ratio, refs)
        else:
            body = self._i2v_body(model, task.prompt, dur, ratio, refs, log)

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

    # -- 三种接口的 body 各不相同，分开写比塞一堆 if 清楚 -------------------
    def _with_ratio(self, body: dict, ratio: str) -> dict:
        # 两个别名都发：文档写 aspect_ratio，实测部分模型读 ratio
        body["aspect_ratio"] = ratio
        body["ratio"] = ratio
        return body

    def _i2v_body(self, model, prompt, dur, ratio, refs, log) -> dict:
        dur = _snap(dur, I2V_DURATIONS, log, model, "duration")
        # duration 给数字、seconds 给字符串（该站 seconds 是 string，发数字会 400）
        body = self._with_ratio({"model": model, "prompt": prompt,
                                 "duration": dur, "seconds": str(dur),
                                 "stream": False}, ratio)
        if len(refs) == 1:
            body["image"] = refs[0]
        elif refs:
            body["images"] = refs
        return body

    def _sora_body(self, model, prompt, dur, ratio, refs) -> dict:
        body = self._with_ratio({"model": model, "prompt": prompt}, ratio)
        if dur > 0:
            body["seconds"] = str(dur)          # 字符串，同上
        if refs:
            body["input_reference"] = "|".join(refs)
        return body

    def _sd2_body(self, model, prompt, dur, ratio, refs, log) -> dict:
        if refs and any(r.startswith("data:") for r in refs):
            # 走到这里说明上传层没生效（没配对象存储、或上传失败后被降级）。
            # 与其发出去等 400，不如现在就说清为什么、两条出路各是什么。
            raise ApiError(
                f"零视 {model}（SD2 新接口）的参考素材只收公网 HTTPS 链接，"
                f"不接受本地图片。要用它，得先在「设置 → 参考图上传」配一个对象存储"
                f"（R2/OSS/COS/MinIO 都行），程序会自动把故事板传上去换成链接。"
                f"不想配的话，把这一类任务的模型换成 "
                f"{' / '.join(I2V_MODELS[:3])} 之一——这些能直接吃本地图。",
                kind=TASK_FATAL)
        body = {"model": model, "prompt": prompt,
                "duration": _snap(dur, SD2_DURATIONS, log, model, "duration"),
                "aspect_ratio": ratio}              # 文档：必填
        if refs:
            body["images"] = refs
        return body
