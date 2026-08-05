# -*- coding: utf-8 -*-
"""一键跑到底：把 12 个环节串成一条流水线，中断后原地续跑。

想解决的就一件事：填完配置、选完模型、丢进剧本，之后不用再手动点每个环节。
40 集手动点是几百次点击，不现实。

**「继续」不是另一个功能，就是再点一次同一个按钮。** 每一步都以磁盘为准判断
做过没有（产物文件在不在），做过就跳过。所以：
  · 跑到第 12 集断了 → 再点一次，前 11 集一步都不重做
  · 改完某段提示词 → 再点一次，只补那一段
  · 换台电脑、重启服务 → 一样

并发：集与集之间除了环节4 没有依赖，所以逐集环节按集并行跑；环节8 内部段与段
也没有依赖，段之间再并行一层。环节4 例外 —— 它要沿用前面几集已建好的资产编号，
必须按集号排队，否则两集会各自给同一个新对象编号。**并发只改调度，产物和串行
跑逐字一致。**

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
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Optional

from . import diagnose, episodes as _eps, stages as S
from .apiutil import BATCH_FATAL
from .llm import LLMCancelled
from .executor import LLM_GATE, Job, run_chain

# 逐集要跑的 LLM 环节，按顺序
PER_EP = ["s2", "s3", "s4", "s5", "s6", "s7", "s8"]
# 出图出片三步：(步骤名, tasks.json 里的键, worker 类型)
PRODUCE = [("s5b", "asset_tasks", "asset"),
           ("s9", "storyboard_tasks", "storyboard"),
           ("s10", "video_tasks", "video")]


def plan(pj, *, include_produce: bool = True, include_deliver: bool = True,
         only_episodes: Optional[list] = None,
         produce_episodes: Optional[list] = None) -> list:
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
    # 环节1 永远吃整部剧本 —— 人物长相、视觉基调、伏笔、集边界都得看全篇才准。
    # only_episodes 只限制「往下逐集加工哪几集」，不影响全局解析的范围。
    eps = _eps.ids(pj)
    if only_episodes:
        want = set(only_episodes)
        have = eps
        eps = [e for e in eps if e in want]
        # 填的集号一个都没对上：以前会安静地一步逐集环节都不排，看着像跑完了。
        # 环节1 之前 have 是空的（还不知道有几集），那时候不校验。
        if have and not eps:
            raise ValueError(
                f"「分析这几集」填的 {'、'.join(sorted(want))} 在这个项目里都不存在 —— "
                f"没有 {sorted(want)[0]} 这一集。当前切出了 {len(have)} 集："
                f"{have[0]} … {have[-1]}。把那个框清空 = 全部集。")
    for ep in eps:
        for sid in PER_EP:
            st = next(s for s in S.STAGES if s["id"] == sid)
            steps.append({"kind": "llm", "stage": sid, "episode": ep,
                          "label": f"{ep} 环节{st['no']} {st['name']}",
                          # 环节5 不在关键路径上：查依赖表，环节6/7/8 都不要它的产物
                          # （s8 的依赖是 s1,s2,s3,s4,s6,s7），它只喂出图。
                          # 所以放旁路跑，别让它挡住后面三步；失败也只影响出图。
                          **({"side": True, "soft": True} if sid == "s5" else {}),
                          # 环节7、8 是一段一次调用的，一段失败只该影响那一段。
                          # skill：「禁止一个错误导致整集全部重做」。所以标 soft ——
                          # 不毒整集，下游自己按段判断哪些能做（没分镜的段不编提示词、
                          # 不出故事板和视频，其余段一路到成片）。
                          **({"soft": True} if sid in ("s7", "s8") else {})})
    # eps 为空说明环节1 还没跑、集还没切出来，这时候别显示「（只 ）」
    # 生产范围默认跟分析范围一致；给了 produce_episodes 就只出那几集的东西。
    # 这两件事本来就该分开：资产**表**要全剧（否则资产库中途返工），
    # 但资产**图**、故事板、视频只出这次要的那几集就够了。
    prod = [e for e in (produce_episodes or eps) if not eps or e in eps] or eps
    ptag = f"（只 {'、'.join(prod)}）" if prod and prod != _eps.ids(pj) else ""
    if include_produce:
        for sid, key, kind in PRODUCE:
            st = next(s for s in S.STAGES if s["id"] == sid)
            steps.append({"kind": "produce", "stage": sid, "task_key": key,
                          "produce": kind, "episode": "",
                          "only": list(prod) if prod else None,
                          "label": f"环节{st['no']} {st['name']}{ptag}"})
    if include_deliver:
        steps.append({"kind": "review", "stage": "s11", "episode": "",
                      "only": list(prod) if prod else None,
                      "label": f"环节11 生成人工复核清单{ptag}"})
        steps.append({"kind": "assemble", "stage": "s12", "episode": "",
                      "only": list(prod) if prod else None,
                      "label": f"环节12 拼接成片{ptag}"})
    return steps


def _llm_done(pj, stage: str, episode: str) -> bool:
    """这一步做过没有 —— 以磁盘产物为准，不看任何运行记录。

    环节7/8 按段跑，所以要按段算：跑了 8 段还剩 4 段时，产物文件是存在的，
    但这一步没做完。只看文件在不在会把剩下 4 段永远漏掉。
    """
    if stage in ("s7", "s8"):
        segs = (pj.stage_data("s2_segments", episode) or {}).get("segments", [])
        if not segs:
            return False
        done = (S.s8_done_segments(pj, episode) if stage == "s8"
                else S.s7_done_segments(pj, episode))
        return all(s["id"] in done for s in segs)
    out = next(s["out"] for s in S.STAGES if s["id"] == stage)
    return pj.stage_data(out, episode) is not None


def _produce_todo(pj, task_key: str, only: Optional[list] = None) -> list:
    items = pj.tasks().get(task_key, [])
    if only:
        want = set(only)
        if task_key == "asset_tasks":
            # 资产表是全剧的，但只出这几集用得到的图（含状态资产的父资产）。
            # 其余资产的编号和外观已经在表里定死，跑到那几集时再补图，仍是同一张脸。
            used = S.assets_used_by(pj, only)
            items = [t for t in items if t["key"] in used] if used else items
        else:
            items = [t for t in items if not t.get("episode") or t["episode"] in want]
    return [t for t in items if not os.path.isfile(pj.p(*t["output"].split("/")))]


def run(job: Job, pj, *, llm_factory: Callable, provider_factory: Callable,
        params: dict, jobs=None, concurrency: int = 3, max_retry: int = 2,
        include_produce: bool = True, include_deliver: bool = True,
        only_episodes: Optional[list] = None,
        produce_episodes: Optional[list] = None,
        ep_concurrency: int = 1, seg_concurrency: int = 1,
        llm_concurrency: int = 0) -> dict:
    """跑完整条流水线。job 用来向前端汇报进度，每一步是一个 item。

    llm_factory() → LLM 实例（延迟构造，密钥错就在第一步暴露）
    provider_factory(kind) → 按优先级排好的服务商列表 [{provider, model, ...}, ...]
        kind ∈ asset/storyboard/video。首选挂了会自动换下一家补剩下的。
    jobs: JobManager，给出图出片开子 job，好让「生产」页也能看到细节

    并发（只影响调度，产物和串行跑逐字一致）：
      ep_concurrency   同时处理几集。集与集之间除了环节4 没有依赖。
      seg_concurrency  环节8 里同时编几段。段与段之间完全独立。
      llm_concurrency  LLM 在途请求总上限（真正的天花板，防止把网关打限流）。
    """
    S.reset_claims()
    LLM_GATE.configure(llm_concurrency or max(1, ep_concurrency, seg_concurrency))
    LLM_GATE.reset_peak()
    steps = plan(pj, include_produce=include_produce, include_deliver=include_deliver,
                 only_episodes=only_episodes, produce_episodes=produce_episodes)
    job.total = len(steps)
    for s in steps:
        job.set_item(s["label"], state="pending")

    st_lock = threading.Lock()
    bad_eps: set = set()          # 这些集已经卡住了，后续环节别再试
    chains_eps: list = []         # 这一趟涉及哪几集（只跑一集时报错文案要不一样）
    failed: list = []
    box: dict = {"llm": None}     # LLM 实例只造一次，多集共用

    def get_llm():
        with st_lock:
            if box["llm"] is None:
                box["llm"] = llm_factory()
                job.model = box["llm"].model
            return box["llm"]

    def stop_now() -> bool:
        return bool(job.cancelled or job.aborted)

    def halt(s: dict) -> bool:
        """要停了吗。

        两种停法在面板上要看得出区别：
          · 整批熔断（余额/密钥）→ **原地留 pending**，因为它压根没发出去，
            标成取消会让人以为是自己点的。abort_with 已经说明了原因。
          · 用户点取消 → 这一步标「已取消」，是人的决定

        先判 aborted：abort_with 会连带把 cancelled 也置真（好让在途的 worker
        赶紧收手），所以反过来判就会把熔断误显示成「已取消」。
        """
        if job.aborted:
            return True
        if job.cancelled:
            job.set_item(s["label"], state="cancelled", msg="已取消")
            return True
        return False

    def do_step(s: dict) -> str:
        """跑一步。返回 ok/skipped/failed/cancelled/aborted。异常都在里面消化掉。"""
        key = s["label"]
        ep = s.get("episode", "")
        if halt(s):
            return "cancelled" if job.cancelled else "aborted"
        with st_lock:
            blocked = ep and ep in bad_eps
        if blocked:
            job.set_item(key, state="skipped", msg=f"{ep} 前面的环节没成，这一步跳过")
            return "skipped"

        def log(m, k=key):
            job.log(k, m)

        try:
            # ---- LLM 环节 --------------------------------------------------
            if s["kind"] == "llm":
                if _llm_done(pj, s["stage"], ep):
                    job.set_item(key, state="skipped", msg="已经做过了，跳过")
                    return "skipped"
                job.set_item(key, state="running")
                S.run_llm_stage(pj, s["stage"], get_llm(), params, log=log, episode=ep,
                                cancel=stop_now, seg_concurrency=seg_concurrency)
                job.set_item(key, state="ok")
                diagnose.clear(pj.root, f"stage:{s['stage']}", ep)

            # ---- 出图 / 出片 -----------------------------------------------
            elif s["kind"] == "produce":
                todo = _produce_todo(pj, s["task_key"], s.get("only"))
                if not todo:
                    # 「一个都不用做」有两种完全不同的原因，混成一句「都齐了」会
                    # 让人以为出完了 —— 实际可能是前置环节没产出、压根没清单。
                    total = len(pj.tasks().get(s["task_key"]) or [])
                    job.set_item(key, state="skipped",
                                 msg=("产物都齐了，跳过" if total else
                                      "前置环节还没产出任务清单，这一步没东西可做"
                                      "（故事板要环节8 的提示词，资产图要环节5 的提示词）"))
                    return "skipped"
                chain = provider_factory(s["produce"])      # 按优先级排好的服务商列表
                if isinstance(chain, dict):                 # 兼容只给一家的写法
                    chain = [chain]
                first = chain[0]
                job.set_item(key, state="running",
                             msg=f"{len(todo)} 项待做 · 首选 {first['provider']} "
                                 f"{first.get('model','')}"
                                 + (f"（备选 {len(chain)-1} 家）" if len(chain) > 1 else ""))

                def mk_worker(pcfg, kind=s["produce"]):
                    return (S.make_video_worker(pj, pcfg) if kind == "video"
                            else S.make_image_worker(pj, pcfg, kind))

                def mk_job(pcfg, n, kind=s["produce"]):
                    return (jobs.create(kind, n, concurrency, project_root=pj.root,
                                        project_name=os.path.basename(pj.root),
                                        provider=pcfg["provider"], model=pcfg.get("model", ""))
                            if jobs else Job(kind, n, concurrency, project_root=pj.root,
                                             provider=pcfg["provider"],
                                             model=pcfg.get("model", "")))

                # 资产图按参考图依赖分层：状态资产的参考图是它的父资产，
                # 不分层的话父子会并发，子任务读不到父资产的 png 直接失败。
                layers = (S.asset_layers(todo) if s["task_key"] == "asset_tasks"
                          else [todo])
                if len(layers) > 1:
                    log(f"按参考图依赖分 {len(layers)} 层："
                        + "、".join(f"第{i}层 {len(g)} 项" for i, g in enumerate(layers, 1))
                        + "。上一层出完才跑下一层，否则状态资产读不到父资产的图")
                r = {"attempts": [], "left": 0, "switched": 0}
                for gi, grp in enumerate(layers, 1):
                    if stop_now():
                        break
                    if len(layers) > 1:
                        log(f"—— 第 {gi}/{len(layers)} 层，{len(grp)} 项")
                    one = run_chain(grp, chain=chain, worker_of=mk_worker, job_of=mk_job,
                                    key_of=lambda t: t["key"],
                                    done_of=lambda t: os.path.isfile(
                                        pj.p(*t["output"].split("/"))),
                                    max_retry=max_retry, log=log)
                    r["attempts"] += one["attempts"]
                    r["left"] += one["left"]
                    r["switched"] = max(r["switched"], one["switched"])
                    if one["left"] and gi < len(layers):
                        log(f"第 {gi} 层还有 {one['left']} 项没成 —— 下一层依赖它们的"
                            f"那些会因为缺参考图失败，先把这一层修好再点一次")
                used = " → ".join(f"{a['provider']}/{a['model']}" for a in r["attempts"])
                if r["left"] == 0:
                    job.set_item(key, state="ok",
                                 msg=f"{len(todo)} 项完成"
                                     + (f"（换了 {r['switched']} 次家：{used}）"
                                        if r["switched"] else ""))
                    return "ok"
                # 还有没做成的：把最后一次的诊断挂上去
                last = r["attempts"][-1] if r["attempts"] else {}
                diag = last.get("diag")
                if not diag:
                    for f in diagnose.load(pj.root):
                        if f.get("stage") == s["produce"]:
                            diag = f
                            break
                job.set_item(key, state="failed", diag=diag,
                             msg=f"还有 {r['left']} 项没做成"
                                 + (f"，已试过 {used}" if len(r["attempts"]) > 1 else "")
                                 + "；照面板上的说明处理后再点一次「开始」")
                with st_lock:
                    failed.append(key)
                # 所有家都因为账户级问题挂了 → 整条流水线没有继续的意义
                if diag and diag.get("scope") == "batch" and r["switched"] + 1 >= len(chain):
                    job.abort_diag = diag
                    job.abort_with(diag)
                    return "aborted"
                log(f"{r['left']} 项没做成，后面依赖它的步骤只做能做的部分")
                return "failed"

            # ---- 复核清单 --------------------------------------------------
            elif s["kind"] == "review":
                job.set_item(key, state="running")
                only = s.get("only") or []
                r = S.build_review_checklist(pj, only[0] if len(only) == 1 else "")
                job.set_item(key, state="ok", msg=f"{len(r.get('rows', []))} 段待人工验收")

            # ---- 拼接 ------------------------------------------------------
            else:
                job.set_item(key, state="running")
                only = s.get("only") or []
                r = S.assemble(pj, params, log=log,
                               episode=only[0] if len(only) == 1 else "")
                n = len(r.get("masters", [])) or (1 if r.get("master") else 0)
                job.set_item(key, state="ok", msg=f"出了 {n} 个成片")

        except LLMCancelled as exc:                  # 用户点了取消：不算失败
            job.set_item(key, state="cancelled", msg=str(exc))
            job.log(key, str(exc))
            return "cancelled"
        except Exception as exc:                     # noqa: BLE001
            kind = getattr(exc, "kind", "")
            diag = diagnose.build(exc, stage=f"stage:{s['stage']}", target=ep or s["stage"],
                                  model=getattr(box["llm"], "model", ""))
            diagnose.record(pj.root, diag)
            job.set_item(key, state="failed", msg=diagnose.one_line(diag), diag=diag)
            job.log(key, diagnose.one_line(diag))
            with st_lock:
                failed.append(key)
            if kind == BATCH_FATAL or diag.get("scope") == "batch":
                # 余额、密钥这类：后面几百步撞的是同一堵墙，立刻停
                job.abort_diag = diag
                job.abort_with(diag)
                return "aborted"
            if ep and not s.get("soft"):
                with st_lock:
                    bad_eps.add(ep)
                more = len([e for e in chains_eps if e != ep]) if chains_eps else 0
                job.log(key, f"{ep} 卡在这一步，这一集后面的环节跳过"
                             + (f"；其它 {more} 集继续" if more else
                                "（这次只跑这一集，所以流水线到此为止）"))
            elif ep:
                # 环节5/7/8 是段级或旁路的，一处失败不该拖累整集：
                # 环节5 只喂出图；环节7/8 按段跑，没做成的那几段单独补
                job.log(key, "做成的部分都存了盘，没做成的那几段再点一次「开始」会自动补；"
                             f"{ep} 其余部分照常往下走")
            return "failed"
        return "ok"

    # ---- 驱动：环节1 → 逐集（可并行）→ 出图出片/交付（串行） -------------
    r1 = do_step(steps[0])                              # 环节1，全剧一次
    if r1 not in ("ok", "skipped"):
        # 环节1 没成就别往下走：人物表、视觉基调、集边界全在它的产物里，
        # 后面每一步都要读。硬跑下去只是把同一个错误重复几百遍。
        job.log(steps[0]["label"], "环节1 没成，后面的步骤全部不发 —— 它是所有环节的输入")
        for s in steps[1:]:
            job.set_item(s["label"], state="skipped", msg="等环节1 先跑通")
    elif not stop_now():
        rest = plan(pj, include_produce=include_produce,
                    include_deliver=include_deliver,
                    only_episodes=only_episodes,
                    produce_episodes=produce_episodes)[1:]
        steps = steps[:1] + rest
        job.total = len(steps)
        for r in rest:
            job.set_item(r["label"], state="pending")
        # 按新计划重排，否则页面上「出图出片/交付」会显示在「逐集环节」
        # 前面（字典按插入顺序），看着像顺序反了
        job.reorder_items([x["label"] for x in steps])
        n = len(_eps.ids(pj))
        ana = "、".join(only_episodes) if only_episodes else f"全部 {n} 集"
        pro = "、".join(produce_episodes) if produce_episodes else "同上"
        job.log(steps[0]["label"],
                f"识别出 {n} 集；分析范围={ana}；出图出片范围={pro}；共 {len(steps)} 步")

        # 逐集分组，组内保持 s2→s8 的顺序（同一集内部有硬依赖）
        chains: dict = {}
        tail: list = []
        for s in steps[1:]:
            ep = s.get("episode", "")
            if s["kind"] == "llm" and ep:
                chains.setdefault(ep, []).append(s)
            else:
                tail.append(s)
        eps_order = list(chains)
        chains_eps[:] = eps_order
        # 环节4 必须按集号顺序跑：它的输入里有「前面几集已建好的资产」，
        # 先出现的定义优先。并行时若 EP05 抢在 EP03 前头做环节4，两集会各自
        # 给同一个新对象编号 —— 同一个编号指两个东西，出图就张冠李戴。
        # 所以只给环节4 加一道按集排队的闸门，其余环节自由并行。
        gates = [threading.Event() for _ in eps_order]

        def run_episode(k: int, ep: str) -> None:
            main = [s for s in chains[ep] if not s.get("side")]
            side = [s for s in chains[ep] if s.get("side")]
            pool = None
            futs: list = []
            try:
                for s in main:
                    if halt(s):
                        continue
                    # 这一集已经卡住了就别再去等环节4 的闸门 —— 等不出任何结果，
                    # 还白占一个并发位，而且页面上这几步会一直挂在 pending，
                    # 看着像卡死了。直接交给 do_step 标 skipped。
                    with st_lock:
                        dead = ep in bad_eps
                    if dead:
                        do_step(s)
                        continue
                    if s["stage"] == "s4" and k > 0:
                        while not gates[k - 1].wait(0.2):
                            if halt(s):
                                return
                        job.log(s["label"], f"{eps_order[k-1]} 的环节4 已完成，开始本集")
                    do_step(s)
                    if s["stage"] == "s4":
                        gates[k].set()
                        # 环节4 一出来，旁路的环节5 就能开跑了 —— 和主链的
                        # 环节6/7/8 并行，不占关键路径
                        if side:
                            pool = ThreadPoolExecutor(max_workers=1)
                            futs = [pool.submit(do_step, x) for x in side]
                            side = []
            finally:
                # 没跑到环节4 就挂了也要放行，否则后面所有集永远等在这儿
                gates[k].set()
                for x in side:          # 旁路还没起就结束了，别把步骤丢了
                    if not halt(x):
                        do_step(x)      # 这一集已经在 bad_eps 里 → 会标 skipped
                for f in futs:
                    f.result()          # do_step 自己不抛异常，这里只是等它跑完
                if pool:
                    pool.shutdown()

        workers = max(1, min(int(ep_concurrency or 1), len(eps_order) or 1))
        if eps_order:
            job.log(steps[0]["label"],
                    f"逐集环节并发 {workers} 集"
                    + (f"，环节8 段内并发 {seg_concurrency}" if seg_concurrency > 1 else "")
                    + f"，LLM 在途上限 {LLM_GATE.snapshot()['llm_limit']}"
                    + ("；环节4 按集号排队（跨集资产编号要沿用）" if workers > 1 else ""))
        if workers > 1 and len(eps_order) > 1:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                list(pool.map(lambda a: run_episode(*a), list(enumerate(eps_order))))
        else:
            for a in enumerate(eps_order):
                run_episode(*a)

        # 出图出片前把 tasks.json 再装配一次：并行下最后一次装配可能读到的是
        # 别的集还没存盘时的状态，这里补一遍，确保清单是完整的
        if tail and not stop_now():
            S.build_tasks(pj, params)
        for s in tail:
            if halt(s):
                continue
            do_step(s)

    job.finished_at = __import__("time").time()
    if job.aborted:
        job.status = "aborted"
    elif job.cancelled:
        job.status = "cancelled"
    else:
        job.status = "error" if failed else "done"
    return {"steps": len(steps), "failed": failed,
            "stuck_episodes": sorted(bad_eps), "status": job.status,
            "llm_peak": LLM_GATE.snapshot().get("llm_peak", 0)}


def start(job: Job, pj, **kw) -> None:
    threading.Thread(target=run, args=(job, pj), kwargs=kw, daemon=True).start()


def preview(pj, *, include_produce: bool = True, include_deliver: bool = True,
            only_episodes: Optional[list] = None,
            produce_episodes: Optional[list] = None) -> dict:
    """不跑，只说清「这一次会做什么、跳过什么」。点之前先看一眼，别花冤枉钱。"""
    steps = plan(pj, include_produce=include_produce, include_deliver=include_deliver,
                 only_episodes=only_episodes, produce_episodes=produce_episodes)
    todo, skip = [], []
    for s in steps:
        if s["kind"] == "llm":
            (skip if _llm_done(pj, s["stage"], s.get("episode", "")) else todo).append(s["label"])
        elif s["kind"] == "produce":
            n = len(_produce_todo(pj, s["task_key"], s.get("only")))
            (todo if n else skip).append(f"{s['label']}（{n} 项）" if n else s["label"])
        else:
            todo.append(s["label"])
    eps = _eps.ids(pj)
    calls = sum(1 for s in steps if s["kind"] == "llm"
                and not _llm_done(pj, s["stage"], s.get("episode", "")))
    produce = {s["produce"]: len(_produce_todo(pj, s["task_key"], s.get("only")))
               for s in steps if s["kind"] == "produce"}
    return {"episodes": len(eps), "total_steps": len(steps),
            "todo": todo, "skip": skip,
            "llm_calls_at_least": calls, "produce": produce,
            "note": "环节8 是逐段调用，实际 LLM 次数会比上面这个数多"
                    "（每集有几段就多几次）。出图出片的次数就是上面 produce 里的数。"}
