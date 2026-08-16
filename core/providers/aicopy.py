# -*- coding: utf-8 -*-
"""小裴 aicopy（api.aicopy.top）。对齐 respect_comfyui 的 image_nodes / video_nodes。

值钱的地方是**它有一堆 gpt-image-2 的备用通道**：`gpt-image-2应急通道01..06` 和
`GPT本地版*` 系列。别家 image-2 出问题时，这里能换通道继续 —— 同一个模型、
不同线路，比换模型对画风的影响小得多。

- 图片：`POST /v1/images/generations`（JSON）。参考图放 `image` 字段，
  **要裸 base64，不带 `data:` 前缀**（这点和别家都不一样）。
  返回 b64_json 或 url。
- 视频：`POST /v1/videos` 提交 + `GET /v1/videos/{id}` 轮询。
"""

from __future__ import annotations

from typing import Callable, Optional

from ..apiutil import (ApiError, extract_image_items, extract_task_id,
                       extract_video_url)
from .base import ImageTask, Provider, VideoTask

# gpt-image-2 的多条线路 —— 这家的核心价值
IMAGE_MODELS = [
    "gpt-image-2应急通道",
    "gpt-image-2应急通道01", "gpt-image-2应急通道02", "gpt-image-2应急通道03",
    "gpt-image-2应急通道04", "gpt-image-2应急通道05", "gpt-image-2应急通道06",
    "GPT本地版", "GPT本地版1k", "GPT本地版2k", "GPT本地版4k",
    "GPT本地版-通道1", "GPT本地版1k-通道1", "GPT本地版2k-通道1", "GPT本地版4k-通道1",
    "GPT本地版-通道2", "GPT本地版1k-通道2", "GPT本地版2k-通道2", "GPT本地版4k-通道2",
    "GPT本地版-通道3", "GPT本地版1k-通道3", "GPT本地版2k-通道3", "GPT本地版4k-通道3",
]
# ---------------------------------------------------------------------------
# 视频分支（对齐「视频插件接口文档 3.3.53」，2026-08-15）
#
# ⚠ 这家的视频**不是一套协议**：16 个分支的请求体形状互不相同，选错了字段
# 多半不报错，只是参考图被静默忽略 —— 图照出、人不对。所以模型名要能反查到
# 分支，由 _branch_of() 负责；每个分支一个 body 构造函数。
#
# 3.3.25 → 3.3.53 的坑：「卡蒸」全改成了「**卡脸**」，且 sd2-*（全系按秒）、
# 火山官方sd2-*、grok-*-支持16s、*最低渠道 这几批模型名整批下线。
# ---------------------------------------------------------------------------

# #1 GROK1.0：duration + video_length + video_config 三处时长要一致，图是 data URL
GROK10_MODELS = ["grok-imagine-1.0-video", "grok-1.0-官转接口", "grok-1.0-备用接口"]
# #2 GROK1.5：seconds 字符串 + size；reference_images 是对象数组 [{url: data-url}]
GROK15_MODELS = ["grok-imagine-video-1.5-preview", "grok-1.5-官转接口",
                 "grok-1.5-备用接口", "grok-1.5-多参接口"]
# #3 Horse 官方：参数在 parameters{} 里，图是 data URL，模式由变体名锁定
HORSE_MODELS = ["happyhorse-1.1-t2v-720p", "happyhorse-1.1-t2v-1080p",
                "happyhorse-1.1-i2v-720p", "happyhorse-1.1-i2v-1080p",
                "happyhorse-1.1-r2v-720p", "happyhorse-1.1-r2v-1080p"]
# #4 Minimax-h3：走 /v1/video/generations，fps 固定 24，图带 role
H3_MODELS = ["开源h3-480p", "开源h3-720p", "开源h3-1080p", "开源h3-2k"]
# #5 火山官方：content 块数组；没有文生模式；首帧/首尾禁传 ratio
VOLC_MODELS = ["火山官方2.5-480p", "火山官方2.5-720p",
               "火山官方2.0-480p-mini", "火山官方2.0-720p-mini"]
# #6 sd-2.5 不卡脸：4-29 秒，裸 URL 数组，不发 resolution
SD25_MODELS = ["sd-2.5-480p不卡脸(按秒)", "sd-2.5-720p不卡脸(按秒)",
               "sd-2.5-480p不卡脸(按秒)-备用", "sd-2.5-720p不卡脸(按秒)-备用"]
