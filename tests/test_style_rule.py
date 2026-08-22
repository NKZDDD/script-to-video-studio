# -*- coding: utf-8 -*-
"""视觉风格必须真的进提示词 —— 「填了但一个字都没生效」那个坑。

实跑（2026-08-23）：用户「拍成什么形式」选 3D漫剧、「视觉风格」填
2.5D动漫风格，资产图提示词里只有「3D漫剧电影感」，2.5D 一个字没有。

根因：visual_style 到今天没有任何模板单独引用，只走 brief 里的一行
（四十行设定中间，模型视而不见）；而 visual_medium 走 _common 第 12 条
全局原则（强位置）。强弱对撞，弱的那项被吞 —— 和当年 visual_medium
只走 brief 一行被吞是同一个坑，只是那次修了 medium、这次轮到 style。

三层防线，各有一条测试钉住：
  ① medium_rule 把风格和视频类型拼进同一条全局原则（两体系共用 _common）
  ② 资产提示词模板（s5 / n4b）直接引用 {{MEDIUM_RULE}} 原词 ——
     即使第一环节的视觉基调转述丢了词（老项目已经写坏的就是这种），
     写提示词的环节也能看到原词补回来
  ③ voided / upgrade：改写版丢了这一格要报得出、补得回
"""
import tempfile
import unittest

from core import prompts as P
from core import settings as S
from core import stages as ST
from core.store import Project


def _tpl(name):
    import os
    return open(os.path.join(os.path.dirname(ST.__file__), "..", "prompts",
                             name + ".md"), encoding="utf-8").read()


class MediumRuleCarriesStyleTests(unittest.TestCase):
    """① 风格和视频类型拼在同一条里 —— 全局原则层。"""

    def setUp(self):
        self.pj = Project(tempfile.mkdtemp(prefix="stylerule-"))
        self.pj.init_dirs()

    def test_style_is_folded_into_the_same_rule(self):
        """★ 实跑场景：3D漫剧 + 2.5D动漫风格，两个原词都得在。"""
        S.save(self.pj, {"visual_medium": "3D漫剧",
                         "visual_style": "2.5D动漫风格"})
        rule = S.medium_rule(self.pj)
        self.assertIn("视频类型：3D漫剧", rule)
        self.assertIn("视觉风格：2.5D动漫风格", rule)

    def test_no_style_leaves_the_rule_unchanged(self):
        """没填风格的老行为不能变 —— 只发视频类型，不多一个空标签。"""
        S.save(self.pj, {"visual_medium": "真人短剧"})
        self.assertEqual(S.medium_rule(self.pj), "视频类型：真人短剧")

    def test_style_goes_in_untouched(self):
        """填空的核心承诺：写什么发什么，不归并、不翻译。"""
        S.save(self.pj, {"visual_medium": "真人短剧",
                         "visual_style": "水墨质感"})
        self.assertIn("视觉风格：水墨质感", S.medium_rule(self.pj))

    def test_the_rule_reaches_the_system_prompt(self):
        """_common 第 12 条每次调用都发 —— 渲染后真的带着风格原词。"""
        S.save(self.pj, {"visual_medium": "3D漫剧",
                         "visual_style": "2.5D动漫风格"})
        system = ST.system_prompt(self.pj, {})
        self.assertIn("视觉风格：2.5D动漫风格", system)


class AssetPromptTemplatesCarryTheRuleTests(unittest.TestCase):
    """② 写资产提示词的两个环节直接看得到原词。"""

    def setUp(self):
        self.pj = Project(tempfile.mkdtemp(prefix="stylerule-"))
        self.pj.init_dirs()
        S.save(self.pj, {"visual_medium": "3D漫剧",
                         "visual_style": "2.5D动漫风格"})

    def test_both_templates_have_the_placeholder(self):
        self.assertIn("{{MEDIUM_RULE}}", _tpl("s5_asset_prompts"))
        self.assertIn("{{MEDIUM_RULE}}", _tpl("n4b_asset_prompts"))

    def test_stage1_templates_say_where_visual_tone_comes_from(self):
        """视觉基调从两项设定推导 —— 不写这条，环节1 还是会自己编。"""
        self.assertIn("视频类型 + 视觉风格", _tpl("s1_global"))
        self.assertIn("视频类型 + 视觉风格", _tpl("n1_truth"))

    def test_v61_asset_prompt_really_receives_the_words(self):
        """★ V6.1 渲染链路：s5 的 user 提示词里出现风格原词。

        业务模板的 mapping 和系统提示词的 mapping 是两份表 ——
        这份不接 MEDIUM_RULE 的话，模板里的占位符会原样发出去
        （{{EPISODE_SECONDS}} 那次的教训）。
        """
        m = ST._mapping(self.pj, "s5", {}, {}, "EP01", "")
        self.assertIn("2.5D动漫风格", m["MEDIUM_RULE"])

    def test_v34_asset_prompt_really_receives_the_words(self):
        """★ V3.4 渲染链路：n4b 的 mapping 同样带得上。"""
        from core import run_v34 as R
        m = R.mapping(self.pj, "n4b", {}, {}, "")
        self.assertIn("2.5D动漫风格", m["MEDIUM_RULE"])


class RewrittenTemplateTests(unittest.TestCase):
    """③ 改写版丢了这一格：报得出（voided）、补得回（upgrade）。"""

    def test_voided_reports_a_template_that_lost_the_placeholder(self):
        lost = "# 我的环节5改写\n只写提示词，别的不管。\n"
        self.assertIn("MEDIUM_RULE", P.voided("s5_asset_prompts", lost))
        lost34 = "# 我的n4b改写\n只写提示词。\n"
        self.assertIn("MEDIUM_RULE", P.voided("n4b_asset_prompts", lost34))

    def test_upgrade_puts_the_placeholder_back(self):
        lost = "# 我的环节5改写\n只写提示词，别的不管。\n"
        up = P.upgrade("s5_asset_prompts", lost)
        # s5 后来还挂了 SUBTITLE_RULE（收尾句口径），会一起补上 ——
        # 这里只断言 MEDIUM_RULE 补回来了。
        self.assertIn("{{MEDIUM_RULE}}", up["text"])
        self.assertTrue(up["changes"])

    def test_the_group_name_is_human_readable(self):
        """voided 报出来的得是人话 —— 「视觉风格填了白填」，不是占位符名。"""
        self.assertIn("视觉风格", P.VAR_GROUPS["MEDIUM_RULE"])


if __name__ == "__main__":
    unittest.main()
