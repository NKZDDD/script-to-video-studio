# -*- coding: utf-8 -*-
"""V3.4 体系的执行层：把环节图和模板真正跑起来。

分工：
    system_v34.py   环节图（数据）
    prompts/n*.md   模板（数据）
    这里            按范围取依赖、填占位符、调模型、存盘
    produce.py      出图出片（体系无关）
    stages.py       V6.1 的执行层，和这里并存互不干扰

单独成文件而不是改 stages.py：两套体系的执行逻辑混在一个 1800 行的文件里，
改一处就要担心碰坏另一套。这里只写 V3.4 的，V6.1 一行不动。
"""

from __future__ import annotations

import itertools
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Optional

from . import diagnose, episodes as _eps, ledger, system_v34 as V
from .executor import LLM_GATE
from .llm import LLMCancelled
from .stages import jd, load_prompt, prompt_files, render
from .store import Project, write_text


# ---------------------------------------------------------------- 依赖与占位符

def deps_data(pj: Project, stage_id: str, episode: str = "") -> dict:
    """把这个环节声明的依赖产物读出来。

    按范围取：全剧级产物存在项目根下（episode=""），逐集/逐段的存在集目录里。
    取错目录的后果是拿到空字典 —— 模板里那个占位符变成 `{}`，
    模型看到空的输入通常会自己编，而不是报错。
    """
    _, deps, _ = V.LLM_SPEC[stage_id]
    out = {}
    for d in deps:
        src = next((s for s in V.STAGES if s.get("out") == d), None)
        ep = "" if (src or {}).get("scope") == "series" else episode
        out[d] = pj.stage_data(d, ep) or {}
    return out


def mapping(pj: Project, stage_id: str, params: dict, data: dict,
            episode: str = "", segment: str = "") -> dict:
    """模板里 {{X}} 各填什么。真跑和预览共用这一个，分开写迟早飘。"""
    m = {
        # 项目参数只发白名单里的几项。以前是「除了剧本全发」，结果配置里
        # 删掉的旧旋钮还留在 config.json 里照样发过去，模型当成指令执行。
        "PARAMS": jd({k: params[k] for k in
                      ("project_code", "duration", "ratio", "image_size")
                      if k in params}),
        "EPISODE": episode,
        "SEGMENT": segment,
        "DURATION": params.get("duration", 15),
        "IMAGE_SIZE": params.get("image_size", "1024x1536"),
        "SEG_COUNT": len(segments_of(pj, episode)) if episode else 0,
        "SCRIPT": _script_for(pj, stage_id, episode, params),
        # 能力档位必须进提示词，否则冻结了也白冻：第九环节会照着「六类机制
        # 随便挑」写，而模型做不出来 —— 转场糊掉或者变成一个长镜头，不报错。
        "CAPABILITY": _capability_block(pj),
    }
    for out_name, obj in data.items():
        if segment:
            obj = _narrow(out_name, obj, segment)
        m[V.placeholder_of(out_name)] = jd(obj)
    return m


# 逐段产物里，哪个数组按段索引、用哪个键认段号。
_SEG_INDEXED = {
    "n10_segs": ("segs", "seg_id"),
    "n11_scstate": ("scstates", "seg_id"),
    "n12_storyboard": ("sbpkg", "seg_id"),
    "n13_video": ("video_plan", "seg_id"),
}


def _narrow(out_name: str, obj: dict, segment: str) -> dict:
    """跑某一段时，把按段索引的产物裁成只剩这一段。

    不裁的话，做 SEG01 会把这一集全部段落的装箱、场景状态图、故事板都发过去：
    一集十几段就是十几倍的输入，钱翻几倍；更糟的是模型会串段 ——
    看到别的段的内容，把那边的动作写进这一段。

    只裁按段索引的那几份。整集共享的（资产表、空间主表、镜头）不裁 ——
    它们本来就是这一段要用的上下文。
    """
    if out_name not in _SEG_INDEXED or not isinstance(obj, dict):
        return obj
    key, id_field = _SEG_INDEXED[out_name]
    rows = obj.get(key)
    if not isinstance(rows, list):
        return obj
    mine = [r for r in rows if isinstance(r, dict) and r.get(id_field) == segment]
    return dict(obj, **{key: mine})