# #7 sd2.0 全系列不卡脸：比例和模式塞在 metadata 里
SD2FULL_MODELS = ["sd2.0-720mini-不卡脸（按秒）", "sd2.0-720fast-不卡脸（按秒）",
                  "sd2.0-720满血-不卡脸（按秒）", "sd2.0-720满血（按次）不卡脸",
                  "sd2.0-720fast（按次）不卡脸", "sd2.0-1080mini-不卡脸（按秒）",
                  "sd2.0-1080fast-不卡脸（按秒）", "sd2.0-1080满血-不卡脸（按秒）"]
# #8 ad 渠道：嵌套 input{prompt, media:[{type,url}]}，固定 15 秒
AD_MODELS = ["sd2.0-480fast-ad渠道16x9", "sd2.0-480fast-ad渠道9x16",
             "sd2.0-480满血-ad渠道16x9", "sd2.0-480满血-ad渠道9x16",
             "sd2.0-720fast-ad渠道16x9", "sd2.0-720fast-ad渠道9x16",
             "sd2.0-720满血-ad渠道16x9", "sd2.0-720满血-ad渠道9x16",
             "sd2.0-1080满血-ad渠道16x9", "sd2.0-1080满血-ad渠道9x16"]
# #9 #12 #13 #14 双端点：无图/单图 → /v1/videos(input_reference)；多图 → /v1/video/generations
DUAL_MODELS = ["sd-480满血-933（按次）", "sd-720满血-933（按次）",
               "sd-480满血-933（按秒）", "sd-720满血-933（按秒）",
               "sd-2.0-480满血（卡脸）惊喜渠道", "sd-2.0-480fast（卡脸）惊喜渠道",
               "sd-2.0-720满血（卡脸）惊喜渠道", "sd-2.0-720fast（卡脸）惊喜渠道",
               "sd-2.0-1080满血（卡脸）惊喜渠道",
               "sd-2.0-720满血（不卡脸）惊喜渠道", "sd-2.0-480满血（不卡脸）惊喜渠道",
               "快乐马1.1（不卡脸）惊喜渠道", "可灵-3.0-omni（不卡脸）惊喜渠道"]
DUAL_NEEDS_N = ("快乐马1.1（不卡脸）惊喜渠道", "可灵-3.0-omni（不卡脸）惊喜渠道")
DUAL_IMAGES_KEY = ("可灵-3.0-omni（不卡脸）惊喜渠道",)      # 多图字段叫 images 不是 image_references
DUAL_SINGLE_ONLY = ("快乐马1.1（不卡脸）惊喜渠道",)         # 只支持文生/单首帧
# #10 轮换渠道：first_frame_url / reference_image_urls 那套专用字段
ROTATE_MODELS = ["sd-720fast-不卡脸（按次）", "sd-720满血-较慢（按次）",
                 "sd-720满血-不卡脸（按次）", "sd-2.5-轮换渠道（按次）",
                 "sd-720fast（按秒）", "sd-720满血（按秒）", "sd-2.5-轮换渠道（按秒）"]
ROTATE_FIXED15 = ("sd-720fast-不卡脸（按次）", "sd-720满血-较慢（按次）", "sd-720满血-不卡脸（按次）")
# #11 只支持多参考图，duration 是字符串 "15"
SD900_MODELS = ["sd-720满血-900（不售后）"]
# #15 omni：固定 10 秒、720p
OMNI_MODELS = ["omni-fast-视频生成（无水印）", "omni-fast-视频生成（带水印）",
               "omni-fast-视频编辑（无水印）", "omni-fast-视频编辑（带水印）"]
# #16 veo
VEO_MODELS = ["veo视频生成"]

VIDEO_MODELS = (GROK10_MODELS + GROK15_MODELS + HORSE_MODELS + H3_MODELS + VOLC_MODELS
                + SD25_MODELS + SD2FULL_MODELS + AD_MODELS + DUAL_MODELS + ROTATE_MODELS
                + SD900_MODELS + OMNI_MODELS + VEO_MODELS)

# 这两支的参考图必须是 Data URL（文档「字段速查」那节），其余分支收公网 URL
DATA_URL_BRANCHES = ("grok10", "grok15", "horse")

