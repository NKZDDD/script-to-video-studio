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

from . import (diagnose, episodes as _eps, gates_v34 as G, probe,
               run_v34 as R, system_v34 as V)
from .apiutil import BATCH_FATAL
from .llm import LLMCancelled
from .executor import LLM_GATE, Job, run_chain


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
    # 全剧级环节**从环节表推导**，不写死。
    # 写死的代价踩过：把 n3..n6 改成全剧级之后，它们既不在写死的头部里、
    # 也不在逐集列表里 —— 整段直接从计划里消失，跑起来是「第7环节失败」，
    # 报错指向下游，看不出上游根本没跑。
    for s in V.STAGES:
        if s["kind"] == "llm" and V.scope_of(s["id"]) == "series":
            steps.append({"kind": "llm", "stage": s["id"], "episode": "",
                          "label": _label(s["id"])})

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
        for sid in ("d1", "d2"):
            steps.append({"kind": "deliver", "stage": sid, "episode": "",
                          "only": list(eps) if eps else None,
                          "label": _label(sid) + _scope_tag(eps, pj)})
    return steps


def _host(base_url: str) -> str:
    """从 base_url 取出域名，当作「哪条线路」。不含 key，可以安全落盘。"""
    s = str(base_url or "").split("//", 1)[-1]
    return s.split("/", 1)[0] or ""


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


def _mark_stopped(job: Job, rest: list) -> None:
    """把这一轮不会再跑的步骤标出来，别留在 pending。

    只动还是 pending 的那些 —— 已经 ok / failed / skipped 的是事实，
    盖掉就等于篡改结果。
    """
    stopped = "熔断停止" if job.aborted else "已取消"
    for s in rest:
        cur = (job.items.get(s["label"]) or {}).get("state")
        if cur in (None, "pending"):
            job.set_item(s["label"], state="cancelled", msg=stopped)


