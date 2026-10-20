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
必须按集号排队，否则两集会各自给同一个新对象编号。出图出片不再是「全部集分析
完才开始」：三个泵和逐集分析**同时**跑，tasks.json 增量装配（每集环节5/8 落盘
就重装），泵重扫捡新任务 —— EP01 分析完它的资产图就开始出，不等 EP21。
**并发只改调度，产物和串行跑逐字一致。**

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
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Optional

from . import diagnose, episodes as _eps, probe, relay as _relay, stages as S
from .store import _host
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
         produce_episodes: Optional[list] = None,
         include_llm: bool = True) -> list:
    """算出要做哪些步骤（含已完成的，执行时再跳过）。

    顺序是有讲究的：
      环节1 先跑，它才知道有几集；
      每集的 2-8 连着跑（同一集内部有依赖）；
      资产图放到所有集的环节5 之后 —— 资产库全剧共享，一次出齐才不会重复出图；
      故事板、视频等资产图；
      拼接放最后，按集各出一个成片。

    `include_llm=False` 是「只跑生产」：材料导入模式下没有那十二个环节的
    中间产物，跑 LLM 步只会一步步失败。而生产这几步要的东西 tasks.json
    里全有 —— 走这条路径才能拿到 relay 的就绪即派和 sweep_redo 的条件补跑，
    那两样以前只有「一键跑到底」有。
    """
    steps = ([{"kind": "llm", "stage": "s1", "episode": "",
               "label": "环节1 整剧全局解析（含切集）"}] if include_llm else [])
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
    for ep in (eps if include_llm else []):
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
                          "produce": kind,
                          # relay 的 kind 用**步**的 id，不用 worker 类型。
                          # V6.1 这三类今天不重名，所以没踩到；但电影级那边
                          # p2/p3 都叫 "storyboard"，一步跑完就提前解除了另一步
                          # 下游的等待（2026-08-20 实跑：视频被派早 4 分钟）。
                          # 同一个隐患在这儿，先按步分掉。
                          "batch": sid, "episode": "",
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


def _pump(pj, s: dict, run_chunk: Callable, llm_all_done: Callable,
          should_stop: Callable, relay, log: Callable,
          interval: float = 5.0) -> int:
    """出图出片泵：和逐集分析**同时**跑，反复扫 tasks.json 捡新任务。

    原来这儿是一道整批闸：所有集的环节2-8 跑完（pool.map 屏障）才装配、
    才声明、才派第一批出图。EP01 上午分析完，它的资产图也要等到 EP21
    落盘才开始 —— 中间那段时间服务商的并发额度完全空转。

    装配侧本来就是增量的（stages.py 里每集环节5/8 落盘就重装一遍
    tasks.json），所以泵只要每 5 秒重扫一次，新任务自动到手。

    两个写错**不报错**的语义（tests/test_pump.py 钉死了，改之前先看）：

      · **finished 必须延迟** —— relay.finished() 只能等「分析全完 +
        最终清空那轮没有新任务」才发。中间轮发了 = 假 finished，
        等这批产物的任务会从「等」瞬间变「条件不具备」，成批误杀。

      · **「没做成」不是终判** —— 一条任务这轮没做成（含「条件不具备，
        没发请求」），等 relay 的声明清单**涨了版本**就重进一轮：
        它缺的参考图很可能正是刚出生的那批。只有「做成了」（产物落盘，
        _produce_todo 不再返回它）才永久除名。版本不涨就重试是白撞 ——
        服务商拒绝的提示词 5 秒后再发一次还是被拒。

    返回最终清空后还剩几条没做成（0 = 全部做成）；
    中途停（取消/熔断）返回 -1，状态由调用方写。
    """
    sent_at: dict = {}            # key → 发出那一轮的 relay 版本号
    final_done = False            # 进没进最终清空轮
    n_round = 0
    task_key, only, batch = s["task_key"], s.get("only"), s["batch"]
    while True:
        if should_stop():
            return -1
        # 声明要每轮都做：别的泵声明的新产物也会让版本号涨，
        # 而那正是本泵「没做成的」重进一轮的触发条件
        relay.declare(batch, pj.tasks().get(task_key) or [])
        v = relay.version
        todo = _produce_todo(pj, task_key, only)
        fresh = [t for t in todo
                 if t["key"] not in sent_at or sent_at[t["key"]] < v]
        if not final_done and llm_all_done():
            # 最终清空轮：分析全完、清单定死。没做成的全部再给一轮完整
            # 预算（等于用户再点一次「开始」），跑完就收摊
            final_done = True
            fresh = todo
        if fresh:
            n_round += 1
            if n_round == 1:
                log(f"边分析边出：先派 {len(fresh)} 项，后面每集分析落盘会自动跟上")
            else:
                log(f"第 {n_round} 轮：补派 {len(fresh)} 项（清单长了/上轮有没做成的）")
            run_chunk(s, fresh)
            # 记「发出这一轮之后的」版本：这轮自己声明过的不再触发自己
            for t in fresh:
                sent_at[t["key"]] = relay.version
            if not final_done:
                continue           # 马上重扫 —— 分析还在跑，可能又有新任务
        if final_done:
            # 成没成都要报 —— 这是下游停止等待的唯一信号。
            # 漏了的话，等它产物的任务会一直等到超时上限。
            relay.finished(batch)
            return len(_produce_todo(pj, task_key, only))
        time.sleep(interval)


