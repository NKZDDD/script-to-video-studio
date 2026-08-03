# -*- coding: utf-8 -*-
"""项目目录 = 唯一真相源；state.json 只记运行态。所有写入带锁。"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Optional

_LOCK = threading.RLock()

# 与 skill V6.1 标准生产包目录对齐
DIRS = [
    "00_项目说明",
    "01_剧本与分段",
    "02_固定资产/人物身份资产", "02_固定资产/群体资产", "02_固定资产/生物资产",
    "02_固定资产/场景资产", "02_固定资产/道具资产", "02_固定资产/连续状态资产",
    "03_提示词/资产生产提示词", "03_提示词/故事板提示词",
    "03_提示词/视频提示词", "03_提示词/定向修订提示词",
    "04_故事板", "05_分段视频", "06_成片", "07_检查与记录",
]


def read_json(path: str, default: Any = None) -> Any:
    if not os.path.isfile(path):
        return default
    with open(path, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def write_json(path: str, data: Any) -> None:
    with _LOCK:
        os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)


def read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8-sig") as f:
        return f.read().strip()


def write_text(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


class Project:
    """一个项目目录的读写门面。"""

    def __init__(self, root: str):
        self.root = os.path.abspath(root)

    # -- 路径 -----------------------------------------------------------
    def p(self, *parts: str) -> str:
        return os.path.join(self.root, *parts)

    def rel(self, path: str) -> str:
        return os.path.relpath(path, self.root).replace("\\", "/")

    def init_dirs(self) -> None:
        for d in DIRS:
            os.makedirs(self.p(*d.split("/")), exist_ok=True)

    # -- 元信息 ---------------------------------------------------------
    @property
    def meta_path(self) -> str:
        return self.p("00_项目说明", "project.json")

    def meta(self) -> dict:
        return read_json(self.meta_path, {}) or {}

    def save_meta(self, data: dict) -> None:
        write_json(self.meta_path, data)

    # -- 环节产物 -------------------------------------------------------
    def stage_path(self, stage_id: str) -> str:
        return self.p("01_剧本与分段", f"{stage_id}.json")

    def stage_data(self, stage_id: str) -> Any:
        return read_json(self.stage_path(stage_id))

    def save_stage(self, stage_id: str, data: Any) -> None:
        write_json(self.stage_path(stage_id), data)

    # -- 任务清单 -------------------------------------------------------
    @property
    def tasks_path(self) -> str:
        return self.p("03_提示词", "tasks.json")

    def tasks(self) -> dict:
        return read_json(self.tasks_path, {}) or {}

    def save_tasks(self, data: dict) -> None:
        write_json(self.tasks_path, data)

    # -- 注册表 ---------------------------------------------------------
    def registry_path(self, kind: str) -> str:
        return self.p("07_检查与记录", f"{kind}_registry.json")

    def registry(self, kind: str) -> list:
        return read_json(self.registry_path(kind), []) or []

    def upsert_registry(self, kind: str, entry: dict, key: str = "id") -> None:
        with _LOCK:
            items = self.registry(kind)
            for i, it in enumerate(items):
                if it.get(key) == entry.get(key):
                    items[i] = entry
                    break
            else:
                items.append(entry)
            write_json(self.registry_path(kind), items)

    # -- 执行日志 -------------------------------------------------------
    def log_event(self, event: dict) -> None:
        with _LOCK:
            event = dict(event)
            event["at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            path = self.p("07_检查与记录", "execution_log.jsonl")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(event, ensure_ascii=False) + "\n")


def list_projects(base: str) -> list:
    """扫描 projects 根目录下的项目。"""
    out = []
    if not os.path.isdir(base):
        return out
    for name in sorted(os.listdir(base)):
        root = os.path.join(base, name)
        if not os.path.isdir(root):
            continue
        pj = Project(root)
        meta = pj.meta()
        out.append({
            "name": name,
            "root": root,
            "title": meta.get("title") or name,
            "project_code": meta.get("project_code", ""),
            "episode": meta.get("episode", ""),
            "updated": time.strftime("%Y-%m-%d %H:%M",
                                     time.localtime(os.path.getmtime(root))),
        })
    return out
