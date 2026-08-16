# -*- coding: utf-8 -*-
"""项目基础信息：从「粘一段文字」变成有类型的字段。

字段表照 skill 的「开始前解析项目参数」那 30 项（SKILL.md 第 28 行起），
key 和枚举值都用 skill 的原名 —— 自己另起一套名字，对着 skill 排查问题时
每次都要先做一遍翻译。

起因是一个很实在的坑：用户把整块【AI电影级短剧项目基础信息】加进全局提示词，
里面写着「字幕：需要，烧录进画面」，而 `_common.md` 第 10 条写着
「画面内禁止出现任何文字、字幕、水印、UI 面板」——
两段散文直接矛盾，**结果本该在图里的字幕消失了，而且没有任何报错**。

散文之间的矛盾检测不了。改成「一个字段两种取值」之后，
第 10 条按取值生成，矛盾从根上不存在。
"""
import io
import os
import shutil
import unittest

from core import settings as ST, stages as S
from test_v34_run import new_project

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class SchemaTests(unittest.TestCase):
    """字段表本身要和 skill 对得上。"""

    def test_it_uses_the_skill_parameter_names(self):
        """★ 用 skill 的原名，别自己造一套。"""
        keys = {f["key"] for f in ST.FIELDS}
        for k in ("adaptation_authority", "visual_medium", "cultural_setting",
                  "dialogue_language", "instruction_language", "video_audio_mode",
                  "aspect_ratio", "seg_duration", "costume_asset_mode",
                  "output_depth", "spatial_consistency_mode"):
            self.assertIn(k, keys, f"skill 里有 {k}，字段表里没有")

    def test_the_enum_values_are_the_skill_values(self):
        f = ST.BY_KEY["adaptation_authority"]
        self.assertEqual(f["options"],
                         ["preserve", "optimize_pacing", "authorized_rewrite"])

    def test_local_extensions_are_marked(self):
        """★ skill 参数表里没有字幕。是本地加的就要标出来，

        否则下次对 skill 时会以为漏实现了什么。
        """
        self.assertTrue(ST.BY_KEY["subtitle"].get("local"))

    def test_every_field_declares_a_source(self):
        for f in ST.FIELDS:
            self.assertIn(f["source"], ("settings", "params", "derived"), f["key"])


class DefaultsTests(unittest.TestCase):

    def setUp(self):
        self.pj = new_project()

    def tearDown(self):
        shutil.rmtree(self.pj.root, ignore_errors=True)

    def test_an_untouched_project_keeps_the_original_rule(self):
        """★ 没填过设定的项目，第 10 条要一字不差还是原来那句。"""
        sp = S.system_prompt(self.pj, {})
        self.assertIn("画面内禁止出现任何文字、字幕、水印、UI 面板", sp)

    def test_defaults_are_listed_but_marked_as_defaults(self):
        """★ skill 第 0 章：「采用**明确标注的默认值**」。

        两个极端都不行：全不列，模型自己猜媒介和权限；
        列了不标，你做 3D 漫剧而没填时，提示词里自信地写着
        「视觉媒介：真人写实」，读起来像是你的决定。
        """
        brief = ST.brief_block(self.pj)
        self.assertIn("视觉媒介", brief)
        self.assertIn("（默认，未指定）", brief)
        self.assertIn("不是用户的决定", brief)

    def test_filling_one_in_clears_its_default_mark(self):
        ST.save(self.pj, {"visual_medium": "3d"})
        line = [l for l in ST.brief_block(self.pj).splitlines()
                if l.startswith("- 视觉媒介")][0]
        self.assertIn("3d", line)
        self.assertNotIn("默认", line)

    def test_defaults_are_returned_for_never_saved_fields(self):
        v = ST.load(self.pj)
        self.assertEqual(v["dialogue_language"], "中文")
        self.assertFalse(v["subtitle"])


class SubtitleRuleTests(unittest.TestCase):
    """★ 这一组就是那个坑。"""

    def setUp(self):
        self.pj = new_project()

    def tearDown(self):
        shutil.rmtree(self.pj.root, ignore_errors=True)

    def test_burning_subtitles_rewrites_the_no_text_rule(self):
        ST.save(self.pj, {"subtitle": True, "subtitle_burn": True,
                          "subtitle_lang": "中英双语"})
        sp = S.system_prompt(self.pj, {})
        self.assertIn("字幕烧录进画面", sp)
        self.assertIn("中英双语", sp)
        # 原来那句无条件禁令必须消失，否则两句并存又是矛盾
        self.assertNotIn("画面内禁止出现任何文字、字幕、水印、UI 面板", sp)

    def test_other_text_is_still_forbidden(self):
        """★ 放开字幕不等于放开水印和 UI —— 只松该松的那一项。"""
        ST.save(self.pj, {"subtitle": True, "subtitle_burn": True})
        sp = S.system_prompt(self.pj, {})
        self.assertIn("仍然禁止", sp)
        self.assertIn("水印", sp)

    def test_subtitles_not_burned_in_keep_the_original_rule(self):
        """要字幕但走后期合成 —— 画面里仍然不该有文字。"""
        ST.save(self.pj, {"subtitle": True, "subtitle_burn": False})
        sp = S.system_prompt(self.pj, {})
        self.assertIn("画面内禁止出现任何文字、字幕、水印、UI 面板", sp)


