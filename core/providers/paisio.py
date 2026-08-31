# -*- coding: utf-8 -*-
"""鹤（api.paisio.online）。SD2 / SD3 / Seedance 全系。

「鹤」和「派系」是同一个网关的两个叫法 —— ComfyUI 侧 he_nodes.py 的 base_url
就是 api.paisio.online，文档是 y5dprsil1i.apifox.cn。之前这里显示成「派系」，
现在统一叫「鹤」。

视频：POST /v1/videos。旧模型沿用 metadata+images 兼容格式；Seedance 2.5
使用文档规定的 aspect_ratio/image_url/extra_* 标准格式。
图片：OpenAI 兼容 /v1/images/generations（同步或异步都兼容）。

这家还有两个能力本程序没实现，需要时再补：
  · POST /v1/images/edits —— 最多 16 张图 + mask，能做局部重绘（定向修订用得上）
  · POST /v1/virtual-assets —— 官方的参考**视频/音频**上传途径（→ va_xxx，
    再 /sync 轮询到 active）。目前 Seedance 2.5 的图片通过项目对象存储转公网 URL；
    虚拟资产上传仍未接入。
"""

from __future__ import annotations

from typing import Callable, Optional

from ..apiutil import ApiError, extract_image_items, extract_task_id, extract_video_url
from .base import ImageTask, Provider, VideoTask


# 旧控制台名称仍保留，避免已有项目失效；2026-08-30 /v1/balance 实际返回的
# 新名称带 paisio- 前缀。两组名字走同一个新版 /v1/videos 请求结构，但实际时长
# 不同：新名称经接口实测明确只收 4-15 秒，不能继续套旧名称的 4-29 秒。
SEEDANCE25_MODELS = ("seedance-2.5-480p", "seedance-2.5-720p")
PAISIO_SEEDANCE25_MODELS = ("paisio-seedance-2.5-480p",
                            "paisio-seedance-2.5-720p")
STANDARD_VIDEO_MODELS = SEEDANCE25_MODELS + PAISIO_SEEDANCE25_MODELS
SEEDANCE25_DURATIONS = list(range(4, 30))
SEEDANCE25_RATIOS = ["9:16", "16:9", "1:1", "4:3", "3:4", "21:9"]
PAISIO_SEEDANCE25_DURATIONS = list(range(4, 16))
PAISIO_SEEDANCE25_RATIOS = SEEDANCE25_RATIOS + ["3:2", "2:3"]


def _standard_limits(model: str) -> tuple[list, list, int, int, int]:
    """返回时长/比例/图片/视频/音频上限；不同控制台模型不能混用。"""
    if model in PAISIO_SEEDANCE25_MODELS:
        # 当前 Apifox VideoRequest：@Image1~9、视频/音频各最多3；真实接口又确认
        # 该模型 duration=30 会报 4-15。宁可超限前停下，也不能裁掉素材后生成。
        return PAISIO_SEEDANCE25_DURATIONS, PAISIO_SEEDANCE25_RATIOS, 9, 3, 3
    return SEEDANCE25_DURATIONS, SEEDANCE25_RATIOS, 30, 10, 10