def _report_produce_left(pj, job: Job, s: dict, r: dict,
                         st_lock: threading.Lock, failed: list,
                         provider_factory: Callable) -> None:
    """收摊时还有没做成的：挂最后一次尝试的诊断、记 failed、该停线就停线。

    调用时机只有两个：泵收摊（run_pump）、单步直调（do_step 的 produce
    分支）。中间轮**不许**调 —— 清单还会长，那不是终态。
    """
    key = s["label"]
    attempts = r.get("attempts") or []
    last = attempts[-1] if attempts else {}
    diag = last.get("diag")
    if not diag:
        for f in diagnose.load(pj.root):
            if f.get("stage") == s["produce"]:
                diag = f
                break
    used = " → ".join(f"{a['provider']}/{a['model']}" for a in attempts)
    job.set_item(key, state="failed", diag=diag,
                 msg=f"还有 {r['left']} 项没做成"
                     + (f"，已试过 {used}" if len(attempts) > 1 else "")
                     + "；照面板上的说明处理后再点一次「开始」")
    with st_lock:
        failed.append(key)
    # 所有家都因为账户级问题挂了 → 整条流水线没有继续的意义
    # （泵的每一轮里已经先拦过一道，这里是单步直调路径的兜底）
    chain = provider_factory(s["produce"])
    if isinstance(chain, dict):
        chain = [chain]
    if diag and diag.get("scope") == "batch" and r.get("switched", 0) + 1 >= len(chain):
        job.abort_diag = diag
        job.abort_with(diag)


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
            # 资产表是全剧的，但只出这几集用得到的图（含状态资产父级与全部依赖）。
            # 其余资产的编号和外观已经在表里定死，跑到那几集时再补图，仍是同一张脸。
            used = S.assets_used_by(pj, only)
            items = [t for t in items if t["key"] in used] if used else items
        else:
            items = [t for t in items if not t.get("episode") or t["episode"] in want]
    return [t for t in items if not probe.have_output(pj.p(*t["output"].split("/")))]


