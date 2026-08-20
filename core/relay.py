# -*- coding: utf-8 -*-
"""出图出片**全程**就绪即派：一条任务的输入齐了就开跑，不等整个阶段。

原来是阶段串行：

    全部资产出完 → 全部场景状态图出完 → 全部故事板出完 → 才开始第一条视频

而依赖关系其实是**逐段独立**的：`EP01-SEG01` 的故事板只需要它自己那张场景
状态图，不需要等另外 239 段；视频要的是自己那一段的有序骨架，别段的和它无关。
所以 SEG01 本可以在别的段还没开始场景状态图的时候就把视频出完，
实际却要等三轮全量。

资产是唯一真的要等齐的一类（全剧共享），而它在自己那一批里已经是就绪即派了。

## 判据是「文件在不在磁盘上」

和这个项目其它「做过没有」的判断同一个口径 —— 重启、换机器、手删产物都认得出来。
不用运行记录：那个在多进程/换家补跑的时候对不上。

## 等不到的时候**不自己报错**

这是刻意的。一条任务的输入永远不会出现时（上游那一批已经跑完、它还是没有），
我们照旧把它派出去，让出图那一层现有的硬停说话：

    参考图指不到文件：SP001。声明了 3 张，只解析出 2 张 —— 少一张就出图，
    脸和场景都会跑掉，所以这里停下。

那条报错已经说得很清楚、而且带着「去哪改」。这里再造一条只会多一种说法，
让同一件事在面板上有两个不同的措辞。
"""

from __future__ import annotations

import threading
from typing import Callable, Optional

from . import probe

# 一条任务最多等自己的输入多久。到点还没齐就照旧派出去 ——
# 让现有的硬停报错，而不是无限挂着。
#
# 上界不是为了正常情况（正常情况由「上游那一批跑完了」这个信号结束等待），
# 是为了防依赖图里出现我们没想到的环：那时候没有任何信号会到来，
# 而**静默挂着是最难查的一种失败**。
MAX_WAIT_SECONDS = 5400


class Relay:
    """一个出图出片阶段里，所有活的「输入齐了没有」。

    用法：
        relay = Relay(pj)
        relay.declare("asset", asset_tasks)      # 谁产出什么
        relay.declare("storyboard", sb_tasks)
        ...
        run_batch(..., ready_of=relay.ready_of("storyboard"))
        relay.finished("asset")                  # 那一批跑完了（成没成都算）
    """

    def __init__(self, pj, log: Optional[Callable] = None):
        self.pj = pj
        self.log = log
        self._lock = threading.RLock()
        self._makers: dict = {}      # 产出路径 → 哪一类活会做它
        self._done_kinds: set = set()
        self._said: set = set()      # 已经说过「在等谁」的任务，别每轮都刷

    # ------------------------------------------------------------ 登记

    def declare(self, kind: str, tasks: list) -> None:
        """这一类活会产出哪些文件。"""
        with self._lock:
            for t in tasks or []:
                out = str(t.get("output") or "")
                if out:
                    self._makers[out] = kind

    def finished(self, kind: str) -> None:
        """这一类活跑完了 —— 成没成都算。

        **这个信号是等待的终点。** 没有它的话，一条输入永远不会出现的任务
        只能等到超时上限，白挂一个多小时。
        """
        with self._lock:
            self._done_kinds.add(kind)

    # ------------------------------------------------------------ 就绪判断

    def _have(self, rel: str) -> bool:
        # 不能用 isfile：0 字节和下了一半的文件都是「文件存在」。
        return probe.have_output(self.pj.p(*rel.split("/")))

    def _missing(self, task: dict) -> list:
        """这一条还缺哪些输入（相对项目根的路径）。"""
        want = []
        for r in (task.get("reference_images") or []):
            f = str(r.get("file_ref") or "")
            # 空的和 http 的都不在这里管：空的由出图那层的硬停报，
            # http 是外部链接，我们等不了也不该等。
            if f and not f.startswith("http"):
                want.append(f)
        for s in (task.get("storyboard_refs") or []):
            f = str(s.get("file_ref") or "")
            if f and not f.startswith("http"):
                want.append(f)
        for key in ("storyboard_ref", "aux_reference"):
            f = str(task.get(key) or "")
            if f and not f.startswith("http"):
                want.append(f)
        return [f for f in dict.fromkeys(want) if not self._have(f)]

    def ready_of(self, kind: str) -> Callable:
        """给 run_batch 用的就绪判断。返回 (能不能跑, 在等什么)。"""

        def ready(task: dict) -> tuple:
            miss = self._missing(task)
            if not miss:
                return True, ""
            with self._lock:
                # 还有别的活会做出这几个文件、而且那一批还没跑完 → 等
                waiting = [f for f in miss
                           if self._makers.get(f)
                           and self._makers[f] not in self._done_kinds]
            if not waiting:
                # 没人会做它了（上游那一批已经跑完，或者压根没这活）——
                # **照旧派出去**，让出图那一层现有的硬停说清缺哪张。
                return True, ""
            return False, "、".join(waiting[:3]) + ("…" if len(waiting) > 3 else "")

        return ready

    def note(self, key: str, what: str) -> str:
        """第一次等某样东西时说一句，之后不再重复刷屏。"""
        with self._lock:
            if key in self._said:
                return ""
            self._said.add(key)
        return f"在等上游产物：{what}（齐了就自动开跑，不用管）"
