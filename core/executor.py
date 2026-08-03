# -*- coding: utf-8 -*-
"""多线程任务执行器 + 三层并发闸门。

纪律（与 skill 一致）：
  · 输出已存在 → 跳过不覆盖（生成即固定）
  · 技术失败同参重试 ≤ N（重试在同一 worker 内串行，不额外占槽）
  · 内容质量不判断，只记录

并发三层（多剧并行时防止把服务商打爆）：
  1. 批内并发    ThreadPoolExecutor(max_workers=job.concurrency)
  2. 服务商配额  每个 provider 一个信号量（跨项目、跨 job 共享）
  3. 全局总闸    所有在途 API 调用的总上限
真正发请求前依次获取 [服务商配额 → 全局总闸]，拿不到就排队等待。
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from typing import Callable, Optional

from . import diagnose
from .apiutil import BATCH_FATAL, RETRYABLE, TASK_FATAL, ApiError


class Gate:
    """全局 + 按服务商的并发闸门。可热更新上限。"""

    def __init__(self, global_limit: int = 8, per_provider: Optional[dict] = None):
        self._lock = threading.RLock()
        self._global_limit = max(1, global_limit)
        self._global = threading.BoundedSemaphore(self._global_limit)
        self._per_conf = dict(per_provider or {})
        self._sems: dict = {}
        self._inflight: dict = {}
        self._inflight_total = 0

    def configure(self, global_limit: int, per_provider: dict) -> None:
        """上限变化时重建信号量；在途任务不受影响，新任务按新上限。"""
        with self._lock:
            gl = max(1, int(global_limit or 1))
            if gl != self._global_limit:
                self._global_limit = gl
                self._global = threading.BoundedSemaphore(gl)
            new_conf = {k: int(v) for k, v in (per_provider or {}).items() if v}
            for k, v in list(self._sems.items()):
                if new_conf.get(k) != getattr(v, "limit", None):
                    self._sems.pop(k, None)
            self._per_conf = new_conf

    def _sem_for(self, provider: str):
        limit = self._per_conf.get(provider)
        if not limit:
            return None
        with self._lock:
            sem = self._sems.get(provider)
            if sem is None:
                sem = threading.BoundedSemaphore(max(1, int(limit)))
                sem.limit = int(limit)               # type: ignore[attr-defined]
                self._sems[provider] = sem
            return sem

    @contextmanager
    def slot(self, provider: str):
        sem = self._sem_for(provider)
        if sem:
            sem.acquire()
        self._global.acquire()
        with self._lock:
            self._inflight[provider] = self._inflight.get(provider, 0) + 1
            self._inflight_total += 1
        try:
            yield
        finally:
            with self._lock:
                self._inflight[provider] = max(0, self._inflight.get(provider, 1) - 1)
                self._inflight_total = max(0, self._inflight_total - 1)
            self._global.release()
            if sem:
                sem.release()

    def snapshot(self) -> dict:
        with self._lock:
            return {"global_limit": self._global_limit,
                    "global_inflight": self._inflight_total,
                    "per_provider_limit": dict(self._per_conf),
                    "per_provider_inflight": {k: v for k, v in self._inflight.items() if v}}


GATE = Gate()


def _is_final(job: "Job") -> bool:
    return job.status in ("done", "error", "cancelled", "aborted")


class Job:
    """一次运行（一批任务）。线程安全的进度容器。"""

    _seq = 0
    _seq_lock = threading.Lock()

    def __init__(self, kind: str, total: int, concurrency: int,
                 project_root: str = "", project_name: str = "", provider: str = "",
                 model: str = ""):
        with Job._seq_lock:
            Job._seq += 1
            self.id = f"job{Job._seq}_{int(time.time())}"
        self.kind = kind
        self.total = total
        self.concurrency = concurrency
        self.project_root = project_root
        self.project_name = project_name
        self.provider = provider
        self.model = model
        self.started_at = time.time()
        self.finished_at: Optional[float] = None
        self.status = "running"
        self.cancelled = False
        self.aborted = False              # 遇到账户级问题，主动停掉整批
        self.abort_reason = ""
        self.abort_diag: Optional[dict] = None
        self.items: dict = {}
        self.logs: list = []
        self._lock = threading.RLock()

    def abort_with(self, diag: dict) -> None:
        """碰到余额、密钥这类整批都过不去的问题：立刻停掉剩下的任务。

        不这么做的话，10 个任务会挨个撞同一堵墙、报 10 遍一样的错，
        有些服务商失败也照样计费。停在第一个，剩下的一个都不发。
        """
        with self._lock:
            if self.aborted:
                return
            self.aborted = True
            self.cancelled = True
            self.abort_diag = diag
            self.abort_reason = f"{diag['title']}：{diag['raw'][:200]}"
        self.log("已停止", f"{diag['title']} —— 剩下的任务一个都没发出去，没有白花钱")

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

    def brief(self) -> dict:
        """列表用的轻量快照（不含 items/logs）。"""
        c = self.counts()
        finished = c.get("ok", 0) + c.get("skipped", 0) + c.get("failed", 0) + c.get("cancelled", 0)
        return {"id": self.id, "kind": self.kind, "status": self.status,
                "total": self.total, "finished": finished, "counts": c,
                "concurrency": self.concurrency, "provider": self.provider,
                "project_root": self.project_root, "project_name": self.project_name,
                "aborted": self.aborted, "abort_reason": self.abort_reason,
                "abort_diag": self.abort_diag, "model": self.model,
                "elapsed": int((self.finished_at or time.time()) - self.started_at)}

    def snapshot(self) -> dict:
        with self._lock:
            d = self.brief()
            d["items"] = {k: dict(v) for k, v in self.items.items()}
            d["logs"] = self.logs[-120:]
            return d

    def cancel(self) -> None:
        with self._lock:
            self.cancelled = True
            self.status = "cancelled"


class JobManager:
    """全局运行记录（内存态）。支持多项目并行。"""

    def __init__(self):
        self.jobs: dict = {}
        self._lock = threading.RLock()

    def create(self, kind: str, total: int, concurrency: int, **kw) -> Job:
        job = Job(kind, total, concurrency, **kw)
        with self._lock:
            self.jobs[job.id] = job
            if len(self.jobs) > 80:                   # 只回收已结束的
                done = [j for j in sorted(self.jobs.values(), key=lambda x: x.started_at)
                        if _is_final(j)][:20]
                for j in done:
                    self.jobs.pop(j.id, None)
        return job

    def get(self, job_id: str) -> Optional[Job]:
        return self.jobs.get(job_id)

    def list(self, project_root: str = "", active_only: bool = False, limit: int = 40) -> list:
        with self._lock:
            out = []
            for j in sorted(self.jobs.values(), key=lambda x: x.started_at, reverse=True):
                if project_root and j.project_root != project_root:
                    continue
                if active_only and _is_final(j):
                    continue
                out.append(j.brief())
                if len(out) >= limit:
                    break
            return out

    def active_count(self) -> int:
        return sum(1 for j in self.jobs.values() if not _is_final(j))


def run_batch(job: Job, tasks: list, worker: Callable, *,
              key_of: Callable, max_retry: int = 2, provider: str = "") -> None:
    """并发跑 tasks。worker(task, log_fn, cancel_fn) 成功返回 dict，失败抛异常。

    每个任务在真正发请求前经过 GATE（服务商配额 + 全局总闸）。
    """
    provider = provider or job.provider
    for t in tasks:
        job.set_item(key_of(t), state="pending")

    def one(task):
        key = key_of(task)
        if job.cancelled:
            job.set_item(key, state="aborted" if job.aborted else "cancelled",
                         msg="前面出错停了，这条还没开始" if job.aborted else "已取消")
            return
        job.set_item(key, state="queued")

        def log(msg):
            job.log(key, msg)

        with GATE.slot(provider):                      # ← 并发闸门
            if job.cancelled:
                job.set_item(key, state="aborted" if job.aborted else "cancelled")
                return
            job.set_item(key, state="running")
            for attempt in range(1 + max_retry):
                if job.cancelled:
                    job.set_item(key, state="aborted" if job.aborted else "cancelled")
                    return
                try:
                    if attempt:
                        log(f"第 {attempt} 次重试（最多 {max_retry} 次）")
                        job.set_item(key, attempts=attempt)
                    result = worker(task, log, lambda: job.cancelled) or {}
                    warn = result.get("warn") if isinstance(result, dict) else None
                    if isinstance(result, dict) and result.get("skipped"):
                        job.set_item(key, state="skipped", msg=result.get("msg", "已存在，跳过"))
                    else:
                        job.set_item(key, state="ok", output=result.get("output", ""),
                                     msg=result.get("msg", "") or (warn or {}).get("title", ""))
                        log("完成" if not warn else f"完成，但要注意：{warn['title']}")
                    # 先销账再记提醒：这一条这次是做成了，上一次的失败记录该清掉；
                    # 但如果做出来的东西有毛病（比如比例不对），得留一条提醒。
                    diagnose.clear(job.project_root, job.kind, key)
                    if warn:
                        job.set_item(key, warn=True, diag=warn)
                        diagnose.record(job.project_root, warn)
                    return
                except Exception as exc:               # noqa: BLE001
                    kind = getattr(exc, "kind", RETRYABLE)
                    diag = diagnose.build(exc, stage=job.kind, target=key,
                                          provider=job.provider, model=job.model)

                    def fail():
                        job.set_item(key, state="failed", msg=diagnose.one_line(diag),
                                     kind=kind, diag=diag)
                        diagnose.record(job.project_root, diag)

                    if kind == BATCH_FATAL:            # 余额/密钥：整批都过不去，立刻停
                        log(diagnose.one_line(diag))
                        fail()
                        job.abort_with(diag)
                        return
                    if kind == TASK_FATAL:             # 只是这一条的问题，别的接着跑
                        log(diagnose.one_line(diag) + "（这条不再重试）")
                        fail()
                        return

                    # 这类错误多半是临时的（网络抖动、服务商忙），等下再试一次
                    log(f"这次没成功: {diag['raw'][:160]}")
                    if attempt >= max_retry:
                        fail()

    with ThreadPoolExecutor(max_workers=max(1, job.concurrency)) as pool:
        list(pool.map(one, tasks))

    job.finished_at = time.time()
    if job.aborted:
        job.status = "aborted"
    elif job.cancelled:
        job.status = "cancelled"
    else:
        job.status = "error" if job.counts().get("failed") else "done"
