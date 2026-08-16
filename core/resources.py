# -*- coding: utf-8 -*-
"""本机资源占用，以及「并发能开到多少」的实测依据。

## 先说一件容易搞反的事

**本机 CPU 和内存通常不是这套流水线的瓶颈。** 每个在途任务干的是
「发一个 HTTP 请求然后等」—— 等的时候既不吃 CPU 也不占多少内存。
真正卡住你的几乎总是**服务商那边**：限流（429）、排队、单账号并发上限。

所以这里报两类东西，分开看：

    本机余量   —— CPU、内存还剩多少。它给的是「还能不能再开」的上限
    服务商反应 —— 429 次数、每次调用耗时随并发怎么变。它给的是
                「再开有没有用」

**只看第一类会把并发调到一个本机撑得住、但服务商全在限流的数字上。**
那时候任务不会失败，只会变慢并且反复重试 —— 看起来在跑，实际在原地烧钱。

## 推荐值是怎么算的

不发明公式。用**这台机器上真实跑出来的数**：

    每个在途任务的内存增量  ← 实测（本进程占用 ÷ 峰值在途数）
    可用内存 ÷ 每任务增量    ← 本机能开多少
    最近的 429 比例          ← 服务商还接不接得住

两个数取小的，再留 30% 余量。数据不够时**明说数据不够**，不给一个
看起来很确定的假数字。
"""

from __future__ import annotations

import os
import threading
import time
from typing import Optional

# 采样点：(时刻, 本进程内存字节, 在途任务数)。只留最近这些。
_SAMPLES: list = []
_LOCK = threading.Lock()
_MAX_SAMPLES = 600          # 每 2 秒一个 ≈ 20 分钟


def _psutil():
    try:
        import psutil
        return psutil
    except ImportError:
        return None


def available() -> bool:
    """能不能读到资源数据。读不到时页面要如实说，不要显示 0。"""
    return _psutil() is not None


def snapshot() -> dict:
    """当前占用。psutil 没装时只返回它确实知道的那几项。"""
    out = {"cpu_count": os.cpu_count() or 0, "has_psutil": False}
    ps = _psutil()
    if not ps:
        return out
    vm = ps.virtual_memory()
    me = ps.Process()
    with me.oneshot():
        rss = me.memory_info().rss
        # 第一次调用返回 0.0（没有上一次采样做基准），如实标出来
        cpu_self = me.cpu_percent(None)
    out.update({
        "has_psutil": True,
        "cpu_percent": ps.cpu_percent(None),
        "cpu_self": cpu_self,
        "mem_total": vm.total,
        "mem_used": vm.total - vm.available,
        "mem_available": vm.available,
        "mem_percent": vm.percent,
        "proc_rss": rss,
        "threads": me.num_threads(),
    })
    return out


def sample(inflight: int) -> None:
    """记一个采样点。跑批时定期调，用来算「每个任务实际占多少内存」。"""
    ps = _psutil()
    if not ps:
        return
    try:
        rss = ps.Process().memory_info().rss
    except Exception:                                   # noqa: BLE001
        return
    with _LOCK:
        _SAMPLES.append((time.time(), rss, int(inflight)))
        if len(_SAMPLES) > _MAX_SAMPLES:
            del _SAMPLES[:len(_SAMPLES) - _MAX_SAMPLES]


def per_task_bytes() -> Optional[float]:
    """实测每个在途任务多占多少内存。样本不够就返回 None。

    做法很直接：拿「在途最多的那一刻」和「在途最少的那一刻」比，
    差额除以任务数差。不做回归 —— 样本少的时候回归只是把噪音包装得
    像个结论。
    """
    with _LOCK:
        pts = [(n, rss) for _, rss, n in _SAMPLES if n >= 0]
    if len(pts) < 8:
        return None
    lo = min(pts, key=lambda p: p[0])
    hi = max(pts, key=lambda p: p[0])
    if hi[0] - lo[0] < 2:           # 并发没拉开过，比不出来
        return None
    delta = (hi[1] - lo[1]) / (hi[0] - lo[0])
    return delta if delta > 0 else None


def advise(inflight_peak: int = 0, recent_429: int = 0,
           recent_calls: int = 0) -> dict:
    """并发能开到多少 —— 给依据，不给一个凭空的数。

    返回 {limit, basis, note}；算不出来时 limit 是 None 而不是随便一个数。
    """
    snap = snapshot()
    if not snap.get("has_psutil"):
        return {"limit": None, "basis": [],
                "note": "没装 psutil，读不到 CPU 和内存 —— "
                        "`pip install psutil` 之后这里才有数。"}

    basis, limit = [], None
    per = per_task_bytes()
    if per and per > 0:
        by_mem = int(snap["mem_available"] * 0.7 / per)
        basis.append(f"实测每个在途任务约占 {per / 1048576:.1f} MB，"
                     f"可用内存 {snap['mem_available'] / 1073741824:.1f} GB "
                     f"（留 30% 余量）→ 本机能开 {by_mem}")
        limit = by_mem
    else:
        basis.append("样本不够，还算不出「每个任务占多少内存」—— "
                     "跑一批任务、并发拉开一点之后再看")

    # **服务商那边才是真瓶颈。** 只报本机余量会把并发调到一个
    # 本机撑得住、服务商全在限流的数字上。
    if recent_calls >= 20:
        rate = recent_429 / recent_calls
        if rate > 0.05:
            basis.append(f"最近 {recent_calls} 次调用里 {recent_429} 次被限流"
                         f"（{rate:.0%}）—— **服务商已经接不住了，"
                         f"本机还有余量也别再往上加**")
            limit = max(1, int(inflight_peak * 0.7)) if inflight_peak else limit
        else:
            basis.append(f"最近 {recent_calls} 次调用里限流 {recent_429} 次"
                         f"（{rate:.0%}），服务商这边还有空间")
    else:
        basis.append("调用次数还太少，看不出服务商接不接得住")

    return {"limit": limit, "basis": basis,
            "note": "本机 CPU 和内存通常**不是**瓶颈 —— 在途任务多数时间在等网络。"
                    "调之前先看限流比例：那才决定「再开有没有用」。"}
