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
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Optional

from . import diagnose, episodes as _eps, ledger, system_v34 as V
from .executor import LLM_GATE
from .llm import LLMCancelled
from .stages import jd, load_prompt, prompt_files, render, system_prompt
from .store import Project, keep_partial, write_text


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
        # DURATION 是**一个容器**的秒数（视频模型一次最多生成多久），
        # 不是这一集多长。这两个数字必须分开发 —— 合成一个的后果实跑撞过：
        # 第九环节是**整集级**的，它要为一整集设计镜头，而拿到的唯一秒数是 15，
        # 就把 8 个场次压成了 15 秒的「高密度蒙太奇」（模型自己在
        # shot_count_rationale 里写了「完整实时演出远超15秒」，它知道装不下）。
        # 然后第十环节照着 15 秒装出 1 个 SEG，第十二环节被迫在一张纸上画 16 格
        # （模板上限 3×3），模型扛不住 8 个场次的世界状态就开始瞎填 ——
        # 审计报的 7 条 BLOCK 里有 5 条是这一个故障的下游。
        "DURATION": params.get("duration", 15),
        "EPISODE_DURATION": _ep_seconds(pj, episode, params),
        "SEGMENTS_TARGET": _seg_target(pj, episode, params)[0],
        "SEGMENTS_WHY": _seg_target(pj, episode, params)[1],
        "IMAGE_SIZE": params.get("image_size", "1024x1536"),
        "SEG_COUNT": len(segments_of(pj, episode)) if episode else 0,
        "SCRIPT": _script_for(pj, stage_id, episode, params),
        # 能力档位必须进提示词，否则冻结了也白冻：第九环节会照着「六类机制
        # 随便挑」写，而模型做不出来 —— 转场糊掉或者变成一个长镜头，不报错。
        "CAPABILITY": _capability_block(pj),
        "REF_LIMIT": _ref_limit_block(params),
    }
    # 项目基础信息：50 多个字段，模板里写了 {{X}} 才收到。
    # 放在产物之前合并，让产物占位符能覆盖同名的（实际上不重名，
    # 但顺序写死比"应该不会撞"可靠）。
    from . import settings as _st
    m.update(_st.mapping(pj, params, {
        "episode_duration": _ep_seconds(pj, episode, params),
        "current_episode": episode,
        "reference_capacity_per_call": params.get("ref_limit", ""),
        "target_video_model": (capability_of(pj) or {}).get("target_video_model", ""),
        "native_multishot_support": (capability_of(pj) or {}).get(
            "native_multishot_support", ""),
    }))

    if stage_id == "n4b":
        data = dict(data, n4_assets=_n4b_worklist(pj, data.get("n4_assets") or {}))
    for out_name, obj in data.items():
        # 三层裁剪，各管一件事：
        #   按集   全剧级产物 → 只剩本集（叙事结构、总账）
        #   按段   逐集产物   → 只剩本段（装箱、场景状态图、故事板）
        #   按用途 这个环节真正要哪几部分（PRODUCT_NEEDS）
        if V.scope_of(stage_id) != "series":
            obj = _narrow_episode(out_name, obj, episode)
        if segment:
            obj = _narrow(out_name, obj, segment)
        # 逐段裁剪之后再按环节投影：两件事都是「只发这一步真正要的」，
        # 但一个按段切、一个按用途切，顺序无所谓，都做才完整。
        obj = project_product(stage_id, out_name, obj)
        m[V.placeholder_of(out_name)] = jd(obj)
    return m


def _ep_seconds(pj: Project, episode: str, params: dict) -> int:
    """这一集**总共**多长（秒）。不知道就返回 0。

    第一环节看完全篇按剧情事件定的，存在 episodes.json 的 duration_sec 里。
    V6.1 一直在用它算段数，V5.6 这边我搭 stage 图时漏搬了 —— 结果第九环节
    只看得见容器的 15 秒，把整集当成 15 秒来设计。

    返回 0（老项目的产物里没有这个字段）时模板会退回只按容器算，
    和以前的行为一致 —— 不至于让老项目跑不动。
    """
    if not episode:
        return 0
    for e in _eps.load(pj).get("episodes", []):
        if e.get("episode") == episode and e.get("duration_sec"):
            return int(e["duration_sec"])
    return 0


