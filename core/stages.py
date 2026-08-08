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
import re
import shutil
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Optional

from . import diagnose, ledger, paths, probe, uploader
from .executor import LLM_GATE
from .llm import LLM, LLMCancelled, rough_tokens
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


def project_prompt_dir(pj: Project) -> str:
    """这一部剧自己的提示词模板目录。

    为什么要分两层：全局那份管的是「这套体系怎么做事」，而每一部剧有自己的
    语言（印尼语剧本 vs 中文）、题材、基础设定 —— 这些只该影响这一部，
    不该改全局。所以项目级放项目目录里，跟着项目走、跟着项目备份。
    """
    return pj.p("00_项目说明", "提示词模板")


def prompt_files(name: str, pj: Optional[Project] = None) -> tuple:
    """(内置, 全局改写, 本剧改写)。后面的盖前面的。

    改写不放程序目录：内置模板打包进 exe 之后是只读的，而且程序更新会覆盖它。
    全局改写在数据目录，项目改写在项目目录。
    """
    return (os.path.join(PROMPT_DIR, f"{name}.md"),
            os.path.join(paths.prompts_dir(), f"{name}.md"),
            os.path.join(project_prompt_dir(pj), f"{name}.md") if pj else "")


def load_prompt(name: str, pj: Optional[Project] = None) -> str:
    """取生效的模板：本剧改写 > 全局改写 > 内置。"""
    builtin, glob, proj = prompt_files(name, pj)
    for p in (proj, glob):
        if p and os.path.isfile(p):
            return read_text(p)
    return read_text(builtin)


def stage_prompt(stage_id: str, template_name: str,
                 pj: Optional[Project] = None) -> str:
    """业务模板原文 + 程序传输层。环节4的 TXT 保持逐字不改。"""
    text = load_prompt(template_name, pj)
    if stage_id == "s4":
        adapter = read_text(os.path.join(PROMPT_DIR, "s4_assets_adapter.md"))
        return text + "\n\n" + adapter
    return text


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
    "s4": ("s4_assets", ["s1_global", "s2_segments", "s3_states"], [
        "assets[]", "assets[].asset_id", "assets[].category", "assets[].asset_type",
        "assets[].name", "assets[].asset_level", "assets[].decision",
        "assets[].decision_reason", "assets[].first_seg", "assets[].used_by_segs",
        "assets[].parent_asset_id", "assets[].reference_assets",
        "assets[].space_master_id", "assets[].space_region_id",
        "assets[].identity_anchors", "assets[].appearance", "assets[].fixed_content",
        "assets[].story_function", "assets[].state_changes", "assets[].allowed_change",
        "assets[].forbidden_change", "assets[].output_spec", "assets[].dependency_order",
        "space_masters", "identity_asset_ids", "group_asset_ids", "space_master_ids",
        "environment_asset_ids", "vehicle_and_prop_asset_ids", "state_asset_ids",
        "dynamic_elements", "reuse_relations", "parent_state_dependency_chains",
        "space_continuity_chains", "character_space_bindings",
        "must_produce_asset_ids", "conditional_asset_ids",
        "skipped", "production_order", "output_register",
    ]),
    "s5": ("s5_asset_prompts", ["s1_global", "s4_assets"], ["asset_prompts[]", "asset_prompts[].prompt"]),
    "s6": ("s6_binding", ["s2_segments", "s3_states", "s4_assets"], [
        "bindings[]", "bindings[].character_space_context",
    ]),
    "s7": ("s7_shots", ["s2_segments", "s3_states", "s6_binding"], [
        "shots[]", "shots[].character_space_note", "shots[].shot_list[].positions",
    ]),
    "s8": ("s8_compile", ["s1_global", "s2_segments", "s3_states", "s4_assets", "s6_binding", "s7_shots"],
           ["compiled[]", "compiled[].storyboard_prompt", "compiled[].video_prompt"]),
}

# 一个项目 = 一部剧。环节1 吃整部剧本、只跑一次；环节2 往后逐集跑。
# 之所以只有环节1 是全剧级：跨集要统一的东西（人物长相、视觉基调、伏笔、
# 不可更改事实）都在它的产物里，往下每集都引用同一份，人物才不会换脸。
SERIES_STAGES = {"s1"}

# {{PARAMS}} 里发给模型的，只有这几个 —— 白名单不是黑名单。
#
# 以前是「除了 script 全发」，出过两次事：
#   · script 没剔时等于把 8 万字剧本发两遍，环节1 从 72K token 涨到 141K，
#     一半是重复内容，钱翻倍、首字延迟翻倍，也是那次超时的主因
#   · 后来删掉的那批旧旋钮（shots_min/max、frames、episode_minutes）还留在
#     config.json 里，照样被转储进去 —— 模型会把它们当成指令，跟模板里
#     「镜头数由你按信息密度定 5-8」直接打架
# 黑名单只挡得住想得到的，白名单挡得住想不到的。加了新配置项默认不外泄。
_PROMPT_PARAMS = ("project_code", "episode", "duration", "ratio", "image_size")


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
    """把已经建好的资产汇总起来，喂给环节4 让它沿用编号。

    资产库全剧共享：同一个角色在 EP01 和 EP07 必须是同一个 asset_id，
    否则会各出一张脸。这里按集顺序累加，先出现的定义优先（后面的不许改写）。

    **本集上一次建的也算**（放在最后，优先级最低）。不然重跑环节4 等于从零
    重编：上一轮的 ST007 是「衣服湿透」，这一轮可能变成「街道积水」——
    编号一洗牌，已经出好的资产图、故事板引用全部对不上号，等于白花的钱。
    重跑通常是为了修内容，不是为了换编号。
    """
    from . import episodes as _eps
    order = _eps.ids(pj)
    if upto_episode:
        # 本集之前的集（优先级高）+ 本集自己上一次的产物（垫最后，优先级最低）
        cut = order.index(upto_episode) if upto_episode in order else len(order)
        order = order[:cut] + [upto_episode]
    out, seen = [], set()
    for ep in order:
        for a in (pj.stage_data("s4_assets", ep) or {}).get("assets", []):
            aid = a.get("asset_id")
            if not aid or aid in seen:
                continue
            seen.add(aid)
            item = {k: a.get(k, "") for k in
                    ("asset_id", "category", "asset_type", "name",
                     "parent_asset_id", "identity_anchors", "appearance",
                     "allowed_change", "forbidden_change", "output_spec",
                     "space_master_id", "space_region_id")}
            item["reference_assets"] = _ordered_asset_refs(
                [a.get("parent_asset_id")], a.get("reference_assets") or [])
            out.append(item)
    return out


def known_space_masters(pj: Project, upto_episode: str = "") -> list:
    """空间母资产全剧复用；同一编号取最近一集记录的完整版本。"""
    from . import episodes as _eps
    order = _eps.ids(pj)
    if upto_episode:
        cut = order.index(upto_episode) if upto_episode in order else len(order)
        order = order[:cut] + [upto_episode]
    out, pos = [], {}
    for ep in order:
        for space in (pj.stage_data("s4_assets", ep) or {}).get("space_masters", []):
            sid = str(space.get("space_id") or "")
            if not sid:
                continue
            item = json.loads(json.dumps(space, ensure_ascii=False))
            if sid in pos:
                out[pos[sid]] = item
            else:
                pos[sid] = len(out)
                out.append(item)
    return out


