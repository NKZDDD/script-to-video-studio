# -*- coding: utf-8 -*-
"""解说剧：对白也由画外音念，画面里没人开口。

这一项和「念旁白时人物要不要动嘴」差一层，混了就漏：

    narration_on_screen  = **旁白**念的时候人物动不动嘴
    dialogue_mode        = **对白**（剧本里带引号那些）是谁在说

实测「烟火尽头」896 段里有 **121 段带引号的对白**、136 段第一人称独白。
只勾「旁白不动嘴」的话，那 121 段照旧被当成台词对口型 ——
于是全片一半画外音一半对口型，看着就是坏的，而且不报错。

所以要有单独一项，而且**不许让剧本推翻**：剧本里写满带引号的对白，
模型看了只会得出「这部剧有对白」，而「让谁来念」是制作决策。
"""
import unittest

from core import settings as S


class _PJ:
    """只喂 load() 的假项目。"""

    def __init__(self, vals):
        self.vals = vals


def _rule(**vals):
    orig = S.load
    S.load = lambda pj: pj.vals
    try:
        return S.narration_rule(_PJ(vals))
    finally:
        S.load = orig


class FieldTests(unittest.TestCase):

    def test_the_field_exists_with_both_gears(self):
        f = next((x for x in S.FIELDS if x["key"] == "dialogue_mode"), None)
        self.assertIsNotNone(f, "没有「对白怎么呈现」这一项，解说剧没法表达")
        self.assertEqual(f["options"], ["in_scene", "voice_over_only"])

    def test_the_default_keeps_todays_behaviour(self):
        """★ 默认必须是「角色开口说」—— 现有项目的行为不许被这次改动改掉。"""
        f = next(x for x in S.FIELDS if x["key"] == "dialogue_mode")
        self.assertEqual(f["default"], "in_scene")

    def test_the_script_may_not_override_it(self):
        """★ **这一条是重点。** 剧本里有 121 段带引号的对白，

        模型只会得出「这部剧有对白」。所以它必须在 NOT_FROM_SCRIPT 里。
        """
        self.assertIn("dialogue_mode", S.NOT_FROM_SCRIPT)


class RuleTests(unittest.TestCase):

    def test_the_narrated_drama_case(self):
        """★ 解说剧：旁白有、不动嘴、对白也是画外音。"""
        out = _rule(narration=True, narration_style="first_person_inner",
                    narration_voice="女主 C001",
                    narration_on_screen="no_lip_sync",
                    dialogue_mode="voice_over_only")
        self.assertIn("第一人称内心独白", out)
        self.assertIn("女主 C001", out)
        self.assertIn("念旁白时人物：不动嘴（画外音）", out)
        self.assertIn("对白呈现：全部画外音，人物不开口", out)

    def test_dialogue_is_reported_even_with_narration_off(self):
        """★ 最常见的解说剧根本不标「旁白」—— 全靠解说把对白念出来。

        把这一行放进 `if narration` 里就是漏掉这一种。
        """
        out = _rule(dialogue_mode="voice_over_only")
        self.assertIn("旁白 / 画外音：无", out)
        self.assertIn("对白呈现：全部画外音，人物不开口", out)

    def test_the_default_says_so_too(self):
        """没设过也要说出来 —— 半截句子和缺项模型都会自己去填。"""
        self.assertIn("对白呈现：角色开口说（对口型）", _rule())

    def test_it_only_states_the_value(self):
        """★ 不许替用户丰富提示词。这一条是反复踩过的：

        我在 medium_rule / narration_rule 里都写过一整段禁令和解释，
        用户的要求是「原原本本给出去」。所以这里只钉一件事：
        规则里不出现我自己编的措辞。
        """
        out = _rule(dialogue_mode="voice_over_only")
        for mine in ("禁止", "必须", "不要", "确保", "严格"):
            self.assertNotIn(mine, out, f"多写了「{mine}」—— 那是替用户做提示词工程")


class ReachTests(unittest.TestCase):
    """算出来送不到就是白做。"""

    def test_both_systems_render_the_narration_slot(self):
        import inspect

        from core import run_v34 as R
        from core import stages as ST
        self.assertIn('m["NARRATION_RULE"] = _st.narration_rule(pj)',
                      inspect.getsource(ST.system_prompt))
        self.assertIn('m["NARRATION_RULE"] = _st.narration_rule(pj)',
                      inspect.getsource(R.system_prompt)
                      if hasattr(R, "system_prompt") else
                      inspect.getsource(R))

    def test_the_common_template_has_the_slot(self):
        import io
        import os
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        text = io.open(os.path.join(root, "prompts", "_common.md"),
                       encoding="utf-8").read()
        self.assertIn("{{NARRATION_RULE}}", text)


if __name__ == "__main__":
    unittest.main()