def _seg_target(pj: Project, episode: str, params: dict) -> tuple:
    """这一集该切几段，以及这个数是怎么来的。没有集号时返回 (0, "")。

    直接用 V6.1 那份 —— 算法是体系无关的（本集秒数 ÷ 单段秒数），
    照着重写一遍只会让两边慢慢飘开。
    """
    if not episode:
        return 0, ""
    return _eps.seg_target(pj, episode, params)


# 全剧级产物里，哪些数组是按集标了号的。逐集环节拿到之后要裁成只剩本集。
#
# 叙事结构、连续性总账这些改成全剧一份之后，逐集环节收到的是**全剧全量**。
# 不裁两个后果，都不报错：
#   · n7 导演拿到全剧的场次，会把别的集的戏排进这一集
#   · 40 集的总账发 40 遍，钱按集翻倍
# 空间主表和资产表**不裁** —— 它们本来就是全剧共享的参照，
# 裁掉会让跨集复用的地点和角色在本集"消失"，模型于是重新发明一个。
_EP_INDEXED = {
    "n3_narrative": [("scenes", "episode"), ("beats", "episode"),
                     ("episode_arcs", "episode")],
    "n6_ledger": [("ledger", "episode")],
}


def _narrow_episode(out_name: str, obj: dict, episode: str) -> dict:
    """把全剧级产物裁成只剩这一集的部分。

    只裁**明确标了集号**的那几个数组。标了集号才裁得准；
    没标的一律整份留着（宁可多发，也别悄悄少给下游一段上下文）。
    """
    if out_name not in _EP_INDEXED or not isinstance(obj, dict) or not episode:
        return obj
    out = dict(obj)
    for key, field in _EP_INDEXED[out_name]:
        rows = obj.get(key)
        if not isinstance(rows, list):
            continue
        mine = [r for r in rows if isinstance(r, dict) and r.get(field) == episode]
        # 一条都没标集号时不裁：那是模型没按 schema 写，
        # 裁完会得到空数组，下游看到"这一集没有任何场次"然后自己编。
        if mine or any(isinstance(r, dict) and r.get(field) for r in rows):
            out[key] = mine
    # beats 按 scene 归属，scene 已经裁过了，再按 scene_id 收一次更准
    if out_name == "n3_narrative":
        ids = {s.get("scene_id") for s in out.get("scenes") or []}
        beats = obj.get("beats")
        if ids and isinstance(beats, list):
            out["beats"] = [b for b in beats
                            if isinstance(b, dict) and b.get("scene_id") in ids]
    return out


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


def _drop_path(obj, path: str):
    """按 `a[].b` 这种路径去掉一处。原对象不动。

    只支持「顶层键」和「数组里每一项的某个键」两种形状 —— 够用了，
    再往下嵌套的话这张表就没人看得懂了。
    """
    if not isinstance(obj, dict):
        return obj
    head, _, rest = path.partition(".")
    if not rest:
        return {k: v for k, v in obj.items() if k != head.rstrip("[]")}
    key = head.rstrip("[]")
    if key not in obj:
        return obj
    rows = obj[key]
    if head.endswith("[]") and isinstance(rows, list):
        return dict(obj, **{key: [_drop_path(r, rest) for r in rows]})
    return dict(obj, **{key: _drop_path(rows, rest)})


def project_product(stage_id: str, out_name: str, obj):
    """把上游产物裁成这个环节真正需要的部分。

    见 system_v34.PRODUCT_NEEDS 里那段说明：不裁的代价是
    「同一份 2.8 万字发给 4 个下游、其中 3 个还是逐集的」，
    既把网关的输入上限试出来，又按集重复付钱。
    """
    spec = V.needs_of(stage_id, out_name)
    if not spec or not isinstance(obj, dict):
        return obj
    keep = spec.get("keep")
    if keep:
        return {k: obj[k] for k in keep if k in obj}
    for path in spec.get("drop") or []:
        obj = _drop_path(obj, path)
    return obj


