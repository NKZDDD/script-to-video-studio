"""删除已生成的产物：挪进回收站，任务留在树上、变回待生产。

只动文件。任务和资源树来自 Agent 的发布，从树上拿掉的东西 Agent 下次发布时又会回来；
而文件一旦真删掉，找回的办法只有重新生成、重新付费，所以挪进回收站而不是删掉。

连带范围按两种关系算：参考图依赖（它拿谁当参考）和资源树父子（身份 → 造型 / 状态）。
  上游（父链路）  它引用的，一直往上
  兄弟链路        和它共用同一个直接上游的其他任务
  下游（子链路）  拿列表里任何一项当参考的，一直往下 —— 它们是用旧图做出来的，
                  旧图没了就对不上
"""
from __future__ import annotations

import os
import time

from . import agent_projects as A, submitted
from .store import write_json

FOLDER = "07_检查与记录/回收站"
_ORDER = {"本身": 0, "上游": 1, "兄弟": 2, "下游": 3}


def _graph(pj):
    data = A.view(pj)
    tasks = {t["key"]: t for t in data["tasks"]}
    owner = {r["id"]: r.get("task_key") for r in data["resources"]}
    names = {r.get("task_key"): r.get("name") for r in data["resources"] if r.get("task_key")}
    parents = {k: set(t["dependencies"]) & set(tasks) for k, t in tasks.items()}
    for r in data["resources"]:
        key, parent = r.get("task_key"), owner.get(r.get("parent_id"))
        if key in parents and parent in tasks and parent != key:
            parents[key].add(parent)
    children = {k: set() for k in tasks}
    for key, ps in parents.items():
        for p in ps:
            children[p].add(key)
    return tasks, parents, children, names


def _walk(start, edges) -> set:
    seen, stack = set(), list(start)
    while stack:
        for n in edges.get(stack.pop(), ()):
            if n not in seen:
                seen.add(n)
                stack.append(n)
    return seen


def plan(pj, key: str, up: bool = False, down: bool = True, siblings: bool = False) -> dict:
    tasks, parents, children, names = _graph(pj)
    if key not in tasks:
        raise ValueError("任务不存在，请刷新")
    why = {key: "本身"}
    if up:
        for k in _walk([key], parents):
            why.setdefault(k, "上游")
    if siblings:
        for p in parents[key]:
            for k in children[p]:
                why.setdefault(k, "兄弟")
    if down:
        for k in _walk(list(why), children):
            why.setdefault(k, "下游")
    items = [{"key": k, "kind": tasks[k]["kind"], "name": names.get(k) or k, "output": tasks[k]["output"],
              "relation": rel, "exists": A.inside(pj.root, tasks[k]["output"]).is_file()}
             for k, rel in why.items()]
    items.sort(key=lambda x: (_ORDER[x["relation"]], x["key"]))
    return {"items": items}


def delete(pj, keys: list, running=()) -> dict:
    rows = {t["key"]: t for t in A.task_rows(pj.tasks(), legacy=True)}
    unknown = [k for k in keys if k not in rows]
    if unknown:
        raise ValueError("这些任务已不在当前计划里，请刷新：" + "、".join(unknown[:5]))
    live = [k for k in keys if k in running]
    if live:
        raise ValueError("这几项正在生成，先停止那一批或等它结束再删：" + "、".join(live[:5]))
    base = f"{FOLDER}/{time.strftime('%Y%m%d-%H%M%S')}"
    moved = []
    for k in keys:
        rel = rows[k]["output"]
        src = A.inside(pj.root, rel)
        if not src.is_file():
            continue
        dest = A.inside(pj.root, f"{base}/{rel}")
        if dest.exists():
            dest = dest.with_name(f"{dest.stem}_{time.time_ns()}{dest.suffix}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        os.replace(src, dest)
        submitted.clear(pj.root, rel)
        moved.append({"key": k, "output": rel,
                      "saved_as": "/".join(f"{base}/{rel}".split("/")[:-1] + [dest.name])})
    if moved:
        write_json(str(A.inside(pj.root, f"{base}/删除清单.json")),
                   {"deleted_at": time.strftime("%Y-%m-%d %H:%M:%S"), "items": moved})
        pj.log_event({"stage": "recycle", "result": "ok", "folder": base, "count": len(moved)})
    return {"ok": True, "moved": moved, "folder": base}
