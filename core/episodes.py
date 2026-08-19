# -*- coding: utf-8 -*-
"""按环节1 给出的集边界，把整部剧本切成一集一份。

为什么这么分工：
  每份剧本的写法都不一样——「第 1 集」「EP1」「第一集」，或者干脆只靠标题样式
  分隔，前面还常挂一大段项目推介。**认边界这件事只有大模型干得了**，写死正则
  等于每来一种格式就得改代码。
  但**切割本身必须是确定性的**：环节1 输出每一集正文的第一行原文（start_anchor），
  代码拿它做精确定位。这样模型只负责"看懂这份怎么分集"，不负责搬运文本——
  模型转述文本会漏字、会翻译、会顺手改写，那是灾难。

切完写进 01_剧本与分段/episodes.json，之后所有环节都以这份为准。
"""

from __future__ import annotations

import re
from typing import Optional

from .store import Project

FILE = "episodes.json"


def _norm(s: str) -> str:
    """比对用的归一化：吃掉全半角空格差异和不可见字符。

    模型抄锚点时极易把「第 1 集」抄成「第1集」，或漏掉行尾空格。
    只在**定位**时用归一化，切出来的正文一律取原文，不受影响。
    """
    s = s.replace("　", " ").replace("\xa0", " ").replace("​", "")
    return re.sub(r"\s+", "", s)


# 锚点至少要这么长才允许做「包含匹配」。
#
# 实跑撞过：环节1 给的锚点是 `10`、`11`、`13`（把集号当成了正文第一行）。
# 两个字符拿去做 `anchor in line`，剧本里上千行都能命中，匹配到哪一行纯属偶然 ——
# 而一旦命中，游标就被带歪，后面每一集都跟着崩。
# 短锚点只允许整行相等或行首匹配，绝不做包含。
_LOOSE_MIN = 6


def _find_line(lines: list, anchor: str, start_at: int = 0) -> int:
    """在 lines 里找 anchor 所在行号，从 start_at 往后找。找不到返回 -1。

    由严到宽：整行相等 → 行首匹配 → 包含。
    **包含只给够长的锚点用**（见 _LOOSE_MIN）——短锚点做包含是在上千行里碰运气。
    每一轮都从 start_at 起，保证切出来的集是顺序递增的。
    """
    a = _norm(anchor)
    if not a:
        return -1
    normed = [_norm(x) for x in lines]
    tests = [lambda x: x == a, lambda x: x.startswith(a)]
    # 包含匹配只给够长的锚点用。短锚点做包含 = 在上千行里碰运气。
    if len(a) >= _LOOSE_MIN:
        tests.append(lambda x: a in x)
    for test in tests:
        for i in range(start_at, len(lines)):
            if normed[i] and test(normed[i]):
                return i
    return -1


def _why_bad_anchor(anchor: str) -> str:
    """这个锚点根本不可能唯一 —— 返回原因；能用就返回空串。

    在**搜索之前**判掉，比搜出一个错位置再事后发现有用得多：
    错位置会把游标带到错的地方，从此每一集都错。
    """
    a = _norm(anchor)
    if not a:
        return "是空的"
    if len(a) < 3:
        return f"只有 {len(a)} 个字符（`{anchor}`），不可能在全剧里唯一"
    if re.fullmatch(r"[\d\W_]+", a):
        return (f"`{anchor}` 里没有文字，看着像集号或分隔符 —— "
                f"要的是**该集正文第一行**，不是章节标记")
    return ""


