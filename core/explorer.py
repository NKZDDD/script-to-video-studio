# -*- coding: utf-8 -*-
"""把散在各处的产物拼成「能看懂」的视图。

产物页原来是个扁平文件列表：看到 C001.png，但不知道它是谁、用在哪几集、
哪个提示词生出来的、有没有参考图、哪家服务商出的。这里把这些接起来。

两个视图，各自对应一个自然的问题：
  · 资产库  —— 「这部剧有哪些固定资产，出到什么程度了」（全剧一份）
  · 按段看  —— 「这一集每一段从段落到成片走到哪一步了」（逐集）

只读，不写任何东西。数据全部来自磁盘产物，所以和「继续跑」看到的是同一份真相。
"""

from __future__ import annotations

import json
import os
from typing import Optional

from . import diagnose, episodes as _eps, ledger, promptfile, stages as S
from .store import Project

_CAT_CN = {
    "identity": "人物身份", "environment": "场景", "prop": "道具",
    "state": "连续状态资产", "group": "群体", "creature": "生物",
}
# 资产库里的显示顺序：先身份，再环境道具，状态放最后（它们挂在前面几类下面）
_CAT_ORDER = ["identity", "group", "creature", "environment", "prop", "state"]


def _text(pj: Project, rel: str, limit: int = 0, edits: Optional[dict] = None) -> dict:
    # edits 由调用方一次算好传进来：算一次要按文件哈希，几十条挨个算太浪费
    ed = (edits or {}).get((rel or "").replace("\\", "/"))
    p = pj.p(*rel.split("/"))
    if not os.path.isfile(p):
        return {"rel": rel, "exists": False, "text": "", "chars": 0, "edited": None}
    try:
        with open(p, "r", encoding="utf-8-sig") as f:
            t = f.read()
    except OSError:
        return {"rel": rel, "exists": True, "text": "", "chars": 0, "edited": ed}
    return {"rel": rel, "exists": True, "chars": len(t), "edited": ed,
            "text": t[:limit] if limit and len(t) > limit else t}


def _file(pj: Project, rel: str) -> dict:
    p = pj.p(*rel.split("/"))
    ex = os.path.isfile(p)
    return {"rel": rel, "exists": ex,
            "size": os.path.getsize(p) if ex else 0,
            "at": (__import__("time").strftime("%m-%d %H:%M",
                                               __import__("time").localtime(
                                                   os.path.getmtime(p))) if ex else "")}


def _spend_index(pj: Project) -> dict:
    """用量账本按 target 归拢：这一项花了几次调用、多少 token、多少秒。"""
    idx: dict = {}
    for e in ledger.load(pj.root):
        key = e.get("target") or e.get("episode") or e.get("stage") or ""
        if not key:
            continue
        g = idx.setdefault(key, {"calls": 0, "tokens": 0, "seconds": 0.0,
                                 "who": [], "stages": []})
        g["calls"] += 1
        g["tokens"] += (e.get("prompt_tokens") or 0) + (e.get("completion_tokens") or 0)
        g["seconds"] += float(e.get("seconds") or 0)
        who = f"{e.get('provider', '')}/{e.get('model', '')}".strip("/")
        if who and who not in g["who"]:
            g["who"].append(who)
        st = e.get("stage") or ""
        if st and st not in g["stages"]:
            g["stages"].append(st)
    return idx


def system_of(pj: Project) -> str:
    """这个项目用哪套生产体系。老项目没这个字段，回落 v61。

    产物页读错体系不会报错，只会**什么都看不到** —— 资产库空的、按段看空的，
    人会以为是没跑成，实际是读错了文件名。
    """
    return (pj.meta() or {}).get("system") or "v61"


def _assets_stage(pj: Project) -> str:
    return "n4_assets" if system_of(pj) == "v34" else "s4_assets"


