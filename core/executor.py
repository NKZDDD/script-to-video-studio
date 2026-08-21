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
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
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

    def set_provider_limit(self, provider: str, limit: int) -> None:
        """单独改某一家的并发上限，不动别家。

        给「按账号串行」那种家用（HVTALD）：它的上限**就是账号数**，
        而账号数是从密钥文本里解出来的，configure() 那份来自 config 的表
        不知道这个数。

        为什么必须让 GATE 卡住而不是在 worker 里等：worker 是在
        `with GATE.slot(provider)` **里面**跑的。一个账号 + 并发 10 的话，
        9 条任务会占着全局槽位干等 —— 而全局默认只有 8 个槽，
        别家的出图会被这 9 条堵死。卡在 GATE 上就不会进来占槽。
        """
        n = max(1, int(limit or 1))
        with self._lock:
            if self._per_conf.get(provider) == n:
                return
            self._per_conf[provider] = n
            self._sems.pop(provider, None)     # 下一个任务按新上限重建

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


class LlmGate:
    """分析引擎（LLM）的并发上限。

    为什么不复用上面的 GATE：那道闸门是按「出图出片服务商的配额」算的，
    LLM 走的是另一个网关、另一套限流规则。混在一条闸门里，出图一忙就把
    分析饿死，反过来也一样。所以单独一道，上限单独配。
    """

    def __init__(self, limit: int = 4):
        self._lock = threading.RLock()
        self._limit = max(1, int(limit))
        self._sem = threading.BoundedSemaphore(self._limit)
        self._inflight = 0
        self._peak = 0

    def configure(self, limit: int) -> None:
        with self._lock:
            n = max(1, int(limit or 1))
            if n != self._limit:
                self._limit = n
                self._sem = threading.BoundedSemaphore(n)

    @contextmanager
    def slot(self):
        with self._lock:
            sem = self._sem          # 取一次；期间改上限也不影响已在排队的
        sem.acquire()
        with self._lock:
            self._inflight += 1
            self._peak = max(self._peak, self._inflight)
        try:
            yield
        finally:
            with self._lock:
                self._inflight = max(0, self._inflight - 1)
            sem.release()

    def snapshot(self) -> dict:
        with self._lock:
            return {"llm_limit": self._limit, "llm_inflight": self._inflight,
                    "llm_peak": self._peak}

    def reset_peak(self) -> None:
        with self._lock:
            self._peak = self._inflight


LLM_GATE = LlmGate()


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

    def reorder_items(self, keys: list) -> None:
        """按给定顺序重排 items，已有的状态原样保留。

        为什么需要：环节1 跑完才知道有几集，之后才能把逐集步骤补进计划。
        而 items 是字典，按插入顺序显示 —— 先插的「出图出片/交付」会排在
        后补的「逐集环节」前面，页面上看着像要先出图再分镜，顺序完全反了。
        执行顺序一直是对的（steps 列表重建过），这里只修显示。
        """
        with self._lock:
            fresh = {}
            for k in keys:
                fresh[k] = self.items.get(
                    k, {"state": "pending", "msg": "", "attempts": 0})
            # 计划外的（比如已取消的旧步骤）挂到末尾，不丢
            for k, v in self.items.items():
                if k not in fresh:
                    fresh[k] = v
            self.items = fresh

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