def _capability_block(pj: Project) -> str:
    """给模板看的能力说明。没冻结过就明说，不要装作有。"""
    cap = capability_of(pj)
    if not cap:
        return ("（还没冻结能力档位。按最保守的来：只用 NATIVE_CUT、"
                "完全遮挡、光学覆盖这三类转场。）")
    lv = cap.get("native_multishot_support", "UNKNOWN")
    hint = {"RELIABLE": "这个模型能可靠地一次生成多镜头，六类机制都可以用。",
            "LIMITED": "多镜头能力有限：优先完全遮挡、黑场闪光失焦、简单甩镜，"
                       "减少跨人物跨地点的直接混合。",
            "UNSUPPORTED": "这个模型做不了多镜头。用单镜头连续的机位和走位表达，"
                           "或者在同一次生成里用完整遮挡完成变化。",
            "UNKNOWN": "模型的多镜头能力未知 —— **不要假设它行**，按有限档来。"}[lv]
    return (f"目标视频模型：{cap.get('target_video_model') or '未指定'}\n"
            f"多镜头能力：{lv} —— {hint}\n"
            f"**允许使用的转场机制（只能从这几类里选）**："
            f"{'、'.join(cap.get('allowed_mechanisms') or ['NATIVE_CUT'])}\n"
            f"执行模式：{cap.get('transition_execution_mode', 'MODEL_NATIVE_ONLY')}"
            f"（外部剪辑和后期补转场一律禁止；模型做不出来就降到更稳的机制，"
            f"不许改用后期）")


def _script_for(pj: Project, stage_id: str, episode: str, params: dict) -> str:
    """环节1 吃整部剧本，逐集环节只吃本集正文。

    别给逐集环节发全剧剧本：40 集的本子发 40 遍，钱翻几十倍，
    而且模型会拿别的集的情节来填这一集。
    """
    if V.scope_of(stage_id) == "series":
        return params.get("script", "")
    if not episode:
        return ""
    try:
        return _eps.script_of(pj, episode)
    except Exception:                                   # noqa: BLE001
        return ""


def segments_of(pj: Project, episode: str) -> list:
    """这一集有哪些段。段是第十环节装箱装出来的，不是按秒数除出来的。"""
    segs = (pj.stage_data("n10_segs", episode) or {}).get("segs", [])
    return [s for s in segs if s.get("seg_id")]


def missing_deps(pj: Project, stage_id: str, episode: str = "") -> list:
    """跑之前先看依赖齐没齐，返回还缺哪几个环节（给人看的名字）。"""
    _, deps, _ = V.LLM_SPEC[stage_id]
    by_out = {s["out"]: s for s in V.STAGES if s.get("out")}
    data = deps_data(pj, stage_id, episode)
    miss = []
    for d in deps:
        if not data.get(d):
            s = by_out.get(d, {})
            miss.append(f"第{s.get('no', '?')}环节「{s.get('name', d)}」")
    return miss


# ---------------------------------------------------------------- 跑一个环节

def build_user(pj: Project, stage_id: str, params: dict,
               episode: str = "", segment: str = "") -> str:
    """这个环节这一次实际会发出去的正文。"""
    tpl_name, _, _ = V.LLM_SPEC[stage_id]
    data = deps_data(pj, stage_id, episode)
    text = render(load_prompt(tpl_name, pj),
                  mapping(pj, stage_id, params, data, episode, segment))
    if segment:
        text += (f"\n\n【只做这一段】{segment}，"
                 f"输出数组里只放这一段，不要带上别的段。")
    return text


def run_stage(pj: Project, stage_id: str, *, llm, params: dict,
              episode: str = "", log: Callable = print,
              cancel: Optional[Callable] = None) -> dict:
    """跑一个全剧级或逐集级的 LLM 环节。逐段的走 run_segment_stage。"""
    if V.scope_of(stage_id) == "segment":
        raise ValueError(f"{stage_id} 是逐段环节，该走 run_segment_stage")
    tpl_name, _, required = V.LLM_SPEC[stage_id]
    miss = missing_deps(pj, stage_id, episode)
    if miss:
        raise RuntimeError(f"{stage_id} 的前置还没跑：{'、'.join(miss)}")

    user = build_user(pj, stage_id, params, episode)
    tag = f"{episode} " if episode else "全剧 "
    log(f"{tag}{stage_id} 提示词 {len(user)} 字，调 {llm.model}")
    with LLM_GATE.slot():
        out = llm.json_call(load_prompt("_common", pj), user, required=required,
                            log=log, cancel=cancel,
                            on_usage=_usage(pj, stage_id, episode),
                            on_partial=keep_partial(pj, stage_id, episode))
    pj.save_stage(tpl_name, out, "" if V.scope_of(stage_id) == "series" else episode)
    diagnose.clear(pj.root, f"stage:{stage_id}", episode or "全剧")
    if stage_id == "n1":
        _split_episodes(pj, out, params, log)
    return out