def trim_saving(pj: Project, stage_id: str, episode: str = "") -> str:
    """这一步的按环节裁剪省了多少字。没裁就返回空。

    必须报出来。裁剪是「悄悄少发一部分输入」，裁错了模型不会报错、
    只会答得差一点 —— 那正是这个项目里最难查的一类问题。
    看得见省了多少，才有人会去核对裁得对不对。
    """
    parts = []
    for out_name, obj in deps_data(pj, stage_id, episode).items():
        if not V.needs_of(stage_id, out_name) or not obj:
            continue
        full, cut = len(jd(obj)), len(jd(project_product(stage_id, out_name, obj)))
        if full > cut:
            parts.append(f"{V.placeholder_of(out_name)} {full:,}→{cut:,} 字")
    if not parts:
        return ""
    return "按环节裁剪：" + "、".join(parts)


def _ref_limit_block(params: dict) -> str:
    """本次生产的模型一次能吃几张参考图。

    这个数服务商注册表里一直有（灵感鸭 sora-2 只收 1 张，坤鸡 9 张），
    但从来没告诉过模型 —— 于是 LLM 按剧情需要引 5、6 张，
    到出图那步才撞上限。V5.6 明确要求把它写进提示词，
    并且强调**这是容量上限，不是推荐装满的数量**。

    拿不到就明说未知，别编一个数：编大了会让它多引，编小了会让它漏掉
    真正需要的覆盖图。
    """
    lim = params.get("ref_limit")
    if not lim:
        return ("本次目标模型一次能吃几张参考图：未知。"
                "按最小充分集来，不确定就少传 —— 撞上限会直接失败，"
                "而多传一张互相打架不会报错，只会把画面搞坏。")
    return (f"本次目标模型一次最多接受 {lim} 张参考图。"
            f"这是**容量上限，不是推荐装满的数量** —— "
            f"按最小充分集挑，够用就停。"
            f"{'超过 5 张时必须逐张写清缺的是哪一项权威。' if lim > 5 else ''}")


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


def needs_script(stage_id: str, pj: Project = None) -> bool:
    """这个环节的模板里有没有 {{SCRIPT}}。看模板本身，不写死一张表。

    写死的话，有人在页面上给别的环节加了 {{SCRIPT}} 就查不到了。
    """
    tpl_name, _, _ = V.LLM_SPEC[stage_id]
    return "{{SCRIPT}}" in load_prompt(tpl_name, pj)


def check_inputs(pj: Project, stage_id: str, params: dict,
                 episode: str = "") -> None:
    """跑之前把这一步的输入验一遍，缺了就停，别拿空输入去调模型。

    实跑撞过：剧本没进去，提示词只剩模板（3246 字里 3177 字是模板本身），
    模型照着空输入吐了个 215 token 的骨架，缺 `characters[]`，
    于是 JSON 校验重试两次、三次调用全废。

    最糟的不是白花钱，是**报错指错了方向** —— 诊断给的是
    「换个更强的模型 / 剧本太长先拆开」，而真实情况正好相反：剧本是空的。
    照着那条建议去换模型，换十个也一样。

    `missing_deps` 查的是上游产物，剧本是**唯一没人查的输入**，这里补齐。
    """
    if not needs_script(stage_id, pj):
        return
    script = _script_for(pj, stage_id, episode, params)
    if script.strip():
        return
    where = "01_剧本与分段/原始剧本.txt" if V.scope_of(stage_id) == "series" \
        else f"{episode} 这一集的正文"
    raise RuntimeError(
        f"{stage_id} 要用剧本，但拿到的是空的（{where}）。"
        f"发出去的话提示词里只有模板、没有剧本，模型只能编一个空壳，"
        f"然后卡在「输出缺少必需字段」上白花三次调用 —— "
        f"而那个报错会让你以为是模型不行。"
        f"去「项目」页确认剧本正文在不在；"
        f"逐集环节的话看环节1 的切集结果有没有把这一集切空。")


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


