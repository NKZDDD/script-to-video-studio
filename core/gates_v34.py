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

import re
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
    # V6.1 新增。模板写了上限，但模板只是"说"——模型不一定听，
    # 而超格的后果要到审计才看得见，那时候图已经出完了。
    "sheet_density": "故事板每张最多 3 格",
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
    # 总账是**全剧一本**（V5.6：维护唯一 Continuity Ledger），
    # 读项目根下那份，不按集循环 —— 按集读的话 40 集里只有一集读得到，
    # 另外 39 集拿到空字典、一条都查不出来，而且不报错。
    bad = []
    for track in (pj.stage_data("n6_ledger", "") or {}).get("prop_tracking", []):
        iid = track.get("instance_id", "?")
        lock = track.get("count_lock") or {}
        total = lock.get("active_total")
        if total is None:
            continue            # 没写对账表不算错，写了就得对得上
        rec = str(lock.get("reconciliation") or "")
        if not _digits(rec):
            bad.append(f"{iid}：写了存在总数 {total}，但没给对账明细")
            continue
        got = _sum_equation(rec)
        if got is None:
            continue        # 算不出来就不拦，理由见 _sum_equation
        left, right = got
        if left != right or right != int(total):
            bad.append(f"{iid}：对账对不上 —— {rec}，但存在总数写的是 {total}")
    return bad


def _digits(s: str) -> list:
    return re.findall(r"\d+", s)


def _sum_equation(rec: str):
    """`a + b + c = 总数` → (左边之和, 右边)。算不出来返回 None。

    **算不出来就不许拦。** 原来的写法是「把整段文字里所有数字抓出来，
    假设最后一个是总数、前面的加起来等于它」—— 而 `reconciliation` 是
    一段自由文本，模型会在等号后面继续解释。实跑被这么误判了 4 条，
    每一条都把整批生产拦死，而模型写的其实是对的：

        一把青菜1 + 野葱1 = 2；成交后摊位库存减少2，林南桥持有2，不得回到摊位
        → 抓到 [1,1,2,2,2]，前四个加起来 6 ≠ 2 → 判「对不上」
        → 而算式本身 1 + 1 = 2 完全正确

        明确可见0至1 + 部分可见0至1 + 遮挡0 + 画外0至1 = 1
        → 「0至1」是区间，一项出两个数，怎么加都不对

    所以只认等号左边那个加法式，且每一项必须是**一个确定的数**；
    出现区间、或者根本没有等号，一律返回 None ——
    我们对格式的假设不该变成硬性拦截。这一条上一次就是这么栽的。
    """
    if "=" not in rec and "＝" not in rec:
        return None
    head = re.split(r"[=＝]", rec)[0]
    tail = re.split(r"[=＝]", rec)[1]
    right = _digits(tail)
    if not right:
        return None
    terms = re.split(r"[+＋]", head)
    if len(terms) < 2:
        return None
    left = 0
    for t in terms:
        n = _digits(t)
        if len(n) != 1:         # 没有数、或者是区间（0至1 会出两个数）
            return None
        left += int(n[0])
    return left, int(right[0])


# ------------------------------------------------- B5 位置状态门控（V5.6 新增）

# 「没有移动事件时不许变」的是哪几维。
#
# **以模板写的为准，不要自己加。** n12 模板原文：
#
#   `authorized_movement_event_id = NONE` 时，这一格只能改变：
#   机位投影、表演、视线、手势、动作阶段。
#   **不能改变**：人物真实的 Zone、Anchor、支撑关系。
#
# 我一开始把 posture_class 和 orientation_yaw_deg 也算了进去，比规则更严 ——
# 结果转个身、换个朝向就被判成「无事件瞬移」，把整批生产拦死。
# 而模板恰恰把「表演、视线、手势」列为**允许改变**，转身就是这一类。
#
# 真的坐下/起身仍然拦得住：那会改 support_binding_id（从 NONE 变成 CHAIR_01），
# 而它在下面这张表里。姿态本身不用单独查。
POSITION_DIMS = ("zone", "anchor_id", "support_binding_id")

