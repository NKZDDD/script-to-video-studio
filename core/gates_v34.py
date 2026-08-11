# -*- coding: utf-8 -*-
"""出图出片之前的几道硬闸门。

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

# 每道闸门各自的授权键。写进 meta.capability.authorizations 才放行。
GATES = {
    "audit_block": "审计的 BLOCK 级发现",
    "visual_coverage": "首次显露覆盖",
    "object_count": "道具存在总数对账",
    # V5.6 新增。放行它等于允许人物无事件换位 —— 后果是整集连起来看才发现
    # 「上一段还坐着，下一段人已经在对面了」，所以理由要写得比别的更实在。
    "position_state": "人物位置状态门控（无事件瞬移）",
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


# ------------------------------------------------- B5 位置状态门控（V5.6 新增）

# 位置状态里必须逐项继承的那几维。少写一维，那一维就成了自由变量。
POSITION_DIMS = ("zone", "anchor_id", "support_binding_id", "posture_class",
                 "orientation_yaw_deg")

# 坐、躺、乘车、跪靠都不是普通姿态 —— 它们绑着一个支撑实体。
# 起身前必须先解除支撑，否则「人还坐着，同时又站在别处」。
SUPPORTED_POSTURES = ("SEATED", "LYING", "KNEELING")


def position_gate(pj: Project, only: Optional[list] = None) -> list:
    """人物不许无事件瞬移。

    V5.6 第 14 章第 1 节把这个列为两类高风险失败之一，原话是：

      「人物在前一状态坐在发布会桌后，下一状态没有起身、绕行或穿越事件，
        却直接出现在观众区争吵。这不是 Camera 变化，而是 World Truth
        被静默改写。」

    为什么必须由程序拦：这类错**画面上是好看的** —— 三个人整整齐齐同框，
    构图甚至更漂亮。它只在你把整集连起来看的时候才露馅：上一段还坐着，
    下一段人已经在对面了，中间没有起身。人工验收一集一集看，基本抓不到。

    模型犯这个错有很强的动机：为了让三人同框、为了露全身、为了做对峙构图，
    把人挪到房间中央是最省事的解法。所以 V5.6 专门写了一句
    「不能为了同框把人物移到前场」，也所以这里要逐项对账。
    """
    bad = []
    for ep in _episodes(pj, only):
        bad += _cvs_position_problems(pj, ep)
        bad += _kf_position_problems(pj, ep)
    return bad


def _pos_of(obj: dict) -> dict:
    """一个人物在某个状态里的位置。字段名容忍几种写法。"""
    p = obj.get("world_position_state") or obj.get("position_state") or {}
    if not isinstance(p, dict):
        return {}
    out = dict(p)
    # anchor / zone 这两个在别处也可能直接挂在人物上（导演环节就是那样写的）
    for k, alt in (("anchor_id", "anchor"), ("zone", "zone_id")):
        if not out.get(k) and obj.get(alt):
            out[k] = obj[alt]
        if not out.get(k) and p.get(alt):
            out[k] = p[alt]
    return out


def _moved_dims(a: dict, b: dict) -> list:
    """两个位置状态之间，哪几维变了。空值不算变 —— 没写不等于改了。"""
    out = []
    for d in POSITION_DIMS:
        x, y = a.get(d), b.get(d)
        if x in (None, "") or y in (None, ""):
            continue
        if str(x) != str(y):
            out.append(d)
    return out


def _cvs_position_problems(pj: Project, ep: str) -> list:
    """相邻 CVS 之间：位置变了就必须有一条被批准的移动事件。

    移动事件在第八环节的 vt[] 里（视觉过渡），它得写清
    起点、解除支撑、走哪条 Route、穿哪个 Portal、终点。
    """
    d = pj.stage_data("n8_cvs", ep) or {}
    cvs = [c for c in (d.get("cvs") or []) if isinstance(c, dict)]
    if len(cvs) < 2:
        return []
    # 哪些 CVS 之间登记过合法的位置变化
    moves = {}
    for vt in d.get("vt") or []:
        if not isinstance(vt, dict):
            continue
        key = (str(vt.get("source_cvs") or ""), str(vt.get("target_cvs") or ""))
        moves.setdefault(key, []).append(vt)
    bad = []
    for prev, cur in zip(cvs, cvs[1:]):
        pid, cid = str(prev.get("cvs_id") or "?"), str(cur.get("cvs_id") or "?")
        by_prev = {str(c.get("character_id") or ""): c
                   for c in (prev.get("characters") or []) if isinstance(c, dict)}
        for c in cur.get("characters") or []:
            if not isinstance(c, dict):
                continue
            who = str(c.get("character_id") or "")
            was = by_prev.get(who)
            if not was:
                continue                  # 这一状态里才出现的人，没有「上一位置」
            dims = _moved_dims(_pos_of(was), _pos_of(c))
            if not dims:
                continue
            vts = moves.get((pid, cid)) or []
            mover_ok = any(who in _movers(v) for v in vts)
            if not mover_ok:
                bad.append(
                    f"{ep} {pid}→{cid} {who} 的 {'、'.join(dims)} 变了，"
                    f"但没有一条批准这个人移动的过渡事件 —— "
                    f"这是无事件瞬移，机位变化不能当理由")
                continue
            for v in vts:
                if who not in _movers(v):
                    continue
                bad += _transition_problems(ep, pid, cid, who, was, c, v)
    return bad


def _movers(vt: dict) -> set:
    """这条过渡批准了谁移动。没写 authorized_movers 就当谁都没批准。"""
    raw = vt.get("authorized_movers") or vt.get("movers") or []
    if isinstance(raw, str):
        raw = [raw]
    return {str(x) for x in raw if x}


def _transition_problems(ep, pid, cid, who, was, cur, vt) -> list:
    """批准了这个人移动，但移动本身写得站不住脚。"""
    bad = []
    p0, p1 = _pos_of(was), _pos_of(cur)
    # 从坐/躺/跪起来，必须先解除支撑。不解除就是「人还坐着又站在别处」。
    posture0 = str(p0.get("posture_class") or "").upper()
    if posture0 in SUPPORTED_POSTURES and posture0 != str(
            p1.get("posture_class") or "").upper():
        if not str(vt.get("release_support_action") or "").strip():
            bad.append(f"{ep} {pid}→{cid} {who} 从 {posture0} 起来了，"
                       f"但没写解除支撑的动作 —— 镜头拉远也取消不了座椅关系")
    # 换了 Zone 就得说走哪条路。不说的话模型会直接穿过会议桌。
    if p0.get("zone") and p1.get("zone") and str(p0["zone"]) != str(p1["zone"]):
        if not str(vt.get("route_id") or "").strip():
            bad.append(f"{ep} {pid}→{cid} {who} 从 {p0['zone']} 到 {p1['zone']}，"
                       f"没写走哪条 Route —— 不写就等于允许穿墙穿桌")
        crossing = str(vt.get("barrier_or_portal_crossing") or "").strip()
        if not crossing:
            bad.append(f"{ep} {pid}→{cid} {who} 跨了 Zone，"
                       f"没说穿的是哪个 Portal 或桌端空隙")
    if not str(vt.get("completion_condition") or
               vt.get("release_completion_condition") or "").strip():
        bad.append(f"{ep} {pid}→{cid} {who} 的移动没写完成条件 —— "
                   f"没有完成条件，下游没法判断这个人到位了没有")
    return bad


def _kf_position_problems(pj: Project, ep: str) -> list:
    """相邻关键帧之间：没有移动事件时，位置必须逐项继承。

    这一层最容易出问题 —— 关键帧是「同一个世界的不同观察」，
    模型很容易把「换机位」理解成「可以重新安排人站哪」。
    V5.6 的原话：Camera may reframe; entities may not reblock.
    """
    bad = []
    for pkg in (pj.stage_data("n12_storyboard", ep) or {}).get("sbpkg", []):
        if not isinstance(pkg, dict):
            continue
        seg = str(pkg.get("seg_id") or pkg.get("sbpkg_id") or "?")
        kfs = [k for k in (pkg.get("kf") or []) if isinstance(k, dict)]
        for prev, cur in zip(kfs, kfs[1:]):
            kid = str(cur.get("kf_id") or "?")
            ev = str(cur.get("authorized_movement_event_id") or "").strip()
            authorized_here = bool(ev) and ev.upper() != "NONE"
            by_prev = {str(e.get("id") or ""): e
                       for e in (prev.get("entity_position_state") or [])
                       if isinstance(e, dict)}
            for e in cur.get("entity_position_state") or []:
                if not isinstance(e, dict):
                    continue
                who = str(e.get("id") or "")
                was = by_prev.get(who)
                if not was:
                    continue
                dims = _moved_dims(_pos_of(was) or was, _pos_of(e) or e)
                if dims and not authorized_here:
                    bad.append(
                        f"{ep} {seg} {kid} {who} 的 {'、'.join(dims)} 相对上一格变了，"
                        f"但这一格的 authorized_movement_event_id 是 NONE —— "
                        f"没有移动事件时，机位只能重新取景，不能重新安排人站哪"
                        f"（为了同框或露全身把人挪到前场，正是这条要拦的）")
    return bad


# ---------------------------------------------------------------- 汇总

def check_all(pj: Project, only: Optional[list] = None) -> dict:
    """跑一遍全部闸门。返回 {闸门: 问题清单}，只含**没被授权放行**的。"""
    out = {}
    for gate, fn in (("audit_block", audit_gate),
                     ("visual_coverage", coverage_gate),
                     ("object_count", object_count_gate),
                     ("position_state", position_gate)):
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