def _dep_data(pj: Project, deps: list, episode: str) -> dict:
    """取前置产物：全剧级的从项目根取，逐集的从本集目录取。"""
    out = {}
    for d in deps:
        owner = _STAGE_OF_OUT.get(d, {}).get("id", "")
        out[d] = pj.stage_data(d, "" if owner in SERIES_STAGES else episode)
    return out


def _mapping(pj: Project, stage_id: str, params: dict, data: dict,
             episode: str, script: str) -> dict:
    """整集级环节（s1-s6）往模板里填的那张表。真跑和预览共用。"""
    from . import episodes as _eps
    tone = ((data.get("s1_global") or {}).get("visual_tone") or {})
    slim = {k: params[k] for k in _PROMPT_PARAMS if k in params}
    slim["episode"] = episode or params.get("episode", "")
    seg_n, seg_why = (_eps.seg_target(pj, episode, params)
                      if is_per_episode(stage_id) and episode else (0, ""))
    return {
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
        "KNOWN_SPACES": jd(known_space_masters(pj, episode) if stage_id == "s4" else []),
        # 环节5 的【待生产资产】会被 _s5_filter 裁成只剩本次要写的几条；
        # 另给一份完整目录，否则模型根本不知道同画面的旧资产 ID，只能退化成一张来源图。
        "ASSET_CATALOG": jd(known_assets(pj, episode) if stage_id == "s5" else []),
        # 整集共用的 180 度轴线约定（环节3 定的）。老项目的产物里没有，
        # 那就是空的 —— 环节7 会自己定轴，和以前一样。
        "AXIS": jd((data.get("s3_states") or {}).get("axis_convention") or {}),
    }


def _s5_filter(pj: Project, data: dict, claim: bool = True,
               force: bool = False) -> tuple:
    """环节5 只给「还没写过提示词的资产」。返回 (裁过的 s4, 要写的, 跳过的)。

    资产提示词全剧共用一份文件，不过滤的话 40 集会把同一个角色的提示词重写
    40 遍：白花钱，还可能越写越飘。

    claim=False 用于**预览**：预览不能真去「领走」资产，否则看一眼就把它占了，
    真跑的时候反而以为别人在写、跳过不写。

    force=True 只用于用户明确点「重跑」环节5：已有 txt 也要重新编译，
    否则提示词规则更新后，界面虽然显示重跑成功，磁盘上仍会一直是旧结果。
    """
    a4 = dict(data.get("s4_assets") or {})
    free, skipped = [], []
    for a in a4.get("assets", []):
        aid = a.get("asset_id", "")
        f = pj.p("03_提示词", "资产生产提示词", f"{aid}_PROMPT.txt")
        (free if force or not os.path.isfile(f) else skipped).append(aid)
    # 多集并行时，别的集可能刚好也在写这几个资产的提示词 —— 领得到才算自己的
    todo = _claim(free) if claim and not force else list(free)
    skipped += [i for i in free if i not in set(todo)]
    a4["assets"] = [a for a in a4.get("assets", []) if a.get("asset_id") in set(todo)]
    return a4, todo, skipped


def _ordered_asset_refs(*groups, exclude: str = "") -> list:
    """按首次出现顺序合并资产 ID。"""
    out, seen = [], set()
    for group in groups:
        if isinstance(group, str):
            group = [group]
        for raw in (group or []):
            rid = str(raw or "").strip()
            if not rid or rid == exclude or rid in seen:
                continue
            seen.add(rid)
            out.append(rid)
    return out


def normalize_s4_asset_refs(out: dict) -> dict:
    """按TXT规则规范父资产、连续性状态资产和完整依赖列表。"""
    for a in out.get("assets") or []:
        aid = str(a.get("asset_id") or "")
        if a.get("category") == "state":
            refs = _ordered_asset_refs(a.get("reference_assets") or [], exclude=aid)
            parent = str(a.get("parent_asset_id") or "").strip()
            if not parent and refs:
                parent = refs[0]
            a["parent_asset_id"] = parent
            a.pop("state_type", None)
            a["output_spec"] = "state_asset"
            a["reference_assets"] = _ordered_asset_refs(
                [parent], refs, exclude=aid)
        else:
            a["parent_asset_id"] = ""
            a.pop("state_type", None)
            a["reference_assets"] = _ordered_asset_refs(
                a.get("reference_assets") or [], exclude=aid)
    return out


_S4_TOP_LISTS = (
    "space_masters", "identity_asset_ids", "group_asset_ids", "space_master_ids",
    "environment_asset_ids", "vehicle_and_prop_asset_ids", "state_asset_ids",
    "dynamic_elements", "reuse_relations", "parent_state_dependency_chains",
    "space_continuity_chains", "character_space_bindings",
    "must_produce_asset_ids", "conditional_asset_ids",
    "skipped", "production_order",
)
_S4_ASSET_STRINGS = (
    "asset_id", "category", "asset_type", "name", "asset_level", "decision",
    "decision_reason", "first_seg", "parent_asset_id", "space_master_id",
    "space_region_id", "identity_anchors", "appearance", "story_function",
    "allowed_change", "forbidden_change", "output_spec",
)
_S4_ASSET_LISTS = ("used_by_segs", "reference_assets", "fixed_content", "state_changes")
_S4_REGISTER_COUNTS = (
    "identity_count", "group_count", "space_master_count", "environment_count",
    "vehicle_count", "prop_count", "state_count", "must_count", "conditional_count",
    "skip_count",
)
_S4_REGISTER_LISTS = (
    "high_risk_assets", "high_risk_spaces", "cross_seg_spaces", "irreversible_states",
)


