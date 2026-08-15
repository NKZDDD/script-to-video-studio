# -*- coding: utf-8 -*-
"""模型为什么停下来，必须记进日志。

实跑撞过一整轮：n4b 收到 30401 字、JSON 没闭合，日志只写了
「生成完成：输出 16942 token，上限 200000」。于是第一反应是「上限不够」，
往调大上限、开流式、加超时三个方向排查——**三个都不是**。

真正需要的那一个字段（finish_reason）就在响应里，我们却只在
「回复内容为空」和 length 两种情况下用，其余直接丢掉：

  · length      → 真撞上限，调大上限或把活拆小
  · stop 但 JSON 没闭合 → 模型「以为」写完了，或者中转站截断却没设这个字段。
    **调上限没有任何用。**
  · 思考 token 占大半 → 输出预算根本没花在正文上

这两种的修法完全相反，分不出来就只能靠猜。
"""
import unittest

from core import llm as L


class StopNoteTests(unittest.TestCase):

    def test_it_always_says_why_it_stopped(self):
        self.assertIn("结束原因=length", L.stop_note("length", {}))
        self.assertIn("结束原因=stop", L.stop_note("stop", {}))

    def test_a_missing_reason_is_said_out_loud(self):
        """★ 空字符串什么都不显示，就等于这个字段不存在。

        「服务商没给」本身是条线索：中转站截断而不设 finish_reason
        是常见毛病，看得见才查得到。
        """
        note = L.stop_note("", None)
        self.assertIn("服务商没给", note)

    def test_reasoning_tokens_are_reported_when_present(self):
        """★ 思考 token 算进输出预算。占掉一大半时，正文其实没写多少。"""
        note = L.stop_note("stop", {"completion_tokens": 16942,
                                    "completion_tokens_details":
                                        {"reasoning_tokens": 9000}})
        self.assertIn("9000", note)
        self.assertIn("思考", note)

    def test_it_stays_quiet_when_there_is_nothing_to_say(self):
        """没有思考 token 就别写「其中思考 0」—— 噪音会淹掉真信号。"""
        self.assertNotIn("思考", L.stop_note("stop", {"completion_tokens": 100}))

    def test_it_is_appended_not_prepended(self):
        """接在记账行后面，别把原来那行挤走。"""
        self.assertTrue(L.stop_note("stop", {}).startswith("　"))


class WiringTests(unittest.TestCase):
    """算出来不打印等于没算。"""

    def _src(self):
        import inspect
        return inspect.getsource(L)

    def test_both_paths_log_it(self):
        """★ 流式和非流式各有一条记账日志，两条都要带。

        只加一条的话，换个开关就又看不见了。
        """
        src = self._src()
        self.assertGreaterEqual(src.count("stop_note("), 4,
                                "流式两处（有/无 usage）+ 非流式一处 + 定义")


if __name__ == "__main__":
    unittest.main()