# V3.4 用 family 分资产（CHAR/LOOK/CT/PROP_SPEC…），比 V6.1 的五类细。
# 归到同一套显示分类里，资产库的分组和排序就不用分两套写。
# 不映射的话 by_category 会是空的 —— 资产在列表里有，分组全没了。
_FAMILY_TO_CAT = {
    "CHAR": "identity", "PH": "identity", "LOOK": "identity", "CT": "state",
    "COST": "prop", "LOC": "environment", "SPATIAL": "environment",
    "PROP_SPEC": "prop", "PROP_INSTANCE": "prop", "PROP_SET": "prop",
    "VEH": "prop", "CRE": "creature", "GRP": "group", "VFX": "state",
}


def _category_of(pj: Project, a: dict) -> str:
    if system_of(pj) == "v34":
        return _FAMILY_TO_CAT.get((a.get("family") or "").upper(), "") \
            or a.get("category", "")
    return a.get("category", "")


def _asset_rel(pj: Project, a: dict) -> str:
    """这个资产的图落在哪。两套体系的目录分法不一样，别写死一套。"""
    if system_of(pj) == "v34":
        from .run_v34 import _asset_out
        return _asset_out(a)
    return S.asset_output_rel(a)


def _merged_assets(pj: Project) -> tuple:
    """全剧资产表：按集顺序累加，先出现的定义优先（和 build_tasks 同一套规则）。"""
    order, amap, by_ep = [], {}, {}
    stage = _assets_stage(pj)
    for ep in _eps.ids(pj) or [""]:
        for a in (pj.stage_data(stage, ep) or {}).get("assets", []):
            aid = a.get("asset_id")
            if not aid:
                continue
            by_ep.setdefault(aid, []).append(ep or "—")
            if aid not in amap:
                amap[aid] = a
                order.append(aid)
    return order, amap, by_ep


def assets(pj: Project) -> dict:
    """资产库视图（全剧一份）。

    每一项都带齐「它是什么 / 用在哪 / 谁生的 / 花了多少」，前端不用再回头查。
    """
    order, amap, by_ep = _merged_assets(pj)
    tasks = {t["key"]: t for t in (pj.tasks().get("asset_tasks") or [])}
    prompts = {}
    for ep in _eps.ids(pj) or [""]:
        pstage = "n4b_asset_prompts" if system_of(pj) == "v34" else "s5_asset_prompts"
        for ap in (pj.stage_data(pstage, ep) or {}).get("asset_prompts", []):
            prompts.setdefault(ap.get("asset_id"), ap)
    reg = {r.get("id"): r for r in pj.registry("asset")}
    spend = _spend_index(pj)
    edits = promptfile.ledger(pj)          # 哪些提示词是人在页面上改过的

    out = []
    for aid in order:
        a = amap[aid]
        cat = _category_of(pj, a)
        t = tasks.get(aid) or {}
        img = _file(pj, _asset_rel(pj, a))
        ap = prompts.get(aid) or {}
        pf = ap.get("filename") or f"{aid}_PROMPT.txt"
        segs = [s for s in (a.get("used_by_segs") or [])]
        out.append({
            "asset_id": aid,
            "category": cat,
            "category_cn": _CAT_CN.get(cat, cat or "未分类"),
            "name": a.get("name", ""),
            "parent": a.get("parent_asset_id") or "",
            "decision": a.get("decision", ""),
            "decision_reason": a.get("decision_reason", ""),
            "output_spec": a.get("output_spec", "") or ap.get("output_spec", ""),
            "appearance": a.get("appearance", ""),
            "identity_anchors": a.get("identity_anchors", ""),
            "allowed_change": a.get("allowed_change", ""),
            "forbidden_change": a.get("forbidden_change", ""),
            "episodes": sorted(set(by_ep.get(aid, []))),
            "segments": segs,
            "seg_count": len(segs),
            "image": img,
            "prompt": _text(pj, f"03_提示词/资产生产提示词/{pf}", edits=edits),
            # 出这张图时要喂进去的依赖图（状态资产第一张是父资产）。
            "refs": [{"image_n": r.get("image_n"), "asset_id": r.get("asset_id"),
                      "file": _file(pj, r.get("file_ref", ""))}
                     for r in (t.get("reference_images") or [])],
            "made_by": reg.get(aid, {}),
            "spend": spend.get(aid, {}),
            # 产生它的环节：表在环节4、提示词在环节5、图在环节5b
            "from": {"表": "环节4", "提示词": "环节5", "图": "环节5b"},
        })
    out.sort(key=lambda x: (_CAT_ORDER.index(x["category"])
                            if x["category"] in _CAT_ORDER else 9, x["asset_id"]))
    have = sum(1 for x in out if x["image"]["exists"])
    return {"items": out, "total": len(out), "with_image": have,
            "by_category": [
                {"category": c, "category_cn": _CAT_CN.get(c, c),
                 "total": sum(1 for x in out if x["category"] == c),
                 "with_image": sum(1 for x in out
                                   if x["category"] == c and x["image"]["exists"])}
                for c in _CAT_ORDER if any(x["category"] == c for x in out)],
            }


