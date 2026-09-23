# -*- coding: utf-8 -*-
"""红鸟 OpenAPI；依据用户文档及 2026-09-23 实拉模型清单。

使用绝对导入，同一文件也可作为旧 Studio 的 providers 外挂加载。
"""
from __future__ import annotations

import copy
import time
import uuid
from urllib.parse import quote

import requests

from core.apiutil import ApiError, BATCH_FATAL, HttpSession, RETRYABLE, TASK_FATAL, classify
from core.providers.base import Provider

RATIOS = ["16:9", "9:16", "1:1", "4:3", "3:4", "21:9"]
MODEL_OPTIONS = {
    "minimax-h3-768-933": dict(durations=list(range(4, 16)), ratios=RATIOS,
                               max_refs=9, max_video_refs=3, max_audio_refs=3),
    "wan3-720p": dict(durations=list(range(4, 31)), ratios=["16:9", "9:16"],
                      max_refs=10, max_video_refs=5, max_audio_refs=5),
    "video-standard-720p": dict(durations=list(range(4, 16)), ratios=RATIOS,
                                max_refs=9, max_video_refs=3, max_audio_refs=3),
    "video-sd25-30s": dict(durations=[5, 10, 30], ratios=RATIOS,
                           max_refs=30, max_video_refs=0, max_audio_refs=0),
    "video-sd25-480p": dict(durations=list(range(4, 31)), ratios=RATIOS,
                            max_refs=30, max_video_refs=10, max_audio_refs=10),
}


def catalog_schema(row):
    """仅提取公开能力字段，供 Studio 的逐模型选项渲染。"""
    schema = {}
    for task in row.get("tasks") or []:
        if not isinstance(task, dict) or task.get("taskKind") != "video.generate":
            continue
        for param in task.get("parameters") or []:
            if not isinstance(param, dict):
                continue
            name = param.get("name")
            values = [v.get("value") for v in param.get("options") or []
                      if isinstance(v, dict) and v.get("value") is not None]
            if name in ("seconds", "duration") and values:
                schema["durations"] = [int(v) for v in values]
            elif name in ("aspect_ratio", "resolution") and values:
                schema["aspect_ratios" if name == "aspect_ratio" else "resolutions"] = values
            for field, target in (("images", "max_reference_images"),
                                  ("videos", "max_reference_videos"),
                                  ("audios", "max_reference_audios")):
                if name == field and isinstance(param.get("maxItems"), int):
                    schema[target] = param["maxItems"]
    return schema


def _cancelled(cancel):
    if cancel and cancel():
        raise ApiError("用户已取消", kind=TASK_FATAL)


def _wait(seconds, cancel):
    end = time.monotonic() + max(0, seconds)
    while time.monotonic() < end:
        _cancelled(cancel)
        time.sleep(min(0.25, max(0, end - time.monotonic())))


class _Session(HttpSession):
    def _download_headers(self):
        # 文档返回公网成片 URL；不要把红鸟 Key 传给结果 CDN。
        return {"Accept": "*/*", "User-Agent": "Respect-Studio/1.0"}


