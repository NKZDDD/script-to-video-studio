# -*- coding: utf-8 -*-
"""链路没走通就自己重发 —— 不要让人守着点「开始」。

用户原话（2026-08-21）：「实际上我人工手动再点一次任务开始就可以了，
所以我才想到用重试来解决这个超时的问题」。

**这一条推翻了我之前的判断。** 我原来写的理由是「同一个提示词重试必然同样
超时，只是把钱花三倍」，并据此把超时归成不可重试。那对**内容类**失败成立
（撞长度上限：同样的内容装不进同一个上限），对**传输类**不成立 ——
读超时、流式卡住是链路状况，换一次连接、换一个边缘节点结果真的会不一样。
而用户手动点一次就成，就是证据。

排错包 …_0821_1603 里 n5 的三次：
  15:26  收到 115 字 → 流式卡住（这一条本来就会重试）
  15:29  收到 20813 字 → 括号没闭合（13765/128000，不是撞上限，是中途被切）
  15:29  重发 72008 字 → 900 秒一个字没回 → 读超时 → **以前到这儿就放弃了**
"""
import unittest

import core.llm as L


class _Fake(L.LLM):
    """不走 __init__ 的假引擎：只控制「第几次调用之前都失败」。"""

    def __init__(self, fails, exc=None):
        self.calls = 0
        self._fails = fails
        self._exc = exc or (lambda: L.LLMTransport("等了 900 秒还没收到新内容"))
        self.max_tokens = 1000
        self.model = "m"

    def chat(self, system, user, **kw):
        self.calls += 1
        self._seen = user
        if self.calls <= self._fails:
            raise self._exc()
        return '{"a": [1]}'


class TransportRetryTests(unittest.TestCase):

    def setUp(self):
        self._old = L.TRANSPORT_BACKOFF
        L.TRANSPORT_BACKOFF = 0          # 测试里别真等
        self.addCleanup(lambda: setattr(L, "TRANSPORT_BACKOFF", self._old))

    def test_one_timeout_then_success(self):
        """★ 这就是用户手动做的那件事，现在程序自己做。"""
        f = _Fake(1)
        self.assertEqual(f.json_call("s", "u", required=["a[]"],
                                     log=lambda m: None), {"a": [1]})
        self.assertEqual(f.calls, 2)

    def test_it_resends_the_original_text_unchanged(self):
        """★ 附一句「上次输出的问题」是错的 —— 上次压根没有输出。

        加了那句话，模型会以为自己写坏了，然后真的去改内容
        （删字段、压缩条目），而问题在链路上。
        """
        f = _Fake(1)
        f.json_call("s", "原始正文", required=["a[]"], log=lambda m: None)
        self.assertEqual(f._seen, "原始正文")

    def test_the_budget_is_bounded(self):
        f = _Fake(99)
        with self.assertRaises(L.LLMTransport):
            f.json_call("s", "u", required=["a[]"], log=lambda m: None,
                        transport_retries=2)
        self.assertEqual(f.calls, 3, "1 次 + 2 次重发")

    def test_zero_means_off(self):
        f = _Fake(99)
        with self.assertRaises(L.LLMTransport):
            f.json_call("s", "u", required=["a[]"], log=lambda m: None,
                        transport_retries=0)
        self.assertEqual(f.calls, 1)

    def test_it_does_not_eat_the_json_retry_budget(self):
        """★ 占同一个额度的话：一次超时之后只剩一次机会给「答得不合格」——

        而那是两码事的预算。
        """
        f = _Fake(1)
        f.json_call("s", "u", required=["a[]"], log=lambda m: None,
                    json_retries=2, transport_retries=2)
        self.assertEqual(f.calls, 2)

    def test_a_length_cap_is_still_not_retried(self):
        """★ 同样的内容装不进同一个上限 —— 重试只是把钱花三倍。"""
        f = _Fake(99, lambda: L.LLMFatal("模型输出撞到了长度上限"))
        with self.assertRaises(L.LLMFatal):
            f.json_call("s", "u", required=["a[]"], log=lambda m: None)
        self.assertEqual(f.calls, 1)

    def test_transport_is_a_subclass_so_old_catchers_still_work(self):
        """★ 外面按 LLMFatal 捕获的地方行为不变 —— 只有认这个子类的才重。"""
        self.assertTrue(issubclass(L.LLMTransport, L.LLMFatal))

    def test_cancel_during_the_backoff_stops_at_once(self):
        """★ 退避里不看取消的话，点了取消还要干等一轮。"""
        L.TRANSPORT_BACKOFF = 5
        f = _Fake(99)
        with self.assertRaises(L.LLMCancelled):
            f.json_call("s", "u", required=["a[]"], log=lambda m: None,
                        cancel=lambda: True)

    def test_the_log_says_it_is_the_line_not_the_content(self):
        """★ 不说的话，人看到「重发」会以为是模型答错了，跑去改提示词。"""
        logs = []
        _Fake(1).json_call("s", "u", required=["a[]"], log=logs.append)
        blob = " ".join(logs)
        self.assertIn("链路重发", blob)
        self.assertIn("不改内容", blob)

    def test_the_timeout_message_no_longer_claims_it_gave_up(self):
        """★ 原来那句话写着「已放弃，没有重试」—— 现在会重试，那句话成了假的。"""
        import io
        import os
        src = io.open(os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "core", "llm.py"), encoding="utf-8").read()
        self.assertNotIn("判定超时（已放弃，没有重试", src)
        self.assertIn("所以会自动重发", src)


if __name__ == "__main__":
    unittest.main()