def segments(pj: Project, episode: str) -> dict:
    """按段看（逐集）：一段一行，从段落信息一路到成片。"""
    if system_of(pj) == "v34":
        return _segments_v34(pj, episode)
    code = (pj.meta().get("params") or {}).get("project_code") \
        or pj.meta().get("project_code", "PROJ-001")
    segs = (pj.stage_data("s2_segments", episode) or {}).get("segments", [])
    s3 = pj.stage_data("s3_states", episode) or {}
    states = {x.get("id"): x for x in s3.get("segment_states", [])}
    cont = {x.get("pair", "").split(" → ")[0]: x for x in s3.get("continuity_check", [])}
    binds = {x.get("id"): x for x in
             (pj.stage_data("s6_binding", episode) or {}).get("bindings", [])}
    shots = {x.get("id"): x for x in
             (pj.stage_data("s7_shots", episode) or {}).get("shots", [])}
    comp = {x.get("id"): x for x in
            (pj.stage_data("s8_compile", episode) or {}).get("compiled", [])}
    _, amap, _ = _merged_assets(pj)
    spend = _spend_index(pj)

    rows = []
    for s in segs:
        sid = s.get("id", "")
        seg = sid.split("-")[-1]
        sh = shots.get(sid) or {}
        b = binds.get(sid) or {}
        rows.append({
            "id": sid,
            "seg": seg,
            "name": s.get("name", ""),
            "time_range": s.get("time_range", ""),
            "story_task": s.get("story_task", ""),
            "entry_state": s.get("entry_state", ""),
            "core_process": s.get("core_process", ""),
            "turn": s.get("turn", ""),
            "exit_state": s.get("exit_state", ""),
            "next_anchor": s.get("next_anchor", ""),
            "dialogues": s.get("dialogues", []),
            "states": states.get(sid, {}),
            "continuity": cont.get(sid, {}),
            "axis_note": sh.get("axis_note", ""),
            "shot_count": len(sh.get("shot_list") or []),
            "keyframes": len(sh.get("keyframe_map") or []),
            "refs": [{"image_n": r.get("image_n"), "asset_id": r.get("asset_id"),
                      "control_scope": r.get("control_scope", ""),
                      "name": (amap.get(r.get("asset_id")) or {}).get("name", ""),
                      "file": _file(pj, _asset_rel(pj, amap[r["asset_id"]]))
                      if r.get("asset_id") in amap else None}
                     for r in (b.get("reference_images") or [])],
            "excluded": b.get("excluded_assets", []),
            "sb_prompt": _text(pj, f"03_提示词/故事板提示词/{sid}_STORYBOARD_PROMPT.txt"),
            "vd_prompt": _text(pj, f"03_提示词/视频提示词/{sid}_VIDEO_PROMPT.txt"),
            "storyboard": _file(pj, f"04_故事板/{code}_{episode}_{seg}_STORYBOARD_V01_FIXED.png"),
            "video": _file(pj, f"05_分段视频/{code}_{episode}_{seg}_VIDEO_V01_FIXED.mp4"),
            "compiled": sid in comp,
            "spend": spend.get(sid, {}),
        })

    # 每一步在这一集的完成度：面板上一眼看出卡在哪
    def n(f):
        return sum(1 for r in rows if f(r))

    steps = [
        {"no": 2, "name": "段落划分", "done": len(rows), "total": len(rows) or 1},
        {"no": 3, "name": "状态时间线", "done": n(lambda r: bool(r["states"])), "total": len(rows)},
        {"no": 6, "name": "资产绑定", "done": n(lambda r: bool(r["refs"])), "total": len(rows)},
        {"no": 7, "name": "正式分镜", "done": n(lambda r: r["shot_count"] > 0), "total": len(rows)},
        {"no": 8, "name": "提示词编译", "done": n(lambda r: r["compiled"]), "total": len(rows)},
        {"no": 9, "name": "故事板", "done": n(lambda r: r["storyboard"]["exists"]), "total": len(rows)},
        {"no": 10, "name": "分段视频", "done": n(lambda r: r["video"]["exists"]), "total": len(rows)},
    ]
    master = _file(pj, f"06_成片/{code}_{episode}_MASTER_V01_FIXED.mp4")
    return {"episode": episode, "rows": rows, "steps": steps, "master": master,
            "axis_convention": s3.get("axis_convention") or {},
            "continuity_bad": [c for c in s3.get("continuity_check", [])
                               if c.get("consistent") is False]}


