# -*- coding: utf-8 -*-
"""一键跑到底：把 12 个环节串成一条流水线，中断后原地续跑。

想解决的就一件事：填完配置、选完模型、丢进剧本，之后不用再手动点每个环节。
40 集手动点是几百次点击，不现实。

**「继续」不是另一个功能，就是再点一次同一个按钮。** 每一步都以磁盘为准判断
做过没有（产物文件在不在），做过就跳过。所以：
  · 跑到第 12 集断了 → 再点一次，前 11 集一步都不重做
  · 改完某段提示词 → 再点一次，只补那一段
  · 换台电脑、重启服务 → 一样

出错策略是分层的，因为「继续跑」和「赶紧停」在不同情况下各是对的：
  · 余额不足 / 密钥失效  → 立刻停整条流水线。继续只是烧钱撞同一堵墙。
  · 某一集的某个环节失败 → 跳过这一集剩下的环节，**其它集照常跑**。
    集与集之间是独立的，一集的段落划分失败没理由拖累另外 39 集。
  · 单个出图/出片任务失败 → run_batch 本来就隔离了，继续跑别的。
  最后汇总：哪些集卡在哪一步，都记在 failures.json 里，面板上照着改就行。
"""

from __future__ import annotations

import os
import threading
from typing import Callable, Optional

from . import diagnose, episodes as _eps, stages as S
from .apiutil import BATCH_FATAL
from .executor import Job, run_batch

# 逐集要跑的 LLM 环节，按顺序
PER_EP = ["s2", "s3", "s4", "s5", "s6", "s7", "s8"]
# 出图出片三步：(步骤名, tasks.json 里的键, worker 类型)
PRODUCE = [("s5b", "asset_tasks", "asset"),
           ("s9", "storyboard_tasks", "storyboard"),
           ("s10", "video_tasks", "video")]


def plan(pj, *, include_produce: bool = True, include_deliver: bool = True) -> list:
    """算出要做哪些步骤（含已完成的，执行时再跳过）。

    顺序是有讲究的：
      环节1 先跑，它才知道有几集；
      每集的 2-8 连着跑（同一集内部有依赖）；
      资产图放到所有集的环节5 之后 —— 资产库全剧共享，一次出齐才不会重复出图；
      故事板、视频等资产图；
      拼接放最后，按集各出一个成片。
    """
    steps = [{"kind": "llm", "stage": "s1", "episode": "",
              "label": "环节1 整剧全局解析（含切集）"}]
    eps = _eps.ids(pj)
    for ep in eps:
        for sid in PER_EP:
            st = next(s for s in S.STAGES if s["id"] == sid)
            steps.append({"kind": "llm", "stage": sid, "episode": ep,
                          "label": f"{ep} 环节{st['no']} {st['name']}"})
    if include_produce:
        for sid, key, kind in PRODUCE:
            st = next(s for s in S.STAGES if s["id"] == sid)
            steps.append({"kind": "produce", "stage": sid, "task_key": key,
                          "produce": kind, "episode": "",
                          "label": f"环节{st['no']} {st['name']}"})
    if include_deliver:
        steps.append({"kind": "review", "stage": "s11", "episode": "",
                      "label": "环节11 生成人工复核清单"})
        steps.append({"kind": "assemble", "stage": "s12", "episode": "",
                      "label": "环节12 逐集拼接成片"})
    return steps


def _llm_done(pj, stage: str, episode: str) -> bool:
    """这一步做过没有 —— 以磁盘产物为准，不看任何运行记录。"""
    if stage == "s8":                       # 按段算：所有段都编完才算完
        segs = (pj.stage_data("s2_segments", episode) or {}).get("segments", [])
        return bool(segs) and len(S.s8_done_segments(pj, episode)) >= len(segs)
    out = next(s["out"] for s in S.STAGES if s["id"] == stage)
    return pj.stage_data(out, episode) is not None


