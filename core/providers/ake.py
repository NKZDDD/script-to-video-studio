# -*- coding: utf-8 -*-
"""阿珂（snumom.com）。Grok Imagine 视频专线，**只做视频**。

  · 创建 POST /v1/videos
  · 查询 GET /v1/videos/{id}（建议 5 秒一次）
    queued / in_progress 生成中；completed 取 `url`；failed 看 `error` / `message`

四个和别家不一样、写错就白花钱的点：
  1. `seconds` 是**字符串**（`"8"`），不是整数。
  2. **`size` 同时决定分辨率和比例** —— 没有单独的 aspect_ratio / resolution 字段。
     只有四种组合：720p 16:9=1280x720 / 9:16=720x1280；480p 16:9=854x480 / 9:16=480x854。
  3. 参考图有**两个字段、二选一**，且形状不同：
       `reference_images` = **对象**数组 `[{"url": "…"}]`（文档推荐，只能给公网 URL）
       `input_reference`  = **字符串**数组（URL 或 base64，可带 data:image/...;base64, 前缀）
     所以：全是链接 → reference_images；含本地图 → 整批走 input_reference。
  4. 最多 **7 张**。

model / prompt / seconds / size 都必填。
"""

from __future__ import annotations

from typing import Callable, Optional

from ..apiutil import ApiError, extract_task_id, extract_video_url
from .base import Provider, VideoTask

VIDEO_MODELS = ["grok-imagine-video-1.5-preview"]
RATIOS = ["16:9", "9:16"]
RESOLUTIONS = ["720p", "480p"]
MAX_REFS = 7

# size 是唯一的画面控制字段：(分辨率, 比例) → 取值
SIZE_TABLE = {
    ("720p", "16:9"): "1280x720", ("720p", "9:16"): "720x1280",
    ("480p", "16:9"): "854x480", ("480p", "9:16"): "480x854",
}


def _size_of(resolution: str, ratio: str) -> str:
    res = (resolution or "").strip().lower() or "720p"
    rat = (ratio or "").strip() or "9:16"
    if res not in RESOLUTIONS:
        res = "720p"
    if rat not in RATIOS:
        # 这家只有横竖两种，其余比例按长宽归到最近的一边
        try:
            w, h = rat.replace("：", ":").split(":")[:2]
            rat = "16:9" if int(w) >= int(h) else "9:16"
        except Exception:                                   # noqa: BLE001
            rat = "9:16"
    return SIZE_TABLE[(res, rat)]


class AkeProvider(Provider):
    id = "ake"
    name = "阿珂 snumom.com（Grok Imagine 视频）"
    aliases = ("snumom", "阿珂", "ako")
    default_base_url = "https://snumom.com"
    supports = ("video",)
    # 链接和 base64 都能收（分别走 reference_images / input_reference）
    ref_mode = "data_uri"

    def capabilities(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "default_base_url": self.default_base_url,
            "supports": list(self.supports),
            "video": {
                "models": VIDEO_MODELS,
                "default_model": VIDEO_MODELS[0],
                "ratios": RATIOS,
                "durations": list(range(1, 16)),
                "default_duration": 8,
                "resolutions": RESOLUTIONS,
                "max_refs": MAX_REFS,
                "ref_mode": "data_uri",
                "notes": "**只有 16:9 / 9:16 两种比例**，分辨率 480p/720p —— 两者合成一个 `size` "
                         "字段发出去（没有单独的 aspect_ratio）。`seconds` 是字符串，1–15 秒。"
                         "参考图最多 7 张：全是链接走 reference_images，含本地图整批走 input_reference。",
            },
            "notes": "Grok Imagine 视频专线。400 多半是参数越界或参考图 URL 不可达，429 是并发/日额度。",
        }

    # ---------------------------------------------------------------- video
    def generate_video(self, task: VideoTask, dest: str, *, log: Callable = print,
                       cancel: Optional[Callable] = None,
                       poll_interval: int = 5, poll_timeout: int = 2400) -> dict:
        model = task.model or VIDEO_MODELS[0]
        sec = int(task.duration or 8)
        if not 1 <= sec <= 15:
            near = min(15, max(1, sec))
            log(f"阿珂 {model} 只支持 1–15 秒，已把 {sec} 纠正为 {near}")
            sec = near
        size = _size_of(task.resolution, task.ratio)

        refs = list(task.refs or [])
        if len(refs) > MAX_REFS:
            log(f"阿珂最多 {MAX_REFS} 张参考图，已裁掉多余 {len(refs) - MAX_REFS} 张")
            refs = refs[:MAX_REFS]

        body: dict = {"model": model, "prompt": task.prompt or "",
                      "seconds": str(sec), "size": size}
        if refs:
            # 两个字段形状不同，别混：全链接用对象数组，含本地图整批用字符串数组
            if all(str(r).startswith(("http://", "https://")) for r in refs):
                body["reference_images"] = [{"url": r} for r in refs]
            else:
                body["input_reference"] = refs
        log(f"阿珂 {model}: seconds='{sec}' size={size} 参考图{len(refs)}张"
            f"（{'reference_images' if 'reference_images' in body else 'input_reference' if refs else '无'}）")

        data = self.session.request("POST", "/v1/videos", json_body=body, retries=2, timeout=300)
        url = extract_video_url(data)
        task_id = extract_task_id(data)
        if not url:
            if not task_id:
                raise ApiError(f"提交没返回任务 ID: {str(data)[:300]}")
            url = self.session.poll("/v1/videos/{id}", task_id, picker=extract_video_url,
                                    interval=poll_interval, timeout=poll_timeout,
                                    log=log, cancel=cancel)
        self.session.save_item(url, dest)
        return {"task_id": task_id, "source": url, "provider": self.id, "model": model}