def run_chain(tasks: list, *, chain: list, worker_of: Callable, job_of: Callable,
              key_of: Callable, done_of: Callable, max_retry: int = 2,
              log: Callable = print, deps_of: Optional[Callable] = None,
              ready_of: Optional[Callable] = None) -> dict:
    """按优先级依次尝试各家服务商：上一家因为「这家不行」挂了就换下一家补剩下的。

    chain 是有序的 [{provider, model, ...}, ...]，第一个是首选。

    什么时候换家由 diagnose.should_failover 说话：余额不足、密钥失效、没这个模型、
    限流、连不上 —— 换家能解决。而提示词被审核挡下、参数不合法、参考图还没生成 ——
    换家也一样，那是内容或流程问题，自动切只会把同一个错误重复几遍还多花钱。

    每一家一个独立子 job（生产面板上能分别看到），已经做出来的产物一律跳过，
    所以换家时只补没做完的那些。
    """
    attempts = []
    for idx, pcfg in enumerate(chain):
        todo = [t for t in tasks if not done_of(t)]
        if not todo:
            break
        who = f"{pcfg.get('provider','')} {pcfg.get('model','')}".strip()
        if idx:
            log(f"改用第 {idx + 1} 家「{who}」补剩下的 {len(todo)} 项")
        sub = job_of(pcfg, len(todo))
        run_batch(sub, todo, worker_of(pcfg), key_of=key_of, max_retry=max_retry,
                  provider=pcfg.get("provider", ""), deps_of=deps_of,
                  ready_of=ready_of)
        c = sub.counts()
        attempts.append({"provider": pcfg.get("provider", ""), "model": pcfg.get("model", ""),
                         "job_id": sub.id, "counts": c, "aborted": sub.aborted,
                         "diag": sub.abort_diag})
        left = [t for t in tasks if not done_of(t)]
        if not left:
            return {"attempts": attempts, "left": 0, "switched": idx}

        # 这一家没做完 —— 值不值得换下一家？
        diag = sub.abort_diag
        if not diag:
            # 没整批熔断，看失败任务里的诊断（取第一条有 diag 的）
            for it in sub.items.values():
                if it.get("state") == "failed" and it.get("diag"):
                    diag = it["diag"]
                    break
        if idx + 1 >= len(chain):
            log(f"「{who}」还有 {len(left)} 项没做成，而且没有下一家可换了")
            break
        if not diagnose.should_failover(diag):
            log(f"「{who}」失败原因是「{(diag or {}).get('title','未知')}」——"
                f"换一家也一样，不自动切换。照面板上的说明处理后再点一次。")
            break
        log(f"⚠️ 「{who}」不行了（{(diag or {}).get('title','未知')}），"
            f"自动换下一家继续，还剩 {len(left)} 项")
    left = [t for t in tasks if not done_of(t)]
    return {"attempts": attempts, "left": len(left),
            "switched": max(0, len(attempts) - 1)}


# 「在等跨批输入」最多等多久。等待的正常终点是上游那一批跑完，
# 这个上界只防依赖图里出现没想到的环。到点就照旧派出去 ——
# 让出图那一层现有的硬停说清缺哪张，而不是在这里另造一条报错。
_CROSS_WAIT_SECONDS = 5400

_DONE_STATES = ("ok", "skipped")
_DEAD_STATES = ("failed", "aborted", "cancelled")


