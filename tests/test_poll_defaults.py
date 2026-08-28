# -*- coding: utf-8 -*-
"""轮询墙按服务商声明，不用一个全局数管所有家。

用户原话（2026-08-26）：「这一家的轮询查看结果超时可以缩短时长，因为主要判断
是这个文件夹下有没有出现作物，如果长时间没有出现作物就是出现了任务异常，
我不去判断是什么异常只要他超过多少时间我就算他失败了」。

能缩短的前提是**排队在墙外**：账号池的槽位是在调 generate_video 之前拿的，
全局闸门上限也被压到账号数（produce.make_video_worker），所以这个墙只覆盖
「投递 + 远端生成」，不含本地等空账号那一段。

反过来也要守住：别家排队几十分钟是常态，墙短了会把**快出来的片子判成失败**
—— 钱照花、东西没拿到，而且报错长得像服务商挂了。所以这是各家各自的数。
"""
import unittest

from core.produce import _poll_of
from core.providers import REGISTRY


class _WithDefaults:
    poll_defaults = {"interval": 10, "timeout": 600}


class _Plain:
    pass


class PriorityTests(unittest.TestCase):
    """优先级：配置 > 服务商声明 > 这一类的兜底。"""

    def test_the_declaration_wins_over_the_generic_fallback(self):
        self.assertEqual(_poll_of(_WithDefaults(), {}, "video", 10, 2400),
                         (10, 600))

    def test_the_config_wins_over_the_declaration(self):
        """★ 页面上那两格改了就该生效 —— 声明只是没填时的默认。
        反过来的话，那两格就是「看起来在那儿、其实没接线」。"""
        self.assertEqual(
            _poll_of(_WithDefaults(), {"poll_timeout": 1200}, "video", 10, 2400),
            (10, 1200))

    def test_a_provider_without_a_declaration_is_unchanged(self):
        """★ 只有声明了的那家变 —— 别家一个数都不能动。"""
        self.assertEqual(_poll_of(_Plain(), {}, "video", 10, 2400), (10, 2400))
        self.assertEqual(_poll_of(_Plain(), {}, "image", 5, 900), (5, 900))

    def test_zero_and_empty_do_not_become_zero_seconds(self):
        """★ 配置里存了 0 或空串时不能当成「0 秒墙」—— 那是一投就判失败。"""
        for bad in (0, "", None):
            self.assertEqual(
                _poll_of(_WithDefaults(), {"poll_timeout": bad},
                         "video", 10, 2400)[1], 600)


class HvtaldTests(unittest.TestCase):

    def test_hvtald_declares_a_short_wall(self):
        d = getattr(REGISTRY["hvtald"], "poll_defaults", None)
        self.assertTrue(d)
        self.assertLess(d["timeout"], 2400)

    def test_it_is_visible_in_the_capability_table(self):
        """★ 页面按能力表显示默认值 —— 不带下来的话框里显示 2400 而程序按
        600 判失败，人会以为程序卡了。"""
        cap = REGISTRY["hvtald"]("").capabilities()["video"]
        self.assertEqual(cap["poll_timeout"],
                         REGISTRY["hvtald"].poll_defaults["timeout"])
        self.assertEqual(cap["poll_interval"],
                         REGISTRY["hvtald"].poll_defaults["interval"])

    def test_only_this_family_declares_one(self):
        """别家没声明就是没声明 —— 这条测试是防「顺手给所有家都加一个」。"""
        got = [pid for pid, cls in REGISTRY.items()
               if getattr(cls, "poll_defaults", None)]
        self.assertEqual(got, ["hvtald"])

    def test_the_page_reads_the_declared_default(self):
        from core.store import read_text
        html = read_text("web/index.html")
        self.assertIn("const pollDef", html)
        i = html.index('data-f="poll_timeout"')
        self.assertIn("pollDef.timeout", html[i:i + 300])