def split(script: str, ranges: list) -> dict:
    """按 episode_ranges 的 start_anchor 切分。返回 {episodes, issues}。

    每集正文 = 本集锚点行 → 下一集锚点行前一行。最后一集吃到文末。
    锚点定位失败的集会记进 issues，正文为空——不静默跳过，也不猜。
    """
    lines = script.split("\n")
    found, issues = [], []
    cursor = 0

    for idx, r in enumerate(ranges):
        ep = (r.get("episode") or f"EP{idx + 1:02d}").strip()
        anchor = (r.get("start_anchor") or "").strip()
        # 锚点「合不合格」那一关**已经去掉**（skill 只要求给出每集正文的
        # 第一行原文，没有对它的长度或字符做任何规定）。找不到的时候
        # 下面那条「找不到这一行」自然会报出来 —— 那是事实，
        # 而「我觉得这个锚点不够好」是我的判断。
        at = _find_line(lines, anchor, cursor)
        if at < 0:
            # 从头再找一次：模型偶尔把集顺序写乱
            at = _find_line(lines, anchor, 0)
            if at >= 0 and found and at <= found[-1]["start_line"]:
                prev = found[-1]
                issues.append({
                    "episode": ep,
                    "reason": f"起始行在剧本里的位置**比上一集还靠前**（第 {at + 1} 行，"
                              f"而 {prev['episode']} 从第 {prev['start_line'] + 1} 行开始）："
                              f"「{anchor[:40]}」。"
                              f"多半是环节1 给的锚点错位了 —— 比如把上一集的标记"
                              f"当成了这一集的第一行。这一集切不出来，"
                              f"下游全部环节都会在错的分集上工作"})
                continue
        if at < 0:
            issues.append({"episode": ep, "reason": f"在剧本里找不到这一行：{anchor[:60]}"})
            continue
        found.append({"episode": ep, "title": (r.get("title") or "").strip(),
                      "range": (r.get("range") or "").strip(),
                      "entry_state": r.get("entry_state", ""),
                      "exit_state": r.get("exit_state", ""),
                      # 每集多长由环节1 定（它是唯一看得到全篇的环节），
                      # 它给的是**秒数**；段数 = 秒数 ÷ 单段秒数，另算。
                      # 这样换视频模型（一次出 10 秒 / 20 秒）段数自动跟着变。
                      "duration_sec": _duration_sec(r.get("duration_sec")),
                      "key_events": r.get("key_events") or [],
                      "pacing_note": (r.get("pacing_note") or "").strip(),
                      "start_line": at, "anchor": anchor})
        cursor = at + 1

    for i, e in enumerate(found):
        end = found[i + 1]["start_line"] if i + 1 < len(found) else len(lines)
        text = "\n".join(lines[e["start_line"]:end]).strip()
        e["end_line"] = end
        e["script"] = text
        e["chars"] = len(text)

    # 正文异常短的集单独提出来：多半是锚点落在了目录或推介里
    for e in found:
        if e["chars"] < 120:
            issues.append({"episode": e["episode"],
                           "reason": f"切出来只有 {e['chars']} 字，可能锚点落在了目录或简介上，"
                                     f"不是正文"})
        # 时长和正文体量差太远时提醒一句。
        # **注意：这不是定时长的依据** —— 时长按剧情事件定，字数只是事后体检，
        # 因为正文里大半是场景描写不是台词，字数和屏幕时间没有稳定比例。
        # 只在差到离谱（每分钟 100 字以下或 2000 字以上）时才提，让人回去看一眼。
        if e["duration_sec"] and e["chars"] >= 120:
            per_min = e["chars"] / (e["duration_sec"] / 60)
            if per_min < 100 or per_min > 2000:
                issues.append({
                    "episode": e["episode"], "level": "warn",
                    "reason": f"正文 {e['chars']} 字要撑 {e['duration_sec']} 秒 = "
                              f"每分钟 {per_min:.0f} 字。"
                              f"{'内容可能撑不满这个时长（会注水）' if per_min < 100 else '这个时长可能塞不下这些剧情'}"
                              f"；环节1 给的理由：{e.get('pacing_note') or '（没写）'}"})

    head = "\n".join(lines[:found[0]["start_line"]]).strip() if found else script.strip()
    return {"episodes": found, "issues": issues,
            "preamble": head, "preamble_chars": len(head),
            "total_chars": len(script)}


SEC_MIN, SEC_MAX = 60, 600      # 环节1 给的每集秒数的合理区间
FALLBACK_MINUTES = 3            # 只在环节1 没给秒数时兜底（老项目），不是配置项