def validate_s4_output(out: Any, episode: str = "", known_asset_ids=None,
                       known_space_ids=None) -> list:
    """环节4保存前的完整结构校验；返回可直接反馈给模型的问题列表。

    通用 required 只能判断“键在不在”。这里继续检查类型、状态资产条件字段、
    引用先后和空间母资产内部结构，避免不完整结果被当成成功保存。
    """
    if not isinstance(out, dict):
        return ["顶层必须是JSON对象"]
    problems = []
    assets = out.get("assets")
    if not isinstance(assets, list) or not assets:
        problems.append("assets必须是非空数组")
        assets = []
    for key in _S4_TOP_LISTS:
        if key not in out:
            problems.append(f"顶层缺少{key}")
        elif not isinstance(out[key], list):
            problems.append(f"{key}必须是数组")
    if "output_register" not in out:
        problems.append("顶层缺少output_register")
    elif not isinstance(out["output_register"], dict):
        problems.append("output_register必须是对象")

    local = {}
    for i, asset in enumerate(assets):
        if not isinstance(asset, dict):
            problems.append(f"assets[{i}]必须是对象")
            continue
        aid = str(asset.get("asset_id") or f"assets[{i}]")
        if aid in local:
            problems.append(f"asset_id重复:{aid}")
        else:
            local[aid] = asset
        for key in _S4_ASSET_STRINGS:
            if key not in asset:
                problems.append(f"{aid}缺少{key}")
            elif not isinstance(asset[key], str):
                problems.append(f"{aid}.{key}必须是字符串")
        for key in _S4_ASSET_LISTS:
            if key not in asset:
                problems.append(f"{aid}缺少{key}")
            elif not isinstance(asset[key], list):
                problems.append(f"{aid}.{key}必须是数组")
        order = asset.get("dependency_order")
        if "dependency_order" not in asset:
            problems.append(f"{aid}缺少dependency_order")
        elif isinstance(order, bool) or not isinstance(order, int) or order < 1:
            problems.append(f"{aid}.dependency_order必须是正整数")

        category = asset.get("category")
        if category not in {"identity", "group", "creature", "environment",
                            "prop", "state", "dynamic"}:
            problems.append(f"{aid}.category取值无效:{category}")
        decision = asset.get("decision")
        if decision not in {"must", "conditional", "skip"}:
            problems.append(f"{aid}.decision取值无效:{decision}")
        spec = asset.get("output_spec")
        if spec not in {"four_view", "scene_wide", "prop_multi", "closeup",
                        "state_asset"}:
            problems.append(f"{aid}.output_spec取值无效:{spec}")
        if not str(asset.get("name") or "").strip():
            problems.append(f"{aid}.name不能为空")
        if not isinstance(asset.get("used_by_segs"), list) or not asset.get("used_by_segs"):
            problems.append(f"{aid}.used_by_segs不能为空")

        parent = str(asset.get("parent_asset_id") or "")
        refs = asset.get("reference_assets") if isinstance(asset.get("reference_assets"), list) else []
        refs = [str(x) for x in refs]
        if category == "state":
            if not parent:
                problems.append(f"{aid}状态资产缺少父资产")
            if not refs or refs[0] != parent:
                problems.append(f"{aid}.reference_assets第一项必须是父资产{parent or 'ID'}")
            if spec != "state_asset":
                problems.append(f"{aid}状态资产必须使用state_asset")
        elif parent or refs:
            problems.append(f"{aid}基础资产的父资产和reference_assets必须为空")

    all_ids = set(local) | set(known_asset_ids or [])
    for aid, asset in local.items():
        refs = [str(x) for x in (asset.get("reference_assets") or [])]
        unknown = [x for x in refs if x not in all_ids]
        if unknown:
            problems.append(f"{aid}引用不存在资产:{','.join(unknown)}")
        cur_order = asset.get("dependency_order")
        if isinstance(cur_order, int) and not isinstance(cur_order, bool):
            for rid in refs:
                dep_order = (local.get(rid) or {}).get("dependency_order")
                if isinstance(dep_order, int) and dep_order >= cur_order:
                    problems.append(f"{aid}引用{rid}但dependency_order没有严格后置")

    cycles = asset_dependency_cycles(assets)
    if cycles:
        problems.append("资产循环依赖:" + "；".join("↔".join(x) for x in cycles))

    spaces = out.get("space_masters") if isinstance(out.get("space_masters"), list) else []
    known_spaces = set(known_space_ids or [])
    current_spaces = set()
    space_regions = {}
    space_fields = {
        "space_id": str, "name": str, "decision": str, "used_by_segs": list,
        "regions": list, "connections": list, "fixed_directions": list,
        "main_entries": list, "main_exits": list, "fixed_large_objects": list,
        "movement_paths": list, "forbidden_changes": list,
    }
    for i, space in enumerate(spaces):
        if not isinstance(space, dict):
            problems.append(f"space_masters[{i}]必须是对象")
            continue
        sid = str(space.get("space_id") or f"space_masters[{i}]")
        if sid in current_spaces:
            problems.append(f"space_id重复:{sid}")
        current_spaces.add(sid)
        for key, typ in space_fields.items():
            if key not in space:
                problems.append(f"{sid}缺少{key}")
            elif not isinstance(space[key], typ):
                problems.append(f"{sid}.{key}类型错误")
        if not isinstance(space.get("regions"), list) or not space.get("regions"):
            problems.append(f"{sid}.regions不能为空")
        region_ids = set()
        for j, region in enumerate(space.get("regions") or []):
            if not isinstance(region, dict):
                problems.append(f"{sid}.regions[{j}]必须是对象")
                continue
            for key in ("region_id", "name", "environment_asset_id", "fixed_features"):
                if key not in region:
                    problems.append(f"{sid}.regions[{j}]缺少{key}")
            region_id = str(region.get("region_id") or "").strip()
            if region_id:
                region_ids.add(region_id)
        space_regions[sid] = region_ids

    valid_spaces = current_spaces | known_spaces
    for aid, asset in local.items():
        sid = str(asset.get("space_master_id") or "")
        if sid and sid not in valid_spaces:
            problems.append(f"{aid}引用不存在空间母资产:{sid}")

    bindings = (out.get("character_space_bindings")
                if isinstance(out.get("character_space_bindings"), list) else [])
    binding_strings = (
        "character_asset_id", "character_name", "continuity_state_asset_id",
        "seg_id", "space_master_id", "space_region_id", "position", "facing",
        "exit_state", "inherit_to_seg", "inheritance_rule", "change_trigger",
    )
    binding_lists = ("fixed_object_relations", "relative_character_positions")
    binding_keys = set()
    for i, binding in enumerate(bindings):
        label = f"character_space_bindings[{i}]"
        if not isinstance(binding, dict):
            problems.append(f"{label}必须是对象")
            continue
        for key in binding_strings:
            if key not in binding:
                problems.append(f"{label}缺少{key}")
            elif not isinstance(binding[key], str):
                problems.append(f"{label}.{key}必须是字符串")
        for key in binding_lists:
            if key not in binding:
                problems.append(f"{label}缺少{key}")
            elif not isinstance(binding[key], list):
                problems.append(f"{label}.{key}必须是数组")

        character_id = str(binding.get("character_asset_id") or "").strip()
        state_id = str(binding.get("continuity_state_asset_id") or "").strip()
        seg_id = str(binding.get("seg_id") or "").strip()
        sid = str(binding.get("space_master_id") or "").strip()
        region_id = str(binding.get("space_region_id") or "").strip()
        inherit_to = str(binding.get("inherit_to_seg") or "").strip()
        for key in ("character_asset_id", "character_name", "seg_id",
                    "space_master_id", "space_region_id", "position", "facing",
                    "exit_state"):
            if not str(binding.get(key) or "").strip():
                problems.append(f"{label}.{key}不能为空")
        if character_id and character_id not in all_ids:
            problems.append(f"{label}引用不存在人物资产:{character_id}")
        if character_id in local and local[character_id].get("category") != "identity":
            problems.append(f"{label}.character_asset_id必须指向人物身份资产")
        if state_id and state_id not in all_ids:
            problems.append(f"{label}引用不存在连续性状态资产:{state_id}")
        if state_id in local and local[state_id].get("category") != "state":
            problems.append(f"{label}.continuity_state_asset_id必须指向状态资产")
        if sid and sid not in valid_spaces:
            problems.append(f"{label}引用不存在空间母资产:{sid}")
        if sid in current_spaces and region_id and region_id not in space_regions.get(sid, set()):
            problems.append(f"{label}引用不存在空间区域:{sid}/{region_id}")
        if episode:
            prefix = f"{episode}-SEG"
            if seg_id and not seg_id.startswith(prefix):
                problems.append(f"{label}.seg_id不属于本集:{seg_id}")
            if inherit_to and not inherit_to.startswith(prefix):
                problems.append(f"{label}.inherit_to_seg不属于本集:{inherit_to}")
        if inherit_to and inherit_to == seg_id:
            problems.append(f"{label}.inherit_to_seg不能指向自身")
        if inherit_to and not str(binding.get("inheritance_rule") or "").strip():
            problems.append(f"{label}.inheritance_rule在跨SEG继承时不能为空")
        binding_key = (character_id, seg_id)
        if character_id and seg_id:
            if binding_key in binding_keys:
                problems.append(f"人物空间记录重复:{character_id}/{seg_id}")
            binding_keys.add(binding_key)

        for j, relation in enumerate(binding.get("fixed_object_relations") or []):
            rel_label = f"{label}.fixed_object_relations[{j}]"
            if not isinstance(relation, dict):
                problems.append(f"{rel_label}必须是对象")
                continue
            for key in ("object_asset_id", "object_name", "relation"):
                if key not in relation or not isinstance(relation.get(key), str):
                    problems.append(f"{rel_label}.{key}必须是字符串")
            object_id = str(relation.get("object_asset_id") or "").strip()
            object_name = str(relation.get("object_name") or "").strip()
            if object_id and object_id not in all_ids:
                problems.append(f"{rel_label}引用不存在资产:{object_id}")
            if not object_id and not object_name:
                problems.append(f"{rel_label}必须填写object_asset_id或object_name")
            if not str(relation.get("relation") or "").strip():
                problems.append(f"{rel_label}.relation不能为空")

        for j, relation in enumerate(binding.get("relative_character_positions") or []):
            rel_label = f"{label}.relative_character_positions[{j}]"
            if not isinstance(relation, dict):
                problems.append(f"{rel_label}必须是对象")
                continue
            for key in ("character_asset_id", "relation"):
                if key not in relation or not isinstance(relation.get(key), str):
                    problems.append(f"{rel_label}.{key}必须是字符串")
            other_id = str(relation.get("character_asset_id") or "").strip()
            if not other_id:
                problems.append(f"{rel_label}.character_asset_id不能为空")
            if other_id and other_id not in all_ids:
                problems.append(f"{rel_label}引用不存在人物资产:{other_id}")
            if other_id and other_id == character_id:
                problems.append(f"{rel_label}不能引用人物自身")
            if not str(relation.get("relation") or "").strip():
                problems.append(f"{rel_label}.relation不能为空")

    register = out.get("output_register")
    if isinstance(register, dict):
        for key in _S4_REGISTER_COUNTS:
            if key not in register:
                problems.append(f"output_register缺少{key}")
            elif isinstance(register[key], bool) or not isinstance(register[key], int):
                problems.append(f"output_register.{key}必须是整数")
        for key in _S4_REGISTER_LISTS:
            if key not in register:
                problems.append(f"output_register缺少{key}")
            elif not isinstance(register[key], list):
                problems.append(f"output_register.{key}必须是数组")
    return problems


