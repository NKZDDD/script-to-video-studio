# -*- coding: utf-8 -*-
"""判成不出图的场景状态，不该被派成出图任务；该出图的要有身份映射。

用户实遇 `SCST_EP01_SC01_01`（电影级）：出图前硬停，报错是

    提示词里没有 `Image N = 资产ID` 的参考图映射，但这一条要传 8 张参考图

而那条提示词的第一行写着：

    当前判定：LOGICAL_ONLY。…候选图片角色：无；**本条不生成图片**。

所以报错指错了地方 —— 它不是「映射漏了」，是**这条压根不该是出图任务**：
第十一环节判它只出文字合同（skill：LOGICAL_ONLY / DEFER_TO_VIDEO 不出图），
而一份文字合同本来就没有参考图映射。

根因是那个判定**只写在正文里**，输出 schema 里原来没有这一栏，程序读不到。

两个 bug 一起修：
  ① 判成不出图的不派出图任务（结构化字段优先，正文兜底给老项目）
  ② 该出图的那些**也要回填身份映射** —— 以前只有资产提示词享受这个，
     而 n11 的 schema 里有一模一样的 `reference_role_map`
"""
import unittest

from core import run_v34 as R


def _sc(**kw):
    base = {"scstate_id": "SCST_EP01_SC01_01", "prompt": "一段正文"}
    base.update(kw)
    return base


class DecisionTests(unittest.TestCase):
    """要不要出图。"""

    def test_the_structured_field_wins(self):
        self.assertTrue(R.scstate_no_image(_sc(decision="LOGICAL_ONLY")))
        self.assertTrue(R.scstate_no_image(_sc(decision="DEFER_TO_VIDEO")))
        self.assertFalse(R.scstate_no_image(_sc(decision="VISUAL_ANCHOR_REQUIRED")))
        self.assertFalse(R.scstate_no_image(_sc(decision="VISUAL_QC_REQUIRED")))

    def test_the_gear_is_case_insensitive(self):
        """模板写大写、代码里那张表是小写 —— 不归一化就永远匹配不上。"""
        self.assertTrue(R.scstate_no_image(_sc(decision="logical_only")))

    def test_the_body_is_the_fallback_for_projects_already_run(self):
        """★ 已经跑完的项目里没有 `decision` 那一栏 ——

        不认正文的话，这次修复对手头这个项目一点用都没有。
        """
        self.assertTrue(R.scstate_no_image(_sc(
            prompt="任务：这是EP01-SEG04入口稳定时刻的场景状态图逻辑合同。"
                   "当前判定：LOGICAL_ONLY。逻辑状态是否已完整登记：是。"
                   "候选图片角色：无；本条不生成图片。")))

    def test_the_other_wording_too(self):
        """模板里那一栏叫「决定：」，正文里实跑写的是「当前判定：」——两种都认。"""
        self.assertTrue(R.scstate_no_image(_sc(prompt="决定：DEFER_TO_VIDEO\n理由：中间动作")))
        self.assertTrue(R.scstate_no_image(_sc(prompt="……本条不生成图片。……")))

    def test_a_normal_scstate_prompt_is_not_misread(self):
        """★ 别拦过头。正则只咬模板规定的那两种写法。

        提到这个档位名字的说明文字不算判定 —— 误判的代价是**该出的图不出**，
        而且悄无声息。
        """
        self.assertFalse(R.scstate_no_image(_sc(
            prompt="本条不是 LOGICAL_ONLY，需要出图。"
                   "（LOGICAL_ONLY 的条目只出文字合同，这里不适用）")))

    def test_the_field_beats_the_body(self):
        """★ 字段是权威：字段说要出图，就别被正文里的说明文字带跑。"""
        self.assertFalse(R.scstate_no_image(_sc(
            decision="VISUAL_ANCHOR_REQUIRED", prompt="决定：LOGICAL_ONLY")))

    def test_an_empty_scstate_asks_for_an_image(self):
        """什么都没说的按出图算 —— 这是原来的行为，不许因为这次改动少出图。"""
        self.assertFalse(R.scstate_no_image(_sc()))


class IdentityMapTests(unittest.TestCase):
    """② 该出图的那些，身份映射要从结构化字段补出来。"""

    def _sc(self):
        return _sc(prompt="任务：出一张场景状态图。\n构图：……",
                   reference_role_map=[
                       {"image_n": 1, "asset_id": "C001", "asset_name": "林溪",
                        "who_what_visible": "成年女性正面半身",
                        "story_time_state": "19岁，校服沾尘",
                        "must_preserve": "五官", "must_not_copy": "姿态",
                        "applicable_scope": "本段"},
                       {"image_n": 2, "asset_id": "LOC001", "asset_name": "医院走廊",
                        "who_what_visible": "公共候诊区",
                        "story_time_state": "当日白天",
                        "must_preserve": "几何", "must_not_copy": "人物",
                        "applicable_scope": "本段"}])

    def test_it_backfills_for_a_scstate(self):
        """★ 这就是那个 bug —— 以前只有资产提示词享受回填。"""
        out = R.with_identity_map(self._sc())
        self.assertIn("Image 1 = C001", out)
        self.assertIn("Image 2 = LOC001", out)
        self.assertIn("任务：出一张场景状态图", out, "正文丢了")

    def test_the_six_fields_come_along(self):
        out = R.with_identity_map(self._sc())
        self.assertIn("是谁/是什么 + 画面可见内容：成年女性正面半身", out)
        self.assertIn("有权控制：五官", out)
        self.assertIn("无权控制：姿态", out)

    def test_a_prompt_that_already_has_the_map_is_left_alone(self):
        """写了就别插手 —— 模型有自己的排版，插进去只会打乱它。"""
        sc = self._sc()
        sc["prompt"] = "Image 1 = C001 林溪\n……"
        self.assertEqual(R.with_identity_map(sc), sc["prompt"])


class WiringTests(unittest.TestCase):

    def test_build_tasks_skips_the_no_image_ones(self):
        import inspect
        src = inspect.getsource(R.build_tasks)
        self.assertIn("scstate_no_image(sc)", src)
        self.assertIn("scst_skipped", src)

    def test_it_records_a_note_instead_of_silently_dropping(self):
        """★ 「少了几张」和「本来就这几张」看着一样 —— 必须记一笔。"""
        import inspect
        src = inspect.getsource(R.build_tasks)
        self.assertIn("SCSTATE_LOGICAL_ONLY", src)

    def test_the_scstate_prompt_file_gets_the_backfill(self):
        import inspect
        src = inspect.getsource(R.write_prompt_files)
        self.assertIn("with_identity_map(sc)", src)

    def test_the_template_asks_for_the_field(self):
        """★ 模板不要求这一栏，模型就不会填，兜底正则得背一辈子。"""
        import io
        import os
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        text = io.open(os.path.join(root, "prompts", "n11_scstate.md"),
                       encoding="utf-8").read()
        self.assertIn('"decision"', text)
        i = text.index('"scstates"')
        self.assertIn('"decision"', text[i:i + 400],
                      "decision 要在 scstates[] 那个结构里，不是别处")


if __name__ == "__main__":
    unittest.main()
