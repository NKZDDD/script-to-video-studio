# -*- coding: utf-8 -*-
"""LLM 抽取：粘一段【项目基础信息】→ 读成字段。

固定四步：**解析 → 预览 → 你挑 → 保存**，中间那两步一步都不能省。
这些值会改变生产结果（画幅、单段时长、改编权限），模型读错一个字，
整部剧就按错的跑 —— 而且要到成片才看得见。

而这次调用真正值钱的**不是抽取，是冲突检测**：散文之间的矛盾没有程序能
自动发现。实跑炸过一次 —— 用户写「字幕：需要，烧录进画面」，和
`_common` 第 10 条「画面内禁止出现任何文字、字幕」并存，
程序不报错，出来的图里字幕被抹掉了。
"""
import io
import os
import unittest

from core import prompts as P, settings as ST

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class SchemaBlockTests(unittest.TestCase):
    """给模型看的字段说明是**生成的**，不是手写的。"""

    def test_it_lists_the_enum_values(self):
        s = ST.schema_block()
        self.assertIn("`preserve`=严格保持原文", s)
        self.assertIn("`live_action`=真人写实", s)

    def test_readonly_fields_are_not_offered(self):
        """★ 抽出来也没处放 —— 列出来只会让模型去填它。"""
        s = ST.schema_block()
        self.assertNotIn("`id_policy`", s)
        self.assertNotIn("`spatial_consistency_mode`", s)

    def test_fields_that_change_production_are_flagged(self):
        """★ 画幅、单段时长这几项改错的代价最大，要单独提醒。"""
        s = ST.schema_block()
        i = s.index("`aspect_ratio`")
        self.assertIn("改变生产参数", s[i:i + 200])

    def test_it_is_generated_not_hand_written(self):
        """★ 手写一份的话，字段表一改这里就落后 ——

        而落后的表现是「新字段永远抽不出来」且不报错。
        """
        n = len([f for f in ST.FIELDS if f["source"] != "derived"])
        self.assertEqual(ST.schema_block().count("\n- `"), n - 1)


class SanitizeTests(unittest.TestCase):
    """**不信任模型的输出。** 这些值直接改变生产结果。"""

    def test_a_bad_enum_value_is_dropped_with_a_reason(self):
        ok, dropped = ST.sanitize({"visual_medium": "漫画"})
        self.assertEqual(ok, {})
        self.assertIn("不在允许的取值里", dropped[0])

    def test_readonly_fields_cannot_be_written(self):
        """★ 静默接受的话，页面显示的和实际跑的就不是一回事。"""
        ok, dropped = ST.sanitize({"id_policy": "随便"})
        self.assertEqual(ok, {})
        self.assertIn("程序算的", dropped[0])

    def test_chinese_yes_becomes_a_real_boolean(self):
        ok, _ = ST.sanitize({"subtitle": "需要"})
        self.assertIs(ok["subtitle"], True)

    def test_a_non_numeric_int_is_dropped(self):
        ok, dropped = ST.sanitize({"seg_duration": "十五"})
        self.assertEqual(ok, {})
        self.assertIn("不是整数", dropped[0])

    def test_empty_values_are_not_errors(self):
        """没抽出来 ≠ 抽错了。空值安静跳过，不占「被挡下」那一栏。"""
        ok, dropped = ST.sanitize({"visual_style": "  ", "cultural_setting": None})
        self.assertEqual((ok, dropped), ({}, []))

    def test_an_unknown_key_is_dropped(self):
        _, dropped = ST.sanitize({"nonexistent_field": "x"})
        self.assertIn("不是已知字段", dropped[0])


class PromptTests(unittest.TestCase):

    def _tpl(self):
        return io.open(os.path.join(ROOT, "prompts", "_settings_extract.md"),
                       encoding="utf-8").read()

    def test_it_asks_for_conflicts_first(self):
        """★ 冲突检测才是这次调用的价值，抽取是顺带的。"""
        t = self._tpl()
        self.assertIn("conflicts", t)
        self.assertIn("字幕被抹掉", t.replace("**", ""))

    def test_it_forbids_inventing_conflicts(self):
        """不写这句，模型会为了显得有用编几条出来。"""
        self.assertIn("不要为了显得有用而编冲突", self._tpl())

    def test_it_forbids_guessing_unmentioned_fields(self):
        self.assertIn("留空，不要猜", self._tpl())

    def test_it_warns_about_the_three_confusable_pairs(self):
        """★ 三组实际混过的：语言/文化、画幅/尺寸、单集/单段时长。"""
        t = self._tpl()
        self.assertIn("语言 vs 文化", t)
        self.assertIn("画幅 vs 出图尺寸", t)
        self.assertIn("单集时长 vs SEG 时长", t)

    def test_the_template_is_editable_on_the_prompts_page(self):
        """★ 文件在盘上但页面上改不了 = 调不动它。"""
        self.assertIn("_settings_extract", P.all_template_names())

    def test_its_placeholders_are_all_filled(self):
        import re
        t = self._tpl()
        want = set(re.findall(r"\{\{([A-Z_]+)\}\}", t))
        # 填不上的占位符会原样出现在提示词里，模型当成要填的空位
        from core.store import Project
        have = set(ST.extract_vars(Project(ROOT), "", ""))
        self.assertEqual(want, have, "模板里有填不上的占位符")


class PageTests(unittest.TestCase):

    def setUp(self):
        self.html = io.open(os.path.join(ROOT, "web", "index.html"),
                            encoding="utf-8").read()

    def test_the_four_steps_are_wired(self):
        for hook in ("briefRaw", "briefParse", "briefPreview", "briefApply"):
            self.assertIn(hook, self.html, hook)

    def test_parsing_does_not_save(self):
        """★ 解析和保存必须是两个动作，不能一步到位。"""
        i = self.html.index("$('#briefParse').onclick")
        blk = self.html[i:i + 900]
        self.assertIn("/api/project/settings/extract", blk)
        self.assertNotIn("/api/project/settings'", blk)

    def test_conflicts_are_shown_before_the_values(self):
        """★ 冲突排在建议值前面 —— 排后面人会先勾完再看到。"""
        i = self.html.index("function renderBriefPreview")
        blk = self.html[i:i + 2600]
        self.assertLess(blk.index("conflicts"), blk.index("proposals"))

    def test_dropped_and_unclear_are_visible(self):
        """★ 不显示的话人以为全抽到了。"""
        blk = self.html[self.html.index("function renderBriefPreview"):]
        self.assertIn("判不了的", blk[:3000])
        self.assertIn("被挡下的", blk[:3000])

    def test_production_changing_fields_are_marked_in_the_preview(self):
        blk = self.html[self.html.index("function renderBriefPreview"):]
        self.assertIn("改生产参数", blk[:3000])


if __name__ == "__main__":
    unittest.main()
