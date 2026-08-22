# -*- coding: utf-8 -*-
"""阿珂（snumom.com）。**只做视频**，同一个网关有两条模型线：

  · Grok Imagine 线（grok-imagine-video-1.5-preview）
  · minimax_h3 线（minimax_h3-768p / minimax_h3-1080p）

两条线共用 `POST /v1/videos` 提交 + `GET /v1/videos/{id}` 轮询，但请求体的
规矩**几乎相反**，所以按模型分支各走各的（`_is_h3`）。

Grok 线四个和别家不一样、写错就白花钱的点：
  1. `seconds` 是**字符串**（`"8"`），不是整数。
  2. **`size` 同时决定分辨率和比例** —— 没有单独的 aspect_ratio / resolution 字段。
     只有四种组合：720p 16:9=1280x720 / 9:16=720x1280；480p 16:9=854x480 / 9:16=480x854。
  3. 参考图有**两个字段、二选一**，且形状不同：
       `reference_images` = **对象**数组 `[{"url": "…"}]`（文档推荐，只能给公网 URL）
       `input_reference`  = **字符串**数组（URL 或 base64，可带 data:image/...;base64, 前缀）
     所以：全是链接 → reference_images；含本地图 → 整批走 input_reference。
  4. 最多 **7 张**。

H3 线（2026-08 文档）自己的硬规矩：
  · size 从「模型×画幅」对照表取，768P 列 / 1080P 列**不通用**（768p 模型传
    1080P 的 size 直接 400）；1080p 没有 21:9（上游 2560x1088 会创建失败）。
  · 参考素材**只收三个字段**：reference_images（对象数组，无 role）、
    reference_videos / reference_audios（**裸字符串数组**）。再写 input_reference
    等任何别的字段会报「metadata is too long」—— 一个都不许多发。
  · 上限：图≤9 视频≤3 音频≤3，合计≤12；参考图**没有首尾帧模式**。
  · seconds 4～15（数字或字符串都认），不传按 5 秒生成**并计费**。
  · `unknown` 状态不是失败（还在排队），照常轮询。
  · 计费按秒：768p $0.07/s、1080p $0.11/s；22 点后八折、0-8 点五折；
    参考图第 6 张起每张 +$0.10；768p 的 4:3/3:4/3:2/2:3 每秒 +$0.005。
    1080P 排队几分钟到十几分钟正常，别急着停。
"""

from __future__ import annotations

from typing import Callable, Optional

from ..apiutil import ApiError, extract_task_id, extract_video_url
from .base import Provider, VideoTask

GROK_MODELS = ["grok-imagine-video-1.5-preview"]
H3_MODELS = ["minimax_h3-768p", "minimax_h3-1080p"]
VIDEO_MODELS = GROK_MODELS + H3_MODELS
RATIOS = ["16:9", "9:16"]
RESOLUTIONS = ["720p", "480p"]
MAX_REFS = 7

# size 是唯一的画面控制字段：(分辨率, 比例) → 取值
SIZE_TABLE = {
    ("720p", "16:9"): "1280x720", ("720p", "9:16"): "720x1280",
    ("480p", "16:9"): "854x480", ("480p", "9:16"): "480x854",
}

# H3 的 size 对照表（文档第四节）—— 计费按表内比例识别，两列不能混用。
# 1080P 的 21:9 上游会创建失败，干脆不给这个键，选了当场说清。
H3_SIZES_768 = {"16:9": "1376x768", "9:16": "768x1376", "1:1": "1024x1024",
                "4:3": "1152x864", "3:4": "864x1152", "3:2": "1248x832",
                "2:3": "832x1248", "21:9": "1792x768"}
H3_SIZES_1080 = {"16:9": "1920x1080", "9:16": "1080x1920", "1:1": "1440x1440",
                 "4:3": "1664x1248", "3:4": "1248x1664", "3:2": "1728x1152",
                 "2:3": "1152x1728"}
# H3 参考素材上限：图≤9 视频≤3 音频≤3，合计≤12
H3_MAX = {"images": 9, "videos": 3, "audios": 3, "total": 12}