def keep_partial(pj: Project, stage_id: str, episode: str = "",
                 segment: str = "") -> Callable:
    """返回一个「把即将丢弃的模型输出存下来」的回调。

    为什么必须存：断流和 JSON 校验不过时，收到的内容原本是直接丢掉的。
    结果是你只知道「收到 9091 字然后断了」，但不知道断在第几个字段、
    模型是不是正在写某个超长数组、还是根本跑偏了 ——
    而那是排「老是断在中途」唯一有用的证据。一次断三遍就是丢三份。

    存在 07_检查与记录/失败原文/ 下，文件名带环节和段号，同一次跑多次失败
    各存一份（带序号），不互相覆盖。
    """
    seq = itertools.count(1)

    def save(text: str, why: str) -> None:
        who = "_".join(x for x in (stage_id, episode, segment) if x)
        name = f"{who}_{next(seq):02d}.txt"
        path = pj.p("07_检查与记录", "失败原文", name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        head = (f"环节 {stage_id}　{episode or '全剧'}"
                f"{('　' + segment) if segment else ''}\n"
                f"原因：{why}\n"
                f"收到 {len(text)} 字\n"
                + "-" * 60 + "\n")
        write_text(path, head + text)

    return save


def _split_episodes(pj: Project, out: dict, params: dict, log: Callable) -> None:
    """环节1 一跑完立刻切集 —— 边界由它判断，切割由代码按锚点做。"""
    res = _eps.build(pj, params.get("script", ""), out)
    eps = res.get("episodes", [])
    log(f"识别出 {len(eps)} 集")
    for e in eps[:60]:
        log(f"  {e['episode']}  {e['chars']:>6} 字  {(e.get('title') or '')[:30]}")
    for it in res.get("issues", []):
        log(f"  {'⚠️' if it.get('level') == 'warn' else '❌'} {it['episode']}：{it['reason']}")


def run_segment_stage(pj: Project, stage_id: str, *, llm, params: dict,
                      episode: str, log: Callable = print,
                      cancel: Optional[Callable] = None,
                      seg_concurrency: int = 1,
                      on_item: Optional[Callable] = None) -> tuple:
    """逐段跑：一段一次调用、每段存盘、一段失败不毒掉整集、天然可续跑。

    整集一次调用的问题是输出太长（一集十几段的故事板包就几十万字节），
    中途失败整批白跑。段与段之间没有数据依赖，拆开是安全的。
    """
    if V.scope_of(stage_id) != "segment":
        raise ValueError(f"{stage_id} 不是逐段环节")
    tpl_name, _, required = V.LLM_SPEC[stage_id]
    miss = missing_deps(pj, stage_id, episode)
    if miss:
        raise RuntimeError(f"{stage_id} 的前置还没跑：{'、'.join(miss)}")

    segs = segments_of(pj, episode)
    if not segs:
        raise RuntimeError(f"{episode} 还没有段落。先把第十环节跑通。")
    key = _result_key(stage_id)
    prev = pj.stage_data(tpl_name, episode) or {key: []}
    by_id = {c.get("seg_id") or c.get("id"): c for c in prev.get(key, [])
             if c.get("seg_id") or c.get("id")}
    todo = [s for s in segs if s["seg_id"] not in by_id]
    log(f"{episode} {stage_id} 共 {len(segs)} 段，已完成 {len(by_id)} 段，"
        f"本次做 {len(todo)} 段")

    failed: list = []
    cancelled: list = []
    lock = threading.Lock()
    n = len(todo)

    def one(i: int, seg: dict) -> None:
        if (cancel and cancel()) or cancelled:
            return
        sid = seg["seg_id"]
        log(f"[{i}/{n}] {sid}")
        try:
            user = build_user(pj, stage_id, params, episode, sid)
            with LLM_GATE.slot():
                out = llm.json_call(
                    load_prompt("_common", pj), user, required=required,
                    log=lambda m, _s=sid: log(f"    {_s}: {m}"), cancel=cancel,
                    on_usage=_usage(pj, stage_id, episode, sid),
                    on_partial=keep_partial(pj, stage_id, episode, sid))
            item = (out.get(key) or [{}])[0]
            item["seg_id"] = sid
            if on_item:
                on_item(sid, item)
            # 每段都存盘：中途中断不丢已完成的。并发下读-改-写必须串行，
            # 否则两段同时保存，后写的会把先写的挤掉。
            with lock:
                by_id[sid] = item
                pj.save_stage(tpl_name, {key: _ordered(by_id, segs)}, episode)
            diagnose.clear(pj.root, f"stage:{stage_id}", sid)
        except LLMCancelled:
            with lock:
                cancelled.append(sid)
        except Exception as exc:                        # noqa: BLE001
            d = diagnose.build(exc, stage=f"stage:{stage_id}", target=sid,
                               model=getattr(llm, "model", ""))
            diagnose.record(pj.root, d)
            with lock:
                failed.append(sid)
            log(f"    {diagnose.one_line(d)}")

    workers = max(1, min(int(seg_concurrency or 1), n or 1))
    if workers > 1 and n > 1:
        log(f"{episode} {stage_id} 段内并发 {workers}")
        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(lambda a: one(*a), list(enumerate(todo, 1))))
    else:
        for a in enumerate(todo, 1):
            one(*a)

    result = {key: _ordered(by_id, segs)}
    pj.save_stage(tpl_name, result, episode)
    return result, failed, cancelled