def run(job: Job, pj, *, llm_factory: Callable, provider_factory: Callable,
        params: dict, jobs=None, concurrency: int = 3, max_retry: int = 2,
        include_produce: bool = True, include_deliver: bool = True,
        only_episodes: Optional[list] = None,
        produce_episodes: Optional[list] = None,
        ep_concurrency: int = 1, seg_concurrency: int = 1,
        llm_concurrency: int = 0, include_llm: bool = True) -> dict:
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
    # 本趟的起点。条件补跑用它区分新旧失败记录（core/relay.py）：
    # 这一刻之前的记录是上一趟的旧账，不拦本趟的补跑。
    epoch = time.strftime("%Y-%m-%d %H:%M:%S")
    steps = plan(pj, include_produce=include_produce, include_deliver=include_deliver,
                 only_episodes=only_episodes, produce_episodes=produce_episodes,
                 include_llm=include_llm)
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

    # 只建对象；登记（谁产出什么）由泵每一轮做 —— tasks.json 是增量装配的，
    # 声明也要跟着长（见 _pump）。
    relay = _relay.Relay(pj)

    def run_produce_chunk(s: dict, todo: list) -> dict:
        """跑**一轮**出图/出片（do_step 的 produce 分支抽出来，泵复用）。

        只管这一轮：派单、换家、整批熔断的**立刻**停线。不在这儿写
        ok/failed 终态 —— 清单还会长，收摊由 run_pump 统一写；
        relay.finished 也不能在这儿发（中间轮发了 = 假 finished，
        见 _pump 的注释）。

        整批熔断（余额/密钥）不等收摊：后面还有几十分钟的分析在跑，
        等它们全撞完同一堵墙再停，钱就白烧了。
        """
        key = s["label"]
        chain = provider_factory(s["produce"])      # 按优先级排好的服务商列表
        if isinstance(chain, dict):                 # 兼容只给一家的写法
            chain = [chain]
        first = chain[0]
        job.set_item(key, state="running",
                     msg=f"{len(todo)} 项待做 · 首选 {first['provider']} "
                         f"{first.get('model','')}"
                         + (f"（备选 {len(chain)-1} 家）" if len(chain) > 1 else ""))

        def mk_worker(pcfg, kind=s["produce"]):
            # llm_factory 要传下去：被审核拒绝时靠它改写提示词重发。
            # 漏传不会报错，只是那个功能悄悄没了。
            return (S.make_video_worker(pj, pcfg, llm_factory)
                    if kind == "video"
                    else S.make_image_worker(pj, pcfg, kind, llm_factory))

        def mk_job(pcfg, n, kind=s["produce"]):
            return (jobs.create(kind, n, concurrency, project_root=pj.root,
                                project_name=os.path.basename(pj.root),
                                provider=pcfg["provider"], model=pcfg.get("model", ""))
                    if jobs else Job(kind, n, concurrency, project_root=pj.root,
                                     provider=pcfg["provider"],
                                     model=pcfg.get("model", "")))

        # 资产图之间有依赖：状态资产依赖父资产及其他来源图，不管依赖地
        # 并发的话父子同时跑，状态任务读不到来源 png 直接失败。
        #
        # 以前是分层（层内并发、层间串行），现在改成**就绪即派** ——
        # 一条的参考图全好了它立刻开跑，不用等同层里那条慢的。
        deps = (S.asset_deps(todo) if s["task_key"] == "asset_tasks" else {})
        waiting = sum(1 for v in deps.values() if v)
        if waiting:
            job.log(key, f"{len(todo)} 项里有 {waiting} 项要等上游资产 —— "
                         f"参考图一齐就开跑，不等整层出完")
        r = run_chain(todo, chain=chain, worker_of=mk_worker, job_of=mk_job,
                      key_of=lambda t: t["key"],
                      # 不能用 isfile：0 字节的残骸也是「文件存在」，
                      # 换家重试时会被当成上一家已经做好了，直接跳过。
                      done_of=lambda t: probe.have_output(
                          pj.p(*t["output"].split("/"))),
                      max_retry=max_retry, log=lambda m: job.log(key, m),
                      deps_of=deps.get if deps else None,
                      ready_of=relay.ready_of(s["batch"]))
        if r["left"]:
            # 所有家都因为账户级问题挂了 → 整条流水线没有继续的意义，
            # 现在就停，别让还在跑的分析继续烧钱
            last = r["attempts"][-1] if r["attempts"] else {}
            diag = last.get("diag")
            if not diag:
                for f in diagnose.load(pj.root):
                    if f.get("stage") == s["produce"]:
                        diag = f
                        break
            if diag and diag.get("scope") == "batch" \
                    and r["switched"] + 1 >= len(chain):
                with st_lock:
                    failed.append(key)
                job.abort_diag = diag
                job.abort_with(diag)
        return r

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
            # 走 pump 的由 run_pump 收摊（见驱动段）；do_step 只兜底单步直调
            # （比如只跑一步 produce 的接口路径）—— 一轮跑完就报终态。
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
                r = run_produce_chunk(s, todo)
                relay.finished(s["batch"])        # 单步直调没有「后面还有」—— 当轮就是终点
                if r["left"] == 0:
                    used = " → ".join(f"{a['provider']}/{a['model']}"
                                      for a in r["attempts"])
                    job.set_item(key, state="ok",
                                 msg=f"{len(todo)} 项完成"
                                     + (f"（换了 {r['switched']} 次家：{used}）"
                                        if r["switched"] else ""))
                    return "ok"
                _report_produce_left(pj, job, s, r, st_lock, failed)
                if job.aborted:
                    return "aborted"
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
                               episode=only[0] if len(only) == 1 else "",
                               # 该有几段按环节2 划的段算 —— 那是这套体系里
                               # 「一集有哪些段」唯一的出处。缺段不许拼。
                               expect_segs=S.segment_ids)
                n = len(r.get("masters", [])) or (1 if r.get("master") else 0)
                job.set_item(key, state="ok", msg=f"出了 {n} 个成片")

        except LLMCancelled as exc:                  # 用户点了取消：不算失败
            job.set_item(key, state="cancelled", msg=str(exc))
            job.log(key, str(exc))
            return "cancelled"
        except Exception as exc:                     # noqa: BLE001
            kind = getattr(exc, "kind", "")
            # 服务商和模型都要记：只记模型的话，同一个模型名在两条线路上
            # 表现可能完全不同（截断、限流、字段支持），事后分不出是哪一条。
            diag = diagnose.build(exc, stage=f"stage:{s['stage']}", target=ep or s["stage"],
                                  provider=_host(getattr(box["llm"], "base_url", "")),
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
        prod = [s for s in tail if s["kind"] == "produce"]
        if eps_order:
            job.log(steps[0]["label"],
                    f"逐集环节并发 {workers} 集"
                    + (f"，环节8 段内并发 {seg_concurrency}" if seg_concurrency > 1 else "")
                    + f"，LLM 在途上限 {LLM_GATE.snapshot()['llm_limit']}"
                    + ("；环节4 按集号排队（跨集资产编号要沿用）" if workers > 1 else "")
                    + (f"；出图出片 {len(prod)} 个泵和分析同时跑"
                       "（分析落盘一集就出一集，不再等全部集分析完）" if prod else ""))

        # 出图出片不再等分析跑完 —— 拆掉原来那道整批闸（pool.map 屏障）。
        # tasks.json 本来就是增量装配的（每集环节5/8 落盘就重装一遍，
        # stages.py），泵每 5 秒重扫一次捡新任务：EP01 分析完的那一刻，
        # 它的资产图就能开始出，不用等 EP21。
        #
        # 前提语义见 _pump 的注释；三个泵各管一类活，步内靠 relay 等输入。
        llm_done = threading.Event()

        def run_pump(s: dict) -> None:
            """一个出图出片步骤的泵 + 收摊（终态在这里写，不在 chunk 里）。"""
            key = s["label"]
            stats: dict = {"rounds": 0, "last": None}

            def chunk(step, todo):
                stats["rounds"] += 1
                stats["last"] = run_produce_chunk(step, todo)
                return stats["last"]

            try:
                left = _pump(pj, s, chunk, llm_done.is_set, stop_now, relay,
                             lambda m: job.log(key, m))
            except Exception as exc:                    # noqa: BLE001
                # 一步的调度炸了要说出来，不能吞 —— 业务失败 chunk 自己记了，
                # 能冒到这里的是程序问题。
                job.log(key, f"这一步的调度出错了：{exc}")
                job.set_item(key, state="failed", msg=f"调度出错：{exc}")
                with st_lock:
                    failed.append(key)
                return
            if left < 0 or halt(s):
                # 取消/熔断中途收手：取消 → halt 已写「已取消」；
                # 熔断 → 原地留 running，abort_with 已经说明了原因
                return
            total = len(pj.tasks().get(s["task_key"]) or [])
            if left == 0:
                if not total:
                    job.set_item(key, state="skipped",
                                 msg="前置环节还没产出任务清单，这一步没东西可做"
                                     "（故事板要环节8 的提示词，资产图要环节5 的提示词）")
                elif not stats["rounds"]:
                    job.set_item(key, state="skipped", msg="产物都齐了，跳过")
                else:
                    job.set_item(key, state="ok", msg=f"{total} 项完成")
                return
            _report_produce_left(pj, job, s, stats["last"] or {}, st_lock,
                                 failed, provider_factory)

        n_threads = workers + len(prod)
        if eps_order or prod:
            with ThreadPoolExecutor(max_workers=n_threads) as pool:
                llm_futs = [pool.submit(run_episode, k, ep)
                            for k, ep in enumerate(eps_order)]
                pump_futs = [pool.submit(run_pump, s) for s in prod]
                for f in llm_futs:
                    f.result()         # do_step 自己不抛异常，这里只是等它跑完
                # 出图出片前把 tasks.json 再装配一次：并行下最后一次装配可能
                # 读到的是别的集还没存盘时的状态，这里补一遍，确保最终清空轮
                # 看到的清单是完整的
                if tail and not stop_now():
                    S.build_tasks(pj, params)
                llm_done.set()         # ← 放泵进最终清空轮（收摊）
                for f in pump_futs:
                    f.result()
            # 条件补跑：泵的最终清空轮给每条没做成的**一次性**重试 —— 但
            # 「条件不具备」的判定发生在派单那一刻，别的泵之后才落盘的
            # 产物它看不见。这里把「再点一次开始」的那次全盘重扫自动化
            # （core/relay.py sweep_redo）：产物没有、输入已落盘、不是本趟
            # 真报过错的，按生产顺序自动重派 —— 前一个泵补出来的文件，
            # 后一个泵同一轮立刻接着用，不用人再点「开始」。
            if prod and not stop_now():
                todo_of = lambda s: _produce_todo(pj, s["task_key"], s.get("only"))
                _relay.sweep_redo(pj, prod, relay, run_step=run_produce_chunk,
                                  todo_of=todo_of, epoch=epoch,
                                  should_stop=stop_now,
                                  log=lambda s, m: job.log(s["label"], m))
                # 补跑之后的步骤终态以磁盘为准重算 —— run_pump 写终态时
                # 补跑还没发生，两个方向都可能骗人（详见函数注释）。
                _relay.reconcile_produce_steps(job, failed, prod,
                                               todo_of=todo_of,
                                               should_stop=stop_now)
        # 复核清单、拼接这些非出图步骤照旧串行在最后
        for s in tail:
            if s["kind"] == "produce":
                continue               # 泵已经收摊了
            if halt(s):
                continue
            do_step(s)

    job.finished_at = time.time()
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
            produce_episodes: Optional[list] = None,
            include_llm: bool = True) -> dict:
    """不跑，只说清「这一次会做什么、跳过什么」。点之前先看一眼，别花冤枉钱。"""
    steps = plan(pj, include_produce=include_produce, include_deliver=include_deliver,
                 only_episodes=only_episodes, produce_episodes=produce_episodes,
                 include_llm=include_llm)
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