def _segments_v34(pj: Project, episode: str) -> dict:
    """V3.4 的按段看。

    单独写一份而不是在原函数里到处加 if：两套体系的段落数据结构差得远
    （段号叫 seg_id 不叫 id、状态在 CVS 里不在 segment_states 里、
    参考图在故事板包里不在绑定表里），混着写会变成一堆看不懂的分支。
    **返回的形状保持一致**，前端不用分两套渲染。
    """
    code = (pj.meta().get("params") or {}).get("project_code") \
        or pj.meta().get("project_code", "PROJ-001")
    segs = (pj.stage_data("n10_segs", episode) or {}).get("segs", [])
    cvs = {c.get("cvs_id"): c for c in
           (pj.stage_data("n8_cvs", episode) or {}).get("cvs", [])}
    shots = {s.get("shot_id"): s for s in
             (pj.stage_data("n9_shots", episode) or {}).get("shots", [])}
    scst = {}
    for x in (pj.stage_data("n11_scstate", episode) or {}).get("scstates", []):
        scst.setdefault(x.get("seg_id"), []).append(x)
    pkgs = {p.get("seg_id"): p for p in
            (pj.stage_data("n12_storyboard", episode) or {}).get("sbpkg", [])}
    plans = {v.get("seg_id"): v for v in
             (pj.stage_data("n13_video", episode) or {}).get("video_plan", [])}
    _, amap, _ = _merged_assets(pj)
    spend = _spend_index(pj)
    edits = promptfile.ledger(pj)

    rows = []
    for s in segs:
        sid = s.get("seg_id", "")
        pkg = pkgs.get(sid) or {}
        plan = plans.get(sid) or {}
        entry, exit_ = cvs.get(s.get("entry_cvs")) or {}, cvs.get(s.get("exit_cvs")) or {}
        my_shots = [shots[i] for i in (s.get("included_shots") or []) if i in shots]
        rows.append({
            "id": sid,
            "seg": sid.split("-")[-1],
            "name": s.get("primary_dramatic_task", ""),
            "time_range": s.get("story_time_range", ""),
            "story_task": s.get("primary_dramatic_task", ""),
            "entry_state": s.get("entry_cvs", ""),
            "core_process": "、".join(s.get("state_change_ownership") or []),
            "turn": "",
            "exit_state": s.get("exit_cvs", ""),
            "next_anchor": s.get("boundary_rationale", ""),
            "dialogues": [s["dialogue"]] if s.get("dialogue") else [],
            "states": {"进入": entry.get("story_time", ""),
                       "退出": exit_.get("story_time", ""),
                       "禁止出现": exit_.get("forbidden_state", [])},
            "continuity": {},
            "axis_note": "、".join(t for t in (s.get("model_native_transition_ids") or [])),
            "shot_count": len(my_shots),
            "keyframes": len(pkg.get("kf") or []),
            # V3.4 的参考图在故事板包的 reference_order 里，不在绑定表
            "refs": [{"image_n": r.get("image_n"), "asset_id": r.get("asset_id"),
                      "control_scope": r.get("must_preserve", ""),
                      "name": (amap.get(r.get("asset_id")) or {}).get("name", ""),
                      "file": _file(pj, _asset_rel(pj, amap[r["asset_id"]]))
                      if r.get("asset_id") in amap else None}
                     for r in (pkg.get("reference_order") or [])],
            "excluded": [],
            # 这一层是 V3.4 独有的，单独列出来，否则页面上看不见它存在
            "scstates": [{"id": x.get("scstate_id"),
                          "file": _file(pj, f"03b_场景状态图/{code}_{x.get('scstate_id')}.png"),
                          "prompt": _text(pj, f"03_提示词/场景状态提示词/"
                                              f"{x.get('scstate_id')}_PROMPT.txt",
                                          edits=edits)}
                         for x in scst.get(sid, [])],
            "sb_prompt": _text(pj, f"03_提示词/故事板提示词/{sid}_STORYBOARD_PROMPT.txt",
                               edits=edits),
            "vd_prompt": _text(pj, f"03_提示词/视频提示词/{sid}_VIDEO_PROMPT.txt",
                               edits=edits),
            "storyboard": _file(pj, f"04_故事板/{code}_{sid}_STORYBOARD.png"),
            "video": _file(pj, f"05_分段视频/{code}_{sid}.mp4"),
            "compiled": bool(plan.get("video_prompt")),
            "spend": spend.get(sid, {}),
        })

    def n(f):
        return sum(1 for r in rows if f(r))

    total = len(rows)
    steps = [
        {"no": 10, "name": "SEG 装箱", "done": total, "total": total or 1},
        {"no": 11, "name": "场景状态图", "done": n(lambda r: bool(r["scstates"])), "total": total},
        {"no": 12, "name": "故事板包", "done": n(lambda r: r["keyframes"] > 0), "total": total},
        {"no": 13, "name": "视频计划", "done": n(lambda r: r["compiled"]), "total": total},
        {"no": 12, "name": "故事板出图", "done": n(lambda r: r["storyboard"]["exists"]), "total": total},
        {"no": 13, "name": "分段视频", "done": n(lambda r: r["video"]["exists"]), "total": total},
    ]
    audit = pj.stage_data("n14_audit", episode) or {}
    return {"episode": episode, "rows": rows, "steps": steps,
            "master": _file(pj, f"06_成片/{code}_{episode}_MASTER.mp4"),
            "axis_convention": {},
            # 审计发现的问题挂在这里，和 V6.1 的连续性检查占同一个位置
            "continuity_bad": [f for f in (audit.get("findings") or [])
                               if f.get("severity") in ("BLOCK", "WARN")]}