def _ordered(by_id: dict, segs: list) -> list:
    return [by_id[s["seg_id"]] for s in segs if s["seg_id"] in by_id]


def _result_key(stage_id: str) -> str:
    """逐段环节的结果数组叫什么。跟模板 schema 里的顶层键保持一致。"""
    return {"n11": "scstates", "n12": "sbpkg", "n13": "video_plan"}[stage_id]


def done_segments(pj: Project, stage_id: str, episode: str) -> set:
    """这一集哪几段已经做完了 —— 以磁盘为准，重启也认。"""
    tpl_name, _, _ = V.LLM_SPEC[stage_id]
    key = _result_key(stage_id)
    got = (pj.stage_data(tpl_name, episode) or {}).get(key, [])
    return {c.get("seg_id") for c in got if c.get("seg_id")}


# ---------------------------------------------------------------- 第 0 章：能力冻结

# 目标视频模型一次能不能出多镜头。分四档，决定后面允许用哪些转场机制。
# 不冻结的话，第九环节会照着「六类机制随便挑」写，而模型做不出来 ——
# 表现是转场糊掉或者干脆变成一个长镜头，不报错。
CAPABILITY = ("RELIABLE", "LIMITED", "UNSUPPORTED", "UNKNOWN")

# 已知支持一次生成多镜头的模型。认不出的一律 UNKNOWN，按 LIMITED 策略走 ——
# 不假设模型有多镜头能力，是这一层的默认立场。
_MULTISHOT = {
    "seedance-2.5": "RELIABLE",     # 鹤 Seedance 2.5：29 秒 / 30 图，实测能多镜头
}


def detect_capability(model: str) -> str:
    m = (model or "").lower()
    for frag, level in _MULTISHOT.items():
        if frag in m:
            return level
    return "UNKNOWN"


def freeze_capability(pj: Project, params: dict, video_model: str = "",
                      log: Callable = print) -> dict:
    """第 0 章：把这次生产的执行模式和模型能力档位冻结进项目。

    冻结而不是每次现算：中途换模型会让前后两段用不同的转场策略，
    接起来就是断的。要换模型就显式改这份配置，别让它随手漂。
    """
    meta = pj.meta() or {}
    frozen = dict(meta.get("capability") or {})
    level = frozen.get("native_multishot_support") or detect_capability(video_model)
    cap = {
        "target_video_model": video_model or frozen.get("target_video_model", ""),
        "seg_duration": int(params.get("duration", 15)),
        "aspect_ratio": params.get("ratio", "9:16"),
        "transition_execution_mode": "MODEL_NATIVE_ONLY",
        "external_transition_editing": "FORBIDDEN",
        "external_shot_assembly": "FORBIDDEN",
        "native_multishot_support": level,
        "allowed_mechanisms": allowed_mechanisms(level),
        "frozen_at": frozen.get("frozen_at") or _now(),
    }
    pj.save_meta(dict(meta, capability=cap))
    log(f"能力冻结：{cap['target_video_model'] or '（未指定模型）'} → "
        f"多镜头 {level}；允许的转场机制 {len(cap['allowed_mechanisms'])} 类")
    if level in ("UNSUPPORTED", "UNKNOWN"):
        log("  这一档只用最稳的几类转场。要放开，去项目参数里把 "
            "native_multishot_support 改成 RELIABLE —— 但先拿一段试出来再改。")
    return cap


