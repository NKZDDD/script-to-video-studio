# -*- coding: utf-8 -*-
"""已经提交给服务商、结果还没落盘的任务。

判断「做过没有」只看输出文件在不在。任务在服务商那边排队、查询超时、点了停止、
程序重启，文件都不在 —— 以前重跑就是重新提交、再付一次钱，而原来那条任务
在服务商那边照样跑完、照样扣费，没人去取。

这里给每个输出记一份：提交成功拿到任务号就落盘，结果下载成功就删。重跑时有记录、
而且这一段的内容（提示词、参考图、参数、服务商、模型）没变，就续查旧任务。

记录落盘由公共轮询 `HttpSession.poll` 自动完成；自己写轮询的服务商在拿到任务号后
调 `remember()`。两者都只在生产工作函数用 `tracking()` 声明了「这是哪个输出」时生效。
"""
from __future__ import annotations

import contextvars
import hashlib
import json
import os
import time
from contextlib import contextmanager
from typing import Optional

from .store import read_json, write_json

_current: contextvars.ContextVar = contextvars.ContextVar("submitted_current", default=None)

# 公共轮询能直接重放的结果解析方式。自定义解析（闭包、按模型分支）没法落盘重建，
# 那几家要在自己的 resume 里处理。
REPLAYABLE = ("extract_video_url", "extract_image_items")


def _path(root: str, output: str) -> str:
    name = hashlib.sha1(output.encode("utf-8")).hexdigest()[:20]
    return os.path.join(root, "07_检查与记录", "submitted", name + ".json")


def identity(**parts) -> str:
    """这一段内容的指纹。任何一项变了，旧任务的成片就不再对应这一段。"""
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False, sort_keys=True,
                                     default=str).encode("utf-8")).hexdigest()


@contextmanager
def tracking(root: str, output: str, **meta):
    token = _current.set({"path": _path(root, output), "meta": {"output": output, **meta}})
    try:
        yield
    finally:
        _current.reset(token)


def remember(task_id, **resume) -> None:
    cur = _current.get()
    if not cur or not task_id:
        return
    write_json(cur["path"], {**cur["meta"], "task_id": str(task_id), "resume": resume,
                             "submitted_at": time.strftime("%Y-%m-%d %H:%M:%S")})


def load(root: str, output: str) -> Optional[dict]:
    rec = read_json(_path(root, output))
    return rec if isinstance(rec, dict) and rec.get("task_id") else None


def pending(root: str) -> dict:
    """整个项目还没取回的任务 → {output: 记录}。页面每几秒刷新一次，别逐个任务读文件。"""
    folder = os.path.join(root, "07_检查与记录", "submitted")
    out = {}
    for name in os.listdir(folder) if os.path.isdir(folder) else []:
        rec = read_json(os.path.join(folder, name)) if name.endswith(".json") else None
        if isinstance(rec, dict) and rec.get("task_id") and rec.get("output"):
            out[rec["output"]] = rec
    return out


def clear(root: str, output: str) -> bool:
    try:
        os.remove(_path(root, output))
        return True
    except FileNotFoundError:
        return False


def finished(exc: BaseException) -> bool:
    """服务商已经给了结论（失败 / 查不到这个任务）：旧任务不会再出结果，下次该重新提交。"""
    return bool(getattr(exc, "task_finished", False))