# 这几维变了不算瞬移，但值得记一笔 —— 它们是「表演」，不是「站位」。
POSE_DIMS = ("posture_class", "orientation_yaw_deg")

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


# 「没有支撑」的几种写法，都是同一个意思。不归一的话，模型这一格写 ""、
# 下一格写 "NONE"，会被当成解除了支撑 —— 一屏误报。
_NO_VALUE = {"", "none", "null", "n/a", "无", "没有"}


def _norm(v) -> str:
    s = str(v).strip()
    return "" if s.lower() in _NO_VALUE else s


def _moved_dims(a: dict, b: dict) -> list:
    """两个位置状态之间，哪几维变了。

    **按「键在不在」判，不按「值空不空」判。**
    这两件事以前混成一条（空值一律跳过），后果是**起身漏判**：
    坐着时 `support_binding_id = "CHAIR_03"`，站起来变成 `""` ——
    而 `""` 被当成「没写」跳过了，于是「人还坐着又站在别处」这类
    恰恰是这道闸门要拦的错，反而拦不住。

    键没写才是真的没写（老产物、模型漏字段），那种不比。
    """
    out = []
    for d in POSITION_DIMS:
        if d not in a or d not in b:
            continue                      # 没写就是没写，不猜
        x, y = _norm(a[d]), _norm(b[d])
        if x != y:
            out.append(d)
    return out


