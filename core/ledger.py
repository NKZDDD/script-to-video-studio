# -*- coding: utf-8 -*-
"""用量账本：这一趟跑下来，每个环节花了多少 token、出了多少张图和片。

为什么要落盘而不是只打日志：日志在内存里，重启就没了；而「这部剧到现在花了
多少」是要跨天、跨多次续跑累计的问题。所以逐条追加到
07_检查与记录/usage.jsonl，只追加不改写 —— 账本不该被覆盖。

金额是**估算**，不是账单。各家计价方式不同（有的按 token、有的按次、
还有缓存读单独计价），程序不可能自己知道价格，所以要在「设置 → 计价」里填。
没填就只统计用量、不算钱 —— 宁可不显示，也不显示一个假数字。
"""

from __future__ import annotations

import json
import os
import time
from typing import Optional

from .store import LOCK

FILE = "usage.jsonl"


def path(project_root: str) -> str:
    return os.path.join(project_root, "07_检查与记录", FILE)


def record(project_root: str, **entry) -> None:
    """记一条用量。只追加，绝不改写已有的行。"""
    if not project_root:
        return
    entry.setdefault("at", time.strftime("%Y-%m-%d %H:%M:%S"))
    p = path(project_root)
    with LOCK:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def load(project_root: str) -> list:
    p = path(project_root)
    if not os.path.isfile(p):
        return []
    out = []
    with LOCK:
        with open(p, "r", encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue          # 半行/坏行跳过，别让整本账读不出来
    return out


# ---------------------------------------------------------------- 计价
def _rate(prices: dict, provider: str, model: str) -> dict:
    """取这个模型的价目。先找「服务商/模型」，再找「模型」，最后找服务商默认。"""
    prices = prices or {}
    for key in (f"{provider}/{model}", model, provider):
        if key in prices and isinstance(prices[key], dict):
            return prices[key]
    return {}


def cost_of(e: dict, prices: dict) -> Optional[float]:
    """估算一条记录的花费。价目缺失就返回 None（不猜）。"""
    r = _rate(prices, e.get("provider", ""), e.get("model", ""))
    if not r:
        return None
    if e.get("kind") == "llm":
        # 缓存读通常单独计价；没给 cached_in 就按 in 算
        cin = float(r.get("in", 0) or 0)
        cout = float(r.get("out", 0) or 0)
        ccached = float(r.get("cached_in", cin) or 0)
        cached = int(e.get("cached_tokens") or 0)
        fresh = max(0, int(e.get("prompt_tokens") or 0) - cached)
        per = float(r.get("per", 1_000_000) or 1_000_000)
        return (fresh * cin + cached * ccached
                + int(e.get("completion_tokens") or 0) * cout) / per
    # 出图出片一般按次
    if "per_call" in r:
        return float(r["per_call"]) * int(e.get("count") or 1)
    return None


def summary(project_root: str, prices: Optional[dict] = None) -> dict:
    """按环节 / 服务商+模型 两个维度汇总。金额没价目就不给。"""
    rows = load(project_root)
    prices = prices or {}
    tot = {"llm_calls": 0, "prompt_tokens": 0, "cached_tokens": 0,
           "completion_tokens": 0, "images": 0, "videos": 0,
           "seconds": 0.0, "cost": 0.0, "cost_known": True}
    by_stage: dict = {}
    by_model: dict = {}

    for e in rows:
        kind = e.get("kind", "")
        st = e.get("stage", "?")
        mk = f"{e.get('provider','?')}/{e.get('model','?')}"
        s = by_stage.setdefault(st, {"llm_calls": 0, "prompt_tokens": 0,
                                     "cached_tokens": 0, "completion_tokens": 0,
                                     "images": 0, "videos": 0, "seconds": 0.0,
                                     "cost": 0.0, "cost_known": True})
        m = by_model.setdefault(mk, dict(s))
        c = cost_of(e, prices)
        for bucket in (tot, s, m):
            if kind == "llm":
                bucket["llm_calls"] += 1
                for k in ("prompt_tokens", "cached_tokens", "completion_tokens"):
                    bucket[k] += int(e.get(k) or 0)
            elif kind == "image":
                bucket["images"] += int(e.get("count") or 1)
            elif kind == "video":
                bucket["videos"] += int(e.get("count") or 1)
            bucket["seconds"] += float(e.get("seconds") or 0)
            if c is None:
                bucket["cost_known"] = False
            else:
                bucket["cost"] += c

    return {"entries": len(rows), "total": tot,
            "by_stage": by_stage, "by_model": by_model,
            "priced": bool(prices),
            "note": ("金额是按「设置 → 计价」里填的价目估算的，不是账单，"
                     "以服务商后台为准" if prices else
                     "还没填价目，所以只统计用量不算钱。"
                     "去「设置 → 计价」按各家的价格填上就会显示估算金额。")}