def allowed_mechanisms(level: str) -> list:
    """按能力档位决定允许哪些原生转场机制。

    降级只往「更稳」的方向走，绝不往「改用外部剪辑」走 ——
    那是项目配置层面的事，不能在这里静默切换。
    """
    cut = ["NATIVE_CUT"]
    safe = cut + ["SHIELDED_OCCLUSION", "OPTICAL_COVER"]
    return {
        "RELIABLE": safe + ["MOTION_BRIDGE", "NATIVE_DISSOLVE",
                            "VFX_THREAD_TRANSITION"],
        "LIMITED": safe,
        # 不支持多镜头：只能一个镜头连续拍，或者在同一次生成里用完整遮挡
        "UNSUPPORTED": cut,
        "UNKNOWN": safe,        # 不假设它行，按 LIMITED 策略
    }[level if level in CAPABILITY else "UNKNOWN"]


def capability_of(pj: Project) -> dict:
    return (pj.meta() or {}).get("capability") or {}


def _now() -> str:
    import time
    return time.strftime("%Y-%m-%d %H:%M:%S")


def preview_prompt(pj: Project, stage_id: str, params: dict,
                   episode: str = "", segment: str = "") -> dict:
    """跑之前先看看这一步到底会发出去什么。**不调模型、不写盘。**

    刻意和真跑共用同一个 build_user —— 分开写迟早飘，那样预览就成了安慰剂。
    返回的形状和 V6.1 那套一致，前端不用分两套渲染。
    """
    from .llm import rough_tokens
    if stage_id not in V.LLM_SPEC:
        raise ValueError(f"环节 {stage_id} 不是 LLM 环节，没有提示词可预览")
    tpl_name, _, required = V.LLM_SPEC[stage_id]
    scope = V.scope_of(stage_id)
    out = {"stage": stage_id, "template": tpl_name, "episode": "", "segment": "",
           "segments": [], "required_fields": required, "missing": [], "note": "",
           "layers": list(prompt_files(tpl_name, pj)), "system": "", "user": "",
           "chars": 0, "tokens": 0, "unfilled": []}

    if scope != "series":
        avail = _eps.ids(pj)
        if not avail:
            out["missing"] = ["第1环节「源解析与故事真相」（还没切集）"]
            return out
        episode = episode if episode in avail else avail[0]
    else:
        episode = ""
    out["episode"] = episode

    miss = missing_deps(pj, stage_id, episode)
    if miss:
        out["missing"] = miss
        return out

    if scope == "segment":
        segs = [s["seg_id"] for s in segments_of(pj, episode)]
        out["segments"] = segs
        if not segs:
            out["missing"] = ["第10环节「SEG 包装」（段落表是空的）"]
            return out
        segment = segment if segment in segs else segs[0]
        out["segment"] = segment
        out["note"] = (f"这一集共 {len(segs)} 段，每段一次调用；"
                       f"下面是 {segment} 这一段的。")
    else:
        segment = ""

    user = build_user(pj, stage_id, params, episode, segment)
    out["system"] = load_prompt("_common", pj)
    out["user"] = user
    out["chars"] = len(user)
    out["tokens"] = rough_tokens(user)
    # 填不上的占位符会原样发给模型，模型看到大括号通常会假装那里有内容继续编
    out["unfilled"] = sorted(set(re.findall(r"\{\{(\w+)\}\}", user)))
    return out


# ---------------------------------------------------------------- 跨集共享资产

def known_asset_ids(pj: Project, upto_episode: str = "") -> set:
    """前面几集已经写过提示词的资产。

    资产库全剧共享：同一个角色只出一张图，跨集人脸才一致。所以第二集起
    只写「本集新出现的」—— 不过滤的话 40 集会把同一个角色的提示词重写 40 遍，
    白花钱，而且每次重写都可能写飘一点。
    """
    ids = set()
    for ep in _eps.ids(pj):
        if upto_episode and ep == upto_episode:
            break
        for ap in (pj.stage_data("n4b_asset_prompts", ep) or {}).get("asset_prompts", []):
            if ap.get("asset_id"):
                ids.add(ap["asset_id"])
    return ids


def assets_to_write(pj: Project, episode: str) -> tuple:
    """这一集要写提示词的资产，和跳过的。喂给 n4b 的【待写清单】。"""
    assets = (pj.stage_data("n4_assets", episode) or {}).get("assets", [])
    done = known_asset_ids(pj, episode)
    todo, skipped = [], []
    for a in assets:
        if a.get("decision") == "skip":
            continue
        (skipped if a.get("asset_id") in done else todo).append(a)
    return todo, skipped