def _cvs_position_problems(pj: Project, ep: str) -> list:
    """**同一场次内**相邻 CVS 之间：位置变了就必须有一条被批准的移动事件。

    移动事件在第八环节的 vt[] 里（视觉过渡），它得写清
    起点、解除支撑、走哪条 Route、穿哪个 Portal、终点。

    跨场次不查 —— 换场是剪辑，不是瞬移。
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
        # **跨场次不比。** 换场是剪辑：不同时间、不同地点，人当然在别处。
        # V5.6 讲的是「同一个连续动作里没有移动事件却换了位置」——
        # 它举的例子（坐在发布会桌后 → 直接在观众区争吵）就发生在同一场之内。
        # 不加这一条会报出一屏跨场次误报，真问题被淹掉，
        # 然后人干脆把整道闸门放行 —— 那比没有闸门更糟。
        # 没写 scene_id 时保守比一次（宁可误报也别漏掉真瞬移）。
        ps, cs = str(prev.get("scene_id") or ""), str(cur.get("scene_id") or "")
        if ps and cs and ps != cs:
            continue
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

# 每张故事板最多几格。V6.1 从 3×3=9 收到 3。
# 这个数同时是项目设定（storyboard_max_kf_per_sheet），项目里改了以那个为准；
# 这里只作为读不到设定时的兜底。
DEFAULT_MAX_KF_PER_SHEET = 3


def _max_kf(pj: Project) -> int:
    try:
        from . import settings as _st
        n = int(_st.load(pj).get("storyboard_max_kf_per_sheet") or 0)
        return n if n > 0 else DEFAULT_MAX_KF_PER_SHEET
    except Exception:                                   # noqa: BLE001
        return DEFAULT_MAX_KF_PER_SHEET


def sheet_density_gate(pj: Project, only: Optional[list] = None) -> list:
    """一张故事板挂了几个关键帧 —— 超了就在出图之前停下。

    「格」= 一个 KF = 那张纸上的一个画格。一张 sheet 出一张图，
    所以同一个 `sheet_id` 下挂 N 个 KF，就是要模型在一张图里画 N 个时刻。

    实跑撞过 16 格：模型记不住 16 个时刻各自的世界状态，
    于是所有格子的 `source_scstate` 全填成第一个、道具状态和 CVS 打架、
    关键帧的时间和它的来源对不上 —— 审计报的 7 条 BLOCK 里 5 条是这么来的。
    而**画面本身是好看的**，人工一格一格看基本抓不到。

    内容不用减，拆续页就行：8 个关键帧分三张纸画，
    每张纸模型只需要管 3 个时刻。

    没写 `sheet_id` 时按「整包算一张」保守判 —— 那正是实跑那次的样子
    （16 个 KF 全挂在一张 SHEET_A 上，或者干脆没分）。
    """
    cap = _max_kf(pj)
    bad = []
    for ep in _episodes(pj, only):
        for pkg in (pj.stage_data("n12_storyboard", ep) or {}).get("sbpkg") or []:
            seg = pkg.get("seg_id") or pkg.get("sbpkg_id") or "?"
            per: dict = {}
            for kf in pkg.get("kf") or []:
                if not isinstance(kf, dict):
                    continue
                per.setdefault(str(kf.get("sheet_id") or "（没写 sheet_id）"),
                               []).append(str(kf.get("kf_id") or "?"))
            for sheet, kfs in per.items():
                if len(kfs) <= cap:
                    continue
                bad.append(
                    f"{ep} {seg} 的 {sheet} 挂了 {len(kfs)} 个关键帧"
                    f"（{'、'.join(kfs[:4])}{'…' if len(kfs) > 4 else ''}），"
                    f"上限是 {cap} —— 一张图里画这么多格，模型记不住每一格各自的"
                    f"世界状态，会把所有格子的来源状态填成同一个。"
                    f"**内容不用减，拆成续页**：每张 {cap} 格，"
                    f"续页不构成第二套真相。")
    return bad


# 闸门 id → 检查函数。**唯一一份**。
#
# 以前 check_all 和 /api/gates 各写了一份，加一道闸门要改两处 ——
# 漏掉端点那处的表现是 KeyError（还算好的），漏掉 check_all 那处
# 则是新闸门**根本不生效而且不报错**。加闸门只往这里加一行。
CHECKS = {
    "audit_block": lambda pj, only: audit_gate(pj, only),
    "visual_coverage": lambda pj, only: coverage_gate(pj, only),
    "object_count": lambda pj, only: object_count_gate(pj, only),
    "position_state": lambda pj, only: position_gate(pj, only),
    "sheet_density": lambda pj, only: sheet_density_gate(pj, only),
}


def problems_of(pj: Project, gate: str, only: Optional[list] = None) -> list:
    """某一道闸门查出来的问题（不管有没有被放行）。"""
    fn = CHECKS.get(gate)
    return fn(pj, only) if fn else []


def check_all(pj: Project, only: Optional[list] = None) -> dict:
    """跑一遍全部闸门。返回 {闸门: 问题清单}，只含**没被授权放行**的。"""
    out = {}
    for gate in GATES:
        problems = problems_of(pj, gate, only)
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
                 "去「生产」页下面那块「出图出片前的闸门」，"
                 "点对应那一道的「显式放行」并写理由 —— "
                 "理由和时间会记进冻结记录，不是静默跳过。")
    return "\n".join(lines)


# ---------------------------------------------------------------- 拦还是记

# **默认不拦，只记。**
#
# 这几道闸门查的都是连续性细节（道具数对不对、人有没有无故换位、
# 某一格该不该补张覆盖图）。它们各自都是真问题，但**没有一条会让产物
# 做不出来** —— 做出来的东西是完整的，只是某处对不上。
#
# 而拦住的代价是整条线停摆：一个道具的对账数字写错，连角色定妆图都出不来，
# 而定妆图是后面所有环节的地基。实跑就是这样 —— 四步生产四行一模一样的
# 「12 处」，其中一半还是我们自己判错的。
#
# 所以默认改成：**照常生产，问题记在失败清单里（提醒级）**。
# 真想要硬拦的，把项目参数里的 gates_block 设成 true。
def should_block(params: Optional[dict] = None) -> bool:
    return bool((params or {}).get("gates_block"))


def gate_notes(blocked: dict) -> list:
    """闸门查出来的问题 → 一条条提醒（不是失败）。

    每道闸门记一条，带上前几例和总数 —— 一处一条会把失败清单淹掉，
    而淹掉之后人就不看了，等于没记。
    """
    out = []
    for gate, problems in blocked.items():
        head = "；".join(problems[:3])
        more = f"（共 {len(problems)} 处，其余见「生产」页的闸门面板）" \
            if len(problems) > 3 else ""
        out.append((gate, f"{GATES[gate]}：{head}{more}"))
    return out
