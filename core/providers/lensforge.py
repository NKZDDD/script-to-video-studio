# -*- coding: utf-8 -*-
"""LF LensForge（lensforge.tsxzz.com）。

开放 API 文档：`https://lensforge.tsxzz.com/lensforge-open-api.md`（站点 /api-docs 可下载）。

一把 `lf_live_` 密钥同时走两套接口：

  · 图片  POST /v1/images/generations（同步，最长约 3 分钟；返回 data[].url）
  · 视频  原生 POST /api/v1/client/tasks（materials 带角色与参考素材时长）
          GET /api/v1/client/tasks/{id} → status=success，成片在 resultUrl
  · 素材  POST /api/v1/client/uploads（multipart 的 file 字段）→ 稳定 url
  · 目录  GET /api/v1/client/catalog/offerings

几个必须照着做的点：
  1. `model` 不是上游模型名，而是**方案 id**。这里对外仍用展示名（下拉框可读），
     创建时换成目录里的 offeringId；展示名被后台改过会自动重拉一次目录再换。
  2. 参考视频/音频**必须**带真实 `durationSeconds`（2～30 秒）。本机没有现成时长，
     这里用 ffprobe/ffmpeg 读一遍；读不出来就明确报错，不猜一个数去打乱计费。
  3. 创建任务必须带 `Idempotency-Key`：超时重发拿到同一个任务号，不会重复扣费。
  4. 参考图本机文件先上传换 url 再放进 materials；公网链接原样使用，顺序即 @Image1..N。
"""

from __future__ import annotations

import base64
import io
import re
import subprocess
import uuid
from pathlib import Path
from typing import Callable, Optional

import requests

from ..apiutil import ApiError, TASK_FATAL, extract_image_items, extract_task_id, extract_video_url
from .base import ImageTask, Provider, VideoTask

