# -*- coding: utf-8 -*-
"""出图出片之前的三道硬闸门。

V3.4 对这几条的措辞是硬的，没有「知道了继续跑」这种运行时放行：

  · 「出现下列任一项，**不得交付生产执行，必须回编**」（第七章 20 条）
  · 「若镜头必须显露而覆盖不存在，状态为 PRODUCTION_BLOCKED，
     先建立/批准覆盖资产。**不得以『出现概率不大』为理由跳过**」
  · 「任一 Reference 未解析时**阻断 Prompt**，不得猜图继续」

所以这里运行时一律硬拦，**没有继续按钮**。

但 V3.4 也给了合规的放行路径 —— 它在第 0 章：冻结任务参数时一并冻结
「用户授权」。和它对降级的态度一致：「若用户以后授权外部剪辑，
必须修改项目配置和执行模式，**不能静默切换**」。

所以要放行，去改项目冻结的授权项（meta.capability.authorizations），
那是一次显式的改配置动作，会留在冻结记录里 —— 不是运行时随手点一下。

这个区别很实际：V3.4 假设有人在逐章执行并当场回编；而这套流水线是
无人值守跑 40 集。一律硬拦又不给配置层出口的话，模型误报一条，
整晚的批量就停在那儿。
"""

from __future__ import annotations

from typing import Optional

from . import episodes as _eps
from .store import Project

# 三道闸门各自的授权键。写进 meta.capability.authorizations 才放行。
GATES = {
    "audit_block": "审计的 BLOCK 级发现",
    "visual_coverage": "首次显露覆盖",
    "object_count": "道具存在总数对账",
}


def authorized(pj: Project, gate: str) -> bool:
    cap = (pj.meta() or {}).get("capability") or {}
    return bool((cap.get("authorizations") or {}).get(gate))


def authorize(pj: Project, gate: str, why: str) -> dict:
    """显式放行一道闸门。必须写理由 —— 三个月后回头看要知道当初为什么放。"""
    if gate not in GATES:
        raise ValueError(f"没有这道闸门：{gate}（有的是 {'、'.join(GATES)}）")
    if not (why or "").strip():
        raise ValueError("放行必须写理由")
    import time
    meta = pj.meta() or {}
    cap = dict(meta.get("capability") or {})
    auth = dict(cap.get("authorizations") or {})
    auth[gate] = {"why": why.strip(),
                  "at": time.strftime("%Y-%m-%d %H:%M:%S")}
    cap["authorizations"] = auth
    pj.save_meta(dict(meta, capability=cap))
    return auth[gate]


def _episodes(pj: Project, only: Optional[list]) -> list:
    eps = _eps.ids(pj) or [""]
    return [e for e in eps if not only or e in only]


# ---------------------------------------------------------------- B4 审计拦截

def audit_gate(pj: Project, only: Optional[list] = None) -> list:
    """第十四环节报了 BLOCK 级发现就不许往下出图出片。

    审计查的是「不报错、只是错」那一类：跑完看着全成功，要到人工验收
    才发现脸不对、片子短了一段。在花钱之前拦住，是这一层存在的全部理由。
    """
    bad = []
    for ep in _episodes(pj, only):
        a = pj.stage_data("n14_audit", ep) or {}
        if not a:
            continue        # 没审过不算失败；审计本身是 soft 的
        for f in a.get("findings") or []:
            if f.get("severity") == "BLOCK":
                bad.append(f"{ep} {f.get('where', '')}：{f.get('what', '')}"
                           f"　→ {f.get('how_to_fix', '')}")
        if a.get("verdict") == "FIX_FIRST" and not any(
                f.get("severity") == "BLOCK" for f in a.get("findings") or []):
            bad.append(f"{ep} 审计判定 FIX_FIRST：{a.get('verdict_reason', '')}")
    return bad


# ---------------------------------------------------------------- B2 覆盖闸门

# 每个视频窗口的覆盖状态只有这三种结果
COVERAGE_OK = "COVERED"
COVERAGE_NEED_REF = "SUPPLEMENTAL_REFERENCE_REQUIRED"
COVERAGE_CONSTRAIN = "CAMERA_CONSTRAINED"


