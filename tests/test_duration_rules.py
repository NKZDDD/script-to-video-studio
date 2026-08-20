# -*- coding: utf-8 -*-
"""时长档位：只报有依据的，实测过的自己填。

用户的话：「不要故意报宽就是模型能用多少秒就可以选多少秒」。

原来小霸龙报的是 4-30，是**故意报宽**的，理由写在代码注释里：
「报宽只会吃 400（不计费），报窄会把能力埋掉」。那个权衡有它的道理，
但有个说不出口的前提：下拉里出现 30 秒，人会当成「这家支持 30 秒」,
而我们根本不知道 —— 文档对现役这批模型的秒数没写，list_models() 也只给 ID。

现在两条腿：
  · 程序只报有依据的（文档给过的数字）
  · 你实测过的写进「服务商 → 时长档位」，页面按模型放开

**下拉报窄 ≠ 把用户填的值改掉。** 手填 29 照旧原样发出去 ——
越界网关 400（不计费），比悄悄改成 15 秒然后出一条短一半的片子安全得多。
"""
import io
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _html() -> str:
    return io.open(os.path.join(ROOT, "web", "index.html"),
                   encoding="utf-8").read()


class ParseTests(unittest.TestCase):
    """`模型名=4,8,15,30`；分号或换行分隔；`*` 兜底。"""

    def setUp(self):
        self.html = _html()

    def test_the_parser_exists(self):
        self.assertIn("function parseDurationRules(text)", self.html)

    def test_it_splits_on_semicolon_and_newline(self):
        i = self.html.index("function parseDurationRules")
        blk = self.html[i:i + 700]
        self.assertIn("/[;\\n]+/", blk)

    def test_it_accepts_chinese_comma(self):
        """★ 中文逗号是最常见的手滑 —— 不认的话整条规则静默失效。"""
        i = self.html.index("function parseDurationRules")
        self.assertIn("，", self.html[i:i + 700])

    def test_it_drops_non_positive_numbers(self):
        i = self.html.index("function parseDurationRules")
        self.assertIn("n > 0", self.html[i:i + 700])

    def test_it_sorts(self):
        """下拉里 30 排在 8 前面会让人以为坏了。"""
        i = self.html.index("function parseDurationRules")
        self.assertIn("sort((a, b) => a - b)", self.html[i:i + 700])


class OverrideTests(unittest.TestCase):

    def setUp(self):
        self.html = _html()

    def test_the_user_rule_wins_over_the_declaration(self):
        """★ 他实测过，比文档和我们的猜测都硬 —— 所以盖在最后。"""
        i = self.html.index("function effectiveBlock")
        blk = self.html[i:i + 700]
        self.assertIn("parseDurationRules(saved.durations)", blk)
        j = blk.index("eff.durations = hit")
        k = blk.index("{...base, ...modelOpt}")
        self.assertLess(k, j, "用户规则要盖在 model_options 之后")

    def test_a_star_rule_covers_the_whole_provider(self):
        i = self.html.index("function effectiveBlock")
        self.assertIn("rules['*']", self.html[i:i + 700])

    def test_only_video_has_this(self):
        """出图没有「时长」，别给它也挂一个框。"""
        i = self.html.index("function effectiveBlock")
        self.assertIn("blockName === 'video'", self.html[i:i + 700])

    def test_the_box_is_only_on_video_providers(self):
        i = self.html.index("时长档位（实测过的写在这里）")
        blk = self.html[max(0, i - 200):i + 400]
        self.assertIn("isVideo ?", blk)
        self.assertIn('data-f="durations"', blk)

    def test_the_saved_value_is_echoed_back(self):
        """★ 不回显的话保存一次就看不见了，等于填了个黑洞。"""
        i = self.html.index('data-f="durations"')
        self.assertIn("saved.durations", self.html[i:i + 260])

    def test_bootstrap_sends_it(self):
        app = io.open(os.path.join(ROOT, "server", "app.py"),
                      encoding="utf-8").read()
        i = app.index('"providers_public"')
        self.assertIn('"durations"', app[i:i + 600])


class DeclarationTests(unittest.TestCase):
    """各家声明的秒数要能说出依据。"""

    def test_xiaobalong_no_longer_reports_wide(self):
        from core.providers.xiaobalong import DEFAULT_DURATIONS
        self.assertEqual(max(DEFAULT_DURATIONS), 15)

    def test_the_reason_is_written_down(self):
        """★ 这条立场被改过一次，方向和理由必须留在代码里 ——

        不然下一个人看到 4-15 只会觉得「报窄了」，然后再改回 4-30。
        """
        src = io.open(os.path.join(ROOT, "core", "providers", "xiaobalong.py"),
                      encoding="utf-8").read()
        i = src.index("DEFAULT_DURATIONS")
        blk = src[max(0, i - 900):i + 100]
        self.assertIn("故意报宽", blk)
        self.assertIn("只报有依据的", blk)
        self.assertIn("时长档位", blk, "没告诉人实测过的写在哪")

    def test_per_model_declarations_still_work(self):
        """paisio 把 29 秒只挂在 seedance2.5 上 —— 那个机制不许被这次改动碰坏。"""
        from core.providers import list_capabilities
        c = next(x for x in list_capabilities() if x["id"] == "paisio")
        opts = (c.get("video") or {}).get("model_options") or {}
        self.assertTrue(opts, "paisio 的按模型声明没了")
        longest = max(max(v.get("durations") or [0]) for v in opts.values())
        self.assertGreater(longest, max((c["video"].get("durations") or [0])),
                           "按模型声明该比整家的基线长，否则它没有意义")


if __name__ == "__main__":
    unittest.main()