def _n4b_worklist(pj: Project, a4: dict) -> dict:
    """n4b 的输入：**要写的留全量，其余压成一行目录。**

    不能直接把已写过的删掉 —— 模型引用别的资产要靠 ID（LOOK 引 PH、
    CT 引 LOOK）。看不到就会重新发明一个 ID，出图那层再报「查不到这个资产」。
    所以留着，但只留 id/family/name 三个字段，一条几十字节而不是八百字符。

    这一步只改**发过去的内容**，不改环节顺序、不改产物结构。
    """
    if not isinstance(a4, dict):
        return a4
    _, todo, _ = n4b_split(pj)
    keep = set(todo)
    full, catalog = [], []
    for a in a4.get("assets") or []:
        aid = a.get("asset_id")
        if aid in keep:
            full.append(a)
        elif aid:
            catalog.append({k: a.get(k) for k in ("asset_id", "family", "name")
                            if a.get(k)})
    return dict(a4, assets=full, assets_already_done=catalog)


# 这些档位**不出图、也不写生产提示词**。
#
# V6.1 把 `decision` 从三档拆细：逻辑对象要完整登记，但登记 ≠ 出图。
# 简单服装走文字契约（logical_only）、中间动作交给视频执行（defer_to_video）、
# 已有 Canon 沿用编号（existing_canonical）—— 这三类都不需要生产提示词。
#
# 代码不认这些档位的后果很具体：模板判出来了，程序照样给它们写提示词，
# 那一步本来就顶着输出上限，白写的部分直接把它推过线。
NO_IMAGE_DECISIONS = frozenset({
    "skip", "logical_only", "defer_to_video", "existing_canonical", "deferred",
})


def n4b_split(pj: Project, episode: str = "") -> tuple:
    """资产表分三份：已经写过的 / 这次要写的 / 根本不用写的。

    返回 (已写的 id 列表, 要写的 id 列表, 不用写的条数)。

    为什么必须增量：n4b 是全剧级的，一次要写全剧所有资产的完整提示词。
    实测 V6.1 逐集是 17 条 / 14,144 字符 ≈ 8,320 token（装得下），
    而全剧级 4 集就 33,280 token —— 超过本机实测的输出天花板 19,612。
    不增量的话，截断之后重跑写的是同一批东西，永远走不完。

    增量之后每一轮只补没写的，截断也在推进。**不改变环节顺序**：
    n4b 还是一个环节、还在原位、产物文件名不变。

    过滤依据用**已存产物里的 prompt**，不用磁盘上的 txt：
    txt 是 write_prompt_files 写的，而那个函数只在逐集环节跑完才调
    （n4b 是全剧级，episode=""），所以 n4b 刚跑完那一刻 txt 还不存在 ——
    拿它当依据会把刚写好的又判成没写。
    """
    assets = (pj.stage_data("n4_assets", "") or {}).get("assets") or []
    written = {ap.get("asset_id") for ap in
               (pj.stage_data("n4b_asset_prompts", "") or {}).get("asset_prompts") or []
               if ap.get("asset_id") and (ap.get("prompt") or "").strip()}
    done, todo, dropped = [], [], 0
    for a in assets:
        aid = a.get("asset_id")
        if not aid:
            continue
        # skill 第五章：只为**当前范围实际需要**且视觉差异有生产价值的状态
        # 建资产。这几档出图那一层本来就会丢掉（见 build_tasks），
        # 在这里写一遍纯属把 token 花在注定要扔的东西上 ——
        # 而这一步恰恰是最容易被截断的那一步。
        if a.get("decision") in NO_IMAGE_DECISIONS:
            dropped += 1
        elif aid in written:
            done.append(aid)
        else:
            todo.append(aid)
    return done, todo, dropped