def _produce_todo(pj, task_key: str) -> list:
    items = pj.tasks().get(task_key, [])
    return [t for t in items if not os.path.isfile(pj.p(*t["output"].split("/")))]


def run(job: Job, pj, *, llm_factory: Callable, provider_factory: Callable,
        params: dict, jobs=None, concurrency: int = 3, max_retry: int = 2,
        include_produce: bool = True, include_deliver: bool = True) -> dict:
    """跑完整条流水线。job 用来向前端汇报进度，每一步是一个 item。

    llm_factory() → LLM 实例（延迟构造，密钥错就在第一步暴露）
    provider_factory(kind) → (provider_cfg, )  kind ∈ asset/storyboard/video
    jobs: JobManager，给出图出片开子 job，好让「生产」页也能看到细节
    """
    steps = plan(pj, include_produce=include_produce, include_deliver=include_deliver)
    job.total = len(steps)
    for s in steps:
        job.set_item(s["label"], state="pending")

    bad_eps: set = set()          # 这些集已经卡住了，后续环节别再试
    failed: list = []
    llm = None
    i = 0

    while i < len(steps):
        s = steps[i]
        key = s["label"]
        i += 1
        if job.cancelled:
            job.set_item(key, state="cancelled", msg="已取消")
            continue
        ep = s.get("episode", "")
        if ep and ep in bad_eps:
            job.set_item(key, state="skipped", msg=f"{ep} 前面的环节没成，这一步跳过")
            continue

        def log(m, k=key):
            job.log(k, m)

        try:
            # ---- LLM 环节 --------------------------------------------------
            if s["kind"] == "llm":
                if _llm_done(pj, s["stage"], ep):
                    job.set_item(key, state="skipped", msg="已经做过了，跳过")
                    continue
                job.set_item(key, state="running")
                if llm is None:
                    llm = llm_factory()
                    job.model = llm.model
                S.run_llm_stage(pj, s["stage"], llm, params, log=log, episode=ep)
                job.set_item(key, state="ok")
                diagnose.clear(pj.root, f"stage:{s['stage']}", ep)
                # 环节1 跑完才知道有几集，把后面的步骤补进计划
                if s["stage"] == "s1":
                    rest = plan(pj, include_produce=include_produce,
                                include_deliver=include_deliver)[1:]
                    steps = steps[:i] + rest
                    job.total = len(steps)
                    for r in rest:
                        job.set_item(r["label"], state="pending")
                    n = len(_eps.ids(pj))
                    log(f"识别出 {n} 集，接下来逐集跑，共 {len(steps)} 步")

            # ---- 出图 / 出片 -----------------------------------------------
            elif s["kind"] == "produce":
                todo = _produce_todo(pj, s["task_key"])
                if not todo:
                    job.set_item(key, state="skipped", msg="产物都齐了，跳过")
                    continue
                pcfg = provider_factory(s["produce"])
                job.set_item(key, state="running",
                             msg=f"{len(todo)} 项待做 · {pcfg['provider']} {pcfg.get('model','')}")
                worker = (S.make_video_worker(pj, pcfg) if s["produce"] == "video"
                          else S.make_image_worker(pj, pcfg, s["produce"]))
                sub = (jobs.create(s["produce"], len(todo), concurrency,
                                   project_root=pj.root,
                                   project_name=os.path.basename(pj.root),
                                   provider=pcfg["provider"], model=pcfg.get("model", ""))
                       if jobs else Job(s["produce"], len(todo), concurrency,
                                        project_root=pj.root, provider=pcfg["provider"],
                                        model=pcfg.get("model", "")))
                log(f"交给「生产」面板的任务 {sub.id}，{len(todo)} 项，并发 {concurrency}")
                run_batch(sub, todo, worker, key_of=lambda t: t["key"],
                          max_retry=max_retry, provider=pcfg["provider"])
                c = sub.counts()
                nfail = c.get("failed", 0)
                if sub.aborted:                     # 余额/密钥 → 整条流水线停
                    job.set_item(key, state="failed",
                                 msg=diagnose.one_line(sub.abort_diag or {}) or "已停止")
                    job.abort_with(sub.abort_diag or {})
                    failed.append(key)
                    break
                if nfail:
                    job.set_item(key, state="failed",
                                 msg=f"{nfail} 项没做成，其余已完成；照面板上的说明处理后再点一次")
                    failed.append(key)
                    # 出图没齐就别急着出片：故事板缺了，视频拿什么当参考
                    log(f"{nfail} 项失败，后面依赖它的步骤会只做能做的部分")
                else:
                    job.set_item(key, state="ok", msg=f"{c.get('ok', 0)} 项完成")

            # ---- 复核清单 --------------------------------------------------
            elif s["kind"] == "review":
                job.set_item(key, state="running")
                r = S.build_review_checklist(pj)
                job.set_item(key, state="ok", msg=f"{len(r.get('rows', []))} 段待人工验收")

            # ---- 拼接 ------------------------------------------------------
            else:
                job.set_item(key, state="running")
                r = S.assemble(pj, params, log=log)
                n = len(r.get("masters", [])) or (1 if r.get("master") else 0)
                job.set_item(key, state="ok", msg=f"出了 {n} 个成片")

        except Exception as exc:                     # noqa: BLE001
            kind = getattr(exc, "kind", "")
            diag = diagnose.build(exc, stage=f"stage:{s['stage']}", target=ep or s["stage"],
                                  model=getattr(llm, "model", ""))
            diagnose.record(pj.root, diag)
            job.set_item(key, state="failed", msg=diagnose.one_line(diag), diag=diag)
            job.log(key, diagnose.one_line(diag))
            failed.append(key)
            if kind == BATCH_FATAL or diag.get("scope") == "batch":
                # 余额、密钥这类：后面几百步撞的是同一堵墙，立刻停
                job.abort_diag = diag
                job.abort_with(diag)
                break
            if ep:
                bad_eps.add(ep)
                job.log(key, f"{ep} 卡在这一步，这一集后面的环节跳过；其它集继续")

    job.finished_at = __import__("time").time()
    if job.aborted:
        job.status = "aborted"
    elif job.cancelled:
        job.status = "cancelled"
    else:
        job.status = "error" if failed else "done"
    return {"steps": len(steps), "failed": failed,
            "stuck_episodes": sorted(bad_eps), "status": job.status}


