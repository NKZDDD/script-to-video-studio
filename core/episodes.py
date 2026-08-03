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


def _find_line(lines: list, anchor: str, start_at: int = 0) -> int:
    """在 lines 里找 anchor 所在行号，从 start_at 往后找。找不到返回 -1。

    三轮由严到宽：整行相等 → 行首匹配 → 包含。
    每一轮都从 start_at 起，保证切出来的集是顺序递增的。
    """
    a = _norm(anchor)
    if not a:
        return -1
    normed = [_norm(x) for x in lines]
    for test in (lambda x: x == a, lambda x: x.startswith(a), lambda x: a in x):
        for i in range(start_at, len(lines)):
            if normed[i] and test(normed[i]):
                return i
    return -1


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
        if not anchor:
            issues.append({"episode": ep, "reason": "环节1 没给出这一集的起始行（start_anchor 为空）"})
            continue
        at = _find_line(lines, anchor, cursor)
        if at < 0:
            # 从头再找一次：模型偶尔把集顺序写乱
            at = _find_line(lines, anchor, 0)
            if at >= 0 and found and at <= found[-1]["start_line"]:
                issues.append({"episode": ep,
                               "reason": f"起始行出现在上一集之前，集顺序可能乱了：{anchor[:40]}"})
                continue
        if at < 0:
            issues.append({"episode": ep, "reason": f"在剧本里找不到这一行：{anchor[:60]}"})
            continue
        found.append({"episode": ep, "title": (r.get("title") or "").strip(),
                      "range": (r.get("range") or "").strip(),
                      "entry_state": r.get("entry_state", ""),
                      "exit_state": r.get("exit_state", ""),
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

    head = "\n".join(lines[:found[0]["start_line"]]).strip() if found else script.strip()
    return {"episodes": found, "issues": issues,
            "preamble": head, "preamble_chars": len(head),
            "total_chars": len(script)}


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
