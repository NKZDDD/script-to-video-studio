# -*- coding: utf-8 -*-
"""生产页要显示「这几个下拉的选项是谁给的」。

不显示的话会撞上一个很费解的情况：明明知道 seedance 2.5 能出 29 秒，
下拉里却只有 15 —— 因为链上首选的是别的模型，而整家声明的上限就是 15。
这次就是这么被绊住的：以为是程序漏了 29 秒，其实是选项跟着模型走。

这个文件钉两件事：
  · 各家/各模型的秒数声明**没写错**（29 是真的，不是我记岔了）
  · 页面上确实把来源显示出来了
"""
import io
import os
import unittest

from core import providers as P

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CAPS = {c["id"]: c for c in P.list_capabilities()}


def effective(cid, block, model=None):
    """和前端 effectiveBlock 同一套：model_options 盖在整家的值上面。"""
    base = dict((CAPS.get(cid) or {}).get(block) or {})
    base.update(((base.get("model_options") or {}).get(model)) or {})
    return base


class Seedance25Tests(unittest.TestCase):
    """seedance 2.5 的能力声明必须和接口的实际校验一致。"""

    def test_the_model_declares_four_to_twentynine_seconds(self):
        d = effective("paisio", "video", "seedance-2.5-720p")["durations"]
        self.assertEqual(min(d), 4)
        self.assertEqual(max(d), 29)

    def test_the_declaration_matches_what_the_request_builder_enforces(self):
        """★ 声明和校验是两处代码，写歪了就会「页面上能选、发出去被拒」。"""
        from core.providers.paisio import _standard_limits
        d = effective("paisio", "video", "seedance-2.5-720p")["durations"]
        self.assertEqual(set(d), set(range(4, 30)))
        self.assertEqual(set(d), set(_standard_limits("seedance-2.5-720p")[0]))

        # 带 paisio- 的真实新模型经接口确认只有 4-15 秒，不能和旧名混用。
        prefixed = effective("paisio", "video", "paisio-seedance-2.5-720p")["durations"]
        self.assertEqual(set(prefixed), set(range(4, 16)))
        self.assertEqual(set(prefixed),
                         set(_standard_limits("paisio-seedance-2.5-720p")[0]))

    def test_twentynine_is_not_offered_at_the_provider_level(self):
        """★ 故意的：29 写成整家通用值的话，切回旧模型时前端还允许选 29，
        要到付费请求发出去才收到 400。"""
        base = CAPS["paisio"]["video"]["durations"]
        self.assertEqual(max(base), 15)

    def test_an_old_model_on_the_same_provider_still_caps_at_fifteen(self):
        self.assertEqual(max(effective("paisio", "video", "sd2-pro-720p")["durations"]),
                         15)

    def test_the_model_needs_public_urls_for_references(self):
        """2.5 只收公网链接。没配对象存储的话它会直接拒，值得在页面上提醒。"""
        e = effective("paisio", "video", "seedance-2.5-720p")
        self.assertEqual(e["ref_mode"], "url")
        self.assertEqual(e["max_refs"], 30)

    def test_it_is_the_only_model_trusted_for_multishot(self):
        """能力冻结把它标成 RELIABLE，六类转场才全放开。"""
        from core.run_v34 import _MULTISHOT, detect_capability
        self.assertEqual(detect_capability("seedance-2.5-720p"), "RELIABLE")
        self.assertEqual(detect_capability("sd2-pro-720p"), "UNKNOWN")
        self.assertEqual(list(_MULTISHOT), ["seedance-2.5"])


class HintWiringTests(unittest.TestCase):

    def setUp(self):
        self.html = io.open(os.path.join(ROOT, "web", "index.html"),
                            encoding="utf-8").read()

    def test_the_production_page_shows_where_the_choices_come_from(self):
        self.assertIn('id="autoCapHint"', self.html)
        self.assertIn("function capHint(", self.html)
        self.assertIn("$('#autoCapHint').innerHTML = capHint(ch);", self.html)

    def test_it_reports_the_max_seconds_and_the_model(self):
        i = self.html.index("function capHint(")
        blk = self.html[i:i + 2200]
        self.assertIn("最长", blk)
        self.assertIn("first.model", blk)

    def test_it_warns_when_the_chain_intersects_the_durations_away(self):
        """★ 最容易困惑的一种：选了 2.5 还是只有 15，因为链上还挂着别家。

        秒数在多家之间取交集。不说清这一条，人会以为是程序漏了。
        """
        i = self.html.index("function capHint(")
        blk = self.html[i:i + 2200]
        self.assertIn("交集", blk)
        self.assertIn("只排一家", blk)

    def test_it_warns_about_url_only_models(self):
        i = self.html.index("function capHint(")
        self.assertIn("只收公网链接", self.html[i:i + 2200])

    def test_no_markdown_leaks_into_the_html(self):
        """`**粗体**` 在 HTML 里会原样显示成星号。"""
        i = self.html.index("function capHint(")
        self.assertNotIn("**", self.html[i:i + 2200])


class AllProvidersTests(unittest.TestCase):

    def test_every_video_provider_declares_some_duration(self):
        """一个都不声明的话下拉会退回写死的 [15]，而那多半是错的。"""
        missing = []
        for cid, c in CAPS.items():
            v = c.get("video") or {}
            if not v:
                continue
            has = bool(v.get("durations")) or any(
                (o or {}).get("durations")
                for o in (v.get("model_options") or {}).values())
            if not has:
                missing.append(cid)
        self.assertEqual(missing, [], f"这几家没声明秒数：{missing}")


if __name__ == "__main__":
    unittest.main()