VIDEO_RATIOS = ["9:16", "16:9", "1:1", "4:3", "3:4", "21:9", "adaptive"]
IMAGE_RATIOS = ["1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "5:4", "4:5", "21:9"]
IMAGE_RESOLUTIONS = ["1k", "2k", "4k"]
IMAGE_SIZES = ["1024x1536", "1024x1024", "1536x1024", "2048x2048", "3840x2160"]

# 目录里的方案（2026-11-01 实测）。名称就是前端下拉框的值，创建时换成 id。
# 名字带「测试」的三条是平台自己标注「个人别使用」的试跑通道，不放进下拉。
_VIDEO_OPTIONS = {
    "渠道5-seedance2.0": {
        "durations": list(range(5, 16)), "ratios": VIDEO_RATIOS,
        "resolutions": ["480p", "720p"],
        "max_refs": 9, "max_audio_refs": 3, "max_video_refs": 0},
    "渠道5-seedance2.5 满血": {
        "durations": list(range(5, 31)), "ratios": VIDEO_RATIOS,
        "resolutions": ["480p", "720p"],
        "max_refs": 30, "max_audio_refs": 10, "max_video_refs": 0},
    "渠道3-seedance2.0（支持视频参考）": {
        "durations": list(range(5, 16)), "ratios": VIDEO_RATIOS,
        "resolutions": ["720p"],
        "max_refs": 9, "max_audio_refs": 3, "max_video_refs": 3},
}
VIDEO_MODELS = list(_VIDEO_OPTIONS)

IMAGE_MODELS = ["gpt-image-2"]
_IMAGE_OPTIONS = {
    "gpt-image-2": {"ratios": IMAGE_RATIOS, "resolutions": IMAGE_RESOLUTIONS, "max_refs": 6},
}

_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")


def _remote(ref: str) -> bool:
    return str(ref or "").startswith(("http://", "https://"))


def _ratio_value(name: str) -> float:
    try:
        a, b = str(name).replace("×", "x").split(":")[:2]
        return float(a) / float(b)
    except Exception:                                    # noqa: BLE001
        return 0.0


def _ratio_of(size: str, allowed: list) -> str:
    """像素尺寸 → 最近的合法比例名（认不出就返回空串，让平台用默认档）。"""
    try:
        w, h = str(size).lower().replace("×", "x").split("x")[:2]
        want = float(w) / float(h)
    except Exception:                                    # noqa: BLE001
        return ""
    names = [x for x in allowed if x not in ("auto", "adaptive") and _ratio_value(x)]
    return min(names, key=lambda n: abs(_ratio_value(n) - want)) if names else ""


def _tier_of(size: str, allowed: list) -> str:
    """像素尺寸 → 清晰度档；按长边向上取最近的一档。"""
    try:
        w, h = str(size).lower().replace("×", "x").split("x")[:2]
        long_side = max(int(w), int(h))
    except Exception:                                    # noqa: BLE001
        return ""
    tiers = []
    for name in allowed:
        try:
            tiers.append((float(str(name).lower().rstrip("k")) * 1024, name))
        except ValueError:
            continue
    if not tiers:
        return ""
    for value, name in sorted(tiers):
        if value >= long_side:
            return name
    return sorted(tiers)[-1][1]


def _measured_seconds(url: str) -> float:
    """用 ffprobe/ffmpeg 读参考素材时长。

    该平台的参考视频/音频必须带真实时长（2～30 秒），漏传或乱传会被拒或算错钱。
    这两个工具在拼接阶段本来就有，所以不额外引入依赖；读不出来就明确报错。
    """
    from .. import probe
    for exe in (probe.find_ffprobe(), probe.find_ffmpeg()):
        if not exe:
            continue
        try:
            done = subprocess.run([exe, "-hide_banner", "-i", url], capture_output=True,
                                  encoding="utf-8", errors="ignore", timeout=60)
        except (OSError, subprocess.SubprocessError):
            continue
        hit = _DURATION_RE.search(done.stderr or "")
        if hit:
            return round(int(hit.group(1)) * 3600 + int(hit.group(2)) * 60
                         + float(hit.group(3)), 2)
    raise ApiError(f"LF 读不出这段参考素材的时长：{str(url)[:140]}。"
                   "该平台要求参考视频/音频必须带真实时长（2～30 秒），"
                   "猜一个数会被拒绝或算错费用；请换成可公网下载的文件，或移除这条参考素材。",
                   kind=TASK_FATAL)


class LensForgeProvider(Provider):
    id = "lensforge"
    name = "LF lensforge.tsxzz.com"
    aliases = ("lf", "LF", "lensforge")
    default_base_url = "https://lensforge.tsxzz.com"
    supports = ("image", "video")
    # 本机参考图先上传换 url；配了对象存储时解析器会直接给公网链接，原样可用。
    ref_mode = "bytes"

    def __init__(self, api_key: str = "", base_url: str = "", proxy: str = "", timeout: int = 900):
        super().__init__(api_key, base_url, proxy, timeout)
        self._offers: Optional[list] = None

    def capabilities(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "default_base_url": self.default_base_url,
            "supports": list(self.supports),
            "image": {
                "models": IMAGE_MODELS,
                "default_model": "gpt-image-2",
                "sizes": IMAGE_SIZES,
                "default_size": "1024x1536",
                "resolutions": IMAGE_RESOLUTIONS,
                "max_refs": 6,
                "ref_mode": "bytes",
                "model_options": _IMAGE_OPTIONS,
                "notes": "同步接口，最多等约 3 分钟；n 只能是 1。参考图上限 6 张，"
                         "本机文件先上传换 url，公网链接原样使用。",
            },
            "video": {
                "models": VIDEO_MODELS,
                "default_model": "渠道5-seedance2.0",
                "ratios": VIDEO_RATIOS,
                "durations": list(range(1, 31)),
                "default_duration": 15,
                "resolutions": ["480p", "720p"],
                "max_refs": 30,
                "max_audio_refs": 10,
                "max_video_refs": 3,
                "reference_video_urls_supported": True,
                "ref_mode": "bytes",
                "model_options": _VIDEO_OPTIONS,
                "notes": "时长/清晰度/素材上限按方案而异，平台会拒绝超范围参数，这里原样发出不夹取。"
                         "参考视频/音频必须能读出真实时长（2～30 秒）；带参考视频走有参考计价。",
            },
            "notes": "模型名是平台方案展示名，创建时换成目录里的 offeringId；"
                     "后台改名后会自动重拉目录匹配。名称带「测试」的试跑方案未上架。",
        }

    # -- 目录与模型 --------------------------------------------------------
    def _catalog(self) -> list:
        if self._offers is None:
            data = self.session.request("GET", "/api/v1/client/catalog/offerings",
                                        retries=2, timeout=60)
            self._offers = list(data.get("items") or [])
        return self._offers

    def catalog_rows(self) -> list:
        """模型清单交给目录的**展示名**，不要用 offering id。

        `/v1/models` 返回的是方案 id（UUID）；直接拿来当下拉值会显示成一串
        乱码，而且创建任务时还得再猜回方案。目录里的 videoDisplayName /
        imageDisplayName 才是平台给方案的可读名字，创建时由 `_offering` 换成 id。
        名称带「测试」的方案是平台自己标注的试跑通道，不放进清单。
        """
        rows = []
        for item in self._catalog():
            for flag, label, media in (("supportsVideo", "videoDisplayName", "video"),
                                       ("supportsImage", "imageDisplayName", "image")):
                if item.get(flag) is False:
                    continue
                name = str(item.get(label) or "").strip()
                if not name or name == "无" or "测试" in name:
                    continue
                rows.append({"id": name, "type": media, "display_name": name})
        return rows

    def filter_catalog(self, rows) -> list:
        """旧版客户端的兜底：它只会拿 `/v1/models` 的 id（UUID）当模型名。

        新版 `model_catalog.fetch` 会优先调用 `catalog_rows`；旧版没有这个钩子，
        但一直支持 `filter_catalog`。这里直接返回目录里的可读行，
        不理会传进来的 UUID —— 旧客户端导入适配包后也能正常显示名称。
        """
        return self.catalog_rows()

    def _offering(self, model: str, media: str) -> dict:
        """展示名 / 方案 id → 目录里的方案。名称被改过就重拉一次目录。"""
        name = str(model or "").strip()
        label = "videoDisplayName" if media == "video" else "imageDisplayName"
        flag = "supportsVideo" if media == "video" else "supportsImage"
        for attempt in range(2):
            rows = [r for r in self._catalog()
                    if r.get(flag) is not False
                    and str(r.get(label) or "").strip() not in ("", "无")]
            for row in rows:
                if str(row.get(label)).strip() == name or row.get("id") == name:
                    return row
            if attempt == 0:
                self._offers = None
        names = [str(r.get(label)).strip() for r in (self._catalog() or [])
                 if r.get(flag) is not False and str(r.get(label) or "").strip() not in ("", "无")]
        kind = "视频" if media == "video" else "图片"
        raise ApiError(f"LF 没有可用的{kind}方案「{name}」。当前目录：{'、'.join(names) or '空'}。"
                       "请在任务里重新选择模型。", kind=TASK_FATAL)

    # -- 素材 --------------------------------------------------------------
    def _image_part(self, ref: str, index: int) -> tuple:
        """读参考图并保留真实格式；坏图直接拦下，不提交生成。"""
        try:
            text = str(ref)
            if text.startswith("data:"):
                head, payload = text.split(",", 1)
                if ";base64" not in head:
                    raise ValueError("不是 Base64 图片")
                raw = base64.b64decode("".join(payload.split()), validate=True)
            elif _remote(text):
                # 不把 API Key 发到参考图域名。
                with requests.get(text, timeout=(20, 60), proxies=self.session._proxies()) as resp:
                    resp.raise_for_status()
                    raw = resp.content
            else:
                raw = Path(text).read_bytes()
            from PIL import Image
            with Image.open(io.BytesIO(raw)) as im:
                fmt = im.format
                im.verify()
            with Image.open(io.BytesIO(raw)) as im:
                im.load()
            ext, mime = {"PNG": ("png", "image/png"), "JPEG": ("jpg", "image/jpeg"),
                         "WEBP": ("webp", "image/webp")}[fmt]
        except requests.RequestException as exc:
            raise ApiError(f"LF 第 {index} 张参考图下载失败：{type(exc).__name__}") from exc
        except Exception as exc:                         # noqa: BLE001
            raise ApiError(f"LF 第 {index} 张参考图无法完整解码为 PNG/JPEG/WebP；"
                           "请检查或替换这张图，未提交生成。", kind=TASK_FATAL) from exc
        return f"reference-{index}.{ext}", raw, mime

    def _upload(self, ref: str, index: int, log: Callable) -> str:
        """本机/data URI 参考图上传换稳定 url；公网链接原样返回。"""
        if _remote(ref):
            return str(ref)
        name, raw, mime = self._image_part(ref, index)
        data = self.session.request("POST", "/api/v1/client/uploads",
                                    files={"file": (name, raw, mime)},
                                    retries=2, timeout=180, submit=False)
        url = str((data or {}).get("url") or "").strip()
        if not url:
            raise ApiError(f"LF 第 {index} 张参考图上传后没有返回 url：{str(data)[:200]}",
                           kind=TASK_FATAL)
        log(f"LF 第 {index} 张参考图已上传")
        return url

    # -- 图片 --------------------------------------------------------------
    def generate_image(self, task: ImageTask, dest: str, *, log: Callable = print,
                       cancel: Optional[Callable] = None,
                       poll_interval: int = 5, poll_timeout: int = 900) -> dict:
        model = (task.model or "gpt-image-2").strip()
        if not (task.prompt or "").strip():
            raise ApiError("LF 图片提示词为空", kind=TASK_FATAL)
        if task.n != 1:
            raise ApiError("LF 的 n 只能是 1，请每次生成一张", kind=TASK_FATAL)
        if cancel and cancel():
            raise ApiError("已取消", kind=TASK_FATAL)
        offering = self._offering(model, "image")
        options = _IMAGE_OPTIONS.get(model) or _IMAGE_OPTIONS["gpt-image-2"]
        allowed = list(offering.get("ratios") or options["ratios"])

        ratio = str(task.extra.get("ratio") or "").strip() or _ratio_of(task.size, allowed)
        resolution = str(task.extra.get("resolution") or "").strip().lower() \
            or _tier_of(task.size, IMAGE_RESOLUTIONS)
        if ratio and ratio not in allowed:
            raise ApiError(f"LF 图片不支持比例 {ratio}；可选：{'、'.join(allowed)}", kind=TASK_FATAL)
        if resolution and resolution not in IMAGE_RESOLUTIONS:
            raise ApiError(f"LF 图片清晰度只能是 {'、'.join(IMAGE_RESOLUTIONS)}，本任务是 {resolution}",
                           kind=TASK_FATAL)
        refs = list(task.refs or [])
        # 文档写死「生图参考素材最多 6 张」；目录里的 maxReferenceImages 是方案级上限，
        # 两者取小，避免发 7 张被上游直接拒掉。
        maximum = min(6, int(offering.get("maxReferenceImages") or 6))
        if len(refs) > maximum:
            raise ApiError(f"LF 图片最多 {maximum} 张参考图，本任务有 {len(refs)} 张；"
                           "不能静默裁图，否则人物/风格绑定会丢失。", kind=TASK_FATAL)

        images = [self._upload(ref, i, log) for i, ref in enumerate(refs, 1)]
        body: dict = {"model": offering["id"], "prompt": task.prompt, "n": 1}
        if ratio:
            body["ratio"] = ratio
        if resolution:
            body["resolution"] = resolution
        if images:
            body["images"] = images
        log(f"LF 图片 {model}: {ratio or '默认比例'} {resolution or '默认清晰度'}，"
            f"参考图 {len(images)} 张")
        data = self.session.request("POST", "/v1/images/generations", json_body=body,
                                    headers={"Idempotency-Key": uuid.uuid4().hex,
                                             "X-Request-ID": uuid.uuid4().hex},
                                    retries=1, timeout=max(300, min(poll_timeout, 900)))
        items = extract_image_items(data)
        if not items:
            raise ApiError("LF 图片接口没有返回可用图片 URL 或 Base64，请检查平台任务记录。")
        log("LF 图片结果已返回，正在下载保存")
        self.session.save_item(items[0], dest, log=log)
        log("LF 图片已保存")
        return {"task_id": extract_task_id(data), "source": str(items[0])[:200],
                "provider": self.id, "model": model}

    # -- 视频 --------------------------------------------------------------
    def _media_refs(self, urls: list, durations: list, kind: str, label: str,
                    limit: int, log: Callable) -> list:
        """参考视频/音频 → materials，并保证 durationSeconds 是真实值。"""
        if urls and not limit:
            raise ApiError(f"LF 当前方案不支持{label}参考", kind=TASK_FATAL)
        if len(urls) > limit:
            raise ApiError(f"LF 当前方案最多 {limit} 个{label}参考，本任务有 {len(urls)} 个",
                           kind=TASK_FATAL)
        out = []
        for i, url in enumerate(urls):
            if not _remote(url):
                raise ApiError(f"LF 的{label}参考只收公网 http(s) 链接：{str(url)[:120]}",
                               kind=TASK_FATAL)
            if i < len(durations) and durations[i]:
                seconds = float(durations[i])
            else:
                seconds = _measured_seconds(url)
            fixed = max(2.0, min(30.0, round(seconds, 2)))
            if fixed != round(seconds, 2):
                log(f"LF {label}参考 {i + 1} 时长 {seconds}s 超出平台 2～30 秒，按 {fixed}s 提交")
            out.append({"type": kind, "role": f"reference_{kind}", "url": url,
                        "durationSeconds": fixed})
        return out

    def generate_video(self, task: VideoTask, dest: str, *, log: Callable = print,
                       cancel: Optional[Callable] = None,
                       poll_interval: int = 10, poll_timeout: int = 2400) -> dict:
        model = (task.model or "渠道5-seedance2.0").strip()
        if not (task.prompt or "").strip():
            raise ApiError("LF 视频提示词为空", kind=TASK_FATAL)
        if cancel and cancel():
            raise ApiError("已取消", kind=TASK_FATAL)
        offering = self._offering(model, "video")
        options = dict(_VIDEO_OPTIONS.get(model) or {})
        resolutions = list(offering.get("videoResolutions") or options.get("resolutions") or [])

        seconds = max(1, int(task.duration or 15))
        ratio = str(task.ratio or "").strip()
        if ratio and ratio not in VIDEO_RATIOS:
            raise ApiError(f"LF 不支持比例 {ratio}；可选：{'、'.join(VIDEO_RATIOS)}", kind=TASK_FATAL)
        resolution = str(task.resolution or "").strip().lower()
        if resolution and resolutions and resolution not in [r.lower() for r in resolutions]:
            raise ApiError(f"LF {model} 不支持 {resolution}；可选：{'、'.join(resolutions)}",
                           kind=TASK_FATAL)

        refs = list(task.refs or [])
        maximum = int(offering.get("maxReferenceImages") or options.get("max_refs") or 30)
        if len(refs) > maximum:
            raise ApiError(f"LF {model} 最多 {maximum} 张参考图，本任务有 {len(refs)} 张；"
                           "不能静默裁图，否则人物/场景绑定会丢失。", kind=TASK_FATAL)
        images = [self._upload(ref, i, log) for i, ref in enumerate(refs, 1)]
        materials = [{"type": "image", "role": "reference_image", "url": url}
                     for url in images]

        videos = list(task.extra.get("video_refs") or [])
        audios = list(task.extra.get("audio_refs") or [])
        materials += self._media_refs(
            videos, list(task.extra.get("video_durations") or []), "video", "视频",
            int(offering.get("maxReferenceVideos") or options.get("max_video_refs") or 0), log)
        materials += self._media_refs(
            audios, list(task.extra.get("audio_durations") or []), "audio", "音频",
            int(offering.get("maxReferenceAudios") or options.get("max_audio_refs") or 0), log)

        body: dict = {"kind": "video", "offeringId": offering["id"], "prompt": task.prompt,
                      "duration": seconds, "generateAudio": True, "materials": materials}
        if ratio:
            body["ratio"] = ratio
        if resolution:
            body["resolution"] = resolution
        log(f"LF 视频 {model}: {seconds}s {ratio or '默认比例'} "
            f"{resolution or '默认清晰度'} 参考图×{len(images)} "
            f"参考视频×{len(videos)} 参考音频×{len(audios)}")
        data = self.session.request("POST", "/api/v1/client/tasks", json_body=body,
                                    headers={"Idempotency-Key": uuid.uuid4().hex,
                                             "X-Request-ID": uuid.uuid4().hex},
                                    retries=1, timeout=120)
        task_id = str(((data or {}).get("task") or {}).get("id")
                      or extract_task_id(data) or "").strip()
        if not task_id:
            raise ApiError(f"LF 提交后没有返回任务号：{str(data)[:300]}", kind=TASK_FATAL)
        log(f"LF 任务已提交：{task_id}")
        got = self.session.poll("/api/v1/client/tasks/{id}", task_id, picker=extract_video_url,
                                interval=poll_interval, timeout=poll_timeout,
                                log=log, cancel=cancel)
        log("LF 视频结果已返回，正在下载保存")
        self.session.save_item(got, dest, log=log)
        log("LF 视频已保存")
        return {"task_id": task_id, "source": str(got)[:200],
                "provider": self.id, "model": model}
