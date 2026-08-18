# -*- coding: utf-8 -*-
"""「失败原文」的文件头要写清这一次是**怎么结束的**。

排错包里只有 failures.json、usage.jsonl 和这份失败原文 —— **没有运行日志**。
而 finish_reason 只进了运行日志。结果发过来的文件长这样：

    环节 n3　全剧
    模型：gpt-5.6-sol　线路：api.paisio.online　流式：开　输出上限：200000
    原因：JSON 校验不过（第 1 次）：上一次的输出在写到一半时被截断了…

看不出是下面哪一种，而它们的修法完全相反：

    结束原因=length          真撞上限 → 调大上限 / 把活拆小
    结束原因=stop            模型以为自己写完了 → **调上限没有任何用**
    结束原因=（服务商没给）    多半是中转站切的 → 查线路，别动模型参数

实跑在这上面耗过一整轮，一直往「调上限」的方向排。
"""
import os
import re
import shutil
import unittest

from core.llm import LLM, LLMFatal
from core.store import keep_partial
from test_v34_run import new_project


def _llm():
    return LLM("k", "https://api.example.com", "gpt-5.6-sol",
               max_tokens=200000, stream=True)


class StopReasonTests(unittest.TestCase):

    def setUp(self):
        self.pj = new_project()

    def tearDown(self):
        shutil.rmtree(self.pj.root, ignore_errors=True)

    def _write(self, llm, why="JSON 校验不过（第 1 次）"):
        keep_partial(self.pj, "n3", llm=llm)("正文", why)
        d = self.pj.p("07_检查与记录", "失败原文")
        return open(os.path.join(d, os.listdir(d)[0]), encoding="utf-8").read()

    def test_the_finish_reason_is_written_down(self):
        """★ 这就是那份文件缺的一行。"""
        llm = _llm()
        # length 会抛 LLMFatal（那是对的），但 _last 在抛之前就该记好了
        with self.assertRaises(LLMFatal):
            llm._finish("内容", "length", {"completion_tokens": 19612})
        head = self._write(llm)
        self.assertIn("结束原因", head)
        self.assertIn("length", head)
        self.assertIn("19612", head, "带上输出 token，好和上限对照")

    def test_a_model_that_thinks_it_finished_looks_different(self):
        """★ 和上面那条必须长得不一样 —— 分不开就等于没写。"""
        llm = _llm()
        llm._finish("内容", "stop", {"completion_tokens": 11420})
        self.assertIn("stop", self._write(llm))

    def test_a_provider_that_gives_nothing_says_so(self):
        llm = _llm()
        llm._finish("内容", "", {})
        self.assertIn("服务商没给", self._write(llm))

    def test_a_mid_stream_break_is_labelled_as_such(self):
        """★ 断流走不到 _finish —— 得单独记，不然会印上一次的原因。"""
        llm = _llm()
        llm._last.stop = {"reason": "（没有收到结束标记，连接中途断了）", "usage": {}}
        self.assertIn("连接中途断了", self._write(llm))

    def test_a_stale_reason_from_the_previous_call_is_not_printed(self):
        """★ 印错比不印更糟：会把人指到完全相反的方向去。"""
        llm = _llm()
        with self.assertRaises(LLMFatal):
            llm._finish("内容", "length", {"completion_tokens": 19612})
        llm._last.stop = None                       # chat() 每次开头做的事
        head = self._write(llm)
        self.assertNotIn("length", head)

    def test_the_header_still_has_everything_it_had_before(self):
        llm = _llm()
        llm._finish("内容", "stop", {})
        head = self._write(llm)
        for must in ("环节 n3", "gpt-5.6-sol", "api.example.com",
                     "流式：开", "输出上限：200000", "原因：", "收到 2 字"):
            self.assertIn(must, head)

    def test_it_survives_an_llm_that_never_ran(self):
        """预览、旧代码路径可能压根没调用过 —— 不能因此炸掉存盘。"""
        head = self._write(_llm())
        self.assertNotIn("结束原因", head)
        self.assertIn("原因：", head)

    def test_it_survives_no_llm_at_all(self):
        head = self._write(None)
        self.assertIn("（没记到）", head)


class ThreadSafetyTests(unittest.TestCase):
    """逐段跑的环节多线程共用同一个 LLM 实例。

    存在实例上的话，A 段的结束原因会被 B 段的覆盖 ——
    于是文件头写的是**别的段**怎么结束的，而这种错完全看不出来。
    """

    def test_each_thread_keeps_its_own(self):
        import threading
        llm = _llm()
        seen = {}

        def worker(name, reason):
            llm._finish("x", reason, {})
            seen[name] = llm._last.stop["reason"]

        ts = [threading.Thread(target=worker, args=(f"t{i}", f"reason{i}"))
              for i in range(6)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        self.assertEqual(seen, {f"t{i}": f"reason{i}" for i in range(6)})

    def test_it_is_thread_local_by_construction(self):
        import inspect
        src = inspect.getsource(LLM.__init__)
        self.assertIn("threading.local()", src)


class ResetTests(unittest.TestCase):

    def test_chat_clears_it_first(self):
        import inspect
        src = inspect.getsource(LLM.chat)
        self.assertIn("self._last.stop = None", src)
        i, j = src.index("self._last.stop = None"), src.index("for attempt in range(retries)")
        self.assertLess(i, j, "得在发请求之前清，清晚了等于没清")

    def test_the_broken_stream_path_records_its_own(self):
        import inspect
        src = inspect.getsource(LLM._stream_body)   # 收流那半边
        self.assertIn("没有收到结束标记", src)
        self.assertLess(src.index("self._last.stop = {"), src.index('keep("流没有正常收尾'),
                        "得在 keep() 之前设好 —— keep 里就要用它")


if __name__ == "__main__":
    unittest.main()