def merge_asset_prompts(previous: dict, fresh: dict) -> dict:
    """按 asset_id 合并。增量跑必须合并，不能用一批补写覆盖整份。

    和 V6.1 的 merge_s5_outputs 是同一件事，逻辑照抄 —— 两套体系的
    资产提示词都是「全剧一份、分几次写完」，合并规则没有体系差异。
    """
    previous, fresh = previous or {}, fresh or {}
    merged = dict(previous)
    merged.update(fresh)
    rows, pos = [], {}
    for ap in previous.get("asset_prompts") or []:
        aid = ap.get("asset_id")
        if not aid or aid in pos:
            continue
        pos[aid] = len(rows)
        rows.append(ap)
    for ap in fresh.get("asset_prompts") or []:
        aid = ap.get("asset_id")
        if not aid:
            continue
        if aid in pos:
            rows[pos[aid]] = ap          # 重写覆盖旧的那一条
        else:
            pos[aid] = len(rows)
            rows.append(ap)
    merged["asset_prompts"] = rows
    return merged


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
    check_inputs(pj, stage_id, params, episode)

    if stage_id == "n4b":
        done, todo, dropped = n4b_split(pj, episode)
        if dropped:
            log(f"  {dropped} 个资产不需要出图（skip / 逻辑契约 / 交给视频 / "
                f"已有 Canon），不写提示词"
                f"（出图那一层本来也会丢掉，写了是白写）")
        if done:
            log(f"  {len(done)} 个资产已经写过提示词，这次不重写")
        if not todo:
            log("  没有新资产要写提示词，直接跳过（不调模型、不花钱）")
            prev = pj.stage_data("n4b_asset_prompts", "") or {"asset_prompts": []}
            diagnose.clear(pj.root, "stage:n4b", "全剧")
            return prev
        log(f"  这次要写 {len(todo)} 个：{'、'.join(todo[:8])}"
            f"{'…' if len(todo) > 8 else ''}")

    user = build_user(pj, stage_id, params, episode)
    tag = f"{episode} " if episode else "全剧 "
    log(f"{tag}{stage_id} 提示词 {len(user)} 字，调 {llm.model}")
    saved = trim_saving(pj, stage_id, episode)
    if saved:
        log(f"  {saved}")
    with LLM_GATE.slot():
        out = llm.json_call(system_prompt(pj, params), user, required=required,
                            log=log, cancel=cancel,
                            on_usage=_usage(pj, stage_id, episode),
                            on_partial=keep_partial(pj, stage_id, episode, llm=llm))
    check_runtime(pj, stage_id, out, params, episode, log)
    if stage_id == "n4b":
        # **必须合并，不能覆盖。** 这一步现在是增量的：输入只给还没写过的，
        # 所以模型回来的也只有那几条。直接存盘会把前几轮写好的全冲掉 ——
        # 而且不报错，只是资产提示词越跑越少。
        out = merge_asset_prompts(pj.stage_data("n4b_asset_prompts", "") or {}, out)
    pj.save_stage(tpl_name, out, "" if V.scope_of(stage_id) == "series" else episode)
    diagnose.clear(pj.root, f"stage:{stage_id}", episode or "全剧")
    if stage_id == "n1":
        _split_episodes(pj, out, params, log)
    return out


# 本集时长和实际排出来的时长，差多少算「压过头了」。
# 给 25% 的余量：镜头时长是估的，取整、转场占时、最后一镜收尾都会有出入。
# 但整集被压进一个容器时差的是好几倍，这个阈值抓得住，又不会天天误报。
_TIME_TOL = 0.75