# ---------------------------------------------------------------- 任务装配

def _rel(kind: str, name: str) -> str:
    return {"asset": f"03_提示词/资产生产提示词/{name}",
            "scstate": f"03_提示词/场景状态提示词/{name}",
            "storyboard": f"03_提示词/故事板提示词/{name}",
            "video": f"03_提示词/视频提示词/{name}"}[kind]


def _asset_out(a: dict, revision: int = 1) -> str:
    """资产图落在哪。按家族分目录，人看文件夹时能对上。

    文件名带版本号：内容要改时建新文件而不是原地覆盖 —— 已经引用过它的
    故事板还指着旧那张，覆盖了就查不出「当时用的是哪一版人脸」。
    """
    sub = {"CHAR": "人物身份资产", "PH": "人物身份资产", "LOOK": "人物造型资产",
           "CT": "连续状态资产", "COST": "服饰资产", "LOC": "场景资产",
           "PROP_SPEC": "道具资产", "PROP_INSTANCE": "道具资产",
           "VEH": "载具资产", "CRE": "生物资产", "GRP": "群体资产",
           "VFX": "特效资产"}.get(a.get("family", ""), "其它资产")
    return f"02_固定资产/{sub}/{a['asset_id']}_R{int(revision):02d}.png"


def asset_out(pj: Project, a: dict) -> str:
    """这个资产**当前版本**的图在哪。"""
    from . import registry_v34 as REG
    return _asset_out(a, REG.current_revision(pj, str(a.get("asset_id") or "")))