_TASK_KINDS = [("asset_tasks", "asset", "资产图", "环节5b"),
               ("storyboard_tasks", "storyboard", "故事板", "环节9"),
               ("video_tasks", "video", "分段视频", "环节10")]

# V3.4 多一类：场景状态图。漏掉它的话那一层的任务在明细里根本不显示 ——
# 出了图、花了钱，页面上看不见。
_TASK_KINDS_V34 = [("asset_tasks", "asset", "资产图", "第5环节"),
                   ("scstate_tasks", "storyboard", "场景状态图", "第11环节"),
                   ("storyboard_tasks", "storyboard", "故事板", "第12环节"),
                   ("video_tasks", "video", "分段视频", "第13环节")]


def _task_kinds(pj: Project) -> list:
    return _TASK_KINDS_V34 if system_of(pj) == "v34" else _TASK_KINDS


def tasks(pj: Project, episode: str = "") -> dict:
    """三类生产任务的完整状态 —— **全部从磁盘读，不用跑起来也能看**。

    以前只能在「生产」页看正在跑的那个 job 的明细，而 job 是内存态：
    服务一重启就没了，想知道上次那张图是哪家出的、用的什么提示词、
    花了多少，只能自己去翻文件夹和 JSON。这里把这些接起来：

      怎么做的 = tasks.json 的提示词/参考图/参数
      做了什么 = 注册表（哪家/什么模型）+ 执行日志（什么时候）
                 + 用量账本（花了多少）+ 产物文件本身（多大、什么时候写的）
      没做成   = failures.json 里的诊断
    """
    t = pj.tasks()
    spend = _spend_index(pj)
    _, amap, _ = _merged_assets(pj)
    edits = promptfile.ledger(pj)          # 哪些提示词是人在页面上改过的
    fails = {}
    for f in diagnose.load(pj.root):
        fails.setdefault(str(f.get("target", "")), f)
    logs = {}
    p = pj.p("07_检查与记录", "execution_log.jsonl")
    if os.path.isfile(p):
        with open(p, "r", encoding="utf-8-sig") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if e.get("id"):
                    logs[str(e["id"])] = e        # 同一个 id 取最后一次

    groups = []
    for key, kind, label, stage in _task_kinds(pj):
        reg = {str(r.get("id")): r for r in pj.registry(kind)}
        rows = []
        for it in (t.get(key) or []):
            k = str(it.get("key", ""))
            ep = it.get("episode") or ""
            if episode and key != "asset_tasks" and ep != episode:
                continue
            out = _file(pj, it.get("output", ""))
            r, lg = reg.get(k, {}), logs.get(k, {})
            if kind == "video":
                # 视频执行器固定按「故事板 → 可选补充资产」传图；这两项分别存放在
                # storyboard_ref / aux_reference，不在通用 reference_images 里。
                # 旧页面只看后者，于是任务明明有故事板却显示“无参考图”。
                display_refs = []
                storyboard_ref = str(it.get("storyboard_ref") or "")
                aux_reference = str(it.get("aux_reference") or "")
                if storyboard_ref:
                    display_refs.append({
                        "image_n": 1, "asset_id": "本段固定故事板",
                        "file": _file(pj, storyboard_ref),
                    })
                if aux_reference:
                    display_refs.append({
                        "image_n": 2, "asset_id": "补充资产参考图",
                        "file": _file(pj, aux_reference),
                    })
            else:
                display_refs = [
                    {"image_n": x.get("image_n"), "asset_id": x.get("asset_id"),
                     "file": _file(pj, x.get("file_ref", ""))}
                    for x in (it.get("reference_images") or [])
                ]
            rows.append({
                "key": k,
                "episode": ep or "、".join(it.get("episodes") or []) or "全剧共享",
                "name": (amap.get(k) or {}).get("name", ""),
                # —— 怎么做的 ——
                "prompt": _text(pj, it.get("prompt_ref", ""), edits=edits),
                "refs": display_refs,
                "params": it.get("params") or {},
                "storyboard_ref": it.get("storyboard_ref", ""),
                # —— 做了什么 ——
                "output": out,
                "done": out["exists"],
                "provider": r.get("provider", ""),
                "model": r.get("model", ""),
                "at": lg.get("at", "") or out["at"],
                "spend": spend.get(k, {}),
                # —— 没做成 ——
                "fail": fails.get(k, {}),
            })
        groups.append({
            "key": key, "kind": kind, "label": label, "stage": stage,
            "total": len(rows),
            "done": sum(1 for x in rows if x["done"]),
            "failed": sum(1 for x in rows if x["fail"] and not x["done"]),
            "rows": rows,
        })
    return {"episode": episode, "groups": groups,
            "has_tasks": any(g["total"] for g in groups)}


def view(pj: Project, episode: str = "") -> dict:
    eps = _eps.ids(pj)
    ep = episode if episode in eps else (eps[0] if eps else "")
    return {"episodes": eps, "episode": ep,
            "assets": assets(pj),
            "segments": segments(pj, ep) if ep else
            {"episode": "", "rows": [], "steps": [], "master": _file(pj, "x"),
             "axis_convention": {}, "continuity_bad": []}}