_BRANCH_TABLE = [
    ("grok10", GROK10_MODELS), ("grok15", GROK15_MODELS), ("horse", HORSE_MODELS),
    ("h3", H3_MODELS), ("volc", VOLC_MODELS), ("sd25", SD25_MODELS),
    ("sd2full", SD2FULL_MODELS), ("ad", AD_MODELS), ("dual", DUAL_MODELS),
    ("rotate", ROTATE_MODELS), ("sd900", SD900_MODELS), ("omni", OMNI_MODELS),
    ("veo", VEO_MODELS),
]

GROK_SIZES = {"16:9": "1280x720", "9:16": "720x1280", "3:2": "1080x720"}
AD_SIZES = {"16x9": "1280x720", "9x16": "720x1280"}


def branch_of(model: str) -> str:
    """模型名 → 分支 id。认不出来的按 sd2full（最通用的那套 metadata 形状）走。"""
    m = (model or "").strip()
    for name, models in _BRANCH_TABLE:
        if m in models:
            return name
    return "sd2full"


def _bare_b64(ref: str) -> str:
    """这家的 image 字段要裸 base64，不带 data: 前缀。"""
    return ref.split(",", 1)[1] if ref.startswith("data:") else ref


class AicopyProvider(Provider):
    id = "aicopy"
    name = "小裴 api.aicopy.top"
    default_base_url = "https://api.aicopy.top"
    supports = ("image", "video")

    def accepts_url(self, model: str = "", media: str = "image") -> bool:
        # 图片接口把参考图内联进 image 字段、只认裸 base64，给链接它读不了。
        # 视频里 GROK / Horse 官方两支同样是内联 Data URL（文档「字段速查」），
        # 给公网链接会被原样发出去 —— 上游读不到，参考图等于没给。
        if media == "image":
            return False
        return branch_of(model) not in DATA_URL_BRANCHES

    def needs_url(self, model: str = "", media: str = "video") -> bool:
        # 除 GROK/Horse 外的视频分支都要公网 URL（本机图得先传对象存储）
        return media == "video" and branch_of(model) not in DATA_URL_BRANCHES

    def capabilities(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "default_base_url": self.default_base_url,
            "supports": list(self.supports),
            "image": {
                "models": IMAGE_MODELS,
                "default_model": "gpt-image-2应急通道",
                "sizes": ["1024x1536", "1024x1024", "1536x1024"],
                "default_size": "1024x1536",
                "max_refs": 1,
                "ref_mode": "data_uri",
                "notes": "★ 这家有 6 条 gpt-image-2 应急通道 + 12 个 GPT本地版通道，"
                         "别家 image-2 挂了可以在这里换线路继续（同一个模型不同线路，"
                         "比换模型对画风影响小）。"
                         "⚠ 参考图只收 1 张，且要裸 base64（本程序已自动处理）。"
                         "模型名带中文，是这家自己的叫法，别改。",
            },
            "video": {
                "models": VIDEO_MODELS,
                "default_model": "sd2.0-720满血-不卡脸（按秒）",
                "ratios": ["9:16", "16:9", "1:1", "4:3", "3:4", "21:9"],
                "durations": [4, 5, 6, 8, 10, 12, 15, 20, 29],
                "default_duration": 10,
                "resolutions": [""],
                "max_refs": 30,
                "ref_mode": "data_uri",
                "notes": "⚠ **这家的视频不是一套协议**：文档 3.3.53 有 16 个分支，请求体形状"
                         "互不相同（metadata / content 块 / input.media / 双端点 / video_config…），"
                         "本类按模型名自动分派，选模型即选协议。"
                         "分辨率和模式常由**模型名锁定**（如 happyhorse 的 t2v/i2v/r2v、"
                         "ad渠道的 16x9/9x16），别指望用参数覆盖。"
                         "GROK 和 Horse 官方两支只吃 Data URL，其余分支要公网 URL —— "
                         "本类已按分支声明，解析器会给对形式。"
                         "3.3.53 起「卡蒸」全部改成「**卡脸**」，模型名照抄旧文档会 503。",
            },
            "notes": "接了很多线路，主要当 image-2 的备份来用。",
        }

    # ---------------------------------------------------------------- image
    def generate_image(self, task: ImageTask, dest: str, *, log: Callable = print,
                       cancel: Optional[Callable] = None,
                       poll_interval: int = 5, poll_timeout: int = 900) -> dict:
        model = task.model or "gpt-image-2应急通道"
        body = {"model": model, "prompt": task.prompt, "n": int(task.n or 1),
                "size": task.size or "1024x1536", "response_format": "b64_json"}
        if task.refs:
            # 以前是 log 一句「只用第 1 张」然后照样出图 —— 静默降级。
            # 状态资产要靠父资产与依赖资产定身份和空间，少一张出来就不是同一个
            # 人/同一个地方，而且任务标 ok 没人知道。报错让优先级链换支持多张的家。
            if len(task.refs) > 1:
                raise ApiError(
                    f"这家的图片接口只收 1 张参考图，这一项要 {len(task.refs)} 张。"
                    f"少了参考图出来的就不是同一个人/同一个东西，所以不出这张图。"
                    f"把需要多张参考图的活（复杂状态资产、故事板）排给别家，"
                    f"这家留着当 image-2 的应急线路（单图或无参考图的活）。",
                    status=0, kind="task_fatal")
            if task.refs[0].startswith("http"):
                raise ApiError(
                    "这家的图片接口把参考图内联进 image 字段、只认裸 base64，"
                    "拿到的却是公网链接 —— 发出去它读不了。"
                    "关掉这家的对象存储（参考图改走 data URI），或者把这类活排给别家。",
                    status=0, kind="task_fatal")
            body["image"] = _bare_b64(task.refs[0])
        data = self.session.request("POST", "/v1/images/generations", json_body=body,
                                    retries=2, timeout=600)
        items = extract_image_items(data)
        task_id = extract_task_id(data)
        if not items:
            if not task_id:
                raise ApiError(f"没返回图片也没返回 task_id：{str(data)[:300]}")
            items = self.session.poll("/v1/images/{id}", task_id, picker=extract_image_items,
                                      interval=poll_interval, timeout=poll_timeout,
                                      content_path_tpl="/v1/images/{id}/content",
                                      log=log, cancel=cancel)
        self.session.save_item(items[0], dest)
        return {"task_id": task_id, "source": items[0][:200], "provider": self.id,
                "model": model}

    # ---------------------------------------------------------------- video
    def build_video_body(self, task: VideoTask) -> tuple:
        """按模型所属分支拼 body。返回 (创建路径, body, 轮询路径)。

        16 个分支的形状互不相同，选错**多半不报错**、只是参考图被忽略 ——
        所以这里按 branch_of() 分派，别想着用一套通用形状糊过去。
        """
        model = task.model or "sd2.0-720满血-不卡脸（按秒）"
        br = branch_of(model)
        prompt = task.prompt or ""
        ratio = task.ratio or "9:16"
        sec = int(task.duration or 10)
        refs = list(task.refs or [])
        vids = [v for v in (task.extra.get("video_refs") or task.extra.get("videos") or []) if v]
        auds = [a for a in (task.extra.get("audio_refs") or task.extra.get("audios") or []) if a]
        # 模式：靠图片张数推断（studio 没有独立的"模式"概念）
        first_last = len(refs) == 2 and bool(task.extra.get("first_last"))
        vpath = "/v1/videos"

        if br == "grok10":
            sec = min((6, 10), key=lambda d: abs(d - sec))
            body = {"model": model, "prompt": prompt, "duration": sec, "video_length": sec,
                    "aspect_ratio": ratio, "resolution": "720p",
                    "video_config": {"video_length": sec, "aspect_ratio": ratio,
                                     "resolution": "720p", "preset": "normal"}}
            if len(refs) == 1:
                body["image"] = refs[0]                 # 单张走 image
            elif refs:
                body["reference_images"] = refs[:7]     # 多张走字符串数组
        elif br == "grok15":
            sec = min((6, 10, 15), key=lambda d: abs(d - sec))
            body = {"model": model, "prompt": prompt, "seconds": str(sec),
                    "size": GROK_SIZES.get(ratio, "720x1280")}
            if refs:
                body["reference_images"] = [{"url": r} for r in refs[:7]]   # 对象数组
        elif br == "horse":
            params = {"duration": max(4, min(15, sec)),
                      "resolution": "1080P" if "1080p" in model else "720P",
                      "watermark": False}
            body = {"model": model, "prompt": prompt, "parameters": params}
            if "-i2v-" in model and refs:
                body["image_url"] = refs[0]             # 首帧模式禁传 parameters.ratio
            elif "-r2v-" in model and refs:
                body["reference_images"] = refs[:9]
                params["ratio"] = ratio
            else:
                params["ratio"] = ratio
        elif br == "h3":
            vpath = "/v1/video/generations"
            body = {"model": model, "prompt": prompt, "aspect_ratio": ratio,
                    "duration": max(5, min(15, sec)), "fps": 24}
            if first_last:
                body["reference_images"] = [{"url": refs[0], "role": "first_frame"},
                                            {"url": refs[1], "role": "last_frame"}]
            elif len(refs) == 1:
                body["reference_images"] = [{"url": refs[0], "role": "first_frame"}]
            elif refs:
                body["reference_images"] = [{"url": r, "role": "reference_image"} for r in refs[:9]]
                if vids:
                    body["reference_videos"] = [{"url": v} for v in vids[:3]]
                if auds:
                    body["reference_audios"] = [{"url": a} for a in auds[:3]]
        elif br == "volc":
            if not refs:
                raise ApiError(
                    "火山官方分支没有文生视频模式（文档 #5），至少要 1 张参考图。"
                    "纯文生请把这活排给 sd-2.5 / sd2.0 全系列的单位。",
                    status=0, kind="task_fatal")
            big = "2.5" in model
            content = [{"type": "text", "text": prompt}]
            if first_last:
                content += [{"type": "image_url", "image_url": {"url": refs[0]}, "role": "first_frame"},
                            {"type": "image_url", "image_url": {"url": refs[1]}, "role": "last_frame"}]
            elif len(refs) == 1:
                content.append({"type": "image_url", "image_url": {"url": refs[0]}, "role": "first_frame"})
            else:
                for r in refs[:30 if big else 9]:
                    content.append({"type": "image_url", "image_url": {"url": r}, "role": "reference_image"})
                for v in vids[:10 if big else 3]:
                    content.append({"type": "video_url", "video_url": {"url": v}, "role": "reference_video"})
                for a in auds[:10 if big else 3]:
                    content.append({"type": "audio_url", "audio_url": {"url": a}, "role": "reference_audio"})
            body = {"model": model, "content": content, "generate_audio": True,
                    "duration": max(4, min(30 if big else 15, sec)),
                    "watermark": False, "resolution": "480p" if "480" in model else "720p"}
            if len(refs) > 2 or (refs and not first_last and len(refs) > 1):
                # 只有多参考才发 ratio；首帧/首尾发了会 InvalidParameter.TaskTypeConstraint
                body["ratio"] = ratio
        elif br == "sd25":
            body = {"model": model, "prompt": prompt,
                    "duration": max(4, min(29, sec)), "aspect_ratio": ratio}
            if refs:
                body["images"] = refs[:30]
            if vids:
                body["videos"] = vids[:10]
            if auds:
                body["audios"] = auds[:10]
        elif br == "ad":
            media = ([{"type": "reference_image", "url": r} for r in refs[:9]]
                     + [{"type": "reference_video", "url": v} for v in vids[:3]]
                     + [{"type": "reference_audio", "url": a} for a in auds[:3]])
            size = next((v for k, v in AD_SIZES.items() if k in model), "1280x720")
            body = {"model": model, "prompt": prompt, "seconds": "15", "size": size,
                    "input": {"prompt": prompt}}
            if media:
                body["input"]["media"] = media
        elif br == "dual":
            multi = len(refs) > 1
            if multi and model in DUAL_SINGLE_ONLY:
                raise ApiError(
                    f"{model} 只支持文生和单首帧（文档 #13），给了 {len(refs)} 张图。"
                    f"多参考请排给 933 或 sd-2.0 惊喜渠道的单位。",
                    status=0, kind="task_fatal")
            res = "1080p" if "1080" in model else ("480p" if "480" in model else "720p")
            if not multi:
                body = {"model": model, "prompt": prompt, "seconds": max(4, min(15, sec)),
                        "size": res, "aspect_ratio": ratio}
                if refs:
                    body["input_reference"] = {"image_url": refs[0]}    # 必须是对象
            else:
                vpath = "/v1/video/generations"
                body = {"model": model, "prompt": prompt, "duration": max(4, min(15, sec)),
                        "resolution": res, "aspect_ratio": ratio}
                body["images" if model in DUAL_IMAGES_KEY else "image_references"] = refs[:9]
                if vids:
                    body["video_references"] = vids[:3]
                if auds:
                    body["audio_references"] = auds[:3]
            if model in DUAL_NEEDS_N:
                body["n"] = 1
        elif br == "rotate":
            big = "2.5" in model
            if model in ROTATE_FIXED15:
                sec = 15
            else:
                sec = max(4, min(29 if "按秒" in model and big else 15, sec))
            body = {"model": model, "prompt": prompt, "aspect_ratio": ratio,
                    "seconds": str(sec), "resolution": "720p"}
            if first_last:
                body["first_frame_url"], body["last_frame_url"] = refs[0], refs[1]
            elif len(refs) == 1:
                body["first_frame_url"] = refs[0]
            elif refs:
                body["reference_image_urls"] = refs[:30 if big else 9]
            if vids:
                body["reference_videos"] = vids[:10 if big else 3]
            if auds:
                body["reference_audios"] = auds[:10 if big else 3]
        elif br == "sd900":
            if not refs:
                raise ApiError("sd-720满血-900（不售后）只支持多参考图（文档 #11），至少要 1 张。",
                               status=0, kind="task_fatal")
            body = {"model": model, "prompt": prompt, "duration": "15",   # 字符串
                    "aspect_ratio": ratio, "resolution": "720p",
                    "reference_images": [{"url": r} for r in refs[:9]]}
        elif br == "omni":
            body = {"model": model, "prompt": prompt, "aspect_ratio": ratio, "seconds": "10"}
            if "编辑" in model:
                if vids:
                    body["video_url" if len(vids) == 1 else "videos"] = (
                        vids[0] if len(vids) == 1 else vids[:2])
            elif first_last:
                body["first_image_url"], body["last_image_url"] = refs[0], refs[1]
            elif len(refs) == 1:
                body["first_image_url"] = refs[0]
            elif refs:
                body["images"] = refs[:5]
        elif br == "veo":
            body = {"model": model, "prompt": prompt, "aspect_ratio": ratio,
                    "resolution": "720p", "generate_audio": True,
                    "duration": min((4, 6, 8), key=lambda d: abs(d - sec))}
            if refs:
                body["image_urls"] = refs[:2]
        else:                                          # sd2full（#7）
            meta = {"ratio": ratio, "enableSound": "on"}
            if refs:
                meta["modeType"] = "frames2video" if first_last else "image2video"
            else:
                meta["modeType"] = "text2video"
            body = {"model": model, "prompt": prompt,
                    "duration": max(4, min(15, sec)), "metadata": meta}
            if refs:
                body["images"] = refs[:9]
            if vids:
                body["videos"] = vids[:3]
            if auds:
                body["audios"] = auds[:3]
        return vpath, body, vpath + "/{id}"

    def generate_video(self, task: VideoTask, dest: str, *, log: Callable = print,
                       cancel: Optional[Callable] = None,
                       poll_interval: int = 10, poll_timeout: int = 2400) -> dict:
        model = task.model or "sd2.0-720满血-不卡脸（按秒）"
        vpath, body, qpath = self.build_video_body(task)
        log(f"小裴 {model}（{branch_of(model)} 分支）→ POST {vpath}")
        data = self.session.request("POST", vpath, json_body=body,
                                    retries=2, timeout=300)
        url = extract_video_url(data)
        task_id = extract_task_id(data)
        if not url:
            if not task_id:
                raise ApiError("提交没返回视频地址也没返回 task_id")
            # 走 /v1/video/generations 创建的必须按同一路径查（文档 404 那条：
            # 多参考是 /v1/video/generations，不是 /v1/videos/generations）
            url = self.session.poll(qpath, task_id, picker=extract_video_url,
                                    interval=poll_interval, timeout=poll_timeout,
                                    content_path_tpl="/v1/videos/{id}/content",
                                    log=log, cancel=cancel)
        self.session.save_item(url, dest)
        return {"task_id": task_id, "source": url, "provider": self.id, "model": model}
