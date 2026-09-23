# -*- coding: utf-8 -*-
"""红鸟 OpenAPI；依据用户文档及 2026-09-23 实拉模型清单。

使用绝对导入，同一文件也可作为旧 Studio 的 providers 外挂加载。
"""
from __future__ import annotations

import copy
import json
import os
import re
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


# 只认「已提交、结果没落盘」的几种报错；「失败：」不在内，那一条重跑就该重新提交。
_PENDING = re.compile(r"红鸟任务 (\S+) (?:查询超过|查询已取消|查询中断|已完成|返回未知状态)")


def _read_json(path):
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _task_lists(root):
    tasks = _read_json(os.path.join(root, "03_提示词", "tasks.json"))
    plan = _read_json(os.path.join(root, "07_检查与记录", "active-plan.json"))
    if isinstance(plan, dict) and plan.get("tasks_path"):
        tasks = [tasks, _read_json(os.path.join(root, *str(plan["tasks_path"]).split("/")))]
    for doc in tasks if isinstance(tasks, list) else [tasks]:
        for rows in (doc.values() if isinstance(doc, dict) else ()):
            if isinstance(rows, list):
                yield from (r for r in rows if isinstance(r, dict))


def pending_task(dest):
    """这一段上次已经在红鸟建过任务、只是没取回时，返回 (任务 ID, "")。

    Studio 判断「做过没有」只看文件在不在，没落盘的段重跑会重新提交、再扣一次费；
    任务号只留在项目的 failures.json 里（raw 原文），按 dest 反查段落 key 取出来。
    失败之后提示词改过（重跑环节、改提示词、新发布），旧任务的成片不再对应这一段，
    返回 ("", 原因)，照常重新提交。
    """
    target = os.path.normcase(os.path.abspath(dest))
    root = os.path.dirname(target)
    while True:
        failures = os.path.join(root, "07_检查与记录", "failures.json")
        if os.path.isfile(failures):
            break
        parent = os.path.dirname(root)
        if parent == root:
            return "", ""
        root = parent
    rows = {r["key"]: r for r in _task_lists(root)
            if r.get("key") and isinstance(r.get("output"), str)
            and os.path.normcase(os.path.join(root, *r["output"].split("/"))) == target}
    for item in reversed(_read_json(failures) or []):
        if not isinstance(item, dict) or item.get("target") not in rows:
            continue
        found = _PENDING.search(str(item.get("raw") or ""))
        if not found:
            return "", ""
        prompt = str(rows[item["target"]].get("prompt_ref") or "")
        try:
            failed_at = time.mktime(time.strptime(str(item.get("at")), "%Y-%m-%d %H:%M:%S"))
            changed = os.path.getmtime(os.path.join(root, *prompt.split("/"))) > failed_at + 1
        except (OSError, ValueError, OverflowError):
            changed = True
        if changed:
            return "", (f"红鸟：失败记录里有未取回的任务 {found.group(1)}，但这一段的提示词"
                        f"在那之后改过（或无法核对），旧任务的成片不对应现在的内容 —— 按现在的提示词重新提交")
        return found.group(1), ""
    return "", ""


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
        old, note = pending_task(dest)
        if old:
            log(f"红鸟：这一段上次已提交任务 {old}，结果还没取回 —— 继续查询这个任务，不重新提交")
            return self._follow(old, None, dest, body["model"], log, cancel,
                                poll_interval, poll_timeout)
        if note:
            log(note)
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
        return self._follow(task_id, data, dest, body["model"], log, cancel,
                            poll_interval, poll_timeout)

    def _follow(self, task_id, data, dest, model, log, cancel, poll_interval, poll_timeout):
        """查询同一个任务直到落盘。data 为 None 时先查一次（续查旧任务）。

        会中断查询的报错都带上「红鸟任务 <ID>」原文：failures.json 只留这一句，
        丢了任务号，下次重跑就会重新提交、再扣一次费。
        """
        try:
            return self._poll(task_id, data, dest, model, log, cancel,
                              poll_interval, poll_timeout)
        except ApiError as exc:
            if task_id in str(exc):
                raise
            if str(exc) == "用户已取消":
                raise ApiError(f"红鸟任务 {task_id} 查询已取消，服务商那边的任务不受影响；"
                               "重跑这一段会继续查询它", kind=TASK_FATAL) from exc
            status, code = getattr(exc, "status", 0), getattr(exc, "err_code", "")
            if status == 404:
                raise ApiError(f"红鸟查询不到原任务 {task_id}（{exc}），重跑这一段将重新提交",
                               kind=TASK_FATAL, err_code=code) from exc
            raise ApiError(f"红鸟任务 {task_id} 查询中断：{exc}", status=status,
                           kind=exc.kind, err_code=code) from exc

    def _poll(self, task_id, data, dest, model, log, cancel, poll_interval, poll_timeout):
        deadline, last = time.monotonic() + poll_timeout, ""
        while time.monotonic() < deadline:
            _cancelled(cancel)
            if data is None:
                try:
                    data = self._request("GET", "/v1/videos/" + quote(task_id, safe=""))
                except ApiError as exc:
                    if exc.kind != RETRYABLE:
                        raise
                    log(f"红鸟任务 {task_id} 查询暂时失败，继续查询同一任务：{exc}")
                    data = {}
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
                return {"task_id": task_id, "source": url, "provider": self.id, "model": model}
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