class DependentFieldTests(unittest.TestCase):

    def setUp(self):
        self.pj = new_project()

    def tearDown(self):
        shutil.rmtree(self.pj.root, ignore_errors=True)

    def test_a_hidden_field_renders_empty_not_as_a_leftover_placeholder(self):
        """★ 漏掉这个键，`{{SUBTITLE_LANG}}` 会原样进提示词，

        模型会把它当成一个要填的空位或者一句奇怪的指令。
        """
        m = ST.mapping(self.pj)
        self.assertEqual(m["SUBTITLE_LANG"], "")
        self.assertIn("SUBTITLE_LANG", m)

    def test_a_hidden_field_is_not_listed_in_the_brief(self):
        ST.save(self.pj, {"subtitle": False, "subtitle_lang": "中文"})
        self.assertNotIn("字幕语言", ST.brief_block(self.pj))


class SourceTests(unittest.TestCase):
    """三种来源，只有一个真相。"""

    def setUp(self):
        self.pj = new_project()

    def tearDown(self):
        shutil.rmtree(self.pj.root, ignore_errors=True)

    def test_params_fields_are_not_copied_into_settings(self):
        """★ 存两份就会出现「页面显示 9:16、实际按 16:9 跑」而且没人发现。"""
        ST.save(self.pj, {"aspect_ratio": "16:9", "visual_style": "写实"})
        self.assertNotIn("aspect_ratio",
                         (self.pj.meta() or {}).get("settings") or {})

    def test_params_come_through_maps_to(self):
        """★ skill 叫 aspect_ratio，我们的参数叫 ratio。

        对不上不会报错，只会静默取空 —— 然后模板里那个占位符是空字符串。
        """
        m = ST.mapping(self.pj, {"ratio": "9:16", "duration": 15,
                                 "project_code": "XY-01"})
        self.assertEqual(m["ASPECT_RATIO"], "9:16")
        self.assertEqual(m["SEG_DURATION"], 15)
        self.assertEqual(m["PROJECT_ID"], "XY-01")

    def test_derived_fields_come_from_the_caller(self):
        m = ST.mapping(self.pj, {}, {"episode_duration": 90,
                                     "reference_capacity_per_call": 9})
        self.assertEqual(m["EPISODE_DURATION"], 90)
        self.assertEqual(m["REFERENCE_CAPACITY_PER_CALL"], 9)

    def test_fixed_values_need_no_caller(self):
        """skill 要求冻结、而我们没有别的选择的那几项，直接给死值。"""
        m = ST.mapping(self.pj)
        self.assertEqual(m["ID_POLICY"], "FQID_CANONICAL_REVISION_REQUIRED")
        self.assertEqual(m["SPATIAL_CONSISTENCY_MODE"], "text_only")

    def test_the_brief_does_not_repeat_production_params(self):
        """★ 两处写法万一不一致，模型信哪个都不对。"""
        ST.save(self.pj, {"visual_style": "写实"})
        brief = ST.brief_block(self.pj, {"ratio": "9:16"})
        self.assertNotIn("9:16", brief)
        self.assertIn("以【项目参数】为准", brief)


class ShowTests(unittest.TestCase):

    def test_enums_show_both_the_skill_value_and_chinese(self):
        """★ 只给中文，对 skill 时要翻译；只给英文，页面上没人看得懂。"""
        s = ST.show(ST.BY_KEY["adaptation_authority"], "optimize_pacing")
        self.assertIn("optimize_pacing", s)
        self.assertIn("允许优化节奏与镜头", s)


class CommentStrippingTests(unittest.TestCase):

    def test_html_comments_do_not_reach_the_model(self):
        """★ 这份东西**每一次调用都发**，注释的噪音要乘以调用次数。"""
        pj = new_project()
        try:
            raw = io.open(os.path.join(ROOT, "prompts", "_common.md"),
                          encoding="utf-8").read()
            self.assertIn("<!--", raw, "源文件本来就该有给人看的注释")
            self.assertNotIn("<!--", S.system_prompt(pj, {}))
        finally:
            shutil.rmtree(pj.root, ignore_errors=True)


class ScanTests(unittest.TestCase):
    """「哪个环节要哪些设定」是扫出来的，不手写。"""

    def test_it_reports_which_templates_use_each_setting(self):
        self.assertEqual(set(ST.used_by()), set(ST.PLACEHOLDERS))

    def test_image_size_is_actually_used(self):
        self.assertTrue(ST.used_by()["IMAGE_SIZE"], "模板里本来就在用")

    def test_unused_settings_are_reportable(self):
        """定义了但没有模板用 = 填了也没人看，页面上要标出来。"""
        self.assertIsInstance(ST.unused(), list)


if __name__ == "__main__":
    unittest.main()