def coverage_gate(pj: Project, only: Optional[list] = None) -> list:
    """出片之前核一遍：会不会显露没定义过的身体/服饰区域。

    这是视频这一层特有的风险 —— 故事板只画了半身，镜头一拉远就看见下半身。
    那时候如果这块没有视觉来源，模型只能自己想：鞋子换一双、背面衣服
    变成另一款，**而且不报错**。

    三种合法结果：已覆盖 / 补一张覆盖图 / 机位受限。
    没给结论、或者要求补图却没给参考图，都算没过。
    """
    bad = []
    for ep in _episodes(pj, only):
        for plan in (pj.stage_data("n13_video", ep) or {}).get("video_plan", []):
            seg = plan.get("seg_id", "?")
            refs = {str(r.get("asset_id") or "")
                    for r in (plan.get("reference_order") or [])}
            for w in plan.get("windows") or []:
                wid = w.get("window_id", "?")
                st = (w.get("visual_coverage_status") or "").strip()
                if not st:
                    bad.append(f"{seg} {wid}：没给覆盖结论 —— "
                               f"镜头会不会显露没定义的区域，这一步必须有答案")
                elif st == COVERAGE_NEED_REF and len(refs) < 2:
                    bad.append(f"{seg} {wid}：判定要补一张当前造型的覆盖图，"
                               f"但参考图里只有故事板，没有那张覆盖图")
                elif st == COVERAGE_CONSTRAIN and not (w.get("camera_path_world")
                                                       or "").strip():
                    bad.append(f"{seg} {wid}：判定机位必须受限，"
                               f"却没写清机位怎么走 —— 不写等于没限制")
                elif st not in (COVERAGE_OK, COVERAGE_NEED_REF, COVERAGE_CONSTRAIN):
                    bad.append(f"{seg} {wid}：覆盖结论 {st!r} 不认识")
    return bad


# ---------------------------------------------------------------- B3 道具对账

def object_count_gate(pj: Project, only: Optional[list] = None) -> list:
    """道具存在总数要对得上账。

    存在总数 = 明确可见 + 部分可见 + 被遮挡 + 画外。
    遮挡、离画、装进容器**都不改变存在数量** —— 对不上账的典型症状是
    道具被遮挡之后复制成两个，或者离画之后凭空消失。
    """
    bad = []
    for ep in _episodes(pj, only):
        for track in (pj.stage_data("n6_ledger", ep) or {}).get("prop_tracking", []):
            iid = track.get("instance_id", "?")
            lock = track.get("count_lock") or {}
            total = lock.get("active_total")
            if total is None:
                continue        # 没写对账表不算错，写了就得对得上
            rec = str(lock.get("reconciliation") or "")
            nums = [int(x) for x in _digits(rec)]
            if not nums:
                bad.append(f"{ep} {iid}：写了存在总数 {total}，但没给对账明细")
            elif sum(nums[:-1]) != nums[-1] or nums[-1] != int(total):
                bad.append(f"{ep} {iid}：对账对不上 —— {rec}，"
                           f"但存在总数写的是 {total}")
    return bad


def _digits(s: str) -> list:
    import re
    return re.findall(r"\d+", s)


# ---------------------------------------------------------------- 汇总

def check_all(pj: Project, only: Optional[list] = None) -> dict:
    """跑一遍三道闸门。返回 {闸门: 问题清单}，只含**没被授权放行**的。"""
    out = {}
    for gate, fn in (("audit_block", audit_gate),
                     ("visual_coverage", coverage_gate),
                     ("object_count", object_count_gate)):
        problems = fn(pj, only)
        if problems and not authorized(pj, gate):
            out[gate] = problems
    return out


def blocked_message(blocked: dict) -> str:
    """拦下来时给人看的话。要能照着改，也要说清怎么放行。"""
    lines = []
    for gate, problems in blocked.items():
        lines.append(f"【{GATES[gate]}】{len(problems)} 处：")
        lines += [f"  · {p}" for p in problems[:6]]
        if len(problems) > 6:
            lines.append(f"  · …还有 {len(problems) - 6} 处")
    lines.append("")
    lines.append("这几条会让出来的东西「看着正常但是错的」，所以在花钱之前停下。")
    lines.append("改完再点一次「开始」；确实要带着问题往下跑的话，"
                 "去项目设置里显式授权对应的那一项（授权会记进冻结记录，"
                 "不是静默跳过）。")
    return "\n".join(lines)
