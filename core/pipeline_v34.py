# -*- coding: utf-8 -*-
"""V3.4 一键跑到底。

和 main 上的 pipeline.py 是两套体系各自的编排，互不引用。
出图出片仍然走 produce.py（体系无关），并发和换家重试走 executor.py。

「继续」不是另一个功能，就是再点一次同一个按钮：每一步都以磁盘为准判断
做过没有，做过就跳过。跑到第 12 集断了，再点一次前 11 集一步都不重做。

出错分三层，因为「继续跑」和「赶紧停」在不同情况下各是对的：
  · 余额不足 / 密钥失效     立刻停整条。继续只是烧钱撞同一堵墙。
  · 某一集的某个环节失败    跳过这一集剩下的，**其它集照常跑**。
  · 单个出图/出片任务失败   本来就隔离了，继续跑别的。
"""

from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Optional

from . import diagnose, episodes as _eps, run_v34 as R, system_v34 as V
from .apiutil import BATCH_FATAL
from .llm import LLMCancelled
from .executor import Job, run_chain


def plan(pj, *, include_produce: bool = True, include_deliver: bool = True,
         only_episodes: Optional[list] = None) -> list:
    """算出要做哪些步骤（含已完成的，执行时再跳过）。

    顺序是有讲究的：
      n1 先跑，它切完集才知道有几集；
      n2 是全剧规则，也只跑一次；
      每集的 n3→n14 连着跑（同一集内部有硬依赖）；
      **资产图等所有集的资产提示词齐了再出** —— 资产库全剧共享，
        一次出齐才不会重复出图，也不会出两张不同的脸；
      场景状态图 → 故事板 → 视频，每一步都拿上一步当参考图；
      交付放最后。
    """
    steps = [{"kind": "freeze", "stage": "n0", "episode": "", "label": _label("n0")}]
    steps.append({"kind": "llm", "stage": "n1", "episode": "", "label": _label("n1")})
    steps.append({"kind": "llm", "stage": "n2", "episode": "",
                  "label": _label("n2")})

    eps = _eps.ids(pj)
    if only_episodes:
        want, have = set(only_episodes), eps
        eps = [e for e in eps if e in want]
        if have and not eps:
            raise ValueError(
                f"「分析这几集」填的 {'、'.join(sorted(want))} 在这个项目里都不存在 —— "
                f"当前切出了 {len(have)} 集：{have[0]} … {have[-1]}。"
                f"把那个框清空 = 全部集。")

    per_ep = [s["id"] for s in V.STAGES
              if s["kind"] == "llm" and V.scope_of(s["id"]) in ("episode", "segment")
              and s["id"] != "n14"]
    for ep in eps:
        for sid in per_ep:
            steps.append({
                "kind": "llm", "stage": sid, "episode": ep, "label": _label(sid, ep),
                # 逐段环节：一段失败不毒掉整集，下游按段自己判断哪些能做
                **({"soft": True} if V.scope_of(sid) == "segment" else {})})

    if include_produce:
        for sid in V.PRODUCE_ORDER:
            s = V.by_id()[sid]
            steps.append({"kind": "produce", "stage": sid,
                          "task_key": s["task_key"],
                          "produce": _worker_kind(sid),
                          "episode": "", "only": list(eps) if eps else None,
                          "label": _label(sid) + _scope_tag(eps, pj)})

    if include_deliver:
        for ep in eps:
            steps.append({"kind": "llm", "stage": "n14", "episode": ep,
                          "soft": True, "label": _label("n14", ep)})
        steps.append({"kind": "deliver", "stage": "d2", "episode": "",
                      "only": list(eps) if eps else None,
                      "label": _label("d2") + _scope_tag(eps, pj)})
    return steps


def _label(stage_id: str, episode: str = "") -> str:
    s = V.by_id()[stage_id]
    head = f"{episode} " if episode else ""
    return f"{head}第{s['no']}环节 {s['name']}" if s["kind"] == "llm" \
        else f"{head}{s['name']}"


def _scope_tag(eps: list, pj) -> str:
    return f"（只 {'、'.join(eps)}）" if eps and eps != _eps.ids(pj) else ""