def start(job: Job, pj, **kw) -> None:
    threading.Thread(target=run, args=(job, pj), kwargs=kw, daemon=True).start()


def preview(pj, *, include_produce: bool = True, include_deliver: bool = True) -> dict:
    """不跑，只说清「这一次会做什么、跳过什么」。点之前先看一眼，别花冤枉钱。"""
    steps = plan(pj, include_produce=include_produce, include_deliver=include_deliver)
    todo, skip = [], []
    for s in steps:
        if s["kind"] == "llm":
            (skip if _llm_done(pj, s["stage"], s.get("episode", "")) else todo).append(s["label"])
        elif s["kind"] == "produce":
            n = len(_produce_todo(pj, s["task_key"]))
            (todo if n else skip).append(f"{s['label']}（{n} 项）" if n else s["label"])
        else:
            todo.append(s["label"])
    eps = _eps.ids(pj)
    calls = sum(1 for s in steps if s["kind"] == "llm"
                and not _llm_done(pj, s["stage"], s.get("episode", "")))
    produce = {s["produce"]: len(_produce_todo(pj, s["task_key"]))
               for s in steps if s["kind"] == "produce"}
    return {"episodes": len(eps), "total_steps": len(steps),
            "todo": todo, "skip": skip,
            "llm_calls_at_least": calls, "produce": produce,
            "note": "环节8 是逐段调用，实际 LLM 次数会比上面这个数多"
                    "（每集有几段就多几次）。出图出片的次数就是上面 produce 里的数。"}