def _is_h3(model: str) -> bool:
    return (model or "") in H3_MODELS


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
    name = "阿珂 snumom.com（Grok Imagine / minimax_h3 视频）"
    aliases = ("snumom", "阿珂", "ako")
    default_base_url = "https://snumom.com"
    supports = ("video",)
    # Grok 线链接和 base64 都能收（分别走 reference_images / input_reference）；
    # H3 线明令只收 reference_* 三个字段 —— 本地图必须先上传换成公网链接。
    ref_mode = "data_uri"
    url_only_models = tuple(H3_MODELS)

    def capabilities(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "default_base_url": self.default_base_url,
            "supports": list(self.supports),
            "video": {
                "models": VIDEO_MODELS,
                "default_model": GROK_MODELS[0],
                "ratios": ["16:9", "9:16", "1:1", "4:3", "3:4", "3:2", "2:3", "21:9"],
                "durations": list(range(1, 16)),
                "default_duration": 8,
                "resolutions": RESOLUTIONS,
                # 上限取两条线的最大值（H3 线 9；Grok 线 7 由 body 层裁）
                "max_refs": 9,
                "ref_mode": "data_uri",
                "notes": "两条模型线，规矩不同（程序按模型自动分派）：\n"
                         "**Grok Imagine** —— 只有 16:9 / 9:16，480p/720p 合成一个 "
                         "`size` 字段（无 aspect_ratio）；seconds 是字符串 1–15 秒；"
                         "参考图最多 7 张：全链接走 reference_images，含本地图整批走 "
                         "input_reference。\n"
                         "**minimax_h3** —— size 由「模型×画幅」对照表锁定（程序自动"
                         "查表），1080p 无 21:9；参考素材只收 reference_* 三个字段、"
                         "**必须公网链接**（本机图先配对象存储），图≤9 视频≤3 音频≤3 "
                         "合计≤12，无首尾帧模式；seconds 4–15。按秒计费：768p $0.07/s、"
                         "1080p $0.11/s，22 点后八折、0-8 点五折；参考图第 6 张起每张 "
                         "+$0.10。1080P 排队几分钟到十几分钟正常，别急着停。",
            },
            "notes": "400 多半是参数越界或参考图 URL 不可达，429 是并发/日额度。",
        }

    # ---------------------------------------------------------------- video
    def build_video_body(self, task: VideoTask, log: Callable = print) -> dict:
        """按模型线拼 body —— 两条线的字段规矩几乎相反，别想着糊成一套。"""
        model = task.model or GROK_MODELS[0]
        if _is_h3(model):
            return self._h3_body(task, log)
        return self._grok_body(task, log)

    def _grok_body(self, task: VideoTask, log: Callable) -> dict:
        model = task.model or GROK_MODELS[0]
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
        return body

    def _h3_body(self, task: VideoTask, log: Callable) -> dict:
        """H3 线：只发 model/prompt/seconds/size + reference_* 三个字段。

        多发任何一个别的字段（input_reference / images / extra…）都会报
        「metadata is too long」—— 不是参数错，是字段本身就不许在。
        """
        model = task.model or H3_MODELS[0]
        ratio = (task.ratio or "9:16").strip()
        table = H3_SIZES_1080 if "1080" in model else H3_SIZES_768
        size = table.get(ratio)
        if not size:
            if "1080" in model and ratio == "21:9":
                raise ApiError(
                    "minimax_h3-1080p 暂不支持 21:9（上游 2560x1088 会创建失败）。"
                    "要 21:9 画幅请换 minimax_h3-768p。",
                    status=0, kind="task_fatal")
            raise ApiError(
                f"{model} 不认识画幅 {ratio}；这条线只收 "
                f"{'、'.join(table)} 这几种（768P 和 1080P 的 size 表不通用）。",
                status=0, kind="task_fatal")

        body: dict = {"model": model, "prompt": task.prompt or "",
                      # 不传 seconds 上游按 5 秒生成并计费 —— 明着传，账才算得清
                      "seconds": max(4, min(15, int(task.duration or 5))),
                      "size": size}

        refs = [r for r in (task.refs or []) if str(r).startswith(("http://", "https://"))]
        vids = [v for v in (task.extra.get("video_refs") or task.extra.get("videos") or [])
                if str(v).startswith(("http://", "https://"))]
        auds = [a for a in (task.extra.get("audio_refs") or task.extra.get("audios") or [])
                if str(a).startswith(("http://", "https://"))]
        dropped = (len(task.refs or []) - len(refs)
                   + len(task.extra.get("video_refs") or task.extra.get("videos") or [])
                   - len(vids)
                   + len(task.extra.get("audio_refs") or task.extra.get("audios") or [])
                   - len(auds))
        if dropped:
            # H3 线只收公网链接；本地图混进来只能舍掉，别让整个任务被拒
            log(f"阿珂 {model}（H3 线）参考素材只收公网链接，已舍掉 {dropped} 项本机素材")
        # 合计≤12：图片最要紧（人物身份全靠它），超了先舍音频、再舍视频
        imgs = refs[:H3_MAX["images"]]
        vids = vids[:H3_MAX["videos"]]
        auds = auds[:H3_MAX["audios"]]
        while len(imgs) + len(vids) + len(auds) > H3_MAX["total"]:
            if auds:
                auds.pop()
            elif vids:
                vids.pop()
            else:
                imgs.pop()
        if imgs:
            # 不带 role —— 这条线没有首尾帧模式
            body["reference_images"] = [{"url": r} for r in imgs]
        if vids:
            body["reference_videos"] = vids            # 裸字符串数组（文档原样）
        if auds:
            body["reference_audios"] = auds            # 裸字符串数组（文档原样）
        log(f"阿珂 {model}（H3 线）: size={size} seconds={body['seconds']} "
            f"图{len(imgs)} 视频{len(vids)} 音频{len(auds)}")
        return body

    def generate_video(self, task: VideoTask, dest: str, *, log: Callable = print,
                       cancel: Optional[Callable] = None,
                       poll_interval: int = 5, poll_timeout: int = 2400) -> dict:
        model = task.model or GROK_MODELS[0]
        body = self.build_video_body(task, log)

        data = self.session.request("POST", "/v1/videos", json_body=body, retries=2, timeout=300)
        url = extract_video_url(data)
        task_id = extract_task_id(data)
        if not url:
            if not task_id:
                raise ApiError(f"提交没返回任务 ID: {str(data)[:300]}")
            # H3 线的 completed 成片在 metadata.url，没有就 GET content 兜底；
            # 排队中 status=unknown 不是失败，轮询层照常等。
            url = self.session.poll("/v1/videos/{id}", task_id, picker=extract_video_url,
                                    interval=poll_interval, timeout=poll_timeout,
                                    content_path_tpl="/v1/videos/{id}/content",
                                    log=log, cancel=cancel)
        self.session.save_item(url, dest)
        return {"task_id": task_id, "source": url, "provider": self.id, "model": model}