class PaisioProvider(Provider):
    # id 保持 "paisio" 不动：它是 config.json 里 providers / chains /
    # limits.per_provider 的键，改了等于让已保存的 key 和优先级链全部失效。
    # 显示名叫「鹤」，内部键叫 paisio —— 这是两回事。
    id = "paisio"
    name = "鹤 api.paisio.online（SD2/SD3/Seedance）"
    aliases = ("he", "pis", "派系")      # 认这些别名，指到同一家
    default_base_url = "https://api.paisio.online"
    supports = ("image", "video")
    # 新文档明确要求 image_url / extra_images 是公网 http(s) URL。
    # 没配对象存储时宁可在发送前报清楚，也不能把 data URI 发出去后让参考图静默失效。
    url_only_models = STANDARD_VIDEO_MODELS

    def capabilities(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "default_base_url": self.default_base_url,
            "supports": list(self.supports),
            "image": {
                "models": ["gpt-image-2-1k", "gpt-image-2-2k", "gpt-image-2-4k",
                           "gpt-image2-low", "gpt-image2-medium", "gpt-image2-high",
                           "nano-banana-2-1k", "nano-banana-2-2k", "nano-banana-2-4k",
                           "nano-banana-pro-1k", "nano-banana-pro-2k", "nano-banana-pro-4k",
                           "gemini-3.1-flash-image-preview", "gemini-3-pro-image-preview",
                           "image-2-1K", "image-2-2K"],
                "default_model": "gpt-image-2-1k",
                "sizes": ["1024x1536", "1024x1024", "1536x1024"],
                "default_size": "1024x1536",
                "max_refs": 9,
                "ref_mode": "data_uri",
                "notes": "⚠ 这家的名字带分辨率后缀：是 gpt-image-2-1k / -2k / -4k，"
                         "**没有**不带后缀的 gpt-image-2（那是灵感鸭的写法，"
                         "填错会报「找不到这个模型」）。1k 最便宜，试跑用它。",
            },
            "video": {
                # 分辨率写在模型名里，所以不用也不能传 resolution。
                # 名字里带 fast 的便宜、带 480p 的更便宜 —— 调试和试跑用它们。
                "models": [
                    # sd3 系（新）
                    "sd3-fast-480p", "sd3-fast-720p", "sd3-480p", "sd3-720p", "sd3-1080p",
                    # sd2 系（实测过的一档在这里）
                    "sd2-pro-720p", "sd2-pro1-720p",
                    "sd2-fast-480p", "sd2-fast-720p", "sd2-fast-1080p",
                    "sd2-480p", "sd2-720p", "sd2-1080p",
                    # seedance 系
                    "seedance2.0-official2-480p", "seedance2.0-official2-720p",
                    "seedance2.0-official2-1080p", "seedance2.0-official1-720p",
                    "seedance2.0-fast2-480p", "seedance2.0-fast2-720p",
                    "seedance-discount-720p", "seedance-discount-fast-720p",
                    "seedance-2-0-fast", "seedance-2-0-mini",
                    # Seedance 2.5（2026-08-08 文档新增）
                    *STANDARD_VIDEO_MODELS,
                    # video 系
                    "video-fast-480p", "video-fast-720p",
                    "video-pro-480p", "video-pro-1080p",
                    "video431-fast-480p", "video431-fast-720p",
                    # grok
                    "grok-imagine-video-1.5-fast", "grok-imagine-video-1.5",
                ],
                "default_model": "sd2-pro-720p",
                "ratios": ["9:16", "16:9", "1:1"],
                "durations": [4, 5, 8, 10, 12, 15],
                "default_duration": 15,
                "resolutions": [""],
                "max_refs": 9,
                "ref_mode": "data_uri",
                # 不能把 2.5 的 29 秒/30 图能力写成整家通用值，否则切回旧模型时
                # 前端仍会允许选 29 秒，直到付费请求发出去才收到 400。
                "model_options": {
                    model: {
                        "durations": _standard_limits(model)[0],
                        "ratios": _standard_limits(model)[1],
                        "max_refs": _standard_limits(model)[2],
                        "max_video_refs": _standard_limits(model)[3],
                        "max_audio_refs": _standard_limits(model)[4],
                        "ref_mode": "url",
                    }
                    for model in STANDARD_VIDEO_MODELS
                },
                "notes": "分辨率写在模型名里，不用也不能单独传。名字带 fast 的便宜、"
                         "带 480p 的更便宜 —— 试跑和调提示词用 sd3-fast-480p / "
                         "sd2-fast-480p，定稿再换 720p/1080p。"
                         "sd2-pro-720p 实测稳定（17/17 一次通过），sd3 系较新未实测。"
                         "旧模型参考图可用压缩 data URI；Seedance 2.5 必须使用公网 URL。"
                         "不带 paisio- 的旧名支持4-29秒；当前 paisio-seedance-2.5-*"
                         "实测只支持4-15秒，按文档最多9图/3视频/3音频。",
            },
            "notes": "视频首选。也提供 chat 模型（claude/gpt 系）可作 LLM 分析引擎。",
        }

    # ---------------------------------------------------------------- image
    def generate_image(self, task: ImageTask, dest: str, *, log: Callable = print,
                       cancel: Optional[Callable] = None,
                       poll_interval: int = 5, poll_timeout: int = 900) -> dict:
        body = {
            "model": task.model or "gpt-image-2",
            "prompt": task.prompt,
            "size": task.size or "1024x1536",
            "n": int(task.n or 1),
        }
        if task.refs:
            body["images"] = task.refs[:9]
        data = self.session.request("POST", "/v1/images/generations", json_body=body,
                                    retries=2, timeout=600)
        items = extract_image_items(data)
        task_id = extract_task_id(data)
        if not items:
            if not task_id:
                raise ApiError("提交未返回 task_id 或图片")
            items = self.session.poll("/v1/images/{id}", task_id, picker=extract_image_items,
                                      interval=poll_interval, timeout=poll_timeout,
                                      content_path_tpl="/v1/images/{id}/content",
                                      log=log, cancel=cancel)
        self.session.save_item(items[0], dest)
        return {"task_id": task_id, "source": items[0][:200], "provider": self.id,
                "model": body["model"]}

    # ---------------------------------------------------------------- video
    def generate_video(self, task: VideoTask, dest: str, *, log: Callable = print,
                       cancel: Optional[Callable] = None,
                       poll_interval: int = 10, poll_timeout: int = 2400) -> dict:
        model = task.model or "sd2-pro-720p"
        if model in STANDARD_VIDEO_MODELS:
            body = self._seedance25_body(task, model)
        else:
            body = self._legacy_video_body(task, model)
        data = self.session.request("POST", "/v1/videos", json_body=body, retries=2, timeout=300)
        url = extract_video_url(data)
        task_id = extract_task_id(data)
        if not url:
            if not task_id:
                raise ApiError("提交未返回 task_id 或视频URL")
            url = self.session.poll("/v1/videos/{id}", task_id, picker=extract_video_url,
                                    interval=poll_interval, timeout=poll_timeout,
                                    content_path_tpl="/v1/videos/{id}/content",
                                    log=log, cancel=cancel)
        self.session.save_item(url, dest)
        return {"task_id": task_id, "source": url, "provider": self.id, "model": model}

    @staticmethod
    def _legacy_video_body(task: VideoTask, model: str) -> dict:
        refs = task.refs[:9]
        prompt = task.prompt or ""
        if refs and "@图" not in prompt:
            prompt = prompt.strip() + " " + " ".join(f"@图{i + 1}" for i in range(len(refs)))
        body = {
            "model": model,
            "prompt": prompt,
            "duration": int(task.duration),
            "metadata": {
                "modeType": "image2video" if refs else "text2video",
                "ratio": task.ratio or "9:16",
                "enableSound": "on" if task.extra.get("enable_sound", True) else "off",
            },
        }
        if refs:
            body["images"] = refs
        return body

    @staticmethod
    def _seedance25_body(task: VideoTask, model: str) -> dict:
        refs = list(task.refs or [])
        videos = list(task.extra.get("video_refs") or task.extra.get("videos") or [])
        audios = list(task.extra.get("audio_refs") or task.extra.get("audios") or [])
        prompt = task.prompt or ""
        duration = int(task.duration or 15)
        ratio = task.ratio or "9:16"
        durations, ratios, image_max, video_max, audio_max = _standard_limits(model)
        low, high = min(durations), max(durations)

        problems = []
        if not prompt.strip():
            problems.append("prompt 不能为空")
        if len(prompt) > 2500:
            problems.append(f"prompt 最多2500字符，收到{len(prompt)}字符；不能截断后生成")
        if duration not in durations:
            problems.append(f"时长只能是{low}-{high}秒，收到{duration}秒")
        if ratio not in ratios:
            problems.append(f"比例只支持{'、'.join(ratios)}，收到{ratio}")
        if len(refs) > image_max:
            problems.append(f"图片最多{image_max}张，收到{len(refs)}张")
        if len(videos) > video_max:
            problems.append(f"视频素材最多{video_max}条，收到{len(videos)}条")
        if len(audios) > audio_max:
            problems.append(f"音频素材最多{audio_max}条，收到{len(audios)}条")
        if any(not str(r).startswith(("http://", "https://"))
               for r in refs + videos + audios):
            problems.append("所有参考图片/视频/音频必须先转成公网 http/https URL"
                            "（请配置对象存储）")
        if problems:
            raise ApiError("Seedance 2.5 参数不符合鹤的接口要求：" + "；".join(problems),
                           status=0, kind="task_fatal")

        body = {
            "model": model,
            # 不追加旧版 @图N，也不改写正文；@ImageN 映射由上游提示词原样保留。
            "prompt": prompt,
            "duration": duration,
            "aspect_ratio": ratio,
        }
        if refs:
            body["image_url"] = refs[0]
            if len(refs) > 1:
                body["extra_images"] = refs[1:]
        if videos:
            body["extra_videos"] = videos
        if audios:
            body["extra_audios"] = audios
        return body
