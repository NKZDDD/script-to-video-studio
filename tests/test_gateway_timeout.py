# -*- coding: utf-8 -*-
"""HTTP 52x 是中转站入口超时，不是模型慢 —— 改法完全相反。

真跑撞到的：环节1 关掉流式之后报

    HTTP 524: <!DOCTYPE html> … paisio.online | 524: A timeout occurred

524 是 Cloudflare 的「源站没在约 100 秒内开始回数据」。那一刻模型还在算。
同一步开着流式跑了 406 秒**成功了** —— 因为流式下数据一直在流，
Cloudflare 看得见就不会超时。

两个坑，都踩过：

  · 诊断把它归到 TIMEOUT，修法是「把等待上限调大」—— 指反了。
    调我们这边的超时对中转站入口的限制毫无作用，调到一小时也一样 524。
  · 报错原文直接截 200 字，得到的是一堆 `<!--[if lt IE 7]>`，
    真正那句「524: A timeout occurred」在几百字之后，被埋掉了。
"""
import unittest

from core import diagnose as D
from core.llm import LLM

REAL_524 = (
    "HTTP 524: <!DOCTYPE html>\n"
    "<!--[if lt IE 7]> <html class=\"no-js ie6 oldie\" lang=\"en-US\"> <![endif]-->\n"
    "<!--[if IE 7]>    <html class=\"no-js ie7 oldie\" lang=\"en-US\"> <![endif]-->\n"
    "<head>\n<title>paisio.online | 524: A timeout occurred</title>\n"
    "<meta charset=\"UTF-8\" />\n")


class ClassifyTests(unittest.TestCase):

    def test_a_524_is_not_our_timeout(self):
        """★ 归错的代价：它会让人去调一个完全无关的参数。"""
        self.assertEqual(D.code_of(REAL_524), "GATEWAY_TIMEOUT")

    def test_522_and_523_too(self):
        for code in ("522", "523", "524"):
            self.assertEqual(D.code_of(f"HTTP {code}: something"),
                             "GATEWAY_TIMEOUT", code)

    def test_our_own_timeout_still_maps_to_timeout(self):
        self.assertEqual(D.code_of("等了 900 秒还没收到新数据，判读超时"), "TIMEOUT")

    def test_it_is_matched_before_timeout(self):
        """现象都是「等太久」，规则顺序决定谁赢。"""
        order = [c for c, _ in D._PATTERNS]
        self.assertLess(order.index("GATEWAY_TIMEOUT"), order.index("TIMEOUT"))


class AdviceTests(unittest.TestCase):

    def setUp(self):
        self.fix = "　".join(D.CATALOG["GATEWAY_TIMEOUT"]["fix"])

    def test_it_tells_you_to_turn_streaming_on(self):
        """★ 这是唯一真正管用的一条。"""
        self.assertIn("流式输出", self.fix)
        self.assertIn("打开", self.fix)

    def test_it_explicitly_says_not_to_raise_the_timeout(self):
        """★ 不写这句的话，人一定会去调超时 —— 那是上一版诊断教他做的。"""
        self.assertIn("不要", self.fix)
        self.assertIn("超时", self.fix)

    def test_it_cites_the_measured_numbers(self):
        """带上实测数字，比「可能有用」有说服力。"""
        self.assertIn("406", self.fix)
        self.assertIn("100", self.fix)


class HtmlErrorBodyTests(unittest.TestCase):

    def test_the_useful_line_is_pulled_out_of_the_html(self):
        got = LLM._err_body(REAL_524.split(": ", 1)[1])
        self.assertIn("524: A timeout occurred", got)
        self.assertNotIn("<!--[if", got)
        self.assertNotIn("<html", got)

    def test_it_says_the_original_was_an_html_page(self):
        """摘要不能看起来像服务商的原话 —— 那会让人以为接口就这么回的。"""
        self.assertIn("HTML", LLM._err_body(REAL_524.split(": ", 1)[1]))

    def test_a_plain_json_error_is_left_alone(self):
        body = '{"error":{"message":"Invalid token","type":"new_api_error"}}'
        self.assertEqual(LLM._err_body(body), body)

    def test_html_without_a_title_still_gets_stripped_of_tags(self):
        got = LLM._err_body("<html><body><h1>Bad Gateway</h1></body></html>")
        self.assertIn("Bad Gateway", got)
        self.assertNotIn("<h1>", got)

    def test_empty_input_does_not_blow_up(self):
        self.assertEqual(LLM._err_body(""), "")
        self.assertEqual(LLM._err_body(None), "")



class UpstreamDownTests(unittest.TestCase):
    """★ 50x / 52x / 429 是三件不同的事，混在一起会让人去调错的东西。

      429  你发太快了        → 调小并发
      52x  中转站入口超时     → 开流式
      50x  对方自己过载/维护  → 等一会儿或换一家

    实跑撞到过 503 报成「没见过的错误」—— 这是最常见的失败之一，
    而「没见过」会让人以为出了什么怪事，去翻 key、翻参数、翻提示词。
    """

    def test_503_is_recognised(self):
        self.assertEqual(
            D.code_of('HTTP 503: {"error":{"message":"Service temporarily '
                      'unavailable","type":"api_error"}}'),
            "UPSTREAM_DOWN")

    def test_500_and_502_too(self):
        self.assertEqual(D.code_of("HTTP 502: Bad Gateway"), "UPSTREAM_DOWN")
        self.assertEqual(D.code_of("HTTP 500: internal server error"),
                         "UPSTREAM_DOWN")

    def test_it_does_not_swallow_the_other_two(self):
        self.assertEqual(D.code_of("HTTP 524: A timeout occurred"),
                         "GATEWAY_TIMEOUT")
        self.assertEqual(D.code_of("HTTP 429: rate limit"), "RATE_LIMITED")

    def test_the_advice_says_not_to_touch_the_key_or_params(self):
        """★ 「没见过的错误」时人的第一反应就是去翻 key 和参数。

        这条要明确劝住 —— 那几样都不是原因。
        """
        fix = "　".join(D.CATALOG["UPSTREAM_DOWN"]["fix"])
        self.assertIn("不用", fix)
        self.assertIn("key", fix)

    def test_it_distinguishes_itself_from_rate_limiting(self):
        why = D.CATALOG["UPSTREAM_DOWN"]["why"]
        self.assertIn("429", why)
        self.assertIn("限流", why)

if __name__ == "__main__":
    unittest.main()
