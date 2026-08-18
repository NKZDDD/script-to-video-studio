# -*- coding: utf-8 -*-
"""52x 要重试、要能换家，报错要说等了多久。

实跑一晚上被 524 咬了三次（电影级的 n4b、n5，通用级环节8 的一段），
**三次都是一次都没重**：

    def _check_status(self, r):
        if r.status_code in (429, 502, 503, 504):   # ← 524 不在这儿
            raise _Retryable(...)
        if r.status_code >= 400:
            raise LLMError(...)                      # ← 落到这儿，判死

`should_failover` 那边也没有 GATEWAY_TIMEOUT，所以也不换家。
于是一次入口抽风就把整个环节判死，下游一串跟着停。

而 52x 是 Cloudflare 边缘自己报的，跟模型答得对不对没有半点关系。

还有一件事：524 快回来和慢回来是**两种毛病**，修法相反 ——
等了 100 秒以上是入口超时（开流式、减输入）；只等了几十秒就回来的
是这一家当下自己出了问题（等一会儿、换线路），去调流式是白调。
所以报错里必须写明等了多久。
"""
import unittest

from core import diagnose
from core.llm import LLM, RETRY_STATUS, LLMError, _Retryable


class Resp:
    def __init__(self, code, text="524: A timeout occurred"):
        self.status_code = code
        self.text = text
        self.encoding = "utf-8"


class RetryStatusTests(unittest.TestCase):

    def _raise(self, code, started=0.0):
        return LLM("k", "https://x", "m")._check_status(Resp(code), started)

    def test_524_is_retryable(self):
        """★ 这就是那个 bug。"""
        with self.assertRaises(_Retryable):
            self._raise(524)

    def test_the_whole_cloudflare_range_is_retryable(self):
        """520-527 都是边缘自己的毛病，没有一个是「模型答错了」。"""
        for code in (520, 521, 522, 523, 524, 525, 526, 527, 529):
            with self.assertRaises(_Retryable, msg=code):
                self._raise(code)

    def test_the_old_ones_still_are(self):
        for code in (429, 502, 503, 504):
            with self.assertRaises(_Retryable, msg=code):
                self._raise(code)

    def test_real_errors_are_still_fatal(self):
        """★ 别拦过头：400/401/404 重试一百次也是同一个错。"""
        for code in (400, 401, 403, 404, 422):
            with self.assertRaises(LLMError) as cm:
                self._raise(code)
            self.assertNotIsInstance(cm.exception, _Retryable, str(code))

    def test_a_good_response_passes(self):
        self.assertIsNone(self._raise(200))

    def test_the_message_says_how_long_we_waited(self):
        """★ 几十秒 vs 一百多秒是两种毛病，修法相反。"""
        import time
        with self.assertRaises(_Retryable) as cm:
            self._raise(524, started=time.time() - 42)
        self.assertIn("等了 42 秒", str(cm.exception))

    def test_it_does_not_lie_when_we_have_no_clock(self):
        with self.assertRaises(_Retryable) as cm:
            self._raise(524)
        self.assertNotIn("等了", str(cm.exception))

    def test_both_call_sites_pass_the_clock(self):
        """流式和非流式各有一处 —— 只接一处，另一半照样看不出等了多久。"""
        import inspect
        from core import llm as L
        # 流式那半边后来拆成了 _stream_once（起心跳）+ _stream_body（真收流），
        # 发请求那句在后者里 —— 只看前者会以为这条线断了
        for fn in (L.LLM._stream_body, L.LLM._plain_once):
            self.assertIn("_check_status(r, started)", inspect.getsource(fn),
                          fn.__name__)


class FailoverTests(unittest.TestCase):

    def test_a_52x_can_switch_providers(self):
        """★ 别家的入口是另一套配置，换过去多半就通了。"""
        self.assertTrue(diagnose.should_failover({"code": "GATEWAY_TIMEOUT"}))

    def test_a_524_is_still_classified_as_a_gateway_timeout(self):
        d = diagnose.build(RuntimeError("HTTP 524（等了 42 秒）: A timeout occurred"))
        self.assertEqual(d["code"], "GATEWAY_TIMEOUT")

    def test_the_card_separates_the_fast_one_from_the_slow_one(self):
        """★ 只讲慢的那种会把人指去开流式、减输入 —— 对快的那种完全无效。"""
        fix = " ".join(diagnose.CATALOG["GATEWAY_TIMEOUT"]["fix"])
        self.assertIn("等了 N 秒", fix)
        self.assertIn("换一条线路", fix)

    def test_things_that_need_a_content_change_still_do_not_switch(self):
        """别把「换家也一样」的那几条卷进来。"""
        for code in ("CONTENT_REJECTED", "PROMPT_INVALID", "PREREQ_MISSING",
                     "APP_BUG"):
            self.assertFalse(diagnose.should_failover({"code": code}), code)


if __name__ == "__main__":
    unittest.main()