def normalize_s5_prompt_refs(pj: Project, out: dict, episode: str) -> dict:
    """环节5必须保留父资产和环节4声明的全部状态依赖。"""
    catalog = {a.get("asset_id"): a for a in known_assets(pj, episode)}
    for ap in out.get("asset_prompts") or []:
        aid = str(ap.get("asset_id") or "")
        asset = catalog.get(aid) or {}
        ap.pop("state_type", None)
        if asset.get("category") == "state":
            ap["parent_asset_id"] = str(asset.get("parent_asset_id") or "")
            ap["output_spec"] = "state_asset"
            ap["reference_assets"] = _ordered_asset_refs(
                [asset.get("parent_asset_id")], asset.get("reference_assets") or [],
                ap.get("reference_assets") or [],
                exclude=aid)
        else:
            ap["parent_asset_id"] = ""
            ap["reference_assets"] = _ordered_asset_refs(
                asset.get("reference_assets") or [],
                ap.get("reference_assets") or [], exclude=aid)
    return out


def merge_s5_outputs(previous: dict, fresh: dict) -> dict:
    """环节5是增量跑的，保存时必须按 asset_id 合并，不能用一条补写覆盖整集。"""
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
            rows[pos[aid]] = ap
        else:
            pos[aid] = len(rows)
            rows.append(ap)
    merged["asset_prompts"] = rows
    return merged


def run_llm_stage(pj: Project, stage_id: str, llm: LLM, params: dict,
                  log: Callable = print, episode: str = "",
                  cancel: Optional[Callable] = None,
                  seg_concurrency: int = 1,
                  force: bool = False) -> dict:
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
        a4, todo, skipped = _s5_filter(pj, data, claim=True, force=force)
        data["s4_assets"] = a4
        if force:
            log(f"{episode} 明确重跑环节5：按当前 TXT 规则重新编译本集全部资产提示词")
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
    seg_n, seg_why = _eps.seg_target(pj, episode, params) if per_ep else (0, "")
    if stage_id == "s2":
        log(f"{episode} 目标 {seg_n} 段（{seg_why}）→ 成片约 "
            f"{seg_n * int(params.get('duration') or 15)} 秒")
    system = load_prompt("_common", pj)
    user = render(stage_prompt(stage_id, tpl_name, pj),
                  _mapping(pj, stage_id, params, data, episode, script))
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
    validator = None
    if stage_id == "s4":
        known_ids = {str(a.get("asset_id") or "") for a in known_assets(pj, episode)}
        known_space_ids = {str(s.get("space_id") or "")
                           for s in known_space_masters(pj, episode)}
        validator = lambda value: validate_s4_output(
            value, episode, known_ids, known_space_ids)
    try:
        with LLM_GATE.slot():
            out = llm.json_call(system, user, required=required, log=log,
                                validator=validator, cancel=cancel, on_usage=_usage)
    except BaseException:
        if stage_id == "s5" and not force:
            # 没写成，把领走的资产放回去，让下一集或重跑能接手
            _unclaim([a.get("asset_id", "") for a in
                      (data.get("s4_assets") or {}).get("assets", [])])
        raise

    # 先规范化再落盘。状态资产保留唯一主父资产，并让完整依赖列表以父资产开头；
    # 任何一层都不许把环节4声明的多图依赖缩成一张。
    fresh_s5_prompts = []
    if stage_id == "s4":
        normalize_s4_asset_refs(out)
    elif stage_id == "s5":
        normalize_s5_prompt_refs(pj, out, episode)
        fresh_s5_prompts = list(out.get("asset_prompts") or [])
        check_prompt_refs(pj, out, episode, log)
        out = merge_s5_outputs(pj.stage_data("s5_asset_prompts", episode) or {}, out)
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

    if stage_id == "s4":
        check_asset_scope(pj, out, episode, log)

    # s5 额外把提示词正文落成 txt，便于人工查看与执行器读取
    if stage_id == "s5":
        # 只写本次新生成的那些；旧提示词虽然合并进 JSON，但不该被无意义地重写。
        for ap in fresh_s5_prompts:
            fn = ap.get("filename") or f"{ap['asset_id']}_PROMPT.txt"
            write_prompt_txt(pj, f"03_提示词/资产生产提示词/{fn}",
                             ap.get("prompt", ""), log)
    if stage_id in ("s5", "s8"):
        build_tasks(pj, params)
    diagnose.clear(pj.root, f"stage:{stage_id}", episode)
    return out