def build_tasks(pj: Project, params: dict) -> dict:
    """把各环节的产物装配成 tasks.json —— 出图出片那一层唯一读的东西。

    四类任务。相对 V6.1 多出来的是 scstate 那一类：故事板不再直接拿一堆
    原子资产当参考，而是先合成一张场景状态图再参考它。

    资产是全剧共享的，所以要把所有集的产物合起来装配，按 asset_id 去重；
    故事板和视频逐集逐段展开。

    参考图里认不出的 ID **留在列表里、file_ref 留空**，不要悄悄删掉 ——
    删了数量看着是对的，反而看不出少了一张，而出图那一层会因为
    「声明了几张就必须解析出几张」停下来并报清楚缺哪张。
    """
    code = params.get("project_code", "PROJ-001")
    eps = _eps.ids(pj) or [params.get("episode", "EP01")]
    size = params.get("image_size", "1024x1536")

    # ---- 资产：全剧合并，先出现的定义优先 ----
    amap, prompts = {}, {}
    for ep in eps:
        for a in (pj.stage_data("n4_assets", ep) or {}).get("assets", []):
            if a.get("asset_id") and a["asset_id"] not in amap:
                amap[a["asset_id"]] = a
        for ap in (pj.stage_data("n4b_asset_prompts", ep) or {}).get("asset_prompts", []):
            prompts.setdefault(ap.get("asset_id"), ap)

    from . import registry_v34 as REG
    REG.sync(pj, list(amap.values()))   # 先登记，版本号才查得到

    asset_tasks = []
    for aid, a in amap.items():
        if a.get("decision") == "skip" or aid not in prompts:
            continue
        ap = prompts[aid]
        asset_tasks.append({
            "key": aid,
            "episodes": sorted({str(s).split("-")[0]
                                for s in (a.get("used_by_segs") or [])
                                if str(s).startswith("EP")}),
            "prompt_ref": _rel("asset", ap.get("filename") or f"{aid}_PROMPT.txt"),
            "reference_images": [
                {"image_n": i + 1, "asset_id": rid,
                 "file_ref": asset_out(pj, amap[rid]) if rid in amap else ""}
                for i, rid in enumerate(ap.get("reference_assets") or [])
            ],
            "params": {"size": ap.get("size") or size},
            "output": asset_out(pj, a),
        })

    # ---- 场景状态图 / 故事板 / 视频：逐集逐段 ----
    scstate_tasks, sb_tasks, vd_tasks = [], [], []
    for ep in eps:
        for sc in (pj.stage_data("n11_scstate", ep) or {}).get("scstates", []):
            sid = sc.get("scstate_id")
            # 场景状态图按**状态**去重，不按段。V3.4 里 SCSTATE 编号不含段号：
            # 同一场戏跨几段而世界状态没变时，本来就该复用同一张。
            # 不去重的话同一张图付几次钱，而且几条任务写同一个文件、
            # 后一条覆盖前一条 —— 不报错，只是白花钱。
            if not sid or any(x["key"] == sid for x in scstate_tasks):
                continue
            scstate_tasks.append({
                "key": sid, "episode": ep, "segment": sc.get("seg_id", ""),
                "prompt_ref": _rel("scstate", f"{sid}_PROMPT.txt"),
                "reference_images": [
                    {"image_n": i + 1, "asset_id": rid,
                     "file_ref": asset_out(pj, amap[rid]) if rid in amap else ""}
                    for i, rid in enumerate(sc.get("reference_assets") or [])
                ],
                "params": {"size": size},
                "output": f"03b_场景状态图/{code}_{sid}.png",
            })

        scst_out = {t["key"]: t["output"] for t in scstate_tasks}
        for pkg in (pj.stage_data("n12_storyboard", ep) or {}).get("sbpkg", []):
            seg = pkg.get("seg_id")
            if not seg:
                continue
            sb_out = f"04_故事板/{code}_{seg}_STORYBOARD.png"
            sb_tasks.append({
                "key": seg, "episode": ep, "segment": seg,
                "prompt_ref": _rel("storyboard", f"{seg}_STORYBOARD_PROMPT.txt"),
                "reference_images": [
                    {"image_n": r.get("image_n", i + 1),
                     "asset_id": r.get("asset_id", ""),
                     "file_ref": scst_out.get(r.get("asset_id"))
                     or (asset_out(pj, amap[r["asset_id"]])
                         if r.get("asset_id") in amap else "")}
                    for i, r in enumerate(pkg.get("reference_order") or [])
                ],
                "params": {"size": size},
                "output": sb_out,
            })

        sb_by_seg = {t["key"]: t["output"] for t in sb_tasks}
        for vp in (pj.stage_data("n13_video", ep) or {}).get("video_plan", []):
            seg = vp.get("seg_id")
            if not seg:
                continue
            vd_tasks.append({
                "key": seg, "episode": ep, "segment": seg,
                "prompt_ref": _rel("video", f"{seg}_VIDEO_PROMPT.txt"),
                "storyboard_ref": sb_by_seg.get(seg, ""),
                # 视频的补充参考图（首次显露覆盖用），认不出就留空
                "reference_images": [
                    {"image_n": r.get("image_n", i + 1),
                     "asset_id": r.get("asset_id", ""),
                     "file_ref": asset_out(pj, amap[r["asset_id"]])
                     if r.get("asset_id") in amap else ""}
                    for i, r in enumerate(vp.get("reference_order") or [])
                    if r.get("asset_id") in amap
                ],
                "params": {"duration": params.get("duration", 15),
                           "ratio": params.get("ratio", "9:16")},
                "output": f"05_分段视频/{code}_{seg}.mp4",
            })

    tasks = {"system": "v34", "project_code": code, "episodes": eps,
             "asset_tasks": asset_tasks, "scstate_tasks": scstate_tasks,
             "storyboard_tasks": sb_tasks, "video_tasks": vd_tasks}
    pj.save_tasks(tasks)
    return tasks


def write_prompt_files(pj: Project, episode: str) -> int:
    """把各环节写好的提示词正文落成 txt —— 出图那一层是按路径读文件的。

    落盘而不是塞进 tasks.json：人要能在页面上直接改这一条，
    改完立刻生效不用重跑文字环节。
    """
    from .produce import write_prompt_txt
    n = 0
    for ap in (pj.stage_data("n4b_asset_prompts", episode) or {}).get("asset_prompts", []):
        if ap.get("prompt"):
            write_prompt_txt(pj, _rel("asset", ap.get("filename")
                                      or f"{ap['asset_id']}_PROMPT.txt"), ap["prompt"])
            n += 1
    for sc in (pj.stage_data("n11_scstate", episode) or {}).get("scstates", []):
        if sc.get("prompt") and sc.get("scstate_id"):
            write_prompt_txt(pj, _rel("scstate", f"{sc['scstate_id']}_PROMPT.txt"),
                             sc["prompt"])
            n += 1
    for pkg in (pj.stage_data("n12_storyboard", episode) or {}).get("sbpkg", []):
        if pkg.get("storyboard_prompt") and pkg.get("seg_id"):
            write_prompt_txt(pj, _rel("storyboard",
                                      f"{pkg['seg_id']}_STORYBOARD_PROMPT.txt"),
                             pkg["storyboard_prompt"])
            n += 1
    for vp in (pj.stage_data("n13_video", episode) or {}).get("video_plan", []):
        if vp.get("video_prompt") and vp.get("seg_id"):
            write_prompt_txt(pj, _rel("video", f"{vp['seg_id']}_VIDEO_PROMPT.txt"),
                             vp["video_prompt"])
            n += 1
    return n