def _worker_kind(stage_id: str) -> str:
    """出图出片那一层认的类型。scstate 和故事板都是出图，走同一个 worker。"""
    return {"p1": "asset", "p2": "storyboard", "p3": "storyboard", "p4": "video"}[stage_id]


def _llm_done(pj, stage_id: str, episode: str) -> bool:
    """这一步做过没有 —— 以磁盘产物为准，不看任何运行记录。

    逐段环节要按段算：跑了 8 段还剩 4 段时产物文件是存在的，但没做完。
    只看文件在不在会把剩下 4 段永远漏掉。
    """
    tpl, _, _ = V.LLM_SPEC[stage_id]
    if V.scope_of(stage_id) == "segment":
        segs = {s["seg_id"] for s in R.segments_of(pj, episode)}
        return bool(segs) and segs <= R.done_segments(pj, stage_id, episode)
    ep = "" if V.scope_of(stage_id) == "series" else episode
    return bool(pj.stage_data(tpl, ep))


def run(job: Job, pj, *, llm_factory: Callable, provider_factory: Callable,
        params: dict, jobs=None, concurrency: int = 3, max_retry: int = 2,
        include_produce: bool = True, include_deliver: bool = True,
        only_episodes: Optional[list] = None,
        ep_concurrency: int = 4, seg_concurrency: int = 4) -> None:
    steps = plan(pj, include_produce=include_produce,
                 include_deliver=include_deliver, only_episodes=only_episodes)
    job.total = len(steps)
    for s in steps:
        job.set_item(s["label"], state="pending")

    st_lock = threading.Lock()
    bad_eps: set = set()
    failed: list = []

    def halt(s: dict) -> bool:
        """要停了吗。先判熔断再判取消 —— abort_with 会连带把 cancelled 置真，
        反过来判会把熔断显示成「已取消」。"""
        if job.aborted:
            return True
        if job.cancelled:
            job.set_item(s["label"], state="cancelled", msg="已取消")
            return True
        return False

    def do_llm(s: dict) -> str:
        key, sid, ep = s["label"], s["stage"], s.get("episode", "")
        if halt(s):
            return "aborted"
        with st_lock:
            if ep and ep in bad_eps:
                job.set_item(key, state="skipped", msg=f"{ep} 前面的环节没成，跳过")
                return "skipped"
        if _llm_done(pj, sid, ep):
            job.set_item(key, state="skipped", msg="已经做过了，跳过")
            return "skipped"

        log = lambda m, _k=key: job.log(_k, m)
        job.set_item(key, state="running")
        try:
            if V.scope_of(sid) == "segment":
                _, seg_failed, cancelled = R.run_segment_stage(
                    pj, sid, llm=llm_factory(), params=params, episode=ep,
                    log=log, cancel=lambda: job.cancelled,
                    seg_concurrency=seg_concurrency)
                if cancelled:
                    job.set_item(key, state="cancelled", msg="已取消")
                    return "cancelled"
                if seg_failed:
                    job.set_item(key, state="failed",
                                 msg=f"{len(seg_failed)} 段没成：{'、'.join(seg_failed[:5])}；"
                                     f"其余段照常往下走")
                    with st_lock:
                        failed.append(key)
                    return "failed"     # soft：不拉黑整集
            else:
                R.run_stage(pj, sid, llm=llm_factory(), params=params,
                            episode=ep, log=log, cancel=lambda: job.cancelled)
            if ep:
                R.write_prompt_files(pj, ep)
            job.set_item(key, state="ok")
            return "ok"
        except LLMCancelled:
            job.set_item(key, state="cancelled", msg="已取消")
            return "cancelled"
        except Exception as exc:                        # noqa: BLE001
            d = diagnose.build(exc, stage=f"stage:{sid}", target=ep or "全剧")
            diagnose.record(pj.root, d)
            job.set_item(key, state="failed", diag=d, msg=diagnose.one_line(d))
            with st_lock:
                failed.append(key)
            if d.get("kind") == BATCH_FATAL or d.get("scope") == "batch":
                job.abort_with(d)
                return "aborted"
            if ep and not s.get("soft"):
                # 这一集后面的都依赖它，跳过；别的集照常
                with st_lock:
                    bad_eps.add(ep)
            return "failed"

    def do_produce(s: dict) -> str:
        key = s["label"]
        if halt(s):
            return "aborted"
        tasks = pj.tasks().get(s["task_key"]) or []
        only = set(s.get("only") or [])
        if only:
            tasks = [t for t in tasks
                     if not t.get("episode") or t["episode"] in only]
        todo = [t for t in tasks
                if not os.path.isfile(pj.p(*t["output"].split("/")))]
        if not todo:
            job.set_item(key, state="skipped",
                         msg="没有要做的（都做过了，或前置还没产出任务）")
            return "skipped"

        chain = provider_factory(s["produce"])
        chain = [chain] if isinstance(chain, dict) else chain
        job.set_item(key, state="running",
                     msg=f"{len(todo)} 项待做 · 首选 {chain[0]['provider']}")

        from .produce import asset_layers, make_image_worker, make_video_worker
        mk = (lambda p: make_video_worker(pj, p)) if s["produce"] == "video" \
            else (lambda p, k=s["produce"]: make_image_worker(pj, p, k))
        mk_job = (lambda p, n: jobs.create(s["produce"], n, concurrency,
                                           project_root=pj.root,
                                           provider=p["provider"],
                                           model=p.get("model", ""))) if jobs else \
            (lambda p, n: Job(s["produce"], n, concurrency, project_root=pj.root,
                              provider=p["provider"], model=p.get("model", "")))

        # 资产图按参考图依赖分层：状态资产的参考是它的父资产，不分层的话
        # 父子并发，子任务读不到父资产的 png 直接失败。
        layers = asset_layers(todo) if s["task_key"] == "asset_tasks" else [todo]
        if len(layers) > 1:
            job.log(key, f"按参考图依赖分 {len(layers)} 层，上一层出完才跑下一层")
        left = 0
        for gi, grp in enumerate(layers, 1):
            if job.cancelled or job.aborted:
                break
            one = run_chain(grp, chain=chain, worker_of=mk, job_of=mk_job,
                            key_of=lambda t: t["key"],
                            done_of=lambda t: os.path.isfile(
                                pj.p(*t["output"].split("/"))),
                            max_retry=max_retry,
                            log=lambda m, _k=key: job.log(_k, m))
            left += one["left"]
            if one["left"] and gi < len(layers):
                job.log(key, f"第 {gi} 层还有 {one['left']} 项没成 —— "
                             f"下一层依赖它们的会因为缺参考图停下")
        if left:
            job.set_item(key, state="failed", msg=f"还有 {left} 项没做成")
            with st_lock:
                failed.append(key)
            return "failed"
        job.set_item(key, state="ok", msg=f"{len(todo)} 项完成")
        return "ok"

    def do_deliver(s: dict) -> str:
        key = s["label"]
        if halt(s):
            return "aborted"
        job.set_item(key, state="ok", msg="交付步骤（拼接由 V6.1 的 assemble 复用）")
        return "ok"

    def do_freeze(s: dict) -> str:
        """第 0 章：把执行模式和模型能力档位冻结进项目。

        必须在第九环节之前 —— 那一步要按能力档位决定允许用哪几类转场。
        不冻结的话它会照着「六类随便挑」写，而模型做不出来：
        转场糊掉或者变成一个长镜头，不报错。
        """
        key = s["label"]
        if halt(s):
            return "aborted"
        chain = provider_factory("video")
        chain = [chain] if isinstance(chain, dict) else chain
        model = (chain[0] or {}).get("model", "") if chain else ""
        R.freeze_capability(pj, params, model, log=lambda m: job.log(key, m))
        job.set_item(key, state="ok")
        return "ok"

    # ---- 跑：全剧级串行，逐集并行，出图出片在所有集之后 -------------------
    head = [s for s in steps
            if s["kind"] in ("freeze", "llm") and not s.get("episode")]
    for s in head:
        if s["kind"] == "freeze":
            if do_freeze(s) == "aborted":
                _finish(job, failed, bad_eps)
                return
            continue
        if do_llm(s) == "aborted":
            _finish(job, failed, bad_eps)
            return
    if job.aborted or job.cancelled:
        _finish(job, failed, bad_eps)
        return

    # 环节1 跑完集才切出来 —— 重新算一次计划，把逐集步骤补上
    rest = plan(pj, include_produce=include_produce,
                include_deliver=include_deliver, only_episodes=only_episodes)[len(head):]
    steps = head + rest
    job.total = len(steps)
    for s in rest:
        job.set_item(s["label"], state="pending")
    job.reorder_items([x["label"] for x in steps])

    by_ep: dict = {}
    for s in rest:
        if s["kind"] == "llm" and s.get("episode"):
            by_ep.setdefault(s["episode"], []).append(s)
    tail = [s for s in rest if not (s["kind"] == "llm" and s.get("episode"))]

    def run_episode(ep: str) -> None:
        for s in by_ep[ep]:
            if job.aborted or job.cancelled:
                return
            do_llm(s)

    order = list(by_ep)
    if order:
        workers = max(1, min(int(ep_concurrency or 1), len(order)))
        if workers > 1:
            job.log(steps[0]["label"], f"{len(order)} 集并发 {workers}")
            with ThreadPoolExecutor(max_workers=workers) as pool:
                list(pool.map(run_episode, order))
        else:
            for ep in order:
                run_episode(ep)

    if not (job.aborted or job.cancelled):
        R.build_tasks(pj, params)
    for s in tail:
        if job.aborted:
            break
        if s["kind"] == "produce":
            do_produce(s)
        elif s["kind"] == "llm":
            do_llm(s)
        else:
            do_deliver(s)
    return _finish(job, failed, bad_eps)