def _duration_sec(v) -> int:
    """环节1 给的本集秒数。不合理就返回 0（由调用方退回全局参数）。"""
    try:
        n = int(float(v))
    except (TypeError, ValueError):
        return 0
    return n if SEC_MIN <= n <= SEC_MAX else 0


def segs_from_sec(sec: int, clip: int) -> int:
    """秒数 → 段数。

    段是**技术单位**：视频模型一次只能生成 clip 秒，所以段数就是秒数除以它。
    向上取整 —— 段不可分，宁可多 15 秒也不能砍掉剧情的尾巴。
    """
    clip = int(clip or 15) or 15
    return max(1, -(-int(sec) // clip))          # 上取整


def seg_target(pj: Project, episode: str, params: dict) -> tuple:
    """这一集该切几段，以及这个数是怎么来的。

    环节1 给的是**秒数**（它按剧情事件定，看得到全篇）；段数在这里现算 ——
    这样以后换成一次能出 10 秒或 20 秒的视频模型，段数自动跟着变，
    不用重跑环节1。字数完全不参与。

    老项目的产物里没有 duration_sec，退回按「单集分钟 ÷ 单段秒数」算
    （就是以前的行为），这样老项目重跑不会炸。
    """
    clip = int(params.get("duration") or 15) or 15
    for e in load(pj).get("episodes", []):
        if e.get("episode") == episode and e.get("duration_sec"):
            sec = int(e["duration_sec"])
            n = segs_from_sec(sec, clip)
            real = n * clip
            extra = f"，取整到 {real} 秒" if real != sec else ""
            return n, (f"环节1 定本集 {sec} 秒 ÷ 单段 {clip} 秒 = {n} 段{extra}"
                       f"（{e.get('pacing_note') or '没写理由'}）")
    # 兜底常量，不是可配项：每集多长该由环节1 按剧情定，给个全局数字反而会
    # 让人以为「所有集都该这么长」。这里只是老项目（产物里没有 duration_sec）
    # 不至于跑不动。
    n = segs_from_sec(FALLBACK_MINUTES * 60, clip)
    return n, (f"环节1 没给秒数（老项目的产物），按兜底的 {FALLBACK_MINUTES} 分钟 "
               f"÷ {clip} 秒 = {n} 段。重跑环节1 就会按剧情定")


# ---------------------------------------------------------------- 落盘 / 读取
def build(pj: Project, script: str, s1: dict) -> dict:
    """环节1 跑完后调用：切集并存盘。"""
    res = split(script, (s1 or {}).get("episode_ranges") or [])
    res["scope"] = (s1 or {}).get("scope", "")
    pj.save_stage(FILE[:-5], res)          # → 01_剧本与分段/episodes.json
    return res


def load(pj: Project) -> dict:
    return pj.stage_data(FILE[:-5]) or {}


def ids(pj: Project) -> list:
    """集编号列表，供前端下拉和逐集循环用。"""
    return [e["episode"] for e in load(pj).get("episodes", [])]


def script_of(pj: Project, episode: str) -> str:
    """取某一集的正文。取不到就抛，别让空字符串一路传到 LLM。"""
    d = load(pj)
    for e in d.get("episodes", []):
        if e["episode"] == episode:
            if not (e.get("script") or "").strip():
                raise RuntimeError(
                    f"{episode} 的正文是空的。多半是环节1 给的起始行没在剧本里找到，"
                    f"去「流程」页看环节1 的产物 episodes.json 里的 issues，"
                    f"或者重跑环节1。")
            return e["script"]
    have = ", ".join(x["episode"] for x in d.get("episodes", [])) or "（还没切集）"
    raise RuntimeError(f"没有 {episode} 这一集。当前项目里有：{have}")


def summary(pj: Project) -> dict:
    """给前端用的轻量摘要，不带正文。"""
    d = load(pj)
    return {
        "episodes": [{k: v for k, v in e.items() if k != "script"}
                     for e in d.get("episodes", [])],
        "issues": d.get("issues", []),
        "preamble_chars": d.get("preamble_chars", 0),
        "total_chars": d.get("total_chars", 0),
        "scope": d.get("scope", ""),
    }