def check_prompt_refs(pj: Project, out: dict, episode: str, log=None) -> list:
    """环节5只做结构化引用检查，不再从剧情文字中的人名猜谁出镜。

    「听到 Dewi 的谎言」「寻找 Aisyah」是离屏原因，不代表人物进入画面。真正的
    视觉依赖由环节4的 reference_assets 明确声明，名字扫描只会制造误报。
    """
    prompts = out.get("asset_prompts") or []
    if not prompts:
        return []
    by_id = {a.get("asset_id"): a for a in known_assets(pj, episode)}
    for ep in ([episode] if episode else [""]):
        for a in (pj.stage_data("s4_assets", ep) or {}).get("assets", []):
            by_id[a.get("asset_id")] = a
    bad = []
    for ap in prompts:
        aid = str(ap.get("asset_id") or "")
        asset = by_id.get(aid) or {}
        refs = [str(r) for r in (ap.get("reference_assets") or [])]
        reasons = []
        if aid not in by_id:
            reasons.append("对应资产不存在")
        parent = str(asset.get("parent_asset_id") or "")
        if asset.get("category") == "state" and not parent:
            reasons.append("连续性状态资产没有 parent_asset_id")
        if asset.get("category") == "state" and (not refs or refs[0] != parent):
            reasons.append("父资产没有排在 reference_assets 第一位")
        if asset.get("category") != "state" and refs:
            reasons.append("基础原子资产不应引用其他资产")
        unknown = [r for r in refs if r not in by_id]
        if unknown:
            reasons.append("引用不存在：" + "、".join(unknown))
        if reasons:
            bad.append((aid, reasons))
    if not bad:
        diagnose.clear(pj.root, "stage:s5", f"{episode}:缺参考图")
        return []
    lines = "；".join(f"{aid} {'、'.join(reasons)}" for aid, reasons in bad[:6])
    if log:
        log(f"⚠️ {episode} 有 {len(bad)} 份资产提示词的参考依赖不完整：{lines}")
    diagnose.record(pj.root, diagnose.warn(
        "PROMPT_REF_MISSING",
        f"{episode} 有 {len(bad)} 份资产提示词的参考依赖不完整："
        + lines + ("…" if len(bad) > 6 else "")
        + "。连续性状态资产必须填写父资产，并在 reference_assets 第一位引用它；"
          "复杂状态还要列出全部依赖资产。",
        stage="stage:s5", target=f"{episode}:缺参考图"))
    return bad


def check_asset_scope(pj: Project, out: dict, episode: str, log=None) -> list:
    """按TXT检查父资产、连续性状态依赖和基础资产原子性。"""
    assets = out.get("assets") or []
    if not assets:
        return []
    by_id = {a.get("asset_id"): a for a in assets}
    # 加上前面几集已建的：本集锚点可以引用早先建好的人物/场景/道具。
    for a in known_assets(pj, episode):
        by_id.setdefault(a.get("asset_id"), a)
    bad = []
    for a in assets:
        aid = str(a.get("asset_id") or "")
        parent = str(a.get("parent_asset_id") or "")
        refs = [str(r) for r in (a.get("reference_assets") or [])]
        reasons = []
        if a.get("category") == "state":
            if not parent:
                reasons.append("连续性状态资产缺少 parent_asset_id")
            elif parent not in by_id:
                reasons.append(f"父资产 {parent} 不存在")
            elif not refs or refs[0] != parent:
                reasons.append(f"父资产 {parent} 必须排在 reference_assets 第一位")
            if a.get("output_spec") != "state_asset":
                reasons.append("连续性状态资产必须使用 state_asset")
        else:
            if parent:
                reasons.append("基础资产不应填写 parent_asset_id")
            if refs:
                reasons.append("基础原子资产不应引用其他资产")
        unknown = [r for r in refs if r not in by_id]
        if unknown:
            reasons.append("参考资产不存在：" + "、".join(unknown))
        if reasons:
            bad.append((aid, a.get("name", ""), reasons))
    cycles = asset_dependency_cycles(assets)
    if cycles:
        detail = "；".join(" ↔ ".join(group) for group in cycles)
        if log:
            log(f"⚠️ {episode} 资产存在循环依赖：{detail}。已禁止进入同层并发生产。")
        diagnose.record(pj.root, diagnose.warn(
            "ASSET_DEP_CYCLE",
            f"{episode} 资产循环依赖：{detail}。互相引用的资产都在等待对方先出图。",
            stage="stage:s4", target=f"{episode}:资产循环依赖"))
    else:
        diagnose.clear(pj.root, "stage:s4", f"{episode}:资产循环依赖")
    if not bad:
        diagnose.clear(pj.root, "stage:s4", f"{episode}:资产越界")
        return [("循环依赖", "", [" ↔ ".join(c) for c in cycles])] if cycles else []
    lines = "；".join(f"{aid}「{nm}」{'、'.join(reasons)}"
                     for aid, nm, reasons in bad[:5])
    if log:
        log(f"⚠️ {episode} 有 {len(bad)} 条资产的参考依赖不完整：{lines}")
    diagnose.record(pj.root, diagnose.warn(
        "ASSET_SCOPE",
        f"{episode} 有 {len(bad)} 条资产的参考依赖不完整："
        + lines + ("…" if len(bad) > 5 else "")
        + "。连续性状态资产必须填写真实父资产，reference_assets 以父资产开头并列出"
          "复杂关系需要的全部依赖；基础资产不引用其他图片资产。",
        stage="stage:s4", target=f"{episode}:资产越界"))
    return bad


def write_prompt_txt(pj: Project, rel: str, text: str, log=None) -> None:
    """环节5/8 落盘一份提示词 txt。

    走这里而不是直接 write_text，是为了在覆盖**手改过**的那份之前先备份 +
    说一声。人在页面上改好一条、隔天重跑一次环节8 就被悄悄盖掉 ——
    这种事不报出来，等出图不对劲再回头找，原文已经没了。
    """
    from . import promptfile
    try:
        promptfile.guard_overwrite(pj, rel, text, log)
    except Exception:                                   # noqa: BLE001
        pass                                            # 备份失败不该挡住主流程
    write_text(pj.p(*rel.split("/")), text)


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


