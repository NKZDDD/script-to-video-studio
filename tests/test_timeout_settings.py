# -*- coding: utf-8 -*-
"""三种超时都要能在页面上改。

以前三个全藏在 config.json 里，页面上一个都没有：

  llm.timeout             调模型：流式=两块数据之间；非流式=整次生成
  providers.*.poll_timeout 出图出片：提交任务之后**等结果**的总时长
  providers.*.poll_interval 隔几秒查一次

三者语义完全不同，混着调会调坏。最坑的是第二个调小了：
视频在快出来的时候被判成失败，钱照花、东西没拿到，
报错还长得像服务商挂了。
"""
import io
import os
import unittest

from server.app import _poll, _timeout

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HTML = io.open(os.path.join(ROOT, "web", "index.html"), encoding="utf-8").read()
APP = io.open(os.path.join(ROOT, "server", "app.py"), encoding="utf-8").read()


class ClampTests(unittest.TestCase):

    def test_llm_timeout_is_clamped(self):
        self.assertEqual(_timeout(900), 900)
        self.assertEqual(_timeout(30), 60, "太小会把正常的长思考判成超时")
        self.assertEqual(_timeout(99999), 3600, "太大等于卡死一小时才放手")

    def test_llm_timeout_falls_back_on_garbage(self):
        for junk in (None, "abc", "", {}):
            self.assertEqual(_timeout(junk), 900, repr(junk))

    def test_poll_timeout_is_clamped(self):
        self.assertEqual(_poll(2400, 60, 7200, 900), 2400)
        self.assertEqual(_poll(10, 60, 7200, 900), 60)
        self.assertEqual(_poll(99999, 60, 7200, 900), 7200)

    def test_poll_interval_is_clamped(self):
        self.assertEqual(_poll(0, 2, 60, 5), 2, "0 秒会把网关打成限流")
        self.assertEqual(_poll(999, 2, 60, 5), 60)

    def test_clamped_on_save_not_only_on_use(self):
        """存的时候不夹住，config.json 里就一直躺着个不合法的值，
        页面回显也是它，人会以为那个值是有效的。"""
        i = APP.index('if path == "/api/config"')
        blk = APP[i:i + 2200]
        self.assertIn("_timeout(", blk)
        self.assertIn("_poll(", blk)


class UiWiringTests(unittest.TestCase):

    def test_the_llm_timeout_has_a_field(self):
        self.assertIn('id="llmTimeout"', HTML)
        self.assertIn("timeout: +$('#llmTimeout').value || 900", HTML)
        self.assertIn("$('#llmTimeout').value = l.timeout || 900;", HTML)

    def test_the_llm_timeout_explains_which_timeout_it_is(self):
        """★ 流式和非流式下这个数的含义完全不同，不说清会调错。"""
        i = HTML.index('id="llmTimeout"')
        blk = HTML[max(0, i - 400):i]
        self.assertIn("流式", blk)
        self.assertIn("非流式", blk)

    def test_the_provider_cards_have_poll_settings(self):
        self.assertIn('data-f="poll_timeout"', HTML)
        self.assertIn('data-f="poll_interval"', HTML)

    def test_the_poll_timeout_says_it_is_not_a_connect_timeout(self):
        """★ 和 LLM 那个超时是两回事。不写清，人会拿同一个数去套。"""
        i = HTML.index('data-f="poll_timeout"')
        blk = HTML[max(0, i - 500):i]
        self.assertIn("等出结果", blk)
        self.assertIn("不是连接超时", blk)

    def test_video_providers_default_to_a_longer_wait(self):
        """29 秒的多镜头片子排队加生成，十几分钟很常见。"""
        i = HTML.index('data-f="poll_timeout"')
        self.assertIn("isVideo ? 2400 : 900", HTML[i:i + 400])

    def test_numbers_are_sent_as_numbers(self):
        """存成字符串的话 config.json 里会出现 "poll_timeout": "2400"。"""
        self.assertIn("NUM.includes(i.dataset.f) ? +i.value : i.value", HTML)


class ProviderEchoTests(unittest.TestCase):
    """★ 保存过的非密钥设置必须回显。

    以前 bootstrap 只回一个「配了没」的布尔值，于是页面上 base_url
    永远显示默认值 —— 改过自定义端点的人再点一次保存就被默认值盖掉了，
    而且一声不吭。加了轮询超时之后这个问题会更明显（每次保存都被重置）。
    """

    def test_bootstrap_returns_the_non_secret_settings(self):
        self.assertIn('"providers_public"', APP)
        i = APP.index('"providers_public"')
        blk = APP[i:i + 400]
        for f in ("base_url", "poll_timeout", "poll_interval"):
            self.assertIn(f, blk)

    def test_it_never_returns_the_key(self):
        """★ 回显不能顺手把密钥也带出去。"""
        i = APP.index('"providers_public"')
        blk = APP[i:i + 400]
        self.assertNotIn("api_key", blk)

    def test_the_page_prefers_the_saved_value(self):
        self.assertIn("BOOT.providers_public", HTML)
        self.assertIn("saved.base_url || c.default_base_url", HTML)


if __name__ == "__main__":
    unittest.main()
