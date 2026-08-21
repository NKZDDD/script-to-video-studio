# -*- coding: utf-8 -*-
"""分析→资产 之间的整批闸拆除：出图出片改成「泵」。

原来：所有集的 LLM 环节跑完（pool.map 屏障）→ 装配 → 三类出图任务
一次性声明、一次性 run_chain。EP01 上午分析完，它的资产图也要等到
EP21 落盘才开始 —— 中间那段服务商并发额度完全空转。

现在：三个出图泵和分析**同时**起跑。泵循环重扫 tasks.json
（每集环节5/8 落盘都会重装一遍，装配侧本来就是增量的），
捡到没派过的新任务就 run_chain 一轮。

两个最容易写错、写错又不报错的语义，这个文件钉死：

  · **finished 延迟** —— relay.finished() 只能等「分析全完 + 最终清空那轮
    没有新任务」才发。中间轮发了 = 假 finished，等它产物的任务
    瞬间从「等」变「条件不具备」，成批误杀。

  · **「没做成」不是终判，重入要有新信息** —— 一条任务这轮没做成
    （含「条件不具备，没发请求」），等 relay 的声明清单**涨了版本**
    （别的集分析落盘、新任务出生）就重进一轮。版本不涨就每 5 秒
    重试是白撞：服务商拒绝的提示词再发一次还是被拒。
"""
import os
import shutil
import tempfile
import threading
import time
import unittest

from core import pipeline as PL
from core.relay import Relay


class _PJ:
    """泵只需要 tasks() 和 p()；产物文件真往临时目录里写。"""

    def __init__(self, root):
        self.root = root
        self.tasks_by_key: dict = {}

    def p(self, *parts):
        return os.path.join(self.root, *parts)

    def tasks(self):
        return self.tasks_by_key


def _t(key, out):
    return {"key": key, "output": out, "reference_images": []}


_STEP = {"task_key": "asset_tasks", "batch": "asset", "produce": "asset",
         "label": "环节5b 固定资产图"}


