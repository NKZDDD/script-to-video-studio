# -*- coding: utf-8 -*-
"""12 环节确定性编排。

环节 1-8：调 LLM（固定模板 + JSON 校验），编排逻辑是代码，不是模型自由发挥。
环节 5b/9/10：出图出片，走 providers + 线程池。
环节 11：产出人工复核清单（不自动判定质量）。
环节 12：ffmpeg 硬切拼接 + 生产包归档。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Optional

from . import diagnose, ledger, paths, probe, uploader
from .executor import LLM_GATE
from .llm import LLM, LLMCancelled
from .providers import ImageTask, VideoTask, build as build_provider
from .apiutil import resolve_ref
from .store import Project, read_text, write_text

HERE = os.path.dirname(os.path.abspath(__file__))
# 打包成 exe 后模板是解压到临时目录的，不能按 __file__ 往上找
PROMPT_DIR = paths.res("prompts")

# 环节定义：驱动前端流程图与执行按钮
STAGES = [
    {"id": "s1", "no": 1, "name": "整剧全局解析", "kind": "llm", "out": "s1_global"},
    {"id": "s2", "no": 2, "name": "节奏驱动段落划分", "kind": "llm", "out": "s2_segments"},
    {"id": "s3", "no": 3, "name": "状态时间线管理", "kind": "llm", "out": "s3_states"},
    {"id": "s4", "no": 4, "name": "资产提取与生产判断", "kind": "llm", "out": "s4_assets"},
    {"id": "s5", "no": 5, "name": "资产生产提示词编译", "kind": "llm", "out": "s5_asset_prompts"},
    {"id": "s5b", "no": 5, "name": "资产图生产（出图）", "kind": "image", "out": ""},
    {"id": "s6", "no": 6, "name": "段落资产绑定", "kind": "llm", "out": "s6_binding"},
    {"id": "s7", "no": 7, "name": "高密度正式分镜", "kind": "llm", "out": "s7_shots"},
    {"id": "s8", "no": 8, "name": "故事板与视频提示词编译", "kind": "llm", "out": "s8_compile"},
    {"id": "s9", "no": 9, "name": "故事板生成与固定", "kind": "image", "out": ""},
    {"id": "s10", "no": 10, "name": "视频执行生成", "kind": "video", "out": ""},
    {"id": "s11", "no": 11, "name": "结果检查清单（人工）", "kind": "local", "out": ""},
    {"id": "s12", "no": 12, "name": "排序拼接与交付", "kind": "local", "out": ""},
]


def prompt_files(name: str) -> tuple:
    """(内置模板路径, 改写模板路径)。改写的存在就用改写的。

    改写放在数据目录而不是程序目录：内置模板打包进 exe 之后是只读的，
    而且程序更新会覆盖它 —— 改在那儿等于白改。
    """
    return (os.path.join(PROMPT_DIR, f"{name}.md"),
            os.path.join(paths.prompts_dir(), f"{name}.md"))


def load_prompt(name: str) -> str:
    builtin, custom = prompt_files(name)
    return read_text(custom if os.path.isfile(custom) else builtin)


def render(tpl: str, mapping: dict) -> str:
    for k, v in mapping.items():
        tpl = tpl.replace("{{" + k + "}}", str(v))
    return tpl


def jd(obj: Any, limit: int = 0) -> str:
    s = json.dumps(obj, ensure_ascii=False, indent=2)
    return s[:limit] if limit and len(s) > limit else s


# ====================================================================== LLM 环节

_LLM_SPEC = {
    # stage_id: (模板名, 依赖的已存产物, 必需字段)
    "s1": ("s1_global", [], ["project_name", "characters[]", "visual_tone"]),
    # 注：episode_ranges[].segments（每集切几段）没列进必需字段 ——
    # 老项目的产物里没有它，列了会让重跑老项目直接失败。缺了就退回按
    # 「单集分钟 ÷ 单段秒数」算，见 _seg_target()。
    "s2": ("s2_segments", ["s1_global"], ["segments[]", "segments[].id", "segments[].exit_state"]),
    "s3": ("s3_states", ["s1_global", "s2_segments"], ["segment_states[]"]),
    "s4": ("s4_assets", ["s1_global", "s2_segments", "s3_states"], ["assets[]", "assets[].asset_id"]),
    "s5": ("s5_asset_prompts", ["s1_global", "s4_assets"], ["asset_prompts[]", "asset_prompts[].prompt"]),
    "s6": ("s6_binding", ["s2_segments", "s3_states", "s4_assets"], ["bindings[]"]),
    "s7": ("s7_shots", ["s2_segments", "s3_states", "s6_binding"], ["shots[]"]),
    "s8": ("s8_compile", ["s1_global", "s2_segments", "s3_states", "s4_assets", "s6_binding", "s7_shots"],
           ["compiled[]", "compiled[].storyboard_prompt", "compiled[].video_prompt"]),
}

# 一个项目 = 一部剧。环节1 吃整部剧本、只跑一次；环节2 往后逐集跑。
# 之所以只有环节1 是全剧级：跨集要统一的东西（人物长相、视觉基调、伏笔、
# 不可更改事实）都在它的产物里，往下每集都引用同一份，人物才不会换脸。
SERIES_STAGES = {"s1"}


def is_per_episode(stage_id: str) -> bool:
    return stage_id not in SERIES_STAGES


# ---- 并行跑多集时必须串起来的三处共享状态 ------------------------------
# 环节5 判断「这个资产的提示词写过没有」是看磁盘上的 txt 在不在。多集并行时
# 两集可能同时看到 C007 还没写 → 各写一遍，钱花两份，后写的还覆盖先写的。
# 所以在进程内先「领走」：领了就算写了，别的集不再碰。失败再放回去。
_CLAIM_LOCK = threading.Lock()
_CLAIMED: set = set()
# tasks.json 是整体重写的（读全部集 → 写一份）。并行时读-改-写会互相盖掉，
# 整段加锁后每次调用都能看到之前所有已保存的产物。
_TASKS_LOCK = threading.Lock()


def _claim(ids: list) -> list:
    """领走还没被别的集领走的那些，返回真正归自己写的。"""
    with _CLAIM_LOCK:
        mine = [i for i in ids if i not in _CLAIMED]
        _CLAIMED.update(mine)
        return mine


def _unclaim(ids: list) -> None:
    with _CLAIM_LOCK:
        _CLAIMED.difference_update(ids)


def reset_claims() -> None:
    """一趟流水线开跑前清空。上一趟失败留下的领取记录不该影响这一趟。"""
    with _CLAIM_LOCK:
        _CLAIMED.clear()


_STAGE_OF_OUT = {s["out"]: s for s in STAGES if s.get("out")}


def known_assets(pj: Project, upto_episode: str = "") -> list:
    """把前面几集已经建好的资产汇总起来，喂给环节4 让它沿用编号。

    资产库全剧共享：同一个角色在 EP01 和 EP07 必须是同一个 asset_id，
    否则会各出一张脸。这里按集顺序累加，先出现的定义优先（后面的不许改写）。
    """
    from . import episodes as _eps
    out, seen = [], set()
    for ep in _eps.ids(pj):
        if upto_episode and ep == upto_episode:
            break
        for a in (pj.stage_data("s4_assets", ep) or {}).get("assets", []):
            aid = a.get("asset_id")
            if not aid or aid in seen:
                continue
            seen.add(aid)
            out.append({k: a.get(k, "") for k in
                        ("asset_id", "category", "name", "parent_asset_id",
                         "identity_anchors", "appearance")})
    return out


def _dep_data(pj: Project, deps: list, episode: str) -> dict:
    """取前置产物：全剧级的从项目根取，逐集的从本集目录取。"""
    out = {}
    for d in deps:
        owner = _STAGE_OF_OUT.get(d, {}).get("id", "")
        out[d] = pj.stage_data(d, "" if owner in SERIES_STAGES else episode)
    return out


def run_llm_stage(pj: Project, stage_id: str, llm: LLM, params: dict,
                  log: Callable = print, episode: str = "",
                  cancel: Optional[Callable] = None,
                  seg_concurrency: int = 1) -> dict:
    from . import episodes as _eps
    tpl_name, deps, required = _LLM_SPEC[stage_id]
    per_ep = is_per_episode(stage_id)

    if per_ep:
        avail = _eps.ids(pj)
        if not episode:
            if not avail:
                raise RuntimeError("还没切集。请先跑环节1「整剧全局解析」——"
                                   "它会判断这份剧本有几集、边界在哪。")
            if len(avail) > 1:
                raise RuntimeError(f"这个项目有 {len(avail)} 集，得指定跑哪一集"
                                   f"（{avail[0]} … {avail[-1]}）。"
                                   f"在「流程」页上方选集，或点「全部集依次跑」。")
            episode = avail[0]
        elif episode not in avail:
            raise RuntimeError(f"没有 {episode} 这一集。当前项目里有：{'、'.join(avail) or '（还没切集）'}")
    else:
        episode = ""

    data = _dep_data(pj, deps, episode)
    missing = [d for d, v in data.items() if v is None]
    if missing:
        names = "、".join(
            f"环节{_STAGE_OF_OUT[m]['no']}「{_STAGE_OF_OUT[m]['name']}」"
            + ("" if _STAGE_OF_OUT[m]["id"] in SERIES_STAGES else f"（{episode} 这一集的）")
            for m in missing if m in _STAGE_OF_OUT)
        raise RuntimeError(f"缺少前置产物，请先跑：{names or missing}")

    if stage_id == "s8":                       # s8 分段编译，天然可续跑
        return run_s8_incremental(pj, llm, params, data, log, episode, cancel,
                                  seg_concurrency=seg_concurrency)
    if stage_id == "s7" and (data.get("s3_states") or {}).get("axis_convention"):
        # 有整集轴线约定才敢拆段：各段照同一套左右位置排镜头，不会跳轴。
        # 没有（老项目的环节3 产物）就走下面整集一次的老路，产物照样能用。
        return run_s7_incremental(pj, llm, params, data, log, episode, cancel,
                                  seg_concurrency=seg_concurrency)

    g = data.get("s1_global") or {}
    tone = (g.get("visual_tone") or {})
    # 逐集环节只送本集正文。整部 40 集全文送进去，模型会按整部去切段，
    # 而且每个环节都重发一遍全文，token 白烧。
    script = _eps.script_of(pj, episode) if per_ep else params.get("script", "")

    # 环节5 只给「还没写过提示词的资产」。资产提示词全剧共用一份文件，
    # 不过滤的话 40 集会把同一个角色的提示词重写 40 遍：白花钱，还可能越写越飘。
    if stage_id == "s5":
        a4 = dict(data.get("s4_assets") or {})
        free, skipped = [], []
        for a in a4.get("assets", []):
            aid = a.get("asset_id", "")
            f = pj.p("03_提示词", "资产生产提示词", f"{aid}_PROMPT.txt")
            (skipped if os.path.isfile(f) else free).append(aid)
        # 多集并行时，别的集可能刚好也在写这几个资产的提示词 —— 领得到才算自己的
        todo = _claim(free)
        skipped += [i for i in free if i not in set(todo)]
        a4["assets"] = [a for a in a4.get("assets", []) if a.get("asset_id") in set(todo)]
        data["s4_assets"] = a4
        if skipped:
            log(f"{episode} 有 {len(skipped)} 个资产前面几集已经写过提示词，跳过："
                f"{'、'.join(skipped[:8])}{'…' if len(skipped) > 8 else ''}")
        if not a4["assets"]:
            log(f"{episode} 没有新资产要写提示词，直接跳过（不调模型、不花钱）")
            prev = pj.stage_data("s5_asset_prompts", episode) or {"asset_prompts": []}
            pj.save_stage("s5_asset_prompts", prev, episode)
            build_tasks(pj, params)
            diagnose.clear(pj.root, "stage:s5", episode)
            return prev
    # PARAMS 里绝不能带 script：它已经在 {{SCRIPT}} 里送了一份。
    # 之前没剔，等于把 8 万字的剧本发两遍 —— 环节1 的输入从 72K token 涨到
    # 141K token，一半是重复内容，钱翻倍、首字延迟翻倍，也是上次超时的主因。
    # 其余字段（尺寸/时长/镜头数）都是几十字节的小值，留着有用。
    slim = {k: v for k, v in params.items() if k != "script"}
    slim["episode"] = episode or params.get("episode", "")
    # 本集切几段：环节1 逐集定的优先，老项目没这个字段就退回全局参数折算
    seg_n, seg_why = _eps.seg_target(pj, episode, params) if per_ep else (0, "")
    if stage_id == "s2":
        log(f"{episode} 目标 {seg_n} 段（{seg_why}）→ 成片约 "
            f"{seg_n * int(params.get('duration') or 15)} 秒")
    mapping = {
        "PARAMS": jd(slim),
        "SCRIPT": script,
        "EPISODE": episode or params.get("episode", "EP01"),
        "DURATION": params.get("duration", 15),
        "SEGMENTS_TARGET": seg_n,
        "SEGMENTS_WHY": seg_why,
        "IMAGE_SIZE": params.get("image_size", "1024x1536"),
        # 镜头数 5-8、关键帧 4-6 这类区间不再当配置项传：它们是给模型判断用的
        # 创作区间（skill 的规定），写死在模板里就行。做成旋钮反而误导人
        # 去「控制」它 —— 该由模型按这一段的信息密度定。
        "TONE": jd({"compressed": tone.get("compressed", ""),
                    "variants": tone.get("compressed_variants", [])}),
        "GLOBAL": jd(data.get("s1_global", {})),
        "SEGMENTS": jd(data.get("s2_segments", {})),
        "STATES": jd(data.get("s3_states", {})),
        "ASSETS": jd(data.get("s4_assets", {})),
        "BINDINGS": jd(data.get("s6_binding", {})),
        "SHOTS": jd(data.get("s7_shots", {})),
        "KNOWN_ASSETS": jd(known_assets(pj, episode) if stage_id == "s4" else []),
        # 整集共用的 180 度轴线约定（环节3 定的）。老项目的产物里没有，
        # 那就是空的 —— 环节7 会自己定轴，和以前一样。
        "AXIS": jd((data.get("s3_states") or {}).get("axis_convention") or {}),
    }
    system = load_prompt("_common")
    user = render(load_prompt(tpl_name), mapping)
    tag = f"{episode} " if episode else "全剧 "
    log(f"{tag}提示词 {len(user)} 字，调用 {llm.model}")
    def _usage(u, _st=stage_id, _ep=episode):
        ledger.record(pj.root, kind="llm", stage=_st, episode=_ep,
                      provider="llm", model=u.get("model", ""),
                      prompt_tokens=u.get("prompt_tokens") or 0,
                      cached_tokens=(u.get("prompt_tokens_details") or {})
                      .get("cached_tokens", 0),
                      completion_tokens=u.get("completion_tokens") or 0,
                      seconds=u.get("seconds") or 0,
                      estimated=bool(u.get("estimated")))
    try:
        with LLM_GATE.slot():
            out = llm.json_call(system, user, required=required, log=log,
                                cancel=cancel, on_usage=_usage)
    except BaseException:
        if stage_id == "s5":
            # 没写成，把领走的资产放回去，让下一集或重跑能接手
            _unclaim([a.get("asset_id", "") for a in
                      (data.get("s4_assets") or {}).get("assets", [])])
        raise
    pj.save_stage(tpl_name, out, episode)

    if stage_id == "s1":
        # 环节1 一跑完立刻切集：边界由它判断，切割由代码按锚点做
        res = _eps.build(pj, params.get("script", ""), out)
        eps = res.get("episodes", [])
        dur = int(params.get("duration") or 15) or 15
        log(f"识别出 {len(eps)} 集"
            + (f"（前 {res['preamble_chars']} 字是推介/说明，已排除在正文外）"
               if res.get("preamble_chars") else ""))
        no_sec = [e["episode"] for e in eps if not e.get("duration_sec")]
        for e in eps[:60]:
            sec = e.get("duration_sec") or 0
            n = _eps.segs_from_sec(sec, dur) if sec else 0
            log(f"  {e['episode']}  {e['chars']:>6} 字  "
                + (f"{sec:>4} 秒 → {n:>3} 段  " if sec else "  秒数未给  ")
                + (e.get('title', '') or '')[:30])
        if eps:
            tot_sec = sum((e.get("duration_sec") or 0) for e in eps)
            tot_seg = sum(_eps.segs_from_sec(e.get("duration_sec") or 0, dur)
                          for e in eps if e.get("duration_sec"))
            log(f"  合计 {tot_sec} 秒 ≈ {tot_sec / 60:.0f} 分钟 → {tot_seg} 段"
                f"（段数 = 秒数 ÷ 单段 {dur} 秒，换视频模型时自动跟着变）"
                + (f"；{len(no_sec)} 集没给秒数，会按「单集分钟」折算" if no_sec else ""))
        for it in res.get("issues", []):
            log(f"  {'⚠️' if it.get('level') == 'warn' else '❌'} {it['episode']}：{it['reason']}")

    if stage_id == "s2":
        got = len(out.get("segments") or [])
        if seg_n and got != seg_n:
            # 不抛错：段落表本身是可用的，硬失败会把整集卡住。但必须记成
            # 待办，否则各集时长悄悄不一致，要到拼完成片才发现。
            log(f"⚠️ {episode} 要求 {seg_n} 段，实际切了 {got} 段"
                f"（成片会变成 {got * int(params.get('duration') or 15)} 秒，"
                f"目标是 {seg_n * int(params.get('duration') or 15)} 秒）")
            diagnose.record(pj.root, diagnose.warn(
                "SEG_COUNT_OFF",
                f"{episode} 要求 {seg_n} 段，模型切了 {got} 段 —— "
                f"成片会是 {got * int(params.get('duration') or 15)} 秒而不是 "
                f"{seg_n * int(params.get('duration') or 15)} 秒",
                stage="stage:s2", target=episode))
        if out.get("segments_note"):
            log(f"环节2 的说明：{out['segments_note']}")

    # s5 额外把提示词正文落成 txt，便于人工查看与执行器读取
    if stage_id == "s5":
        for ap in out.get("asset_prompts", []):
            write_text(pj.p("03_提示词", "资产生产提示词", ap.get("filename") or f"{ap['asset_id']}_PROMPT.txt"),
                       ap.get("prompt", ""))
    if stage_id in ("s5", "s8"):
        build_tasks(pj, params)
    diagnose.clear(pj.root, f"stage:{stage_id}", episode)
    return out


def s8_done_segments(pj: Project, episode: str = "") -> set:
    """已经编好两份提示词的段落（磁盘为准，重启也认）。

    段落 id 自带 EPxx 前缀（EP01-SEG03），所以提示词目录不用按集分子目录，
    传 episode 就按前缀过滤出本集的。
    """
    done = set()
    sb_dir, vd_dir = pj.p("03_提示词", "故事板提示词"), pj.p("03_提示词", "视频提示词")
    if not os.path.isdir(sb_dir):
        return done
    for f in os.listdir(sb_dir):
        if f.endswith("_STORYBOARD_PROMPT.txt"):
            sid = f[: -len("_STORYBOARD_PROMPT.txt")]
            if episode and not sid.startswith(f"{episode}-"):
                continue
            if os.path.isfile(os.path.join(vd_dir, f"{sid}_VIDEO_PROMPT.txt")):
                done.add(sid)
    return done


def s7_done_segments(pj: Project, episode: str = "") -> set:
    """已经排好分镜的段落（磁盘为准）。和 s8 一样按段续跑。

    **必须要求 shot_list 非空**，而且要和环节8 的判据一致：环节8 会跳过没有
    镜头表的段（拿空分镜硬编会出一份没依据的提示词）。如果这里只认 id 在不在，
    一个「有 id、镜头表是空的」的段就会卡死 —— 环节7 认为做完了不再重排，
    环节8 永远跳过它，谁都不管。宁可每次重跑时重排一次并报出来。
    """
    return {x.get("id") for x in
            (pj.stage_data("s7_shots", episode) or {}).get("shots", [])
            if x.get("id") and x.get("shot_list")}


def _usage_of(pj: Project, stage: str, episode: str, target: str = "") -> Callable:
    def rec(u: dict) -> None:
        ledger.record(pj.root, kind="llm", stage=stage, episode=episode, target=target,
                      provider="llm", model=u.get("model", ""),
                      prompt_tokens=u.get("prompt_tokens") or 0,
                      cached_tokens=(u.get("prompt_tokens_details") or {})
                      .get("cached_tokens", 0),
                      completion_tokens=u.get("completion_tokens") or 0,
                      seconds=u.get("seconds") or 0,
                      estimated=bool(u.get("estimated")))
    return rec


def run_segmented(pj: Project, *, stage_id: str, out_name: str, key: str,
                  segs: list, done_ids: set, llm: LLM, build_user: Callable,
                  required: list, log: Callable, episode: str,
                  cancel: Optional[Callable], seg_concurrency: int,
                  on_item: Optional[Callable] = None) -> tuple:
    """按段跑一个环节：一段一次调用、每段存盘、失败只影响那一段、天然可续跑。

    环节7 和环节8 共用这一套。为什么能按段拆：段与段之间没有数据依赖 ——
    每段只吃自己那段的 seg/state/binding/shots。整集一次调用的问题是输出太长
    （环节8 一集 12 段就 20 万字节），中途失败整批白跑。

    **共享上下文必须由 build_user 按段裁过再送**，否则整份资产表重发十几遍，
    时间省了钱翻几倍。环节7/8 的输入本来就是按段组织的，裁完基本不多花。

    返回 (结果, 失败的段, 被取消的段)。异常不往外抛，由调用方决定怎么报。
    """
    prev = pj.stage_data(out_name, episode) or {key: []}
    by_id = {c["id"]: c for c in prev.get(key, []) if c.get("id")}
    todo = [s for s in segs if s["id"] not in done_ids]
    log(f"{episode or '本集'} 共 {len(segs)} 段，已完成 {len(done_ids)} 段，本次做 {len(todo)} 段")

    failed: list = []
    cancelled: list = []
    save_lock = threading.Lock()
    n = len(todo)

    def one(i: int, seg: dict) -> None:
        if (cancel and cancel()) or cancelled:
            return
        sid = seg["id"]
        log(f"[{i}/{n}] {sid}")
        try:
            with LLM_GATE.slot():
                out = llm.json_call(
                    system=load_prompt("_common"), user=build_user(seg),
                    required=required,
                    log=lambda m, _s=sid: log(f"    {_s}: {m}"), cancel=cancel,
                    on_usage=_usage_of(pj, stage_id, episode, sid))
            item = out[key][0]
            item["id"] = sid
            if on_item:
                on_item(sid, item)
            # 每段都存盘：中途中断也不丢已完成的。并发下读-改-写必须串行，
            # 否则两段同时保存，后写的会把先写的那段从数组里挤掉。
            with save_lock:
                by_id[sid] = item
                pj.save_stage(out_name,
                              {key: [by_id[s["id"]] for s in segs if s["id"] in by_id]},
                              episode)
            diagnose.clear(pj.root, f"stage:{stage_id}", sid)
        except LLMCancelled:
            # 用户点了取消：不算失败，也别再往下派活
            with save_lock:
                cancelled.append(sid)
        except Exception as exc:                            # noqa: BLE001
            d = diagnose.build(exc, stage=f"stage:{stage_id}", target=sid, model=llm.model)
            diagnose.record(pj.root, d)
            with save_lock:
                failed.append(sid)
            log(f"    {diagnose.one_line(d)}")

    workers = max(1, min(int(seg_concurrency or 1), n or 1))
    if workers > 1 and n > 1:
        log(f"{episode or '本集'} {stage_id} 段内并发 {workers}")
        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(lambda a: one(*a), list(enumerate(todo, 1))))
    else:
        for a in enumerate(todo, 1):
            one(*a)

    result = {key: [by_id[s["id"]] for s in segs if s["id"] in by_id]}
    pj.save_stage(out_name, result, episode)
    return result, failed, cancelled


def run_s7_incremental(pj: Project, llm: LLM, params: dict, data: dict,
                       log: Callable = print, episode: str = "",
                       cancel: Optional[Callable] = None,
                       seg_concurrency: int = 1) -> dict:
    """环节7 逐段排分镜。

    前提是环节3 给出了整集共用的轴线约定（axis_convention）。没有它就不能拆：
    实测一次看完 12 段时，各段的 axis_note 会互相引用（「沿同一玻璃门轴线」
    「轴线保持不变」），Aisyah 一直在左、Dewi 一直在右。拆开并行、各段独立
    定左右，硬切就会跳轴 —— 而这是模板里的强制规则。
    调用方负责检查轴线约定在不在，不在就走整集一次的老路。
    """
    segs = (data.get("s2_segments") or {}).get("segments", [])
    if not segs:
        raise RuntimeError("段落表为空，请先跑环节2")
    s3 = data.get("s3_states") or {}
    states = {s["id"]: s for s in s3.get("segment_states", [])}
    binds = {b["id"]: b for b in (data.get("s6_binding") or {}).get("bindings", [])}
    tpl = load_prompt("s7_shots")
    axis = s3.get("axis_convention")

    def build_user(seg: dict) -> str:
        sid = seg["id"]
        return render(tpl, {
            "EPISODE": episode, "AXIS": jd(axis),
            "DURATION": params.get("duration", 15),
            "SEGMENTS": jd(seg),
            "STATES": jd(states.get(sid, {})),
            "BINDINGS": jd(binds.get(sid, {})),
        }) + (f"\n\n【只排这一段】{sid}，shots 数组只放这一段。"
              f"轴线必须照上面的整集约定执行，不要另立一套。")

    result, failed, cancelled = run_segmented(
        pj, stage_id="s7", out_name="s7_shots", key="shots", segs=segs,
        done_ids=s7_done_segments(pj, episode), llm=llm, build_user=build_user,
        required=["shots[]"], log=log, episode=episode, cancel=cancel,
        seg_concurrency=seg_concurrency)

    if cancelled and not failed:
        raise LLMCancelled(
            f"{episode or '本集'} 环节7 已按取消停下，排好的 {len(result['shots'])} 段都存了盘；"
            f"再点一次「开始」只补没排的那些段。")
    if failed:
        raise RuntimeError(
            f"{episode or '本集'} 有 {len(failed)} 段分镜失败：{'、'.join(failed[:8])}"
            f"{'…' if len(failed) > 8 else ''}。已完成的 {len(result['shots'])} 段已保存，"
            f"重跑环节7 只会补失败的这些段。")
    log(f"{episode or '本集'} 全部 {len(result['shots'])} 段分镜完成")
    return result


def run_s8_incremental(pj: Project, llm: LLM, params: dict, data: dict,
                       log: Callable = print, episode: str = "",
                       cancel: Optional[Callable] = None,
                       seg_concurrency: int = 1) -> dict:
    """环节8 逐段编译：一段一次 LLM 调用，段之间并发。

    整集一次调用的问题：17 段 × 2 份提示词输出太长，中途失败整批白跑。
    逐段调用后，已完成的段落跳过，失败只影响那一段 —— 天然支持续跑。
    """
    segs = (data.get("s2_segments") or {}).get("segments", [])
    if not segs:
        raise RuntimeError("段落表为空，请先跑环节2")

    tone = ((data.get("s1_global") or {}).get("visual_tone") or {})
    states = {s["id"]: s for s in (data.get("s3_states") or {}).get("segment_states", [])}
    binds = {b["id"]: b for b in (data.get("s6_binding") or {}).get("bindings", [])}
    shots = {s["id"]: s for s in (data.get("s7_shots") or {}).get("shots", [])}
    assets = (data.get("s4_assets") or {}).get("assets", [])
    tpl = load_prompt("s8_compile")

    # 环节7 是按段跑的，可能有几段没排出分镜（比如空回复重试完还是空）。
    # 那几段必须跳过：拿空分镜硬编，会出一份没有镜头依据的提示词，
    # 而它一旦落盘就被当成"做过了"，后面出图出片全按这份错的走。
    # 跳过的段留在段落表里，修好分镜后再点一次就补上了。
    no_shots = [s["id"] for s in segs if not (shots.get(s["id"], {}).get("shot_list"))]
    if no_shots:
        segs = [s for s in segs if s["id"] not in set(no_shots)]
        log(f"{episode or '本集'} 有 {len(no_shots)} 段还没排出分镜，这次不编："
            f"{'、'.join(no_shots[:8])}{'…' if len(no_shots) > 8 else ''}"
            f"（先把环节7 那几段补上，再点一次「开始」会自动补编）")
    if not segs:
        raise RuntimeError(
            f"{episode or '本集'} 一段分镜都没有，没东西可编。先把环节7 跑通。")

    def build_user(seg: dict) -> str:
        sid = seg["id"]
        used = set((binds.get(sid, {}).get("reference_images") or [])
                   and [r.get("asset_id") for r in binds[sid]["reference_images"]] or [])
        seg_assets = [a for a in assets if a["asset_id"] in used] or assets
        return render(tpl, {
            "EPISODE": episode,
            "DURATION": params.get("duration", 15),
            "TONE": jd({"compressed": tone.get("compressed", ""),
                        "variants": tone.get("compressed_variants", [])}),
            "SEGMENTS": jd(seg),
            "STATES": jd(states.get(sid, {})),
            "ASSETS": jd(seg_assets),
            "BINDINGS": jd(binds.get(sid, {})),
            "SHOTS": jd(shots.get(sid, {})),
        }) + f"\n\n【只编译这一段】{sid}，compiled 数组只放这一段。"

    def on_item(sid: str, c: dict) -> None:
        write_text(pj.p("03_提示词", "故事板提示词", f"{sid}_STORYBOARD_PROMPT.txt"),
                   c.get("storyboard_prompt", ""))
        write_text(pj.p("03_提示词", "视频提示词", f"{sid}_VIDEO_PROMPT.txt"),
                   c.get("video_prompt", ""))

    result, failed, cancelled = run_segmented(
        pj, stage_id="s8", out_name="s8_compile", key="compiled", segs=segs,
        done_ids=s8_done_segments(pj, episode), llm=llm, build_user=build_user,
        required=["compiled[]", "compiled[].storyboard_prompt", "compiled[].video_prompt"],
        log=log, episode=episode, cancel=cancel, seg_concurrency=seg_concurrency,
        on_item=on_item)
    build_tasks(pj, params)

    if cancelled and not failed:
        raise LLMCancelled(
            f"{episode or '本集'} 环节8 已按取消停下，编好的 {len(result['compiled'])} 段都存了盘；"
            f"再点一次「开始」只补没编的那些段。")
    if failed:
        raise RuntimeError(f"{episode or '本集'} 有 {len(failed)} 段编译失败：{'、'.join(failed[:8])}"
                           f"{'…' if len(failed) > 8 else ''}。"
                           f"已完成的 {len(result['compiled'])} 段已保存，重跑环节8只会补失败的这些段。")
    log(f"{episode or '本集'} 全部 {len(result['compiled'])} 段编译完成，tasks.json 已装配")
    return result


# ====================================================================== 任务装配

_CAT_DIR = {
    "identity": "人物身份资产", "environment": "场景资产", "prop": "道具资产",
    "state": "连续状态资产", "group": "群体资产", "creature": "生物资产",
}


def asset_output_rel(asset: dict) -> str:
    d = _CAT_DIR.get(asset.get("category", ""), "人物身份资产")
    return f"02_固定资产/{d}/{asset['asset_id']}.png"


def asset_layers(tasks: list) -> list:
    """把资产任务按参考图依赖分层：第 0 层没有参考图，第 N 层的参考图都在前面几层。

    为什么必须分层：状态资产 ST007 的参考图是 ST006，而任务是按环节4 的输出顺序
    排的、并发跑的。ST006 还在生成时 ST007 就被派出去，读不到 ST006.png 就直接
    失败（实测：ST007 重试两次全报「参考图文件不存在」）。
    分层之后层内并发、层间串行，父资产必然先出完，竞态从根上没了。

    成环（互相引用）时把剩下的全塞最后一层 —— 那是数据问题，不该让调度死循环。
    """
    by_key = {t["key"]: t for t in tasks}
    layers, placed = [], set()
    rest = list(tasks)
    while rest:
        cur = [t for t in rest
               if all(r.get("asset_id") in placed or r.get("asset_id") not in by_key
                      for r in (t.get("reference_images") or []))]
        if not cur:                       # 成环，别卡死
            layers.append(rest)
            break
        layers.append(cur)
        placed.update(t["key"] for t in cur)
        rest = [t for t in rest if t["key"] not in placed]
    return layers


def assets_used_by(pj: Project, episodes_wanted: list) -> set:
    """这几集实际用到哪些资产（含状态资产的父资产）。

    资产**表**是全剧的（skill：第 8 集才砸毁的房间也要在环节1 进演化图，
    否则资产库中途返工）。但**出图**没必要一上来就出全剧几十张 ——
    只出这几集用得到的，其余留着，跑到那几集时再出。
    编号和外观在表里已经定死，所以后面补图仍是同一张脸。
    """
    from . import episodes as _eps
    want_ep = set(episodes_wanted or [])
    all_assets, used = {}, set()
    for ep in (_eps.ids(pj) or [""]):
        for a in (pj.stage_data("s4_assets", ep) or {}).get("assets", []):
            all_assets.setdefault(a.get("asset_id"), a)
    if not all_assets:                          # 老单集项目
        for a in (pj.stage_data("s4_assets") or {}).get("assets", []):
            all_assets.setdefault(a.get("asset_id"), a)

    for aid, a in all_assets.items():
        segs = a.get("used_by_segs") or []
        if any(str(s).split("-")[0] in want_ep for s in segs):
            used.add(aid)
    # 状态资产要连父资产一起出，否则没有身份基准
    for aid in list(used):
        p = (all_assets.get(aid) or {}).get("parent_asset_id")
        while p and p in all_assets and p not in used:
            used.add(p)
            p = (all_assets.get(p) or {}).get("parent_asset_id")
    # 环节6 的绑定里出现过的也算（有些资产 used_by_segs 可能没填全）
    for ep in want_ep:
        for b in (pj.stage_data("s6_binding", ep) or {}).get("bindings", []):
            for r in (b.get("reference_images") or []):
                if r.get("asset_id") in all_assets:
                    used.add(r["asset_id"])
    return used


def build_tasks(pj: Project, params: dict) -> dict:
    """把 s4/s5/s6/s8 的产物装配成 tasks.json（执行器消费的机器可读清单）。

    一个项目可能有 40 集，所以要把所有集的产物合起来装配：
      · 资产任务按 asset_id 去重 —— 同一个角色跨集只出一张图，
        这既是省钱，更是跨集人脸一致的前提（输出路径不含集号，天生同一个文件）
      · 故事板/视频任务按段落 id（自带 EPxx 前缀）天然不冲突

    整段加锁：多集并行时每集跑完环节5/8 都会来重装一次，读-改-写不串行的话
    后写的那份会漏掉别的集刚存的产物，tasks.json 就少任务。
    """
    with _TASKS_LOCK:
        return _build_tasks(pj, params)


def _build_tasks(pj: Project, params: dict) -> dict:
    from . import episodes as _eps
    code = params.get("project_code", "PROJ-001")
    eps = _eps.ids(pj) or [params.get("episode", "EP01")]

    # ---- 资产：跨集合并去重 -------------------------------------------
    assets, amap, aprompts = [], {}, {}
    for ep in eps:
        for a in (pj.stage_data("s4_assets", ep) or {}).get("assets", []):
            aid = a.get("asset_id")
            if aid and aid not in amap:          # 先出现的定义优先，后面的不覆盖
                amap[aid] = a
                assets.append(a)
        for ap in (pj.stage_data("s5_asset_prompts", ep) or {}).get("asset_prompts", []):
            aprompts.setdefault(ap["asset_id"], ap)
    # 兼容老的单集项目：产物直接躺在项目根下，没有集子目录
    if not assets:
        assets = (pj.stage_data("s4_assets") or {}).get("assets", [])
        amap = {a["asset_id"]: a for a in assets}
        aprompts = {a["asset_id"]: a
                    for a in (pj.stage_data("s5_asset_prompts") or {}).get("asset_prompts", [])}

    asset_tasks = []
    for a in assets:
        if a.get("decision") == "skip" or a["asset_id"] not in aprompts:
            continue
        ap = aprompts[a["asset_id"]]
        asset_tasks.append({
            "key": a["asset_id"],
            # 哪几集用到它 —— 「只出第一集的资产图」靠这个字段过滤
            "episodes": sorted({str(s).split("-")[0]
                                for s in (a.get("used_by_segs") or [])
                                if str(s).startswith("EP")}),
            "prompt_ref": f"03_提示词/资产生产提示词/{ap.get('filename') or a['asset_id'] + '_PROMPT.txt'}",
            "reference_images": [
                {"image_n": i + 1, "asset_id": rid, "file_ref": asset_output_rel(amap[rid])}
                for i, rid in enumerate(ap.get("reference_assets", [])) if rid in amap
            ],
            "params": {"size": ap.get("size") or params.get("image_size", "1024x1536")},
            "output": asset_output_rel(a),
        })

    # ---- 故事板 / 视频：逐集展开 ---------------------------------------
    sb_tasks, vd_tasks = [], []
    for ep in eps:
        bindings = {b["id"]: b for b in (pj.stage_data("s6_binding", ep) or {}).get("bindings", [])}
        compiled = (pj.stage_data("s8_compile", ep) or {}).get("compiled", [])
        if not compiled and len(eps) == 1:       # 老单集项目
            bindings = {b["id"]: b for b in (pj.stage_data("s6_binding") or {}).get("bindings", [])}
            compiled = (pj.stage_data("s8_compile") or {}).get("compiled", [])
        for c in compiled:
            sid = c["id"]
            seg = sid.split("-")[-1]
            b = bindings.get(sid, {})
            refs = c.get("reference_order") or b.get("reference_images") or []
            sb_out = f"04_故事板/{code}_{ep}_{seg}_STORYBOARD_V01_FIXED.png"
            sb_tasks.append({
                "key": sid, "episode": ep,
                "prompt_ref": f"03_提示词/故事板提示词/{sid}_STORYBOARD_PROMPT.txt",
                "reference_images": [
                    {"image_n": r.get("image_n", i + 1), "asset_id": r.get("asset_id", ""),
                     "file_ref": asset_output_rel(amap[r["asset_id"]]) if r.get("asset_id") in amap else ""}
                    for i, r in enumerate(refs)
                ],
                # 不再往任务里塞 frames：格数写在提示词正文里（环节8 按分镜采样定的
                # 4-6 格），出图接口本来也没有「格数」这个参数
                "params": {"size": params.get("image_size", "1024x1536")},
                "output": sb_out,
            })
            aux = c.get("aux_reference_asset_id") or ""
            vd_tasks.append({
                "key": sid, "episode": ep,
                "prompt_ref": f"03_提示词/视频提示词/{sid}_VIDEO_PROMPT.txt",
                "storyboard_ref": sb_out,
                "aux_reference": asset_output_rel(amap[aux]) if aux in amap else None,
                "params": {"duration": params.get("duration", 15),
                           "ratio": params.get("ratio", "9:16")},
                "output": f"05_分段视频/{code}_{ep}_{seg}_VIDEO_V01_FIXED.mp4",
            })

    tasks = {"project_code": code, "episodes": eps, "episode": eps[0],
             "asset_tasks": asset_tasks, "storyboard_tasks": sb_tasks, "video_tasks": vd_tasks}
    pj.save_tasks(tasks)
    return tasks


# ====================================================================== 出图/出片 worker

def make_ref_resolver(pj: Project, prov, provider_cfg: dict, model: str,
                      ref_side: int, media: str = "image") -> Callable:
    """把参考图引用变成能发出去的形式。

    配了对象存储 → 一律传上去换公网链接。不只是为了那些只收 URL 的接口：
    能吃 data URI 的家，请求体也从几 MB 的 base64 缩成一行链接，快且稳。
    没配 → 转 data URI；碰上只收 URL 的模型就明确报错说去哪配。

    **例外必须按服务商的声明来，不能一刀切上传**（这是踩过的坑）：
      · needs_bytes 的家（multipart）→ 给本机路径
      · accepts_url 为假的家（把参考图内联进某字段、只认裸 base64）→ 给 data URI
    给错形式的后果不是报错，是**参考图被丢掉照样出图** —— 状态资产没了父资产
    参考，脸就不是本人，而且任务标 ok 没人知道。
    """
    up = provider_cfg.get("upload") or {}
    configured = uploader.configured(up) and up.get("mode", "always") != "when_required"
    need_url = prov.needs_url(model, media)
    need_bytes = prov.needs_bytes(model)
    can_url = prov.accepts_url(model, media)
    use_url = need_url or (configured and can_url)

    def resolve(src: str, log: Callable = print) -> str:
        src = (src or "").strip()
        if not src or src.startswith("http"):
            return src
        if need_bytes:
            # 本机绝对路径，provider 自己读字节塞 multipart
            return src if os.path.isabs(src) else os.path.join(pj.root, src)
        if not use_url:
            return resolve_ref(src, pj.root, max_side=ref_side)
        path = src if os.path.isabs(src) else os.path.join(pj.root, src)
        return uploader.to_url(path, up, project_root=pj.root,
                               max_side=ref_side, log=log)

    return resolve


def _ratio_warn(pj: Project, path: str, want: str, stage: str, key: str,
                provider_cfg: dict, model: str, media: str):
    """出完东西量一下真实尺寸，比例不对就挂一条提醒。

    这类问题接口不会报错——200、文件也正常下载，只是画面躺倒了。
    不主动量，就得等人工验收才发现，那时候钱早花完了。
    量不到（缺 ffprobe、格式不认识）就当没这回事，不能反过来卡住主流程。
    """
    try:
        bad = probe.check(path, want, kind=media)
    except Exception:                                  # noqa: BLE001
        return None
    if not bad:
        return None
    flip = ("你要的是竖屏，出来的是横屏。"
            if bad["portrait_wanted"] and not bad["portrait_got"] else
            "你要的是横屏，出来的是竖屏。"
            if not bad["portrait_wanted"] and bad["portrait_got"] else "")
    pj.log_event({"stage": stage, "id": key, "result": "ratio_mismatch", **bad})
    return diagnose.warn(
        "WRONG_RATIO",
        f"要的是 {bad['want']}，实际出来 {bad['got']}（约 {bad['got_ratio']}）。{flip}",
        stage=stage, target=key, provider=provider_cfg.get("provider", ""), model=model,
        extra_fix=[f"这个文件在：{pj.rel(path)}"])


def make_image_worker(pj: Project, provider_cfg: dict, kind: str) -> Callable:
    """kind: asset | storyboard。返回 worker(task, log, cancel)。"""
    prov = build_provider(provider_cfg["provider"], provider_cfg["api_key"],
                          provider_cfg.get("base_url", ""), provider_cfg.get("proxy", ""))
    model = provider_cfg.get("model", "")
    interval = int(provider_cfg.get("poll_interval", 5))
    timeout = int(provider_cfg.get("poll_timeout", 900))
    ref_side = int(provider_cfg.get("ref_max_side", 1024))
    to_ref = make_ref_resolver(pj, prov, provider_cfg, model, ref_side, media="image")

    def worker(task: dict, log: Callable, cancel: Callable) -> dict:
        out = pj.p(*task["output"].split("/"))
        want = (task.get("params") or {}).get("size", "1024x1536")
        if os.path.isfile(out):
            # 跳过也要重新量一遍：不然「比例不对」的提醒会被这次跳过悄悄清掉，
            # 而文件其实还是那个躺倒的文件。以磁盘上的东西为准。
            return {"skipped": True, "msg": "已经有了，跳过（做出来的就不再动）",
                    "warn": _ratio_warn(pj, out, want, kind, task["key"],
                                        provider_cfg, model, "image")}
        prompt = read_text(pj.p(*task["prompt_ref"].split("/")))
        refs = []
        for r in sorted(task.get("reference_images", []), key=lambda x: x.get("image_n", 0)):
            src = r.get("url") or r.get("file_ref") or ""
            if src:
                refs.append(to_ref(src, log))
        log(f"参考图×{len(refs)}")
        meta = prov.generate_image(
            ImageTask(prompt=prompt, refs=refs, size=want, model=model),
            out, log=log, cancel=cancel, poll_interval=interval, poll_timeout=timeout)
        pj.upsert_registry(kind, {"id": task["key"], "file_ref": task["output"],
                                  "status": "generated", **meta})
        pj.log_event({"stage": kind, "id": task["key"], "result": "ok", **meta})
        # 记一次出图。出图出片是按次计费的，钱主要花在这里，必须入账。
        ledger.record(pj.root, kind="image", stage=kind, target=task["key"],
                      episode=task.get("episode", ""),
                      provider=meta.get("provider", provider_cfg.get("provider", "")),
                      model=meta.get("model", model), count=1, size=want)
        return {"output": task["output"],
                "warn": _ratio_warn(pj, out, want, kind, task["key"],
                                    provider_cfg, model, "image")}

    return worker


def make_video_worker(pj: Project, provider_cfg: dict) -> Callable:
    prov = build_provider(provider_cfg["provider"], provider_cfg["api_key"],
                          provider_cfg.get("base_url", ""), provider_cfg.get("proxy", ""))
    model = provider_cfg.get("model", "")
    interval = int(provider_cfg.get("poll_interval", 10))
    timeout = int(provider_cfg.get("poll_timeout", 2400))
    ref_side = int(provider_cfg.get("ref_max_side", 1024))
    to_ref = make_ref_resolver(pj, prov, provider_cfg, model, ref_side, media="video")

    def worker(task: dict, log: Callable, cancel: Callable) -> dict:
        out = pj.p(*task["output"].split("/"))
        p = task.get("params") or {}
        want = p.get("ratio", "9:16")
        if os.path.isfile(out):
            # 同上：跳过时也重新量，别把「比例不对」的提醒清没了
            return {"skipped": True, "msg": "已经有了，跳过",
                    "warn": _ratio_warn(pj, out, want, "video", task["key"],
                                        provider_cfg, model, "video")}
        sb = task["storyboard_ref"]
        if not (sb.startswith("http") or os.path.isfile(pj.p(*sb.split("/")))):
            raise RuntimeError(f"固定故事板不存在，请先跑环节9: {sb}")
        prompt = read_text(pj.p(*task["prompt_ref"].split("/")))
        refs = [to_ref(sb, log)]
        if task.get("aux_reference"):
            refs.append(to_ref(task["aux_reference"], log))
        log(f"model={model} {p.get('duration', 15)}s {want} 参考图×{len(refs)}")
        meta = prov.generate_video(
            VideoTask(prompt=prompt, refs=refs, duration=int(p.get("duration", 15)),
                      ratio=want, model=model,
                      resolution=provider_cfg.get("resolution", "")),
            out, log=log, cancel=cancel, poll_interval=interval, poll_timeout=timeout)
        pj.upsert_registry("video", {"id": task["key"], "file_ref": task["output"],
                                     "storyboard_ref": sb, "status": "generated", **meta})
        pj.log_event({"stage": "video", "id": task["key"], "result": "ok", **meta})
        # 出片是最贵的一步，必须入账。带上时长——按秒计价的家要用
        ledger.record(pj.root, kind="video", stage="video", target=task["key"],
                      episode=task.get("episode", ""),
                      provider=meta.get("provider", provider_cfg.get("provider", "")),
                      model=meta.get("model", model), count=1,
                      duration=int(p.get("duration", 15)), ratio=want)
        return {"output": task["output"],
                "warn": _ratio_warn(pj, out, want, "video", task["key"],
                                    provider_cfg, model, "video")}

    return worker


# ====================================================================== 环节 11 / 12

def build_review_checklist(pj: Project, episode: str = "") -> dict:
    """环节11：产出人工复核清单。程序不判定内容质量，只把该看的点摆出来。

    不传 episode 就把全剧所有集的段落都列进来。
    """
    from . import episodes as _eps
    eps = [episode] if episode else (_eps.ids(pj) or [""])
    segs, states = [], {}
    for ep in eps:
        segs += (pj.stage_data("s2_segments", ep) or {}).get("segments", [])
        states.update({s["id"]: s
                       for s in (pj.stage_data("s3_states", ep) or {}).get("segment_states", [])})
    videos = {v.get("id"): v for v in pj.registry("video")}
    rows = []
    for s in segs:
        sid = s["id"]
        st = states.get(sid, {})
        rows.append({
            "segment_id": sid,
            "video": videos.get(sid, {}).get("file_ref", "（未生成）"),
            "check_layers": {
                "技术状态": "文件可播放、时长/画幅正确",
                "剧情完整性": s.get("story_task", ""),
                "人物身份一致性": "对照资产 identity_anchors",
                "状态连续性": f"进入={st.get('entry', '')} / 退出={st.get('exit', '')}",
                "动作因果": s.get("turn", ""),
                "空间轴线": "180度轴线、左右阵营、运动方向",
                "进入与退出状态": s.get("exit_state", ""),
                "跨段衔接": s.get("next_anchor", ""),
            },
            "forbidden_future": s.get("forbidden_future", []),
            "irreversible": st.get("irreversible", []),
            "verdict": "",           # 人工填：pass / L1 / L2 / L3
            "note": "",
        })
    out = {"episodes": eps,
           "levels": {
               "L1 可接受偏差": "背景人物少量变化、非核心褶皱、特效形态、轻微机位偏移 → 直接固定",
               "L2 可定向修订": "节奏过慢、动作方向错、道具短暂消失、局部口型、短暂变脸 → 视频提示词V02，只改出错时间区间",
               "L3 结构性错误": "身份错误、关键节点缺失、因果改变、不可逆状态被恢复、退出状态错误、提前剧透 → 按依赖链回溯重做"},
           "rows": rows}
    pj.save_stage("s11_review", out)
    return out


def health(pj: Project, episode: str = "") -> dict:
    """项目体检：每个环节做到哪、卡在哪、下一步该干什么。

    完全以磁盘为准 —— 重启服务、换电脑都能算出同样的结果。

    episode 给了就只看这一集；不给则看全剧：逐集环节的 total/done 是所有集的合计，
    这样 40 集的项目一眼能看出「环节2 做了 12/40 集」。
    """
    from . import episodes as _eps
    eps = _eps.ids(pj)
    scope_eps = [episode] if episode else (eps or [""])
    steps = []
    tasks = pj.tasks()

    def seg_count(ep):
        return len((pj.stage_data("s2_segments", ep) or {}).get("segments", []))

    for st in STAGES:
        item = {"id": st["id"], "no": st["no"], "name": st["name"], "kind": st["kind"],
                "out": st.get("out", ""),
                "done": 0, "total": 0, "state": "pending", "action": "",
                "per_episode": st["kind"] == "llm" and is_per_episode(st["id"])}
        if st["kind"] == "llm":
            if st["id"] in SERIES_STAGES:               # 全剧只跑一次
                ok = pj.stage_data(st["out"]) is not None
                item["total"], item["done"] = 1, (1 if ok else 0)
                if ok and eps:
                    item["note"] = f"识别出 {len(eps)} 集"
            elif st["id"] == "s8":                      # 按段算，跨集累加
                for ep in scope_eps:
                    item["total"] += seg_count(ep)
                    item["done"] += len(s8_done_segments(pj, ep))
                item["unit"] = "段"
            else:                                       # 按集算
                item["total"] = len(scope_eps)
                item["done"] = sum(1 for ep in scope_eps
                                   if pj.stage_data(st["out"], ep) is not None)
                item["unit"] = "集"
            item["action"] = "流程页 → 执行"
        elif st["kind"] in ("image", "video"):
            key = {"s5b": "asset_tasks", "s9": "storyboard_tasks", "s10": "video_tasks"}[st["id"]]
            items = [t for t in tasks.get(key, [])
                     if not episode or t.get("episode", episode) == episode]
            item["total"] = len(items)
            item["done"] = sum(1 for t in items
                               if os.path.isfile(pj.p(*t["output"].split("/"))))
            item["action"] = "生产页 → 开始"
        else:
            if st["id"] == "s11":
                item["total"] = 1
                item["done"] = 1 if pj.stage_data("s11_review") else 0
                item["action"] = "产物页 → 生成人工复核清单"
            else:
                # 一集一个成片：40 集就要 40 个，不能出了一个就算做完
                masters = []
                d = pj.p("06_成片")
                if os.path.isdir(d):
                    masters = [f for f in os.listdir(d) if f.lower().endswith(".mp4")]
                item["total"] = len(scope_eps)
                item["done"] = sum(1 for ep in scope_eps
                                   if any(f"_{ep}_MASTER" in m for m in masters)) \
                    if eps else (1 if masters else 0)
                item["unit"] = "集"
                item["action"] = "产物页 → 硬切拼接成片"

        if item["total"] == 0:
            item["state"] = "blocked" if st["kind"] != "llm" else "pending"
        elif item["done"] >= item["total"]:
            item["state"] = "done"
        elif item["done"] > 0:
            item["state"] = "partial"
        else:
            item["state"] = "pending"
        steps.append(item)

    fails = diagnose.summary(pj.root)
    nxt = next((s for s in steps if s["state"] in ("partial", "pending") and s["total"] > 0), None)
    if nxt is None:
        nxt = next((s for s in steps if s["state"] == "blocked"), None)

    # 「下一步做什么」永远要说出来。报错只是插在前面提醒你先处理，
    # 不能把下一步顶掉——否则用户处理完报错，又不知道接着干嘛了。
    if nxt:
        remain = nxt["total"] - nxt["done"]
        unit = nxt.get("unit", "")
        nxt_tip = (f"下一步：环节{nxt['no']}「{nxt['name']}」"
                   + (f"（还差 {remain}/{nxt['total']}{unit}）" if nxt["total"] > 1 else "")
                   + f" —— {nxt['action']}")
        # 逐集环节要说清该跑哪一集，不然 40 集面前不知道从哪下手
        if nxt.get("per_episode") and nxt.get("out") and len(scope_eps) > 1:
            pend = [ep for ep in scope_eps if pj.stage_data(nxt["out"], ep) is None]
            if pend:
                nxt_tip += f"，先跑 {pend[0]}（还差 {len(pend)} 集：{'、'.join(pend[:6])}"
                nxt_tip += "…）" if len(pend) > 6 else "）"
    else:
        nxt_tip = "所有环节都跑完了"

    if fails.get("errors"):
        tip = f"有 {fails['errors']} 条没做出来。先照下面的说明处理，再点「开始」补上。{nxt_tip}"
    elif fails.get("warns"):
        tip = f"{nxt_tip}（另外有 {fails['warns']} 条做出来了但可能不对，看下面，不着急处理也行）"
    else:
        tip = nxt_tip

    return {"steps": steps, "failures": fails, "next": nxt, "tip": tip,
            "resumable": True}


def find_ffmpeg() -> str:
    return probe.find_ffmpeg()          # 只保留一份查找逻辑，见 core/probe.py


def assemble(pj: Project, params: dict, log: Callable = print,
             episode: str = "") -> dict:
    """环节12：按段号排序、硬切拼接、生成成片。

    一部剧有 40 集就出 40 个成片，绝不能把所有集拼成一个文件。
    不指定 episode 时逐集拼接，返回 {"masters": [...]}。
    """
    from . import episodes as _eps
    eps = _eps.ids(pj)
    if not episode and len(eps) > 1:
        outs, failed = [], []
        for e in eps:
            try:
                outs.append(dict(assemble(pj, params, log, e), episode=e))
            except RuntimeError as exc:
                failed.append(f"{e}：{exc}")
                log(f"{e} 跳过 —— {exc}")
        if not outs:
            raise RuntimeError("没有任何一集能拼接。" + ("；".join(failed[:4]) if failed else ""))
        log(f"拼好 {len(outs)} 集" + (f"，{len(failed)} 集还不能拼" if failed else ""))
        return {"masters": outs, "count": sum(o["count"] for o in outs),
                "skipped": failed}

    code = params.get("project_code", "PROJ-001")
    ep = episode or (eps[0] if eps else params.get("episode", "EP01"))
    vids = sorted(
        (v for v in pj.registry("video")
         if v.get("file_ref") and (not episode or str(v.get("id", "")).startswith(f"{ep}-"))),
        key=lambda v: v.get("id", ""))
    exist = [v for v in vids if os.path.isfile(pj.p(*v["file_ref"].split("/")))]
    if not exist:
        raise RuntimeError(f"{ep} 没有可拼接的分段视频")
    lines = ["file '" + os.path.relpath(pj.p(*v["file_ref"].split("/")),
                                        pj.p("06_成片")).replace("\\", "/") + "'" for v in exist]
    concat = pj.p("06_成片", f"{ep}_concat.txt")
    os.makedirs(os.path.dirname(concat), exist_ok=True)
    with open(concat, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")

    master = pj.p("06_成片", f"{code}_{ep}_MASTER_V01_FIXED.mp4")
    ff = find_ffmpeg()
    if not ff:
        return {"concat": pj.rel(concat), "master": "", "count": len(exist),
                "msg": "未找到 ffmpeg，已生成 concat 清单，请手动拼接或 pip install imageio-ffmpeg"}
    cmd = [ff, "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", concat, "-c", "copy", master]
    log("流拷贝拼接…")
    # 走 probe.run_text：它显式给 utf-8，不用系统默认的 GBK。
    # 拼接可能跑很久（重编码几分钟），所以超时给足。
    r = probe.run_text(cmd, timeout=3600)
    if r.returncode != 0 or not os.path.isfile(master):
        log("流拷贝失败，重编码兜底")
        cmd = [ff, "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", concat,
               "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-c:a", "aac", master]
        r = probe.run_text(cmd, timeout=7200)
        if r.returncode != 0:
            raise RuntimeError(f"ffmpeg 拼接失败: {(r.stderr or '')[:300]}")
    size = os.path.getsize(master)
    pj.log_event({"stage": "assemble", "episode": ep, "result": "ok",
                  "count": len(exist), "size": size})
    return {"concat": pj.rel(concat), "master": pj.rel(master), "episode": ep,
            "count": len(exist), "size": size}