def check_runtime(pj: Project, stage_id: str, out: dict, params: dict,
                  episode: str, log: Callable = print) -> None:
    """第九环节排出来的总时长，和这一集该有多长，对得上吗。

    这是「不报错、只是错」里最贵的一种，实跑撞过整轮：
    第九环节只拿得到容器的 15 秒，就把 8 个场次压成 15 秒的
    「高密度因果蒙太奇」—— 而且它**知道**装不下，自己在
    shot_count_rationale 里写了「完整实时演出远超15秒」，还是压了。

    压完之后一路不报错：第十环节照 15 秒装出 1 个 SEG，第十二环节
    在一张纸上画 16 格（模板上限 9 格），模型记不住 8 个场次的世界状态，
    于是所有格子的 source_scstate 全填第一个、道具状态和 CVS 互相打架。
    要到第十四环节审计才有人说话，而那时候已经跑了十几个环节。

    所以在这里就停 —— 时间对不上，往下每一步都在错的前提上工作。
    """
    if stage_id != "n9":
        return
    want = _ep_seconds(pj, episode, params)
    if not want:
        return                      # 老项目没存本集秒数，没得比
    got = _plan_seconds(out)
    if not got:
        return                      # 没给时间计划是另一回事，schema 那层管
    if got >= want * _TIME_TOL:
        return
    clip = int(params.get("duration") or 15) or 15
    raise RuntimeError(
        f"{episode} 第九环节把整集排成了 {got:g} 秒，"
        f"但这一集该有 {want} 秒（第一环节按剧情定的）—— 压掉了 "
        f"{100 - got * 100 / want:.0f}%。\n"
        f"最常见的原因是把 SEG 容器的 {clip} 秒当成了整集预算。"
        f"这两个数字不一样：{clip} 秒是视频模型一次最多生成多久，"
        f"{want} 秒是这一集多长。\n"
        f"压缩之后往下全线出错而且不报错 —— 第十环节只装得出 1 个 SEG，"
        f"故事板一张纸要画十几格，模型记不住那么多场次的世界状态，"
        f"就会把所有格子的场景状态全填成第一个。所以在这里停。")


def _plan_seconds(out: dict) -> float:
    """时间计划铺到了第几秒。取最大的 end —— 不假设它是按顺序写的。"""
    ends = [float(t["end"]) for t in (out or {}).get("timing_plan") or []
            if isinstance(t, dict) and isinstance(t.get("end"), (int, float))]
    return max(ends) if ends else 0.0


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
                    system_prompt(pj, params), user, required=required,
                    log=lambda m, _s=sid: log(f"    {_s}: {m}"), cancel=cancel,
                    on_usage=_usage(pj, stage_id, episode, sid),
                    on_partial=keep_partial(pj, stage_id, episode, sid, llm=llm))
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
    out["system"] = system_prompt(pj, params)
    out["user"] = user
    out["chars"] = len(user)
    out["tokens"] = rough_tokens(user)
    # 填不上的占位符会原样发给模型，模型看到大括号通常会假装那里有内容继续编
    out["unfilled"] = sorted(set(re.findall(r"\{\{(\w+)\}\}", user)))
    return out


# ---------------------------------------------------------------- 跨集共享资产

# 注：这里原来有一整套「跨集资产去重」—— known_asset_ids / assets_to_write /
# 资产认领表 / 进程锁。资产表和资产提示词改成全剧级之后**全部不需要了**：
# 一张表、一遍提示词，同一个角色天然只有一份定义。
# 它防的三个坑（重复编写、C001_PROMPT.txt 互相覆盖、并发抢同一个资产）
# 也随之消失 —— 那些坑本来就是「全剧级的活拆到逐集去做」造出来的。

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

    # ---- 资产：全剧一份，直接读 ----
    # 以前是「逐集产出、这里按 asset_id 合并去重」。资产表和资产提示词
    # 改成全剧级之后不需要合并了 —— 一张表、一遍提示词，同一个角色
    # 天然只有一份定义。跨集去重那套（认领表、known_asset_ids）连同
    # 它防的那些坑一起没了：重复编写、文件互相覆盖、并发抢同一个资产。
    amap = {a["asset_id"]: a
            for a in (pj.stage_data("n4_assets", "") or {}).get("assets", [])
            if a.get("asset_id")}
    prompts = {ap["asset_id"]: ap
               for ap in (pj.stage_data("n4b_asset_prompts", "") or {}).get(
                   "asset_prompts", []) if ap.get("asset_id")}

    from . import registry_v34 as REG
    REG.sync(pj, list(amap.values()))   # 先登记，版本号才查得到

    asset_tasks = []
    for aid, a in amap.items():
        if a.get("decision") in NO_IMAGE_DECISIONS or aid not in prompts:
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
    # 资产提示词是**全剧一份**的，不跟着集走。按集读的话，
    # 一是每集都会把同一批文件重写一遍，二是 40 集里只有一集读得到
    # （产物在项目根下），另外 39 集写 0 个文件而且不报错。
    for ap in (pj.stage_data("n4b_asset_prompts", "") or {}).get("asset_prompts", []):
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
