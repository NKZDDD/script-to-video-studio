# -*- coding: utf-8 -*-
"""项目目录 = 唯一真相源；state.json 只记运行态。所有写入带锁。"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Optional

# 同一把可重入锁保护所有 JSON 读写。
# 必须连读也锁住：Windows 上 os.replace 目标文件若被别的线程打开着读，会 WinError 5。
LOCK = threading.RLock()
_LOCK = LOCK  # 兼容旧引用

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
    with LOCK:
        if not os.path.isfile(path):
            return default
        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return default


def write_json(path: str, data: Any) -> None:
    with LOCK:
        os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
        tmp = f"{path}.{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        # Windows 上偶发被杀软/索引器短暂占用，重试几次
        for i in range(5):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                if i == 4:
                    raise
                time.sleep(0.05 * (i + 1))


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
    # 一个项目 = 一部剧。全剧共享的产物放 01_剧本与分段/ 下，
    # 逐集的产物放 01_剧本与分段/EP01/ 这样的子目录里。
    # episode 传空 = 全剧级，向后兼容单集项目（老项目目录结构不变）。
    def stage_path(self, stage_id: str, episode: str = "") -> str:
        if episode:
            return self.p("01_剧本与分段", episode, f"{stage_id}.json")
        return self.p("01_剧本与分段", f"{stage_id}.json")

    def stage_data(self, stage_id: str, episode: str = "") -> Any:
        return read_json(self.stage_path(stage_id, episode))

    def save_stage(self, stage_id: str, data: Any, episode: str = "") -> None:
        write_json(self.stage_path(stage_id, episode), data)

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
            # 这个项目用哪套生产体系。老项目没有这个字段 —— 它们本来就是
            # V6.1 跑出来的，回落 v61；换一套去读会把产物全判成「还没做」。
            "system": meta.get("system") or "v61",
            "updated": time.strftime("%Y-%m-%d %H:%M",
                                     time.localtime(os.path.getmtime(root))),
        })
    return out
