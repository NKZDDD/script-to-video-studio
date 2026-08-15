# -*- coding: utf-8 -*-
"""断流有两种，长得像，修法相反。

一、**一个字都没收到就断了** —— 模型还在思考。它在吐出第一个 token 之前
    不发任何数据，中间那层看不到字节就掐（灵感鸭说他们往上游那条腿是
    125 秒）。**流式盖不住这段静默**，因为思考期本来就不吐字。
    要减小的是**输入**，让它别想那么久；调输出上限、开关流式全是白费。

二、**写到一半断了** —— 字已经在流了才断，那是真的传输出问题，重试就好。

以前两种共用一句话，而且**两种都报「没见过的错误」** ——
连带 NETWORK 那张卡会把人指去查代理和网络，第一种查了也白查。
"""
import unittest

from core import diagnose


class ClassificationTests(unittest.TestCase):

    def test_nothing_received_is_a_gateway_idle_timeout(self):
        """★ 别落到 NETWORK 上 —— 那张卡会让人去查代理，方向全错。"""
        msg = ("连接断开时一个字都还没收到（等了 127 秒）。"
               "这多半是模型的思考期超过了中转站的空闲上限")
        self.assertEqual(diagnose.code_of(msg), "GATEWAY_TIMEOUT")

    def test_a_mid_stream_break_is_a_network_problem(self):
        msg = "连接在流传输中途断开：只收到 13778 字就没了（等了 240 秒）"
        self.assertEqual(diagnose.code_of(msg), "NETWORK")

    def test_neither_falls_through_to_unknown(self):
        """★ 这两条以前都报「没见过的错误」。"""
        for msg in ("连接断开时一个字都还没收到（等了 127 秒）",
                    "连接在流传输中途断开：只收到 100 字就没了"):
            self.assertNotEqual(diagnose.code_of(msg), "UNKNOWN", msg)

    def test_the_gateway_card_says_streaming_will_not_help_here(self):
        """★ 这一条最容易指错：524 的主解法是「打开流式」，

        但思考期静默这一种流式救不了。同一张卡里必须把这个例外说清楚，
        否则人会反复去确认「我流式明明开着啊」。
        """
        d = diagnose.CATALOG["GATEWAY_TIMEOUT"]
        fix = "　".join(d["fix"])
        self.assertIn("一个字都没收到", fix)
        self.assertIn("流式也救不了", fix)
        self.assertIn("减小**输入**", fix)


class MessageTests(unittest.TestCase):

    def test_the_two_messages_are_actually_different(self):
        """算出来不分开说，等于没分。"""
        import inspect

        from core import llm
        src = inspect.getsource(llm._Client if hasattr(llm, "_Client") else llm)
        self.assertIn("一个字都还没收到", src)
        self.assertIn("流传输中途断开", src)


if __name__ == "__main__":
    unittest.main()