# ---------------------------------------------------------------- 交付

def build_review_checklist(pj: Project, episode: str = "") -> dict:
    """人工复核清单：程序不判定内容好坏，只把该看的点摆出来。

    和第十四环节的审计是两件事：审计查**结构性矛盾**（程序和模型能查的），
    这里列的是**只有人能判**的（脸像不像、演得对不对、转场看着糊不糊）。
    """
    eps = [episode] if episode else (_eps.ids(pj) or [""])
    rows = []
    for ep in eps:
        segs = segments_of(pj, ep)
        plans = {v.get("seg_id"): v for v in
                 (pj.stage_data("n13_video", ep) or {}).get("video_plan", [])}
        cvs = {c.get("cvs_id"): c for c in
               (pj.stage_data("n8_cvs", ep) or {}).get("cvs", [])}
        vids = {v.get("id"): v for v in pj.registry("video")}
        audit = {f.get("affected_segs") and f["affected_segs"][0]: f
                 for f in (pj.stage_data("n14_audit", ep) or {}).get("findings", [])}
        for s in segs:
            sid = s["seg_id"]
            plan = plans.get(sid) or {}
            exit_cvs = cvs.get(s.get("exit_cvs")) or {}
            rows.append({
                "episode": ep, "segment_id": sid,
                "video": vids.get(sid, {}).get("file_ref", "（未生成）"),
                "check_layers": {
                    "技术状态": "文件能播、时长和画幅对",
                    "人物身份一致性": "对照资产的 identity_anchors，脸有没有变",
                    "当前状态连续性": f"进入={s.get('entry_cvs', '')} / "
                                      f"退出={s.get('exit_cvs', '')}",
                    "转场执行": "、".join(s.get("model_native_transition_ids") or [])
                                or "（这一段没有转场）",
                    "转场是不是模型一次出的": "有没有看起来像后期拼的痕迹",
                    "空间与走位": "人物位置、朝向、与固定物的关系有没有跳",
                    "首次显露": "镜头拉远/转身时，下半身、背面、鞋有没有变样",
                    "动作没有重演": "签字、跌倒、拔针这类完成过的动作有没有又来一次",
                },
                "forbidden_future": exit_cvs.get("forbidden_state", []),
                "transition_failures": [
                    w.get("failure_signature", "")
                    for w in (plan.get("transition_windows") or [])
                    if w.get("failure_signature")],
                "audit_finding": audit.get(sid, {}).get("what", ""),
                "verdict": "",      # 人工填：pass / L1 / L2 / L3
                "note": "",
            })
    out = {"system": "v34", "episodes": eps,
           "levels": {
               "L1 可接受偏差": "背景人物少量变化、非核心褶皱、轻微机位偏移 → 直接固定",
               "L2 可定向修订": "节奏、动作方向、道具短暂消失、局部口型、转场略糊 → "
                                "只改出错的那个时间窗口，重出这一段",
               "L3 结构性错误": "身份错误、关键节点缺失、因果改变、不可逆状态被恢复、"
                                "提前剧透、转场跨段 → 按依赖链回溯重做"},
           "rows": rows}
    pj.save_stage("d1_review", out, episode)
    return out


def assemble(pj: Project, params: dict, log: Callable = print,
             episode: str = "") -> dict:
    """拼接成片。拼接本身（排序、concat、流拷贝失败退回重编码）是体系无关的，
    直接复用，只把成片文件名换成这套体系的。"""
    from .stages import assemble as _assemble
    return _assemble(pj, params, log, episode,
                     master_name=lambda code, ep: f"{code}_{ep}_MASTER.mp4")


def _usage(pj: Project, stage_id: str, episode: str, target: str = "") -> Callable:
    def rec(u: dict) -> None:
        ledger.record(pj.root, kind="llm", stage=stage_id, episode=episode,
                      target=target, provider="llm", model=u.get("model", ""),
                      prompt_tokens=u.get("prompt_tokens") or 0,
                      cached_tokens=(u.get("prompt_tokens_details") or {})
                      .get("cached_tokens", 0),
                      completion_tokens=u.get("completion_tokens") or 0,
                      seconds=u.get("seconds") or 0,
                      estimated=bool(u.get("estimated")))
    return rec
