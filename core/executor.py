# -*- coding: utf-8 -*-
"""多线程任务执行器。

纪律（与 skill 一致）：
  · 输出已存在 → 跳过不覆盖（生成即固定）
  · 技术失败同参重试 ≤ N
  · 内容质量不判断，只记录
可配置并发度；视频/图片本质都是 API 调用，天然适合并行。
"""

from __future__ import annotations

import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Optional


class Job:
    """一次运行（一批任务）。线程安全的进度容器。"""

    _seq = 0
    _seq_lock = threading.Lock()

    def __init__(self, kind: str, total: int, concurrency: int):
        with Job._seq_lock:
            Job._seq += 1
            self.id = f"job{Job._seq}_{int(time.time())}"
        self.kind = kind
        self.total = total
        self.concurrency = concurrency
        self.started_at = time.time()
        self.finished_at: Optional[float] = None
        self.status = "running"          # running | done | cancelled | error
        self.cancelled = False
        self.items: dict = {}            # task_key -> {state, msg, output, attempts}
        self.logs: list = []             # 最近日志
        self._lock = threading.RLock()

    # -- 状态更新 -------------------------------------------------------
    def set_item(self, key: str, **kw) -> None:
        with self._lock:
            cur = self.items.setdefault(key, {"state": "pending", "msg": "", "attempts": 0})
            cur.update(kw)

    def log(self, key: str, msg: str) -> None:
        line = f"[{time.strftime('%H:%M:%S')}] {key}: {msg}"
        with self._lock:
            self.logs.append(line)
            if len(self.logs) > 500:
                del self.logs[:200]

    def counts(self) -> dict:
        with self._lock:
            c: dict = {}
            for it in self.items.values():
                c[it["state"]] = c.get(it["state"], 0) + 1
            return c

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "id": self.id,
                "kind": self.kind,
                "status": self.status,
                "total": self.total,
                "concurrency": self.concurrency,
                "counts": self.counts(),
                "items": {k: dict(v) for k, v in self.items.items()},
                "logs": self.logs[-120:],
                "elapsed": int((self.finished_at or time.time()) - self.started_at),
            }

    def cancel(self) -> None:
        with self._lock:
            self.cancelled = True
            self.status = "cancelled"


class JobManager:
    """全局运行记录（内存态）。"""

    def __init__(self):
        self.jobs: dict = {}
        self._lock = threading.RLock()

    def create(self, kind: str, total: int, concurrency: int) -> Job:
        job = Job(kind, total, concurrency)
        with self._lock:
            self.jobs[job.id] = job
            if len(self.jobs) > 50:                       # 只留最近 50 次
                for k in sorted(self.jobs)[:10]:
                    self.jobs.pop(k, None)
        return job

    def get(self, job_id: str) -> Optional[Job]:
        return self.jobs.get(job_id)

    def latest(self) -> Optional[Job]:
        with self._lock:
            if not self.jobs:
                return None
            return max(self.jobs.values(), key=lambda j: j.started_at)


def run_batch(job: Job, tasks: list, worker: Callable, *,
              key_of: Callable, max_retry: int = 2) -> None:
    """并发跑 tasks。worker(task, log_fn, cancel_fn) 成功返回 dict，失败抛异常。

    在后台线程里调用；worker 内部只做「一个任务」的事，重试逻辑在这里统一。
    """
    for t in tasks:
        job.set_item(key_of(t), state="pending")

    def one(task):
        key = key_of(task)
        if job.cancelled:
            job.set_item(key, state="cancelled")
            return
        job.set_item(key, state="running")

        def log(msg):
            job.log(key, msg)

        last_err = ""
        for attempt in range(1 + max_retry):
            if job.cancelled:
                job.set_item(key, state="cancelled")
                return
            try:
                if attempt:
                    log(f"技术重试 {attempt}/{max_retry}")
                    job.set_item(key, attempts=attempt)
                result = worker(task, log, lambda: job.cancelled)
                if isinstance(result, dict) and result.get("skipped"):
                    job.set_item(key, state="skipped", msg=result.get("msg", "已存在，跳过"))
                else:
                    job.set_item(key, state="ok",
                                 output=(result or {}).get("output", ""),
                                 msg=(result or {}).get("msg", ""))
                    log("完成")
                return
            except Exception as exc:                       # noqa: BLE001 全部按技术失败处理
                last_err = str(exc)[:300]
                log(f"失败: {last_err}")
                if attempt >= max_retry:
                    job.set_item(key, state="failed", msg=last_err)
                    job.log(key, "重试耗尽" if max_retry else "失败")
                    if "--debug" in str(getattr(job, "_debug", "")):
                        traceback.print_exc()

    with ThreadPoolExecutor(max_workers=max(1, job.concurrency)) as pool:
        list(pool.map(one, tasks))

    job.finished_at = time.time()
    if job.cancelled:
        job.status = "cancelled"
    else:
        counts = job.counts()
        job.status = "error" if counts.get("failed") else "done"