def run(job: Job, pj, *, llm_factory: Callable, provider_factory: Callable,
        params: dict, jobs=None, concurrency: int = 3, max_retry: int = 2,
        include_produce: bool = True, include_deliver: bool = True,
        only_episodes: Optional[list] = None,
        ep_concurrency: int = 4, seg_concurrency: int = 4,
        llm_concurrency: int = 0) -> None:
    # **必须配。** 这一行以前只有 pipeline.py（通用级）有，这边一直没接 ——
    # 于是电影级的分析并发永远是 LlmGate 的构造默认值 4，
    # 页面上「分析·总上限」填 200 也没用，状态栏一直显示「分析 3/4」。
    # 这种漏接不会报错，只会让一个旋钮**看起来在那儿、其实没接线**。
    LLM_GATE.configure(llm_concurrency or max(1, ep_concurrency, seg_concurrency))
    LLM_GATE.reset_peak()
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
        # 失败记录里要认得出是**哪个模型、哪条线路**答成这样的。
        # 出图出片那边一直带着（executor 传 job.provider/job.model），
        # 分析引擎这边一直没带 —— 于是 failures.json 里 provider/model 是空的，
        # 而分析环节恰恰是最常出问题的一层。
        llm = llm_factory()
        who = {"provider": _host(getattr(llm, "base_url", "")),
               "model": getattr(llm, "model", "")}
        try:
            if V.scope_of(sid) == "segment":
                _, seg_failed, cancelled = R.run_segment_stage(
                    pj, sid, llm=llm, params=params, episode=ep,
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
                R.run_stage(pj, sid, llm=llm, params=params,
                            episode=ep, log=log, cancel=lambda: job.cancelled)
            # **不能写成 `if ep:`。** 资产提示词是 n4b 出的，而 n4b 是全剧级，
            # 这里的 ep 是空字符串 —— 加了这个条件，那 80 多份提示词就永远
            # 落不成 txt，出图那一层按路径读文件，全部报「文件不存在」。
            # 实跑撞到：n4b 八批都跑完了、产物里有 prompt，磁盘上一个文件都没有。
            # 传空 episode 是安全的：逐集那几个循环读不到东西，自然什么都不写。
            R.write_prompt_files(pj, ep)
            job.set_item(key, state="ok")
            return "ok"
        except LLMCancelled:
            job.set_item(key, state="cancelled", msg="已取消")
            return "cancelled"
        except Exception as exc:                        # noqa: BLE001
            d = diagnose.build(exc, stage=f"stage:{sid}", target=ep or "全剧", **who)
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
                if not probe.have_output(pj.p(*t["output"].split("/")))]
        if not todo:
            job.set_item(key, state="skipped",
                         msg="没有要做的（都做过了，或前置还没产出任务）")
            return "skipped"

        chain = provider_factory(s["produce"])
        chain = [chain] if isinstance(chain, dict) else chain
        job.set_item(key, state="running",
                     msg=f"{len(todo)} 项待做 · 首选 {chain[0]['provider']}")

        from .produce import asset_layers, make_image_worker, make_video_worker
        # llm_factory 要传下去：被审核拒绝时靠它改写提示词重发。
        # 漏传不会报错，只是那个功能悄悄没了。
        mk = (lambda p: make_video_worker(pj, p, llm_factory)) \
            if s["produce"] == "video" \
            else (lambda p, k=s["produce"]: make_image_worker(pj, p, k, llm_factory))
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
        if s["task_key"] == "asset_tasks":
            # 出完就登记指纹。登记是为了后面能查出「文件被人换过」——
            # 换过之后下游还照着旧的引用跑，出来的东西看着正常但用的是另一张图。
            from . import registry_v34 as REG
            n = 0
            for t in tasks:
                if probe.have_output(pj.p(*t["output"].split("/"))):
                    try:
                        REG.promote(pj, t["key"], t["output"])
                        n += 1
                    except Exception as exc:            # noqa: BLE001
                        job.log(key, f"⚠️ {t['key']} 登记失败：{exc}")
            if n:
                job.log(key, f"已登记 {n} 张资产图的版本和指纹")
        if left:
            job.set_item(key, state="failed", msg=f"还有 {left} 项没做成")
            with st_lock:
                failed.append(key)
            return "failed"
        job.set_item(key, state="ok", msg=f"{len(todo)} 项完成")
        return "ok"

    def do_deliver(s: dict) -> str:
        key, sid = s["label"], s["stage"]
        if halt(s):
            return "aborted"
        log = lambda m, _k=key: job.log(_k, m)
        job.set_item(key, state="running")
        try:
            if sid == "d1":
                only = s.get("only") or []
                n = 0
                for ep in (only or _eps.ids(pj) or [""]):
                    n += len(R.build_review_checklist(pj, ep).get("rows", []))
                job.set_item(key, state="ok", msg=f"{n} 段待人工复核")
            else:
                r = R.assemble(pj, params, log,
                               (s.get("only") or [""])[0] if len(s.get("only") or []) == 1 else "")
                got = r.get("masters") or [r]
                done = [x for x in got if x.get("master")]
                job.set_item(key, state="ok" if done else "failed",
                             msg=(f"拼好 {len(done)} 集" if done
                                  else r.get("msg") or "没有可拼接的分段视频"))
                if not done:
                    with st_lock:
                        failed.append(key)
                    return "failed"
            return "ok"
        except Exception as exc:                        # noqa: BLE001
            d = diagnose.build(exc, stage=f"stage:{sid}", target="交付")
            diagnose.record(pj.root, d)
            job.set_item(key, state="failed", diag=d, msg=diagnose.one_line(d))
            with st_lock:
                failed.append(key)
            return "failed"

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
        for i, s in enumerate(by_ep[ep]):
            if job.aborted or job.cancelled:
                # 剩下的一个个标出来。直接 return 会让它们留在 pending，
                # 而 pending 的字面意思是「等会儿会跑」—— 整个 job 都停了
                # 还这么显示，人会以为程序卡住了在干等。
                _mark_stopped(job, by_ep[ep][i:])
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
        # 出图出片之前过三道硬闸门。拦下来的都是「做出来看着正常但是错的」，
        # 在花钱之前停，比出完几百张再人工发现便宜得多。
        blocked = G.check_all(pj, only_episodes)
        if blocked:
            job.log(steps[0]["label"], G.blocked_message(blocked))
            # **默认记下来就继续跑，不拦。**
            # 这几道查的是连续性细节，没有一条会让产物做不出来 ——
            # 而拦住的代价是整条线停摆：一个道具的对账数字写错，
            # 连角色定妆图都出不来，那是后面所有环节的地基。
            # 要硬拦的把项目参数里的 gates_block 设成 true。
            for gate, note in G.gate_notes(blocked):
                diagnose.record(pj.root, diagnose.warn(
                    "GATE_FINDING", note, stage="gate", target=gate,
                    extra_fix=["这一条不挡生产 —— 东西照常做出来了，"
                               "但那几处对不上的地方值得回头看一眼",
                               "要让它挡住生产：项目参数里把 gates_block 设成 true"]))
            if G.should_block(params):
                msg = G.blocked_message(blocked)
                for s in tail:
                    if s["kind"] == "produce":
                        job.set_item(s["label"], state="failed",
                                     msg=msg.splitlines()[0])
                        with st_lock:
                            failed.append(s["label"])
                tail = [s for s in tail if s["kind"] != "produce"]
    for i, s in enumerate(tail):
        if job.aborted or job.cancelled:
            _mark_stopped(job, tail[i:])
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
            if not probe.have_output(pj.p(*t["output"].split("/")))]


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