class PumpTests(unittest.TestCase):
    """泵循环本身 —— 不碰 LLM、不碰 run_chain，只验证调度语义。"""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.pj = _PJ(self.dir)
        self.relay = Relay(self.pj)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _make(self, rel):
        full = self.pj.p(*rel.split("/"))
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 4000)

    def _start(self, run_chunk, llm_all_done, should_stop=None):
        """起一个泵线程（真实短扫描间隔 —— 不 mock time.sleep，
        那是全局模块，mock 了连主线程的等待都会变成假睡）。"""
        chunks: list = []

        def wrapped(s, todo):
            chunks.append([t["key"] for t in todo])
            return run_chunk(s, todo)

        th = threading.Thread(
            target=PL._pump,
            args=(self.pj, _STEP, wrapped, llm_all_done,
                  should_stop or (lambda: False),
                  self.relay, lambda m: None),
            kwargs={"interval": 0.01},
            daemon=True)
        th.start()
        return th, chunks

    # ---- 核心语义 --------------------------------------------------------

    def test_new_tasks_are_picked_up_across_rounds(self):
        """★ 分析中途出生的任务，下一轮被捡到 —— 闸真的拆了。"""
        self.pj.tasks_by_key = {"asset_tasks": [_t("A", "02/A.png")]}

        def run_chunk(s, todo):
            for t in todo:
                self._make(t["output"])
            return len(todo)

        llm_done = threading.Event()
        th, chunks = self._start(run_chunk, llm_done.is_set)
        try:
            time.sleep(0.05)
            # 分析中途：EP05 的环节5 落盘，S003 的任务出生
            self.pj.tasks_by_key["asset_tasks"].append(_t("S003", "02/S003.png"))
            time.sleep(0.15)
        finally:
            llm_done.set()
            th.join(timeout=5)
        sent = [k for c in chunks for k in c]
        self.assertIn("A", sent, "第一批任务没被派出去")
        self.assertIn("S003", sent, "中途出生的任务没被捡到 —— 闸还在")

    def test_finished_is_sent_only_after_llm_done_and_drained(self):
        """★ finished 延迟：中间轮清空绝不发 finished。

        发早了，等这批产物的下游任务会从「等上游」瞬间变
        「条件不具备」—— 成批误杀，还都不报错。
        """
        self.pj.tasks_by_key = {"asset_tasks": [_t("A", "02/A.png")]}

        def run_chunk(s, todo):
            for t in todo:
                self._make(t["output"])
            return len(todo)

        llm_done = threading.Event()
        finished_at: list = []
        orig = self.relay.finished
        self.relay.finished = lambda kind: (finished_at.append(kind), orig(kind))[1]
        th, chunks = self._start(run_chunk, llm_done.is_set)
        try:
            time.sleep(0.08)               # A 已做完、清单已空 —— 中间轮
            self.assertEqual(finished_at, [],
                             "中间轮清空就发了 finished —— 下游会被成批误杀")
        finally:
            llm_done.set()
            th.join(timeout=5)
        self.assertEqual(len(finished_at), 1, "最终清空后 finished 恰好一次")

    def test_failed_tasks_reenter_when_the_universe_grows(self):
        """★ 「没做成」不是终判：声明清单涨了版本就重进一轮。

        A 永远做不成（产物不落盘）。第一轮之后它不该每 5 秒白撞一次 ——
        但 EP05 分析落盘、S003 出生（版本号涨了）之后，A 必须跟着重进：
        它缺的参考图可能正是刚出生的那批。
        """
        self.pj.tasks_by_key = {"asset_tasks": [_t("A", "02/A.png")]}

        def run_chunk(s, todo):
            for t in todo:
                if t["key"] != "A":            # A 永远做不成；别的都做成
                    self._make(t["output"])
            return len(todo)

        llm_done = threading.Event()
        th, chunks = self._start(run_chunk, llm_done.is_set)
        try:
            # 中途：EP05 的环节5 落盘，S003 出生 → 声明清单涨版本
            time.sleep(0.05)
            self.pj.tasks_by_key["asset_tasks"].append(
                _t("S003", "02/S003.png"))
            deadline = time.time() + 5
            while len(chunks) < 2 and time.time() < deadline:
                time.sleep(0.02)
            self.assertGreaterEqual(len(chunks), 2,
                                    "清单涨了版本，泵却没再派过一轮")
            self.assertIn("A", chunks[1],
                          "清单涨了，没做成的 A 没跟着重进 —— 一次失败变永久丢失")
            self.assertIn("S003", chunks[1], "新出生的 S003 没被捡到")
        finally:
            llm_done.set()
            th.join(timeout=5)

    def test_failed_tasks_do_not_hammer_without_new_info(self):
        """★ 反面：版本不涨就不重试 —— 服务商拒绝的提示词 5 秒后再发还是被拒。

        llm_done 之前 A 只被派过**一次**。（收摊那轮再跑一次是设计：
        那等于用户「再点一次开始」，清单已定死，不是白撞。）
        """
        self.pj.tasks_by_key = {"asset_tasks": [_t("A", "02/A.png")]}

        def run_chunk(s, todo):
            return len(todo)                    # 谁也做不成，产物不落盘

        llm_done = threading.Event()
        th, chunks = self._start(run_chunk, llm_done.is_set)
        try:
            time.sleep(0.2)                 # 泵空转二十来轮
        finally:
            mid = len(chunks)               # 收摊轮之前的派单数
            llm_done.set()
            th.join(timeout=5)
        a_rounds = sum(1 for c in chunks[:mid] if "A" in c)
        self.assertEqual(a_rounds, 1,
                         f"没有新信息还重试了 {a_rounds} 次 —— 每次都是白撞")

    # ---- 停止与收摊 ------------------------------------------------------

    def test_stop_signal_ends_the_pump_promptly(self):
        """用户点取消 / 整批熔断：泵必须立刻收手，不能等下一轮扫描。"""
        self.pj.tasks_by_key = {"asset_tasks": []}
        stop = threading.Event()

        def run_chunk(s, todo):
            return 0

        llm_done = threading.Event()
        th, _ = self._start(run_chunk, llm_done.is_set, stop.is_set)
        time.sleep(0.05)
        t0 = time.time()
        stop.set()
        th.join(timeout=3)
        self.assertFalse(th.is_alive())
        self.assertLess(time.time() - t0, 1.0, "取消信号来了泵还赖着不走")

    def test_abort_mid_pump_stops_sending(self):
        """一轮中途整批熔断（余额/密钥）：后面出生的任务绝不再发。"""
        self.pj.tasks_by_key = {"asset_tasks": [_t("A", "02/A.png")]}
        stop_flag = [False]
        sent: list = []

        def run_chunk(s, todo):
            sent.extend(t["key"] for t in todo)
            stop_flag[0] = True                 # 这一轮触发了熔断
            return len(todo)

        llm_done = threading.Event()
        th, _ = self._start(run_chunk, llm_done.is_set,
                            lambda: stop_flag[0])
        try:
            deadline = time.time() + 5
            while not sent and time.time() < deadline:
                time.sleep(0.01)
            # 熔断之后清单又长了（分析还在跑）—— 但泵已经收手了
            self.pj.tasks_by_key["asset_tasks"].append(
                _t("B", "02/B.png"))
            time.sleep(0.1)
        finally:
            llm_done.set()
            th.join(timeout=5)
        self.assertEqual(sent, ["A"], "熔断之后还接着派 —— 后面全是白花钱")

    def test_final_round_reports_leftovers(self):
        """最终清空那轮还有没做成的 → 返回值要能驱动步骤标 failed。"""
        self.pj.tasks_by_key = {"asset_tasks": [_t("A", "02/A.png")]}

        def run_chunk(s, todo):
            return 0                            # 派了但一条没做成

        left = PL._pump(self.pj, _STEP, run_chunk, lambda: True,
                        lambda: False, self.relay, lambda m: None,
                        interval=0.01)
        self.assertEqual(left, 1, "最终清空还有剩的，泵却报 0 —— 步骤会错标成功")


if __name__ == "__main__":
    unittest.main()
