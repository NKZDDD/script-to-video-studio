# -*- coding: utf-8 -*-
"""项目目录 = 唯一真相源；state.json 只记运行态。所有写入带锁。"""

from __future__ import annotations

import itertools
import json
import os
import threading
import time
from typing import Any, Callable, Optional

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


# ------------------------------------------------ 失败原文（两套体系共用）
def _stop_line(llm) -> str:
    """「上一次是怎么结束的」——判断截断属于哪一类，唯一有用的就是这一行。

    这份文件多半会被单独发出去（排错包里只有它，没有运行日志）。
    少了这一行，收到的人分不出下面三种，而它们的修法完全相反：

        结束原因=length  真撞上限了 → 调大上限，或者把活拆小
        结束原因=stop    模型「以为」自己写完了 → **调上限没有任何用**
        结束原因=（服务商没给）  多半是中转站切的 → 查线路，别动模型参数

    实跑在这上面耗过一整轮：三次全是断流，一直往「调上限」的方向排。
    """
    last = getattr(getattr(llm, "_last", None), "stop", None)
    if not last:
        return ""
    from .llm import stop_note
    u = last.get("usage") or {}
    got = (f"，服务商记账输出 {u['completion_tokens']} token"
           if u.get("completion_tokens") else "")
    return f"结束原因：{stop_note(last.get('reason', '')).strip()}{got}\n"


def keep_partial(pj: Project, stage_id: str, episode: str = "",
                 segment: str = "", llm=None) -> Callable:
    """返回一个「把即将丢弃的模型输出存下来」的回调。

    为什么必须存：断流和 JSON 校验不过时，收到的内容原本是直接丢掉的。
    结果是你只知道「收到 9091 字然后断了」，但不知道断在第几个字段、
    模型是不是正在写某个超长数组、还是根本跑偏了 ——
    而那是排「老是断在中途」唯一有用的证据。一次断三遍就是丢三份。

    存在 07_检查与记录/失败原文/ 下，文件名带环节和段号，同一次跑多次失败
    各存一份（带序号），不互相覆盖。

    **文件头要写清是谁答的。** 这一份多半会被单独发给别人看，
    脱离了当时的日志 —— 不写模型和线路的话，收到的人第一句话就得回问
    「你用的哪个模型」，一来一回半天。时间同理：对得上日志才查得下去。
    """
    seq = itertools.count(1)

    def save(text: str, why: str) -> None:
        who = "_".join(x for x in (stage_id, episode, segment) if x)
        name = f"{who}_{next(seq):02d}.txt"
        path = pj.p("07_检查与记录", "失败原文", name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        head = (f"环节 {stage_id}　{episode or '全剧'}"
                f"{('　' + segment) if segment else ''}\n"
                f"时间：{time.strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"模型：{getattr(llm, 'model', '') or '（没记到）'}"
                f"　线路：{_host(getattr(llm, 'base_url', '')) or '（没记到）'}"
                f"　流式：{'开' if getattr(llm, 'stream', None) else '关'}"
                f"　输出上限：{getattr(llm, 'max_tokens', '') or '（没设）'}\n"
                + _stop_line(llm)
                + f"原因：{why}\n"
                f"收到 {len(text)} 字\n"
                + "-" * 60 + "\n")
        write_text(path, head + text)

    return save


def _host(base_url: str) -> str:
    """从 base_url 取域名当「哪条线路」。**不含 key**，可以安全落盘外发。"""
    s = str(base_url or "").split("//", 1)[-1]
    return s.split("/", 1)[0] or ""