def start(job: Job, pj, **kw) -> None:
    threading.Thread(target=run, args=(job, pj), kwargs=kw, daemon=True).start()


def preview(pj, *, include_produce: bool = True, include_deliver: bool = True,
            only_episodes: Optional[list] = None) -> dict:
    """不跑，只说清「这一次会做什么、跳过什么」。点之前先看一眼，别花冤枉钱。

    逐段环节的待办按**段**算而不是按环节算：一集十几段时，
    「还要跑 1 个环节」和「还要跑 13 次调用」差着一个数量级。
    """
    steps = plan(pj, include_produce=include_produce,
                 include_deliver=include_deliver, only_episodes=only_episodes)
    todo, skip, calls = [], [], 0
    for s in steps:
        if s["kind"] == "freeze":
            (skip if R.capability_of(pj) else todo).append(s["label"])
        elif s["kind"] == "llm":
            sid, ep = s["stage"], s.get("episode", "")
            if _llm_done(pj, sid, ep):
                skip.append(s["label"])
                continue
            n = 1
            if V.scope_of(sid) == "segment":
                segs = {x["seg_id"] for x in R.segments_of(pj, ep)}
                n = len(segs - R.done_segments(pj, sid, ep)) or 1
            calls += n
            todo.append(f"{s['label']}（{n} 次调用）" if n > 1 else s["label"])
        elif s["kind"] == "produce":
            n = len(_produce_todo(pj, s["task_key"], s.get("only")))
            (todo if n else skip).append(
                f"{s['label']}（{n} 项）" if n else s["label"])
        else:
            todo.append(s["label"])
    return {"system": "v34", "episodes": _eps.ids(pj),
            "todo": todo, "skip": skip,
            "llm_calls": calls,
            "produce": {s["produce"]: len(_produce_todo(pj, s["task_key"], s.get("only")))
                        for s in steps if s["kind"] == "produce"}}


def _produce_todo(pj, task_key: str, only: Optional[list]) -> list:
    tasks = pj.tasks().get(task_key) or []
    if only:
        keep = set(only)
        tasks = [t for t in tasks if not t.get("episode") or t["episode"] in keep]
    return [t for t in tasks
            if not os.path.isfile(pj.p(*t["output"].split("/")))]


def _finish(job: Job, failed: list, bad_eps: Optional[set] = None) -> dict:
    """收尾：把状态和「哪些集卡住了」落下来，面板照着改就行。"""
    import time
    job.finished_at = time.time()
    if job.aborted:
        job.status = "aborted"
    elif job.cancelled:
        job.status = "cancelled"
    else:
        job.status = "error" if failed else "done"
    return {"failed": list(failed), "stuck_episodes": sorted(bad_eps or ()),
            "status": job.status}