def preview_prompt(pj: Project, stage_id: str, params: dict,
                   episode: str = "", segment: str = "") -> dict:
    """跑之前先看看这一步到底会发出去什么。**不调模型、不写盘、不占资产。**

    为什么值得单独做：LLM 环节的提示词是调用那一刻现渲染的，跑之前谁都看不见。
    而模板改坏了、前置产物缺了、集号选错了，这些都要等几百次调用之后才显形。
    这里让人在花钱之前看到原文。

    刻意和真跑共用同一套构造器（_s5_filter / s7_user_builder / s8_user_builder /
    同一份 mapping），分开写迟早飘 —— 那样预览就成了安慰剂。
    """
    from . import episodes as _eps
    if stage_id not in _LLM_SPEC:
        raise ValueError(f"环节 {stage_id} 不是 LLM 环节，没有提示词可预览")
    tpl_name, deps, required = _LLM_SPEC[stage_id]
    per_ep = is_per_episode(stage_id)
    out = {"stage": stage_id, "template": tpl_name, "episode": "",
           "segment": "", "segments": [], "required_fields": required,
           "missing": [], "note": ""}

    if per_ep:
        avail = _eps.ids(pj)
        if not avail:
            out["missing"] = ["环节1「整剧全局解析」（还没切集）"]
            return out
        episode = episode if episode in avail else avail[0]
    else:
        episode = ""
    out["episode"] = episode

    data = _dep_data(pj, deps, episode)
    missing = [d for d, v in data.items() if v is None]
    if missing:
        out["missing"] = [
            f"环节{_STAGE_OF_OUT[m]['no']}「{_STAGE_OF_OUT[m]['name']}」"
            + ("" if _STAGE_OF_OUT[m]["id"] in SERIES_STAGES else f"（{episode} 这一集的）")
            for m in missing if m in _STAGE_OF_OUT] or missing
        return out

    system = load_prompt("_common", pj)

    # 环节7/8 是按段跑的：一段一个提示词，得挑一段看
    if stage_id in ("s7", "s8") and not (
            stage_id == "s7" and not (data.get("s3_states") or {}).get("axis_convention")):
        segs = (data.get("s2_segments") or {}).get("segments", [])
        out["segments"] = [s.get("id", "") for s in segs]
        if not segs:
            out["missing"] = ["环节2「节奏驱动段落划分」（段落表是空的）"]
            return out
        seg = next((s for s in segs if s.get("id") == segment), segs[0])
        out["segment"] = seg.get("id", "")
        builder = (s7_user_builder if stage_id == "s7" else s8_user_builder)(
            pj, params, data, episode)
        user = builder(seg)
        out["note"] = (f"这一集共 {len(segs)} 段，每段一次调用；下面是 "
                       f"{out['segment']} 这一段的。")
    else:
        script = _eps.script_of(pj, episode) if per_ep else params.get("script", "")
        if stage_id == "s5":
            a4, todo, skipped = _s5_filter(pj, data, claim=False)   # 预览不占资产
            data["s4_assets"] = a4
            out["note"] = (f"本次要写 {len(todo)} 个资产的提示词"
                           + (f"；{len(skipped)} 个前面已经写过，跳过" if skipped else "")
                           + ("。一个都不用写，真跑时这一步会直接跳过、不花钱。"
                              if not todo else "。"))
        user = render(stage_prompt(stage_id, tpl_name, pj), _mapping(pj, stage_id, params, data,
                                                          episode, script))
        if stage_id == "s7":
            out["note"] = "这一集的环节3 没给轴线约定，环节7 会整集一次跑（不按段拆）。"

    left = re.findall(r"\{\{(\w+)\}\}", user)
    out.update({
        "system": system, "user": user,
        "chars": len(user) + len(system),
        "tokens": rough_tokens(user) + rough_tokens(system),
        "unfilled": sorted(set(left)),
        "layers": prompt_files(tpl_name, pj),
    })
    return out


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
                    system=load_prompt("_common", pj), user=build_user(seg),
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


def s7_user_builder(pj: Project, params: dict, data: dict, episode: str) -> Callable:
    """环节7 单段提示词的构造器。真跑和预览共用同一个 —— 分开写迟早飘。"""
    s3 = data.get("s3_states") or {}
    states = {s["id"]: s for s in s3.get("segment_states", [])}
    binds = {b["id"]: b for b in (data.get("s6_binding") or {}).get("bindings", [])}
    tpl = load_prompt("s7_shots", pj)
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

    return build_user


