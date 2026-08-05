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

import os
from typing import Optional

from . import episodes as _eps, ledger, stages as S
from .store import Project

_CAT_CN = {
    "identity": "人物身份", "environment": "场景", "prop": "道具",
    "state": "连续状态", "group": "群体", "creature": "生物",
}
# 资产库里的显示顺序：先身份，再环境道具，状态放最后（它们挂在前面几类下面）
_CAT_ORDER = ["identity", "group", "creature", "environment", "prop", "state"]


def _text(pj: Project, rel: str, limit: int = 0) -> dict:
    p = pj.p(*rel.split("/"))
    if not os.path.isfile(p):
        return {"rel": rel, "exists": False, "text": "", "chars": 0}
    try:
        with open(p, "r", encoding="utf-8-sig") as f:
            t = f.read()
    except OSError:
        return {"rel": rel, "exists": True, "text": "", "chars": 0}
    return {"rel": rel, "exists": True, "chars": len(t),
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


def _merged_assets(pj: Project) -> tuple:
    """全剧资产表：按集顺序累加，先出现的定义优先（和 build_tasks 同一套规则）。"""
    order, amap, by_ep = [], {}, {}
    for ep in _eps.ids(pj) or [""]:
        for a in (pj.stage_data("s4_assets", ep) or {}).get("assets", []):
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
        for ap in (pj.stage_data("s5_asset_prompts", ep) or {}).get("asset_prompts", []):
            prompts.setdefault(ap.get("asset_id"), ap)
    reg = {r.get("id"): r for r in pj.registry("asset")}
    spend = _spend_index(pj)

    out = []
    for aid in order:
        a = amap[aid]
        cat = a.get("category", "")
        t = tasks.get(aid) or {}
        img = _file(pj, S.asset_output_rel(a))
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
            "prompt": _text(pj, f"03_提示词/资产生产提示词/{pf}"),
            # 出这张图时要喂进去的参考图（状态资产靠它继承父资产的身份）
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
                      "file": _file(pj, S.asset_output_rel(
                          amap[r["asset_id"]])) if r.get("asset_id") in amap else None}
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


def view(pj: Project, episode: str = "") -> dict:
    eps = _eps.ids(pj)
    ep = episode if episode in eps else (eps[0] if eps else "")
    return {"episodes": eps, "episode": ep,
            "assets": assets(pj),
            "segments": segments(pj, ep) if ep else
            {"episode": "", "rows": [], "steps": [], "master": _file(pj, "x"),
             "axis_convention": {}, "continuity_bad": []}}
