"""外部图片批量放进任务的输出位置：按文件名对应任务，先给人确认再放。

「做过没有」只看输出路径上有没有完整的图。别处拿来的图名字、格式、文件夹差一点
程序就认不出，一张张手动放又太慢。这里只负责「哪个文件对应哪个任务」，
真正存图走手动放图同一条路（格式按输出路径转换、完整校验、原子写入）。

对应规则按可信度排：文件名和输出文件名相同 > 和任务 ID / 资源 ID 相同 > 和资源名称相同，
再往下是包含关系。全角半角、大小写、空格和常见标点都不算差别。
同分的几个任务不替人挑；两个文件对应到同一个任务时只留分高的那个。
"""
from __future__ import annotations

import os
import re
import unicodedata

from . import agent_projects as A

IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp")
_SEP = re.compile(r"[\s\-_·.,，。、:：;；()（）\[\]【】{}「」《》'\"`~!！?？]+")


def _norm(text) -> str:
    s = unicodedata.normalize("NFKC", str(text or "")).strip().lower()
    base, ext = os.path.splitext(s)
    if ext in IMAGE_EXT:
        s = base
    return _SEP.sub("", s)


_CJK = re.compile(r"[\u3400-\u9fff]")


def _score(name: str, marks: list) -> int:
    # 中文名常常只有两三个字（「云黑」），按英文的 4 个字符算就一张都对不上。
    shortest = 2 if _CJK.search(name) else 4
    best = 0
    for weight, mark in marks:
        if not mark or not name:
            continue
        if name == mark:
            best = max(best, weight)
        elif len(mark) >= 2 and mark in name:
            best = max(best, weight - 20)
        elif len(name) >= shortest and name in mark:
            best = max(best, weight - 40)
    return best


def plan(pj, names: list) -> dict:
    data = A.view(pj)
    res = {r.get("task_key"): r for r in data["resources"] if r.get("task_key")}
    tasks = []
    for t in data["tasks"]:
        if t["kind"] == "video":
            continue
        r = res.get(t["key"]) or {}
        stem = os.path.splitext(t["output"].rsplit("/", 1)[-1])[0]
        tasks.append((t, r, A.inside(pj.root, t["output"]).is_file(),
                      [(100, _norm(stem)), (95, _norm(t["key"])), (95, _norm(r.get("id"))),
                       (90, _norm(r.get("name")))]))
    rows = []
    for name in names:
        key = _norm(os.path.basename(str(name)))
        scored = sorted(((s, t, r, exists) for t, r, exists, marks in tasks
                         if (s := _score(key, marks))), key=lambda x: -x[0])[:5]
        cands = [{"key": t["key"], "kind": t["kind"], "name": r.get("name") or t["key"],
                  "output": t["output"], "score": s, "exists": exists} for s, t, r, exists in scored]
        row = {"name": os.path.basename(str(name)), "candidates": cands, "choice": "", "note": ""}
        if not cands:
            row["note"] = "对应不上任何出图任务，可以输入任务 ID 手动选"
        elif len(cands) > 1 and cands[0]["score"] == cands[1]["score"]:
            row["note"] = "有几个任务一样像，请手动选一个"
        elif cands[0]["exists"]:
            row["note"] = f"对应的 {cands[0]['key']} 已经有文件了，先删除那张再放"
        else:
            row["choice"] = cands[0]["key"]
        rows.append(row)
    taken: dict = {}
    for i, row in enumerate(rows):
        if not row["choice"]:
            continue
        j = taken.get(row["choice"])
        if j is None:
            taken[row["choice"]] = i
            continue
        keep, drop = (j, i) if rows[j]["candidates"][0]["score"] >= row["candidates"][0]["score"] else (i, j)
        rows[drop]["choice"] = ""
        rows[drop]["note"] = f"和 {rows[keep]['name']} 对应到同一个任务 {rows[keep]['choice']}，请手动选"
        taken[rows[keep]["choice"]] = keep
    return {"rows": rows}