def s8_user_builder(pj: Project, params: dict, data: dict, episode: str) -> Callable:
    """环节8 单段提示词的构造器。真跑和预览共用。"""
    tone = ((data.get("s1_global") or {}).get("visual_tone") or {})
    states = {s["id"]: s for s in (data.get("s3_states") or {}).get("segment_states", [])}
    binds = {b["id"]: b for b in (data.get("s6_binding") or {}).get("bindings", [])}
    shots = {s["id"]: s for s in (data.get("s7_shots") or {}).get("shots", [])}
    assets = (data.get("s4_assets") or {}).get("assets", [])
    tpl = load_prompt("s8_compile", pj)

    def build_user(seg: dict) -> str:
        sid = seg["id"]
        binding = binds.get(sid, {})
        used = {r.get("asset_id") for r in binding.get("reference_images", [])
                if r.get("asset_id")}
        # 人物空间绑定中的固定物或对位人物不一定都能挤进参考图上限，
        # 但环节8仍需看到它们的资产原文，才能正确编译位置与关系。
        for context in binding.get("character_space_context", []) or []:
            character_id = context.get("character_asset_id")
            if character_id:
                used.add(character_id)
            state_id = context.get("continuity_state_asset_id")
            if state_id:
                used.add(state_id)
            for relation in context.get("fixed_object_relations", []) or []:
                object_id = relation.get("object_asset_id")
                if object_id:
                    used.add(object_id)
            for relation in context.get("relative_character_positions", []) or []:
                other_id = relation.get("character_asset_id")
                if other_id:
                    used.add(other_id)
        seg_assets = [a for a in assets if a["asset_id"] in used] or assets
        return render(tpl, {
            "EPISODE": episode,
            "DURATION": params.get("duration", 15),
            "TONE": jd({"compressed": tone.get("compressed", ""),
                        "variants": tone.get("compressed_variants", [])}),
            "SEGMENTS": jd(seg),
            "STATES": jd(states.get(sid, {})),
            "ASSETS": jd(seg_assets),
            "BINDINGS": jd(binding),
            "SHOTS": jd(shots.get(sid, {})),
        }) + f"\n\n【只编译这一段】{sid}，compiled 数组只放这一段。"

    return build_user


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
    build_user = s7_user_builder(pj, params, data, episode)

    result, failed, cancelled = run_segmented(
        pj, stage_id="s7", out_name="s7_shots", key="shots", segs=segs,
        done_ids=s7_done_segments(pj, episode), llm=llm, build_user=build_user,
        required=["shots[]", "shots[].character_space_note",
                  "shots[].shot_list[].positions"],
        log=log, episode=episode, cancel=cancel,
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

    shots = {s["id"]: s for s in (data.get("s7_shots") or {}).get("shots", [])}

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

    build_user = s8_user_builder(pj, params, data, episode)

    def on_item(sid: str, c: dict) -> None:
        write_prompt_txt(pj, f"03_提示词/故事板提示词/{sid}_STORYBOARD_PROMPT.txt",
                         c.get("storyboard_prompt", ""), log)
        write_prompt_txt(pj, f"03_提示词/视频提示词/{sid}_VIDEO_PROMPT.txt",
                         c.get("video_prompt", ""), log)

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


class AssetDependencyCycleError(RuntimeError):
    """资产参考图形成闭环，任何一个成员都不可能成为第一张。"""


def asset_dependency_cycles(items: list) -> list:
    """返回资产/任务依赖图里的强连通分量；每一组都是一个真实循环。

    同时接受环节4的资产结构（asset_id/reference_assets）和 tasks.json 的任务结构
    （key/reference_images），便于分析阶段与生产阶段使用同一套判断。
    """
    by_key = {}
    order = {}
    for item in items or []:
        key = str(item.get("key") or item.get("asset_id") or "").strip()
        if key and key not in by_key:
            order[key] = len(order)
            by_key[key] = item
    graph = {}
    for key, item in by_key.items():
        if "key" in item:
            refs = [r.get("asset_id") for r in (item.get("reference_images") or [])]
        else:
            refs = _ordered_asset_refs([item.get("parent_asset_id")],
                                       item.get("reference_assets") or [])
        graph[key] = [str(r) for r in refs if str(r) in by_key]

    index = 0
    stack, on_stack = [], set()
    indexes, lowlinks, cycles = {}, {}, []

    def visit(node):
        nonlocal index
        indexes[node] = lowlinks[node] = index
        index += 1
        stack.append(node)
        on_stack.add(node)
        for dep in graph[node]:
            if dep not in indexes:
                visit(dep)
                lowlinks[node] = min(lowlinks[node], lowlinks[dep])
            elif dep in on_stack:
                lowlinks[node] = min(lowlinks[node], indexes[dep])
        if lowlinks[node] != indexes[node]:
            return
        component = []
        while stack:
            member = stack.pop()
            on_stack.remove(member)
            component.append(member)
            if member == node:
                break
        if len(component) > 1 or (component and component[0] in graph[component[0]]):
            cycles.append(sorted(component, key=lambda x: order[x]))

    for node in graph:
        if node not in indexes:
            visit(node)
    return sorted(cycles, key=lambda c: min(order[x] for x in c))


def asset_layers(tasks: list) -> list:
    """把资产任务按参考图依赖分层：第 0 层没有参考图，第 N 层的参考图都在前面几层。

    为什么必须分层：状态资产 ST007 的父资产或来源图是 ST006，而任务是按环节4 的输出顺序
    排的、并发跑的。ST006 还在生成时 ST007 就被派出去，读不到 ST006.png 就直接
    失败（实测：ST007 重试两次全报「参考图文件不存在」）。
    分层之后层内并发、层间串行，来源资产必然先出完，竞态从根上没了。

    成环时必须在派任务前立即失败并报出成员。把循环组塞进同一层并发并没有消除依赖，
    只会让它们同时读取尚不存在的参考图，看起来像互相等待。
    """
    cycles = asset_dependency_cycles(tasks)
    if cycles:
        detail = "；".join(" ↔ ".join(group) for group in cycles)
        raise AssetDependencyCycleError(
            f"资产循环依赖：{detail}。这些资产互相把对方当参考图，没有任何一个能先生产。"
            "请回到环节4调整 dependency_order/reference_assets：同级资产不得互相引用，"
            "每条引用只能指向更早完成的基础资产或状态资产。")
    by_key = {t["key"]: t for t in tasks}
    layers, placed = [], set()
    rest = list(tasks)
    while rest:
        cur = [t for t in rest
               if all(r.get("asset_id") in placed or r.get("asset_id") not in by_key
                      for r in (t.get("reference_images") or []))]
        if not cur:  # 理论上已被上面的强连通分量检查覆盖；保留防御，绝不静默并发。
            blocked = "、".join(t["key"] for t in rest)
            raise AssetDependencyCycleError(f"资产依赖无法继续分层：{blocked}")
        layers.append(cur)
        placed.update(t["key"] for t in cur)
        rest = [t for t in rest if t["key"] not in placed]
    return layers


def assets_used_by(pj: Project, episodes_wanted: list) -> set:
    """这几集实际用到哪些资产（含状态资产父级与全部来源）。

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
    # 依赖闭包：状态资产要先出完父资产与全部来源。
    # 来源本身还可能是锚点，所以用队列一直追到底。
    queue = list(used)
    while queue:
        a = all_assets.get(queue.pop(0)) or {}
        deps = _ordered_asset_refs([a.get("parent_asset_id")],
                                   a.get("reference_assets") or [])
        for dep in deps:
            if dep in all_assets and dep not in used:
                used.add(dep)
                queue.append(dep)
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
    ghost: dict = {}          # 目标 → 声明了、但资产表里根本没有的那些引用
    noprompt = []             # 环节4 说要出、环节5 却没写提示词的
    for a in assets:
        if a.get("decision") == "skip":
            continue
        if a["asset_id"] not in aprompts:
            # 环节4 判定要出，环节5 却没给提示词 —— 以前 continue 掉，
            # 结果是这个资产**永远不会出图**，任务列表里连它都没有，没人吭声。
            noprompt.append(f"{a['asset_id']} {a.get('name', '')}".strip())
            continue
        ap = aprompts[a["asset_id"]]
        # 状态资产的父资产排第一，再与环节4/5的全部依赖合并。
        legacy_parent = str(a.get("parent_asset_id") or "") \
            if a.get("category") == "state" else ""
        refs = _ordered_asset_refs([legacy_parent], a.get("reference_assets") or [],
                                   ap.get("reference_assets") or [],
                                   exclude=a["asset_id"])
        bad = [str(r) for r in refs if r not in amap]
        if bad:
            ghost[a["asset_id"]] = bad
        asset_tasks.append({
            "key": a["asset_id"],
            # 哪几集用到它 —— 「只出第一集的资产图」靠这个字段过滤
            "episodes": sorted({str(s).split("-")[0]
                                for s in (a.get("used_by_segs") or [])
                                if str(s).startswith("EP")}),
            "prompt_ref": f"03_提示词/资产生产提示词/{ap.get('filename') or a['asset_id'] + '_PROMPT.txt'}",
            # 认不出的引用**留在列表里、file_ref 留空**，跟故事板那边一个规矩：
            # 悄悄删掉的话数量看着是对的，反而看不出少了一张参考图
            "reference_images": [
                {"image_n": i + 1, "asset_id": rid,
                 "file_ref": asset_output_rel(amap[rid]) if rid in amap else ""}
                for i, rid in enumerate(refs)
            ],
            "params": {"size": ap.get("size") or params.get("image_size", "1024x1536")},
            "output": asset_output_rel(a),
        })

    # ---- 故事板 / 视频：逐集展开 ---------------------------------------
    sb_tasks, vd_tasks = [], []
    noref, uncompiled = [], []
    for ep in eps:
        bindings = {b["id"]: b for b in (pj.stage_data("s6_binding", ep) or {}).get("bindings", [])}
        compiled = (pj.stage_data("s8_compile", ep) or {}).get("compiled", [])
        if not compiled and len(eps) == 1:       # 老单集项目
            bindings = {b["id"]: b for b in (pj.stage_data("s6_binding") or {}).get("bindings", [])}
            compiled = (pj.stage_data("s8_compile") or {}).get("compiled", [])
        # 环节2 切了段、环节8 却没编出来的：那几段根本不会有故事板和视频任务。
        # 不记一笔的话，「6 段只出了 4 段」看起来就像本来只有 4 段。
        done_ids = {c.get("id") for c in compiled}
        uncompiled += [s.get("id") for s in
                       (pj.stage_data("s2_segments", ep) or {}).get("segments", [])
                       if s.get("id") and s.get("id") not in done_ids]
        for c in compiled:
            sid = c["id"]
            seg = sid.split("-")[-1]
            b = bindings.get(sid, {})
            refs = c.get("reference_order") or b.get("reference_images") or []
            # 环节8 有时会把「本段故事板」自己写进参考图顺序 —— 那是环节10 视频的
            # 约定（视频以故事板为参考），串到故事板这一步就成了自己参考自己。
            # 这种 id 在资产表里找不到，file_ref 只能是空。留着不删（删了数量就
            # 对不上，看不出少了东西），但要在这里就记一条，别等出图才发现。
            bad = [str(r.get("asset_id") or "") for r in refs
                   if r.get("asset_id") not in amap]
            if bad:
                ghost[sid] = bad
            if not refs:
                # 一张参考图都没有 —— 人脸、场景全靠模型现编，跨段必然不一致。
                # 这个「0 声明 0 解析」躲得过出图时的缺图检查，只能在这里逮。
                noref.append(sid)
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
            if aux and aux not in amap:
                ghost.setdefault(sid, []).append(f"{aux}（视频的补充参考图）")
            vd_tasks.append({
                "key": sid, "episode": ep,
                "prompt_ref": f"03_提示词/视频提示词/{sid}_VIDEO_PROMPT.txt",
                "storyboard_ref": sb_out,
                "aux_reference": asset_output_rel(amap[aux]) if aux in amap else None,
                "params": {"duration": params.get("duration", 15),
                           "ratio": params.get("ratio", "9:16")},
                "output": f"05_分段视频/{code}_{ep}_{seg}_VIDEO_V01_FIXED.mp4",
            })

    # ---- 装配时就能看出来的窟窿，全部记进待办 ---------------------------
    # 这一类的共同点是**不报错、只是少**：少一张参考图、少一个资产、少一段任务。
    # 跑完看着都成功，要到人工验收才发现脸不对、片子短了一段。
    for key, bad in ghost.items():
        diagnose.record(pj.root, diagnose.warn(
            "GHOST_REF",
            f"{key} 的参考图里有 {len(bad)} 个资产表里没有的东西："
            + "、".join(bad[:5]) + ("…" if len(bad) > 5 else "")
            + "。它们指不到任何文件，出图那一步会停下 —— "
            + ("其中有『本段故事板自己』，那是环节10 视频的约定，"
               "不该出现在故事板的参考图里。"
               if any("STORYBOARD" in x.upper() for x in bad) else "")
            + "改「任务明细」里这一条的提示词，或者重跑对应的文字环节。",
            stage="storyboard", target=key))
    if noprompt:
        diagnose.record(pj.root, diagnose.warn(
            "ASSET_NO_PROMPT",
            f"环节4 判定要出、环节5 却没写提示词的资产有 {len(noprompt)} 个："
            + "、".join(noprompt[:8]) + ("…" if len(noprompt) > 8 else "")
            + "。它们不会进出图任务，等于**永远不会出图**，"
            "而引用到它们的故事板会因为缺参考图停下。重跑这一集的环节5。",
            stage="asset", target="(缺提示词)"))
    if noref:
        diagnose.record(pj.root, diagnose.warn(
            "NO_REF",
            f"有 {len(noref)} 段的故事板一张参考图都没有："
            + "、".join(noref[:8]) + ("…" if len(noref) > 8 else "")
            + "。人脸、场景全靠模型现编，跨段必然不一致。"
            "多半是环节6 没给这几段绑资产，重跑环节6 再重跑环节8。",
            stage="storyboard", target="(无参考图)"))
    if uncompiled:
        diagnose.record(pj.root, diagnose.warn(
            "SEG_NOT_COMPILED",
            f"环节2 切了段、环节8 没编出来的有 {len(uncompiled)} 段："
            + "、".join(uncompiled[:8]) + ("…" if len(uncompiled) > 8 else "")
            + "。这几段不会有故事板和视频任务，成片会直接少这几段 —— "
            "而任务列表里看不出来，因为它们压根没被排进去。重跑环节8。",
            stage="storyboard", target="(未编译的段)"))
    # 上一轮记过、这一轮已经好了的要清掉，否则面板上一直挂着假警报
    for cleared, targets in ((not ghost, {t["key"] for t in sb_tasks} | {t["key"] for t in asset_tasks}),
                             (not noprompt, {"(缺提示词)"}),
                             (not noref, {"(无参考图)"}),
                             (not uncompiled, {"(未编译的段)"})):
        if cleared:
            for tg in targets:
                diagnose.clear(pj.root, "storyboard", tg)
                diagnose.clear(pj.root, "asset", tg)

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
    给错形式的后果不是报错，是**参考图被丢掉照样出图** —— 状态资产没了父资产或来源
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
        want_refs = sorted(task.get("reference_images", []),
                           key=lambda x: x.get("image_n", 0))
        # 先把整批都点一遍再动手解析。一是别为注定失败的任务白传几 MB 上对象存储，
        # 二是要一次说清缺哪几张，而不是缺一张报一张。
        srcs = [(r, r.get("url") or r.get("file_ref") or "") for r in want_refs]
        missing = [r.get("asset_id") or f"第{r.get('image_n', '?')}张"
                   for r, s in srcs if not s]
        if missing:
            # 声明了要这张参考图，却指不到任何文件。以前是 `if src` 跳过 ——
            # 于是「声明 1 张、一张没传」照样出图，出来的脸不是本人，任务还标 ok。
            # 这类静默降级只能靠肉眼在几百张里发现，是最坏的一类错。
            raise RuntimeError(
                f"参考图指不到文件：{'、'.join(missing)}。"
                f"声明了 {len(want_refs)} 张，只解析出 {len(want_refs) - len(missing)} 张 —— "
                f"少一张就出图，脸和场景都会跑掉，所以这里停下。"
                f"多半是环节8 把不存在的东西写进了参考图顺序"
                f"（比如把本段故事板自己写进去），去「任务明细」看这一条的参考图那栏。")
        refs = [to_ref(s, log) for _, s in srcs]
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
