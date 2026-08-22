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

from . import diagnose, probe

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
        # 声明清单每长一批（有新的产物会被做出来）就 +1。
        # 泵靠它判断「有没有新信息值得把没做成的再试一轮」——
        # 一条任务「条件不具备」往往是因为它要的文件还没人声明会做，
        # 等声明它的那一环落盘、版本号涨了，这条就该重进一轮。
        self._version = 0

    # ------------------------------------------------------------ 登记

    def declare(self, kind: str, tasks: list) -> None:
        """这一类活会产出哪些文件。幂等 —— 重复声明同一批不涨版本号。"""
        added = False
        with self._lock:
            for t in tasks or []:
                out = str(t.get("output") or "")
                if out and out not in self._makers:
                    self._makers[out] = kind
                    added = True
            if added:
                self._version += 1

    @property
    def version(self) -> int:
        """声明清单的版本号。涨了 = 有新的产物会被做出来（任何一类都算）。"""
        with self._lock:
            return self._version

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
        """给 run_batch 用的就绪判断。返回 (状态, 说明)，状态三种：

            True   可以跑
            False  在等上游那一批，等就对了
            None   **条件不具备**：缺的东西没人会做出来，别派 ——
                   用户原话「他缺少实际条件他不能去做才对」
        """

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
                # 没人会做它了（上游那一批已经跑完，或者压根没这活）。
                #
                # **这里原来是「照旧派出去，让出图那层的硬停说话」。用户否掉了：**
                # 「他缺少实际条件他不能去做才对」。对 —— 派出去撞一次空，
                # 面板上留下的是一条「失败」，而它不是失败，是**条件不具备**。
                # 两者在面板上长得一样，人分不出「这条要修」和「这条在等」。
                #
                # 所以现在返回「不能做」，并带上缺的是哪几个文件。
                # run_batch 会把它标成没做成、写清缺什么，**不发请求**。
                return None, "、".join(miss[:4]) + ("…" if len(miss) > 4 else "")
            return False, "、".join(waiting[:3]) + ("…" if len(waiting) > 3 else "")

        return ready

    def missing(self, task: dict) -> list:
        """这一条**现在**还缺哪些输入 —— 纯磁盘判断，不看谁会做它。

        就绪判断（ready_of）说「不能做」时，那是**那一趟**的结论：当时
        产它的那一环还没跑、或者已经跑完而它没做成。补跑要的是**现在**
        的磁盘状态 —— 上游被补跑成功之后，同一个判断会给出不同的答案。
        """
        return self._missing(task)

    def note(self, key: str, what: str) -> str:
        """第一次等某样东西时说一句，之后不再重复刷屏。"""
        with self._lock:
            if key in self._said:
                return ""
            self._said.add(key)
        return f"在等上游产物：{what}（齐了就自动开跑，不用管）"


# ---------------------------------------------------------------- 条件补跑
def _fresh_hard_errors(pj, step: dict, epoch: str) -> set:
    """这一步里「本趟真报过错」的任务键 —— 重派是白撞的那些。

    判据是失败记录（07_检查与记录/failures.json）：条件不具备、等同批
    上游这些**没发过请求**的失败根本不落记录，天然不在这儿；落了记录
    的都是真花过一次调用的。两条豁免：

      · 记录时间早于 epoch —— 上一趟的旧账。本趟开头那一轮已经按磁盘
        重派过它们了，成败都翻过篇，旧记录不该拦住这趟的补跑。
      · code 是 REF_MISSING —— 排早了撞空。worker 在发请求**之前**
        就停下来报「参考图指不到文件」，重派一次不花一分钱；而它的
        输入这一趟里**可能真的落盘了**（上游慢、等超时被硬派出去的
        就是这一类）。别的「活儿本身缺东西」的码（GHOST_REF 之类）
        输入永远等不来，但它们不豁免 —— 由重派次数上限兜着。
    """
    out = set()
    for d in diagnose.load(pj.root):
        if d.get("stage") != step.get("produce"):
            continue
        if str(d.get("at") or "") < epoch:
            continue
        if d.get("code") == "REF_MISSING":
            continue
        out.add(str(d.get("target") or ""))
    return out


def sweep_redo(pj, steps: list, relay: Relay, *, run_step: Callable,
               todo_of: Callable, log: Callable, epoch: str,
               should_stop: Callable, max_rounds: int = 3,
               max_tries: int = 2) -> None:
    """条件补跑：上一趟没派成的，输入落盘之后自动重派，不用人再点「开始」。

    用户原话：「只要有出现满足条件，没报错的情况，东西还没做出来就要
    重试去做」。就绪即派只在**派单那一刻**检测一次 —— 一条任务那会儿
    输入没齐、被标成「条件不具备」，之后上游补完了，它不会自己复活，
    得等用户再点一次「开始」重扫全盘。这个函数就是把那次重扫自动化：
    出图出片各步收摊之后，再按磁盘现状捞一遍 —— 产物没有、输入齐了、
    而且不是真报过错的，自动重派。真报过错的照旧等人处理：服务商拒绝
    的提示词五秒后再发还是被拒（和泵「版本不涨不重试」同一条规矩）。

    按生产顺序一步步扫（资产→场景状态→故事板→视频）：前一步这一轮
    补出来的文件，后一步同一轮立刻接着用，一条链一次跑通，不用多轮。

    有限轮数 + 每条有限次数：重派的活要么落盘（下次不再捞到）、要么
    落一条新的失败记录（下次进「真报过错」被排除），所以循环收敛；
    上限只是防「记录没落上」的极端情况把循环变成死循环。
    """
    tried: dict = {}
    for _round in range(max_rounds):
        if should_stop():
            return
        picked_any = False
        for s in steps:
            if should_stop():
                return
            hard = _fresh_hard_errors(pj, s, epoch)
            pick = [t for t in todo_of(s)
                    if not relay.missing(t)
                    and t["key"] not in hard
                    and tried.get(t["key"], 0) < max_tries]
            if not pick:
                continue
            picked_any = True
            for t in pick:
                tried[t["key"]] = tried.get(t["key"], 0) + 1
            log(s, f"输入已齐，自动补做 {len(pick)} 项（上一轮没派成的；"
                    f"上游补完了不用再点「开始」）")
            run_step(s, pick)
        if not picked_any:
            return


def reconcile_produce_steps(job, failed: list, steps: list, *,
                            todo_of: Callable, should_stop: Callable) -> None:
    """补跑之后的步骤终态以磁盘为准重算 —— 中间那轮只知道自己那一轮的事。

    两个方向都得修，漏一个都是骗人：

      · 步骤上一轮标了 failed，补跑把剩下的全做完了 → 升回 ok。
        不修的话整个 job 错报 error，人白紧张一遍。
      · 补跑那一轮全成了、步骤标了 ok，但初始轮真报错的还欠着 → 降回
        failed。不修的话「做完了」是假的，缺的条数没人报。
    """
    if should_stop():
        return
    for s in steps:
        label = s["label"]
        st = str((job.items.get(label) or {}).get("state") or "")
        if st in ("", "skipped", "cancelled", "aborted"):
            continue
        left = len(todo_of(s))
        if not left:
            if label in failed:
                failed.remove(label)
            if st == "failed":
                job.set_item(label, state="ok", msg="条件补跑后全部完成")
        elif st != "failed":
            job.set_item(label, state="failed",
                         msg=f"还有 {left} 项没做成（真报错的看失败记录，"
                             f"改完再点一次「开始」）")
            if label not in failed:
                failed.append(label)
