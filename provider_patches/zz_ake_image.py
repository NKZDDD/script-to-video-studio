# -*- coding: utf-8 -*-
"""阿珂 snumom.com 图片与统一视频 API。

文档版本：用户提供的《snumom 视频 API》（2026-08-30）。
图片：2026-09-30 官网 gpt-image-2 / grok-imagine-image-2.0 文档。

  · 文生图 POST /v1/images/generations（JSON）
  · 图生图 POST /v1/images/edits（multipart image 文件）

  · 创建 POST /v1/videos
  · 查询 GET  /v1/videos/{task_id}
  · 下载 GET  /v1/videos/{task_id}/content

统一协议的重要约束：seconds 是字符串；图片/视频/音频都是带 url 的对象数组；
aspect_ratio 与 size 分开。万相 3.0 的提示词增强默认开启，参考图顺序对应“图1、图2”。
"""

from __future__ import annotations

import base64
import io
from pathlib import Path
from typing import Callable, Optional

import requests

from core.apiutil import ApiError, extract_image_items, extract_task_id, extract_video_url
from core.providers.base import ImageTask, Provider, VideoTask

PATCH_VERSION = "20261009.2"
# https://snumom.com/docs/gpt-image-2.html
# https://snumom.com/docs/grok-imagine-image-2.0.html
IMAGE_MODELS = ["gpt-image-2", "grok-imagine-image-2.0"]
IMAGE_OPTIONS = {
    "gpt-image-2": {"sizes": ["2048x2048", "3840x2160"],
                    "resolutions": ["2K", "4K"]},
    "grok-imagine-image-2.0": {"sizes": ["1024x1024"], "resolutions": ["1K"]},
}


def _image_size(task: ImageTask, model: str) -> str:
    options = IMAGE_OPTIONS[model]
    resolution = str(task.extra.get("resolution") or "").strip().upper()
    if resolution and resolution not in options["resolutions"]:
        raise ApiError(f"阿珂 {model} 不支持 {resolution}；可选：{options['resolutions']}",
                       kind="task_fatal")
    want = str(task.size or "").strip().lower().replace("×", "x")
    aliases = ({"1:1": "2048x2048", "16:9": "3840x2160", "2k": "2048x2048",
                "4k": "3840x2160"} if model == "gpt-image-2" else
               {"1:1": "1024x1024", "1k": "1024x1024"})
    size = aliases.get(want, want) or options["sizes"][0]
    if size not in options["sizes"]:
        raise ApiError(f"阿珂 {model} 当前文档公布的尺寸为 {'、'.join(options['sizes'])}；"
                       f"本任务是 {task.size}。请在生产页这一行的尺寸中选择，不能静默改变构图。",
                       kind="task_fatal")
    if resolution:
        selected = {"1K": "1024x1024", "2K": "2048x2048", "4K": "3840x2160"}[resolution]
        if want and size != selected:
            raise ApiError(f"阿珂的 {resolution} 对应 {selected}，与本次尺寸 {size} 不一致。"
                           "请同时选择对应尺寸，或把清晰度恢复为跟随任务。", kind="task_fatal")
        size = selected
    return size

VIDEO_MODELS = [
    "wan3.0-video", "wan3.0-video-prime", "wan3.0-image", "wan3.0-image-prime",
    "grok-imagine-video-1.5", "grok-imagine-video-1.5-preview", "wan-3.0",
]
RATIOS = ["9:16", "16:9", "1:1", "4:3", "3:4", "21:9", "adaptive"]
RESOLUTIONS = ["", "480P", "720P", "1080P"]


def _remote(ref: str) -> bool:
    return str(ref or "").startswith(("http://", "https://"))


