# -*- coding: utf-8 -*-
"""环节8 的画面文字条款必须带口径，不许模型自由发挥。

实跑踩过（烟火尽头项目，EP16-SEG01）：模板只说「禁止画面文字」没给口径，
模型自己措辞成 ——「画面内不得出现字幕、弹幕、可读文字、校牌文字、品牌、
水印或任何UI元素」。其中「弹幕」「校牌文字」恰恰是设置里**明确允许**的
剧情文字（_common 第 10 条生成的正文把它们列进了允许清单）。

字静默消失、不报错 —— 和当年 `_common` 第 10 条写死散文是同一个坑，
只是换了位置：那次是规则本身写死，这次是**让模型转述却不给原文**。

三条防线，各有一条测试钉住：
  ① 模板带 {{SUBTITLE_RULE}}（改写丢失时 voided 能报出、upgrade 能补回）
  ② 渲染时真的填进去（口径出现在发给模型的提示词里）
  ③ 渲染后兜底（改写版丢了占位符，规则也要拼进去，不许静默丢）
"""
import tempfile
import unittest

from core import prompts as P
from core import settings as S
from core import stages as ST
from core.store import Project


def _builder(pj=None, rewritten=None):
    """造一个环节8 的 user builder。rewritten 给了就把模板换成那份改写版。"""
    pj = pj or Project(tempfile.mkdtemp(prefix="s8rule-"))
    pj.init_dirs()
    if rewritten is not None:
        # 本剧改写层：load_prompt 优先读它（三层：内置 > 全局改写 > 本剧改写）
        import os
        d = pj.p("00_项目说明", "提示词模板")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "s8_compile.md"), "w", encoding="utf-8") as f:
            f.write(rewritten)
    data = {
        "s1_global": {"visual_tone": {"compressed": "", "compressed_variants": []}},
        "s3_states": {"segment_states": []},
        "s6_binding": {"bindings": []},
        "s7_shots": {"shots": []},
        "s4_assets": {"assets": []},
    }
    return ST.s8_user_builder(pj, {"duration": 15}, data, "EP01")


class TemplateCarriesTheRuleTests(unittest.TestCase):
    """① 模板本身带口径 —— 这是规则的入口。"""

    def test_builtin_template_has_the_placeholder(self):
        import core.stages as st
        import os
        tpl = open(os.path.join(os.path.dirname(st.__file__), "..", "prompts",
                                "s8_compile.md"), encoding="utf-8").read()
        self.assertIn("{{SUBTITLE_RULE}}", tpl)

    def test_the_two_clauses_point_at_the_rule(self):
        """★ 两处「画面文字」条款都得指向规则原文，不许留「禁止画面文字」
        这种没口径的说法 —— 那正是模型自由发挥的入口。"""
        import core.stages as st
        import os
        tpl = open(os.path.join(os.path.dirname(st.__file__), "..", "prompts",
                                "s8_compile.md"), encoding="utf-8").read()
        self.assertNotIn("禁止画面文字，编号除外", tpl)
        self.assertNotIn("防剧透项 + 画面文字）", tpl)
        # 两条条款（各两次：条款名 + 【】引用）+ 输入区标题，共 5 处指向规则
        self.assertEqual(tpl.count("画面文字规则"), 5)

    def test_voided_reports_a_template_that_lost_the_placeholder(self):
        """改写版丢了占位符 → voided 用人话报出「字幕设置进不了提示词」。"""
        lost = "# 我自己写的环节8\n只编译不创作。\n"
        self.assertEqual(P.voided("s8_compile", lost), ["SUBTITLE_RULE"])

    def test_upgrade_puts_the_placeholder_back(self):
        lost = "# 我自己写的环节8\n只编译不创作。\n"
        up = P.upgrade("s8_compile", lost)
        self.assertIn("{{SUBTITLE_RULE}}", up["text"])
        self.assertTrue(up["changes"])


class TheRuleReachesThePromptTests(unittest.TestCase):
    """② 渲染时口径真的进提示词 —— 模板有占位符不等于填了。"""

    def test_subtitle_rule_is_in_the_built_prompt(self):
        build = _builder()
        user = build({"id": "EP01-SEG01"})
        self.assertIn("剧情本身要求的文字", user)
        self.assertIn("弹幕", user)          # 允许清单里的例子必须带上

    def test_subtitle_setting_changes_the_built_prompt(self):
        """要字幕的项目，环节8 收到的口径也得跟着变 —— 否则两处打架。"""
        pj = Project(tempfile.mkdtemp(prefix="s8rule-"))
        pj.init_dirs()
        S.save(pj, {"subtitle": True, "subtitle_lang": "中文"})
        build = _builder(pj)
        user = build({"id": "EP01-SEG01"})
        self.assertIn("要有中文字幕", user)


class FallbackWhenRewrittenTests(unittest.TestCase):
    """③ 改写版丢了占位符，规则也要拼进去 —— {{MEDIUM_RULE}} 的教训。"""

    def test_the_rule_is_appended_when_the_placeholder_is_gone(self):
        """★ 静默丢规则是这个项目最贵的故障模式：不报错，只是不生效。"""
        rewritten = "# 我的环节8改写\n只编译不创作，输出结构自己看着办。\n"
        build = _builder(rewritten=rewritten)
        user = build({"id": "EP01-SEG01"})
        self.assertIn("剧情本身要求的文字", user)
        self.assertIn("弹幕", user)

    def test_the_rule_is_not_duplicated_when_the_placeholder_works(self):
        """占位符在的时候兜底不许再拼一份 —— 重复的规则两头措辞不一更糟。

        用规则正文独有的特征串数（模板说明文里的引述不算）。
        """
        build = _builder()          # 内置模板，占位符齐全
        user = build({"id": "EP01-SEG01"})
        self.assertEqual(user.count("判据是它在故事里真的存在"), 1)


if __name__ == "__main__":
    unittest.main()