def run_batch(job: Job, tasks: list, worker: Callable, *,
              key_of: Callable, max_retry: int = 2, provider: str = "",
              deps_of: Optional[Callable] = None,
              ready_of: Optional[Callable] = None) -> None:
    """并发跑 tasks。worker(task, log_fn, cancel_fn) 成功返回 dict，失败抛异常。

    每个任务在真正发请求前经过 GATE（服务商配额 + 全局总闸）。

    `deps_of(key) -> [key, ...]` 给的是「这一条要等这一批里的哪几条」。
    给了就**就绪即派**：一条的参考图全好了它立刻开跑，不用等同层的其他条。

    以前是分层跑：层内并发、层间串行。正确但是慢 ——
    一层里有一条慢的（4K 出图、被审核挡了要改写重试几轮），
    **整层都在等它**，后面那些参考图早就齐了的也干等着。
    改成就绪即派之后，等的只是自己真正依赖的那几条。

    上游没做成时下游**当场标失败并说清是等谁**，不再派出去。以前会派 ——
    然后那一条花一次调用去读一个不存在的参考图，报「参考图指不到文件」，
    人还得自己回头找是哪个上游没成。既费钱又难查。
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
                    # 程序自己的 bug 重试是纯浪费：同一段代码、同一个错。
                    # 默认 kind 是 RETRYABLE，不拦的话每条任务白跑三遍 ——
                    # 而且是在**每一条**任务上，一批几百条就是几百次无用调用。
                    if diagnose.is_app_bug(exc):
                        kind = TASK_FATAL

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

    if deps_of is None and ready_of is None:
        with ThreadPoolExecutor(max_workers=max(1, job.concurrency)) as pool:
            list(pool.map(one, tasks))
        return

    def _state(key: str) -> str:
        return str((job.items.get(key) or {}).get("state") or "")

    # **只等这一批里的。** 换家补跑时 tasks 只剩没做成的那些，上一家已经出好的
    # 不在这一批里 —— 不过滤的话它们的状态查出来是空，依赖它们的会被判成
    # 「既没做成也没在跑」，于是整批误报成环。已经在磁盘上的本来就不用等。
    mine = {key_of(t) for t in tasks}

    def _deps(key: str) -> list:
        if deps_of is None:
            return []
        return [d for d in (deps_of(key) or ()) if d in mine and d != key]

    left = list(tasks)
    # 「在等跨批输入」那种等待要有个上界。正常情况下等待的终点是上游那一批
    # 跑完（ready_of 那边会知道），这个上界只防依赖图里出现没想到的环 ——
    # 那时候没有任何信号会到来，而**静默挂着是最难查的一种失败**。
    idle_since = 0.0
    with ThreadPoolExecutor(max_workers=max(1, job.concurrency)) as pool:
        running: dict = {}
        while left or running:
            if job.cancelled:
                # 取消了就把剩下的全交给 one —— 它自己会标成已取消/已中止。
                # 不派的话它们一直挂在 pending 上，面板上看着像还在跑。
                for t in left:
                    running[pool.submit(one, t)] = t
                left = []
            fire, hold = [], []
            for t in left:
                deps = _deps(key_of(t))
                dead = [d for d in deps if _state(d) in _DEAD_STATES]
                if dead:
                    # 上游没做成 —— 这一条不可能成，别花钱去撞那个不存在的文件。
                    job.set_item(key_of(t), state="failed", kind=TASK_FATAL,
                                 msg="它要的参考图没做成："
                                     + "、".join(dead[:4])
                                     + ("…" if len(dead) > 4 else "")
                                     + "。先把那几条弄成，这条会自动跟着能跑。")
                    continue
                if not all(_state(d) in _DONE_STATES for d in deps):
                    hold.append((t, ""))        # 等同批的 —— 空的等待说明
                    continue
                # 同批的依赖齐了，再问一句**跨批**的输入齐没齐 ——
                # 出图出片四类活是并发跑的，故事板可能比它要的场景状态图先排到。
                waits = ""
                if ready_of is not None:
                    ok, waits = ready_of(t)
                    if ok is None:
                        # **条件不具备，不是失败也不是等待。**
                        # 用户原话：「他缺少实际条件他不能去做才对」。
                        # 派出去撞一次空，面板上留下的是一条「失败」——
                        # 而它不是失败，是这一条**还不能做**。两者长得一样，
                        # 人就分不出「这条要修」和「这条在等前面」。
                        job.set_item(key_of(t), state="failed", kind=TASK_FATAL,
                                     msg="条件不具备，没发请求：缺 " + (waits or "上游产物")
                                         + "。而且没有任何一步会做出它们 —— "
                                           "先把产它的那一环跑出来，这条会自动跟着能跑。")
                        continue
                    if not ok:
                        hold.append((t, waits))
                        continue
                fire.append(t)
            left = [t for t, _ in hold]
            for t in fire:
                running[pool.submit(one, t)] = t
            if running:
                idle_since = 0.0
                done, _ = wait(running, return_when=FIRST_COMPLETED)
                for f in done:
                    running.pop(f, None)
                    f.result()      # one 内部已消化业务异常；这里让程序 bug 冒出来
                continue
            # 没有在跑的、也没有能跑的。两种情况，处理方式完全不同：
            #
            #   全都在等**跨批**输入  → 上游那一批还在跑，等就对了（不是错误）
            #   等的是**同批**依赖    → 只可能是成环，当场报出来
            #
            # 混在一起处理的话，「四类活并发跑、故事板比场景状态图先排到」
            # 这种完全正常的情况会被整批误杀。
            crossing = [(t, w) for t, w in hold if w]
            if crossing:
                now = time.time()
                if not idle_since:
                    idle_since = now
                    for t, w in crossing[:6]:
                        job.log(key_of(t),
                                f"在等上游产物：{w}（齐了就自动开跑，不用管）")
                if now - idle_since < _CROSS_WAIT_SECONDS:
                    time.sleep(2)
                    continue
                job.log("等待超时", f"等上游产物等了 {int(now - idle_since)} 秒还没齐，"
                                    f"不再等了 —— 剩下 {len(crossing)} 条照旧派出去，"
                                    f"缺什么由出图那一步报清楚")
                for t, _ in crossing:
                    running[pool.submit(one, t)] = t
                left = [t for t, w in hold if not w]
                continue
            # 派任务前的成环检查该拦住这种情况，拦漏了也**绝不静默挂着**。
            for t, _ in hold:
                job.set_item(key_of(t), state="failed", kind=TASK_FATAL,
                             msg="它等的参考图既没做成也没在跑（多半是互相引用成了环）："
                                 + "、".join(_deps(key_of(t))[:4]))
            break

    job.finished_at = time.time()
    if job.aborted:
        job.status = "aborted"
    elif job.cancelled:
        job.status = "cancelled"
    else:
        job.status = "error" if job.counts().get("failed") else "done"
