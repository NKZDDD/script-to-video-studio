# -*- coding: utf-8 -*-
"""老 v34 项目 → V5.6 全剧级布局的迁移。

V5.6 对照下来，叙事结构、资产表、资产提示词、空间主表、连续性总账
本来就该是**全剧一份**（唯一 Continuity Ledger、LONG_TERM 跨 Episode、
Project→Episode→Scene→Beat 解析）。这五份产物因此从
`01_剧本与分段/EP01/n4_assets.json` 挪到 `01_剧本与分段/n4_assets.json`。

不迁移的后果很具体：程序去项目根找，找不到，判成「这个环节还没跑」，
然后**把已经花过钱的七个环节重跑一遍**。所以这一步必须有，而且要能
在打开项目时自动发现。

合并规则：按集顺序拼，同 id 先出现的赢（和以前 build_tasks 的规则一致），
并且**给每条打上它原来属于哪一集** —— 不打的话跨集信息就丢了，
而那正是这次重定级要换来的东西。
"""

from __future__ import annotations

import os
from typing import Optional

from . import episodes as _eps
from .store import Project

# 这五份从逐集变成全剧级。值是「这份产物里按集合并哪几个数组」。
# 数组之外的顶层键取第一集那份（scope、note 之类的说明性字段）。
MOVED = {
    "n3_narrative": ["scenes", "beats", "episode_arcs"],
    "n4_assets": ["assets", "costume_contracts", "prop_specs", "prop_instances"],
    "n4b_asset_prompts": ["asset_prompts", "skipped"],
    "n5_spatial": ["spatial_masters", "loc_views"],
    "n6_ledger": ["ledger", "prop_tracking", "position_tracking"],
}

# 每个数组里拿哪个字段当去重键。没列的按整条内容去重。
_ID_KEY = {
    "scenes": "scene_id", "beats": "beat_id", "episode_arcs": "episode",
    "assets": "asset_id", "asset_prompts": "asset_id",
    "prop_specs": "spec_id", "prop_instances": "instance_id",
    "spatial_masters": "spatial_id", "loc_views": "view_id",
    "ledger": "event_id", "prop_tracking": "instance_id",
    "position_tracking": "character_id",
}


def pending(pj: Project) -> list:
    """还有哪几份产物停在集目录里。空列表 = 不用迁。"""
    out = []
    for stage in MOVED:
        if os.path.isfile(pj.stage_path(stage)):
            continue                        # 已经在项目根了
        if any(os.path.isfile(pj.stage_path(stage, ep)) for ep in _eps.ids(pj)):
            out.append(stage)
    return out


def _merge(pj: Project, stage: str, arrays: list) -> Optional[dict]:
    """把各集的这份产物合成一份。没有任何一集有就返回 None。"""
    eps = [ep for ep in _eps.ids(pj)
           if os.path.isfile(pj.stage_path(stage, ep))]
    if not eps:
        return None
    merged: dict = {}
    seen = {k: set() for k in arrays}
    for ep in eps:
        d = pj.stage_data(stage, ep) or {}
        for k, v in d.items():
            if k not in arrays:
                merged.setdefault(k, v)     # 说明性字段取第一集那份
                continue
            if not isinstance(v, list):
                continue
            rows = merged.setdefault(k, [])
            idk = _ID_KEY.get(k)
            for r in v:
                if not isinstance(r, dict):
                    rows.append(r)
                    continue
                key = r.get(idk) if idk else repr(sorted(r.items()))
                if key is not None and key in seen[k]:
                    continue                # 先出现的赢，和旧的合并规则一致
                if key is not None:
                    seen[k].add(key)
                # 打上原来属于哪一集。不打的话跨集信息就丢了 ——
                # 而「哪条状态是第几集留下的」正是这次重定级要换来的东西。
                rows.append(dict(r, episode=r.get("episode") or ep))
    merged["scope"] = "full_series"
    merged["migrated_from_episodes"] = eps
    return merged


def run(pj: Project, log=print) -> dict:
    """真迁。老文件**不删**，改名成 .bak —— 迁错了还能拿回来。"""
    todo = pending(pj)
    if not todo:
        return {"ok": True, "moved": [], "note": "不用迁"}
    moved = []
    for stage in todo:
        merged = _merge(pj, stage, MOVED[stage])
        if merged is None:
            continue
        pj.save_stage(stage, merged, "")
        n = sum(len(merged.get(k) or []) for k in MOVED[stage])
        log(f"  {stage}：{len(merged['migrated_from_episodes'])} 集合并成一份，"
            f"共 {n} 条")
        for ep in merged["migrated_from_episodes"]:
            old = pj.stage_path(stage, ep)
            if os.path.isfile(old):
                os.replace(old, old + ".bak")
        moved.append(stage)
    return {"ok": True, "moved": moved,
            "note": "老文件改名成 .bak 留着了，确认没问题再删"}