def _duration(model: str, want: int) -> int:
    sec = int(want or 5)
    if model.startswith("wan3.0-"):
        return min(30, max(2, sec))
    if model == "wan-3.0":
        return min(60, max(1, sec))
    if model.startswith("grok-imagine-video-1.5"):
        return min(15, max(4, sec))
    if model.startswith("minimax_h3-"):
        return min(15, max(4, sec))
    return min(10, max(4, sec)) if "h3" in model.lower() else max(1, sec)


def _limits(model: str) -> tuple[int | None, int, int]:
    """返回图片/视频/音频上限。None 不做本地张数限制，0 表示不支持。"""
    if "h3" in model.lower():
        return (None, 3, 3) if model.lower().startswith("minimax_h3-") else (None, 0, 0)
    if model.startswith("wan3.0-video"):
        return 10, 5, 5
    if model.startswith("wan3.0-image"):
        return 10, 0, 5
    if model == "wan-3.0":
        return 9, 3, 3
    if model.startswith("grok-imagine-video-1.5"):
        return 7, 0, 0
    return 4, 0, 0


class AkeProvider(Provider):
    id = "ake"
    name = "阿珂 snumom.com"
    aliases = ("snumom", "阿珂", "ako")
    default_base_url = "https://snumom.com"
    supports = ("image", "video")
    # 文档的三类 reference_* 都要求服务端可访问的 URL。
    ref_mode = "url"

    def needs_url(self, model: str = "", media: str = "image") -> bool:
        return media == "video"

    def needs_bytes(self, model: str = "") -> bool:
        return model in IMAGE_MODELS

    def accepts_url(self, model: str = "", media: str = "image") -> bool:
        return media == "video"

    def capabilities(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "default_base_url": self.default_base_url,
            "supports": list(self.supports),
            "image": {
                "models": IMAGE_MODELS,
                "default_model": "gpt-image-2",
                "sizes": ["2048x2048", "3840x2160", "1024x1024"],
                "default_size": "2048x2048",
                "resolutions": ["1K", "2K", "4K"],
                "ref_mode": "bytes",
                # 文档没有公布张数上限。逐张发送，不臆造上限或截掉参考图。
                "refs_unlimited": True,
                "model_options": IMAGE_OPTIONS,
                "notes": "gpt-image-2：2K 方图 2048x2048 / 原生 4K 横图 3840x2160；"
                         "Grok Imagine 2.0：仅 1024x1024。图生图用 multipart 的 image 文件字段。"
                         "参考图张数上限文档未声明，本地不截断，最终以服务商响应为准。",
            },
            "video": {
                "models": VIDEO_MODELS,
                "default_model": "wan3.0-video",
                "ratios": RATIOS,
                "durations": list(range(2, 31)),
                "default_duration": 15,
                "resolutions": RESOLUTIONS,
                "max_refs": 10,
                # H3 无本地图片张数上限；拉取清单时也不能恢复旧的 4/9 图限制。
                "unlimited_ref_model_pattern": "(?i)h3",
                "ref_mode": "url",
                "model_options": {
                    "wan3.0-video": {"max_audio_refs": _limits("wan3.0-video")[2], "durations": list(range(2, 31)), "max_refs": 10},
                    "wan3.0-video-prime": {"max_audio_refs": _limits("wan3.0-video-prime")[2], "durations": list(range(2, 31)), "max_refs": 10},
                    "wan3.0-image": {"max_audio_refs": _limits("wan3.0-image")[2], "durations": list(range(2, 31)), "max_refs": 10},
                    "wan3.0-image-prime": {"max_audio_refs": _limits("wan3.0-image-prime")[2], "durations": list(range(2, 31)), "max_refs": 10},
                    "grok-imagine-video-1.5": {"max_audio_refs": _limits("grok-imagine-video-1.5")[2],
                        "durations": list(range(4, 16)), "max_refs": 7},
                    "grok-imagine-video-1.5-preview": {"max_audio_refs": _limits("grok-imagine-video-1.5-preview")[2],
                        "durations": list(range(4, 16)), "max_refs": 7},
                    "wan-3.0": {"max_audio_refs": _limits("wan-3.0")[2], "durations": list(range(1, 61)), "max_refs": 9},
                },
                "notes": "seconds 必须按字符串发送；reference_images 是 {url,role?} 对象数组。"
                         "wan3.0-video* 支持 10 图/5 视频/5 音频，wan3.0-image* 不支持参考视频；"
                         "Grok 最多 7 图且不支持视频/音频。万相 3.0 未指定 size 时默认 1080P。",
            },
            "notes": "模型清单最终以控制台和 GET /v1/models 为准。万相创建时预扣，"
                     "必须显式传 seconds，不能用 -1 智能时长。",
        }

    def _image_file(self, ref: str, index: int) -> tuple:
        """保留完整原图，依据真实格式声明 MIME，阻止错误网页和截断文件被上传。"""
        try:
            ref = str(ref)
            if ref.startswith("data:"):
                head, payload = ref.split(",", 1)
                if ";base64" not in head:
                    raise ValueError("参考图不是 Base64 图片")
                raw = base64.b64decode("".join(payload.split()), validate=True)
            elif _remote(ref):
                # 不把 API Key 发到参考图域名。
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
                im.load()
            ext, mime = {"PNG": ("png", "image/png"), "JPEG": ("jpg", "image/jpeg"),
                         "WEBP": ("webp", "image/webp")}[fmt]
        except requests.RequestException as exc:
            raise ApiError(f"阿珂第 {index} 张参考图下载失败：{type(exc).__name__}") from exc
        except Exception as exc:
            raise ApiError(f"阿珂第 {index} 张参考图无法完整解码为 PNG/JPEG/WebP；"
                           "请检查或替换这张图，未提交生成。", kind="task_fatal") from exc
        return (f"reference-{index}.{ext}", raw, mime)

    def generate_image(self, task: ImageTask, dest: str, *, log: Callable = print,
                       cancel: Optional[Callable] = None,
                       poll_interval: int = 5, poll_timeout: int = 900) -> dict:
        model = task.model or "gpt-image-2"
        if model not in IMAGE_MODELS:
            raise ApiError(f"阿珂图片模型 {model} 尚未接入；请选择 {'、'.join(IMAGE_MODELS)}",
                           kind="task_fatal")
        if not (task.prompt or "").strip():
            raise ApiError("阿珂图片提示词为空", kind="task_fatal")
        if task.n != 1:
            raise ApiError("Studio 一个图片任务对应一个输出文件，请每次生成 1 张", kind="task_fatal")
        if cancel and cancel():
            raise ApiError("已取消", kind="task_fatal")
        size = _image_size(task, model)
        body = {"model": model, "prompt": task.prompt, "size": size, "n": 1}
        files = [("image", self._image_file(ref, i)) for i, ref in enumerate(task.refs, 1)]
        if cancel and cancel():
            raise ApiError("已取消", kind="task_fatal")
        log(f"阿珂 图片 {model}: size={size}，参考图 {len(files)} 张")
        # 同步接口没有公开幂等键；网络异常不能在请求层盲目重复付费提交。
        if files:
            fields = [(key, (None, str(value))) for key, value in body.items()]
            data = self.session.request("POST", "/v1/images/edits", files=fields + files,
                                        retries=1, timeout=600)
        else:
            data = self.session.request("POST", "/v1/images/generations", json_body=body,
                                        retries=1, timeout=600)
        items = extract_image_items(data)
        if not items:
            raise ApiError("阿珂图片接口没有返回可用图片 URL 或 Base64，请检查服务商任务记录。")
        log("阿珂 图片结果已返回，正在下载保存")
        self.session.save_item(items[0], dest)
        log("阿珂 图片已保存")
        return {"task_id": extract_task_id(data), "source": str(items[0])[:200],
                "provider": self.id, "model": model}

    def generate_video(self, task: VideoTask, dest: str, *, log: Callable = print,
                       cancel: Optional[Callable] = None,
                       poll_interval: int = 5, poll_timeout: int = 2400) -> dict:
        model = (task.model or "wan3.0-video").strip()
        sec = _duration(model, task.duration)
        if sec != int(task.duration or 5):
            log(f"阿珂 {model} 时长范围有限，已把 {task.duration} 秒收敛为 {sec} 秒")

        img_max, video_max, audio_max = _limits(model)
        refs = list(task.refs or [])
        bad = [r for r in refs if not _remote(r)]
        if bad:
            raise ApiError(
                f"阿珂 reference_images 只收公网 URL；本任务有 {len(bad)} 张不是公网链接。"
                "请在设置里配置参考图对象存储，不能少图后继续生成。",
                status=0, kind="task_fatal")
        if img_max is not None and len(refs) > img_max:
            raise ApiError(f"阿珂 {model} 最多 {img_max} 张参考图，本任务有 {len(refs)} 张；"
                           "不能静默裁图，否则人物/场景绑定会丢失。",
                           status=0, kind="task_fatal")

        videos = list(task.extra.get("video_refs") or task.extra.get("videos") or [])
        audios = list(task.extra.get("audio_refs") or task.extra.get("audios") or [])
        if videos and not video_max:
            raise ApiError(f"阿珂 {model} 不支持参考视频", status=0, kind="task_fatal")
        if audios and not audio_max:
            raise ApiError(f"阿珂 {model} 不支持参考音频", status=0, kind="task_fatal")
        if len(videos) > video_max or len(audios) > audio_max:
            raise ApiError(f"阿珂 {model} 素材超限：视频 {len(videos)}/{video_max}，"
                           f"音频 {len(audios)}/{audio_max}", status=0, kind="task_fatal")
        if any(not _remote(r) for r in videos + audios):
            raise ApiError("阿珂参考视频/音频也必须是公网 URL", status=0, kind="task_fatal")

        body: dict = {"model": model, "prompt": task.prompt or "", "seconds": str(sec)}
        if task.resolution:
            body["size"] = task.resolution.upper()
        if task.ratio:
            body["aspect_ratio"] = task.ratio
        if refs:
            roles = list(task.extra.get("image_roles") or [])
            body["reference_images"] = [
                dict({"url": ref}, **({"role": roles[i]} if i < len(roles) and roles[i] else {}))
                for i, ref in enumerate(refs)
            ]
        if videos:
            durations = list(task.extra.get("video_durations") or [])
            body["reference_videos"] = [
                dict({"url": ref}, **({"duration": durations[i]}
                                      if i < len(durations) and durations[i] else {}))
                for i, ref in enumerate(videos)
            ]
        if audios:
            body["reference_audios"] = [{"url": ref} for ref in audios]
        if model.startswith("wan3.0-") and "prompt_extend" in task.extra:
            body["prompt_extend"] = bool(task.extra["prompt_extend"])

        log(f"阿珂 {model}: seconds='{sec}' size={body.get('size', '默认')} "
            f"aspect_ratio={body.get('aspect_ratio', '默认')} "
            f"图{len(refs)}/视频{len(videos)}/音频{len(audios)}")
        data = self.session.request("POST", "/v1/videos", json_body=body,
                                    # 创建接口没有幂等键，网络层不能自动重发，否则可能重复扣费。
                                    retries=1, timeout=300)
        task_id = extract_task_id(data)
        url = extract_video_url(data)
        if not url:
            if not task_id:
                raise ApiError(f"提交没返回任务 ID: {str(data)[:300]}")
            url = self.session.poll("/v1/videos/{id}", task_id, picker=extract_video_url,
                                    content_path_tpl="/v1/videos/{id}/content",
                                    interval=poll_interval, timeout=poll_timeout,
                                    log=log, cancel=cancel)
        self.session.save_item(url, dest)
        return {"task_id": task_id, "source": url, "provider": self.id, "model": model}
