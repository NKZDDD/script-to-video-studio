# -*- coding: utf-8 -*-
"""Julun result hotfix 2026-09-21. Install in the OLD Studio data/providers directory.

Inherits the host's Julun model list, submission, uploads and downloads.
Only replaces task-result polling. Contains no credentials; no import-time requests.
"""
import re
import time

from core import apiutil as _api
from core.apiutil import DONE_STATES, FAIL_STATES, TASK_FATAL, ApiError, task_failed
from core.providers.julun import JulunProvider as _BuiltinJulunProvider

PATCH_VERSION = "20260921.1"
# Early Julun-capable builds do not yet export RUNNING_STATES.
RUNNING_STATES = getattr(_api, "RUNNING_STATES", (
    "queued", "pending", "processing", "in_progress", "in-progress", "inprogress",
    "running", "waiting", "created", "submitted", "start", "starting",
    "generating", "uploading", "preparing", "init", "initializing",
    "progress", "incomplete", "not_start", "notstart", "doing", "active",
    "accepted", "scheduled", "retrying",
))


class JulunResultHotfix(_BuiltinJulunProvider):
    """Same provider ID means the external loader replaces only built-in Julun."""
    id = "julun"

    def _poll(self, task_id: str, interval: int, timeout: int, *, log, cancel=None) -> str:
        """兼容顶层和 data 包装；终态以 status 为准，不等待 progress 到 100。"""
        start, last = time.time(), ""
        while time.time() - start < timeout:
            if cancel and cancel():
                raise ApiError("用户已取消")
            data = self.session.request("GET", f"/v1/videos/{task_id}",
                                        retries=1, timeout=60)
            inner = (data or {}).get("data")
            if not isinstance(inner, dict) or not inner.get("status"):
                inner = data or {}
            status = str(inner.get("status") or "").strip()
            low = status.lower()
            if status != last:
                log(f"巨轮 {task_id}: {status} {inner.get('progress', '')}")
                last = status
            if low in FAIL_STATES or low == "failure":
                detail = inner.get("error")
                if not isinstance(detail, dict):
                    detail = {"message": inner.get("fail_reason") or detail or
                              inner.get("message") or "服务商未提供失败原因",
                              "code": inner.get("error_code") or ""}
                exc = task_failed(detail)
                exc.args = (f"巨轮 {task_id}：{exc}",)
                # 原样重发不能解决素材肖像要求；只停此任务，不影响其他任务。
                if re.search(r"肖像(?:权)?保护|likeness protection", str(exc), re.I):
                    exc.kind = TASK_FATAL
                    exc.extra_fix.append("按服务商的肖像要求检查参考素材与账号条件，处理后再手动重试。")
                raise exc
            url = inner.get("result_url") or ""
            # **别只认 `SUCCESS` 这一个词。** 认死一个词的后果是：平台上
            # 显示已完成、地址也回来了，而我们接着等到超时 ——
            # 报出来的是「超时」，人会去查线路，查不到任何东西。
            # 所以：认识的完成词收；认不出的词**只要地址已经到手就收**；
            # 只有认识的「还在跑」才接着等。
            done = low in DONE_STATES
            unknown = bool(low) and not done and low not in RUNNING_STATES
            if done:
                if url:
                    return url
                return f"{self.session.base_url.rstrip('/')}/v1/videos/{task_id}/content"
            if url and unknown:
                log(f"⚠️ 巨轮的状态词 `{status}` 我们不认识，但 result_url 已经"
                    f"回来了 —— 按完成收下。把这个词补进 apiutil 的 DONE_STATES。")
                return url
            time.sleep(interval)
        raise ApiError(f"巨轮任务超时：{task_id}（一般 1–3 分钟，高峰更久）",
                       status=0, kind="retryable")