class HongniaoProvider(Provider):
    id = "hongniao"
    name = "红鸟"
    default_base_url = "https://an.hongniaoai.com/api"
    supports = ("video",)

    def __init__(self, api_key="", base_url="", proxy="", timeout=900):
        base = (base_url or self.default_base_url).strip().rstrip("/")
        if base.endswith("/v1"):
            base = base[:-3]
        if base == "https://an.hongniaoai.com":
            base += "/api"
        self.session = _Session(api_key, base, timeout, proxy)

    def capabilities(self):
        return {"id": self.id, "name": self.name, "supports": list(self.supports),
                "default_base_url": self.default_base_url,
                "video": {"models": list(MODEL_OPTIONS), "default_model": "video-sd25-30s",
                          "durations": list(range(4, 31)), "default_duration": 30,
                          "ratios": RATIOS, "max_refs": 30,
                          "max_video_refs": 10, "max_audio_refs": 10,
                          "ref_mode": "data_uri", "poll_interval": 5, "poll_timeout": 2400,
                          "model_options": copy.deepcopy(MODEL_OPTIONS)},
                "notes": "红鸟视频 OpenAPI。参考图支持 URL/Base64，参考视频和音频使用公网链接。"
                         "模型与参数可在设置中拉取更新；图片模型暂未开放。"}

    def _request(self, method, path, body=None):
        if not self.session.api_key:
            raise ApiError("请先填写红鸟 API Key", kind=BATCH_FATAL)
        try:
            with requests.Session() as client:
                client.trust_env = False
                with client.request(method, self.session.base_url + path,
                                    headers=self.session._headers(), json=body,
                                    proxies=self.session._proxies(), timeout=(10, 60),
                                    allow_redirects=False) as response:
                    status = response.status_code
                    try:
                        data = response.json()
                    except ValueError:
                        data = {"message": "接口未返回 JSON"}
                    if not isinstance(data, dict):
                        data = {"message": "接口返回格式错误"}
                    code = data.get("code")
                    if 200 <= status < 300 and code in (None, 0, "0", 200, "200"):
                        return data
                    error = data.get("error")
                    error = error if isinstance(error, dict) else {}
                    message = str(error.get("message") or data.get("message") or "请求失败")
                    message = message.replace(self.session.api_key, "[已隐藏]")
                    business_status = int(code) if str(code).isdigit() else status
                    error_code = str(error.get("code") or error.get("type") or code or "")
                    raise ApiError(f"红鸟接口错误：{message}", status=business_status,
                                   kind=classify(business_status, message, error_code),
                                   err_code=error_code)
        except requests.RequestException as exc:
            raise ApiError(f"红鸟网络请求失败：{type(exc).__name__}", kind=RETRYABLE) from exc

    def list_models(self):
        data = self._request("GET", "/v1/models").get("data")
        rows = data.get("models") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            raise ApiError("红鸟未返回 data.models 模型列表", kind=TASK_FATAL)
        return [r["id"] for r in rows if isinstance(r, dict) and r.get("id")
                and r.get("type") == "video_generation"]

    def selftest(self):
        try:
            models = self.list_models()
            return {"ok": bool(models), "msg": f"红鸟鉴权通过，可用视频模型 {len(models)} 个"}
        except ApiError as exc:
            return {"ok": False, "msg": str(exc)}

    def build_body(self, task):
        if not str(task.prompt or "").strip():
            raise ApiError("红鸟视频提示词不能为空", kind=TASK_FATAL)
        try:
            seconds = int(task.duration)
            if seconds <= 0 or float(task.duration) != seconds:
                raise ValueError
        except (TypeError, ValueError):
            raise ApiError("红鸟视频时长须为正整数秒", kind=TASK_FATAL)
        body = {"model": task.model or "video-sd25-30s", "prompt": task.prompt,
                "seconds": str(seconds), "aspect_ratio": task.ratio or "9:16"}
        for field, value in (("images", task.refs),
                             ("videos", task.extra.get("video_refs") or task.extra.get("videos")),
                             ("audios", task.extra.get("audio_refs") or task.extra.get("audios"))):
            if value:
                prefixes = ("https://", "http://", "data:image/") if field == "images" else ("https://", "http://")
                if not isinstance(value, list) or any(not isinstance(v, str) or
                                                     not v.startswith(prefixes) for v in value):
                    raise ApiError(f"红鸟 {field} 参考素材格式无效；图片须为 URL/Data URI，"
                                   "视频和音频须为公网 URL", kind=TASK_FATAL)
                body[field] = value
        if task.resolution:
            body["resolution"] = task.resolution
        for field in ("parameters", "metadata"):
            if field in task.extra:
                if not isinstance(task.extra[field], dict):
                    raise ApiError(f"红鸟 {field} 必须是对象", kind=TASK_FATAL)
                body[field] = dict(task.extra[field])
        body.setdefault("metadata", {}).setdefault("order_id", "studio-" + uuid.uuid4().hex)
        return body

    def generate_video(self, task, dest, *, log=print, cancel=None,
                       poll_interval=5, poll_timeout=2400):
        _cancelled(cancel)
        body = self.build_body(task)
        log(f"红鸟 / {body['model']} / {body['seconds']}秒 / 参考图×{len(task.refs)}")
        try:
            data = self._request("POST", "/v1/videos", body)
        except ApiError as exc:
            # 无幂等契约；网络中断不代表创建失败，不能重投再扣费。
            if exc.kind == RETRYABLE:
                raise ApiError(f"红鸟提交未取得可靠回执，请先核对服务商订单 "
                               f"{body['metadata']['order_id']}：{exc}", kind=TASK_FATAL,
                               err_code="result_download_failed") from exc
            raise
        task_id = str(data.get("id") or "")
        if not task_id:
            raise ApiError(f"红鸟提交未返回任务 ID，请核对订单 {body['metadata']['order_id']}",
                           kind=TASK_FATAL, err_code="result_download_failed")
        log(f"红鸟任务 {task_id} 已提交，开始查询")
        deadline, last = time.monotonic() + poll_timeout, ""
        while time.monotonic() < deadline:
            _cancelled(cancel)
            status = str(data.get("status") or "").lower()
            if status != last:
                log(f"红鸟 {task_id} 状态: {status}")
                last = status
            if status == "failed":
                error = data.get("error") or {}
                message = error.get("message", "生成失败") if isinstance(error, dict) else str(error)
                code = str(error.get("code") or "generation_failed") if isinstance(error, dict) else "generation_failed"
                raise ApiError(f"红鸟任务 {task_id} 失败：{message}".replace(self.session.api_key, "[已隐藏]"),
                               kind=TASK_FATAL, err_code=code)
            if status == "completed":
                result = data.get("result") or {}
                url = data.get("video_url") or (result.get("video_url") if isinstance(result, dict) else "")
                if not isinstance(url, str) or not url.startswith(("https://", "http://")):
                    raise ApiError(f"红鸟任务 {task_id} 已完成但未返回视频地址，请到服务商取回结果",
                                   kind=TASK_FATAL, err_code="result_download_failed")
                _cancelled(cancel)
                log(f"红鸟任务 {task_id} 已完成，开始下载视频")
                try:
                    self.session.save_item(url, dest, log=log)
                except Exception as exc:
                    raise ApiError(f"红鸟任务 {task_id} 已完成，视频下载未成功：{exc}",
                                   kind=TASK_FATAL, err_code="result_download_failed") from exc
                return {"task_id": task_id, "source": url, "provider": self.id, "model": body["model"]}
            if status not in ("", "queued", "processing"):
                raise ApiError(f"红鸟任务 {task_id} 返回未知状态 {status!r}，请核对服务商任务页",
                               kind=TASK_FATAL, err_code="result_download_failed")
            _wait(min(max(3, poll_interval), max(0, deadline - time.monotonic())), cancel)
            if time.monotonic() >= deadline:
                break
            try:
                data = self._request("GET", "/v1/videos/" + quote(task_id, safe=""))
            except ApiError as exc:
                if exc.kind != RETRYABLE:
                    raise
                log(f"红鸟任务 {task_id} 查询暂时失败，继续查询同一任务：{exc}")
        raise ApiError(f"红鸟任务 {task_id} 查询超过 {poll_timeout} 秒，请先核对服务商结果再重试",
                       kind=TASK_FATAL, err_code="result_download_failed")
