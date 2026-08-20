# -*- coding: utf-8 -*-
"""V6.2：强制故事板时间骨架 + 导演级视频执行。

这一版改的是**电影级的一条硬结构**，所以钉紧一点。

三件事：

① 视频不许再只给一张起始图
   V6.1 按可靠度分路：`high` → `START_ONLY`。V6.2 原话是「可靠度只决定
   故事板**承载颗粒度**、补图数量与提示词冗余度，**不决定是否提供故事板**」。

② 一个 SEG 是 1..N 张**有序**故事板
   V6.1 的产物把 `storyboard_prompt` 挂在包一级 → 装配层只有一个输出位置 →
   模型只能把 6 格挤进一张（实遇 EP01-SEG06 的提示词就是「一张六格」），
   而那和「每张最多 3 格」的上限直接冲突。V6.2 把提示词下移到 sheet 一级。

③ 老项目照旧能跑
   已经出好的几百张故事板不许被判成没出 —— 路径一个字都不能改。
"""
import unittest

from core import produce as P
from core import run_v34 as R
from core import settings as S
from core import system_v34 as V


def _pkg(**kw):
    base = {"sbpkg_id": "PKG", "seg_id": "EP01-SEG01", "kf": []}
    base.update(kw)
    return base


class PolicyTests(unittest.TestCase):
    """① 参数照 skill 改，值不许自己发明。"""

    def test_the_spine_is_mandatory(self):
        self.assertEqual(S.FIXED_DERIVED["storyboard_video_reference_policy"],
                         "mandatory_temporal_spine")

    def test_the_reference_policy_is_two_layered(self):
        self.assertEqual(
            S.FIXED_DERIVED["video_reference_policy"],
            "mandatory_storyboard_plus_selective_effective_supplemental")

    def test_the_new_gates_are_required(self):
        for k in ("storyboard_reference_admission_gate",
                  "effective_reference_selection_gate",
                  "micro_performance_contract",
                  "cinematic_camera_grammar_contract"):
            self.assertEqual(S.FIXED_DERIVED[k], "required", k)

    def test_the_prompt_mode_is_director_level(self):
        self.assertEqual(S.FIXED_DERIVED["video_prompt_detail_mode"],
                         "director_level_expanded")

    def test_action_phase_is_risk_driven(self):
        """不是一律展开 —— skill 写的是 risk_driven。"""
        self.assertEqual(S.FIXED_DERIVED["action_phase_physical_response_contract"],
                         "risk_driven_required")

    def test_every_fixed_policy_has_a_field_that_explains_it(self):
        """★ 只加值不加字段的话，页面上那一条根本不显示 ——

        用户看不到它，也就不知道为什么行为变了。
        """
        for k in S.FIXED_DERIVED:
            self.assertIn(k, S.BY_KEY, f"{k} 没有对应的字段说明")

    def test_the_carrier_is_now_a_choice_not_a_quantity(self):
        """★ V6.2 把「出多少张」改成了「用哪种载体」。"""
        f = S.BY_KEY["storyboard_materialization_policy"]
        self.assertEqual(f["options"], ["mandatory_temporal_spine",
                                        "ordered_continuation_sheets",
                                        "ordered_kf_anchors"])
        self.assertEqual(f["default"], "mandatory_temporal_spine")

    def test_old_saved_values_are_translated(self):
        """★ 老项目里存的是 `anchor_only` —— 不翻译的话模板里会渲染出

        一个 V6.2 不认识的词，而页面上那个下拉显示为空。两边都不报错。
        """
        ren = S._RENAMED_VALUES["storyboard_materialization_policy"]
        for old in ("anchor_only", "selected_kf", "full_storyboard"):
            self.assertIn(ren[old],
                          S.BY_KEY["storyboard_materialization_policy"]["options"],
                          f"{old} 翻译成了一个不在选项里的值")


class SheetNormalizerTests(unittest.TestCase):
    """② 两种形状归一。"""

    def test_the_new_shape_gives_one_row_per_sheet(self):
        out = R.sb_sheets(_pkg(sheets=[
            {"sheet_id": "SHEET_A", "order": 1, "storyboard_prompt": "a"},
            {"sheet_id": "SHEET_B", "order": 2, "storyboard_prompt": "b"}]),
            "EP01-SEG01")
        self.assertEqual([s["sheet_id"] for s in out], ["SHEET_A", "SHEET_B"])
        self.assertEqual([s["prompt"] for s in out], ["a", "b"])
        self.assertFalse(any(s["legacy"] for s in out))

    def test_it_sorts_by_order_not_by_position(self):
        """★ 顺序错了模型会把后段当前段 —— 而画面照样出得来。"""
        out = R.sb_sheets(_pkg(sheets=[
            {"sheet_id": "SHEET_B", "order": 2, "storyboard_prompt": "b"},
            {"sheet_id": "SHEET_A", "order": 1, "storyboard_prompt": "a"}]),
            "EP01-SEG01")
        self.assertEqual([s["sheet_id"] for s in out], ["SHEET_A", "SHEET_B"])

    def test_a_missing_order_falls_back_to_position(self):
        out = R.sb_sheets(_pkg(sheets=[
            {"sheet_id": "X", "storyboard_prompt": "a"},
            {"sheet_id": "Y", "storyboard_prompt": "b"}]), "EP01-SEG01")
        self.assertEqual([s["order"] for s in out], [1, 2])

    def test_a_sheet_without_a_prompt_is_dropped(self):
        """没有提示词的那张出不了图 —— 别给它建任务，但会被记成骨架不全。"""
        out = R.sb_sheets(_pkg(sheets=[
            {"sheet_id": "A", "storyboard_prompt": "a"},
            {"sheet_id": "B", "kf_range": "KF04-KF06"}]), "EP01-SEG01")
        self.assertEqual([s["sheet_id"] for s in out], ["A"])

    def test_the_old_package_level_shape_still_works(self):
        """★ **这一条最要紧。** 老项目已经出好几百张，不许判成没出。"""
        out = R.sb_sheets(_pkg(storyboard_prompt="p",
                               reference_order=[{"image_n": 1, "asset_id": "X"}],
                               sheets=[{"sheet_id": "SHEET_A",
                                        "kf_range": "KF01-KF06"}]),
                          "EP01-SEG01")
        self.assertEqual(len(out), 1)
        self.assertTrue(out[0]["legacy"])
        self.assertEqual(out[0]["prompt"], "p")
        self.assertEqual(out[0]["reference_order"][0]["asset_id"], "X")

    def test_the_old_paths_do_not_change_by_one_character(self):
        """★ 路径改了 = 几百张重新花钱做一遍。"""
        old = R.sb_sheets(_pkg(storyboard_prompt="p"), "EP01-SEG01")[0]
        self.assertEqual(R.sb_file("PROJ-001", "EP01-SEG01", old),
                         "04_故事板/PROJ-001_EP01-SEG01_STORYBOARD.png")
        self.assertEqual(R.sb_prompt_name("EP01-SEG01", old),
                         "EP01-SEG01_STORYBOARD_PROMPT.txt")

    def test_the_new_paths_are_per_sheet_and_distinct(self):
        rows = R.sb_sheets(_pkg(sheets=[
            {"sheet_id": "SHEET_A", "order": 1, "storyboard_prompt": "a"},
            {"sheet_id": "SHEET_B", "order": 2, "storyboard_prompt": "b"}]),
            "EP01-SEG01")
        paths = {R.sb_file("PROJ-001", "EP01-SEG01", s) for s in rows}
        self.assertEqual(len(paths), 2, "两张写同一个文件，后一张覆盖前一张")
        names = {R.sb_prompt_name("EP01-SEG01", s) for s in rows}
        self.assertEqual(len(names), 2)

    def test_a_package_with_nothing_at_all_gives_nothing(self):
        self.assertEqual(R.sb_sheets(_pkg(), "EP01-SEG01"), [])


class SpineDeliveryTests(unittest.TestCase):
    """③ 视频拿到的必须是整条有序骨架。"""

    def test_the_worker_reads_the_whole_ordered_spine(self):
        import inspect
        src = inspect.getsource(P.make_video_worker)
        self.assertIn('task.get("storyboard_refs")', src)
        self.assertIn('key=lambda s: s.get("order") or 0', src,
                      "没按 order 排 —— 顺序靠字典顺序是碰运气")

    def test_a_missing_middle_sheet_stops_it(self):
        """★ 缺中间那张也要停。只检查第一张的话，骨架断了照样出片 ——

        而出来的画面和这一段的剧情没有关系，任务还标 ok。
        """
        import inspect
        src = inspect.getsource(P.make_video_worker)
        self.assertIn("bad = [s for s in spine", src)
        self.assertIn("缺 ", src)

    def test_the_whole_spine_goes_into_the_ledger(self):
        """事后要查得出这一段是拿哪几张、按什么顺序做的。"""
        import inspect
        self.assertIn("storyboard_spine", inspect.getsource(P.make_video_worker))


class TemplateTests(unittest.TestCase):
    """模板层：改到位了没有。"""

    @staticmethod
    def _tpl(name):
        import io
        import os
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return io.open(os.path.join(root, "prompts", f"{name}.md"),
                       encoding="utf-8").read()

    def test_n12_offers_both_carriers(self):
        t = self._tpl("n12_storyboard")
        self.assertIn("ORDERED_CONTINUATION_SHEETS", t)
        self.assertIn("ORDERED_KF_ANCHORS", t)
        self.assertIn("STORYBOARD_REFERENCE_CAPACITY_BLOCKED", t)

    def test_n12_no_longer_lets_a_sheet_be_text_only(self):
        """★ V6.1 允许「只剩 1–2 个锚点时整个 Sheet 不出图」，V6.2 禁了。"""
        t = self._tpl("n12_storyboard")
        self.assertNotIn("Sheet 可以完全不出图", t)
        self.assertIn("必须拥有覆盖", t)

    def test_n12_puts_the_prompt_on_the_sheet(self):
        """★ 挂在包一级就是「一个 SEG 只能出一张」的根因。"""
        t = self._tpl("n12_storyboard")
        i = t.index('"sheets": [')
        j = t.index('"kf_count"')
        self.assertIn("storyboard_prompt", t[i:j], "提示词没在 sheet 一级")
        self.assertIn('"order"', t[i:j])
        self.assertIn('"filename"', t[i:j])

    def test_n13_drops_the_reliability_branch(self):
        """★ V6.2 的主题就是这一条。"""
        t = self._tpl("n13_video")
        self.assertNotIn("| `high` | `START_ONLY`", t)
        self.assertIn("VIDEO_STORYBOARD_SPINE_MISSING", t)
        self.assertIn("不决定是否提供故事板", t)

    def test_n13_keeps_reliability_for_what_it_still_decides(self):
        """★ 别删过头：可靠度还在管承载颗粒度和补图数量。"""
        self.assertIn("{{VIDEO_EXECUTION_RELIABILITY}}", self._tpl("n13_video"))

    def test_n13_has_the_director_level_blocks(self):
        t = self._tpl("n13_video")
        for k in ("TIME WINDOW", "MICRO-PERFORMANCE", "ACTION PHASE",
                  "CUT OR TRANSITION MOTIVATION", "时间窗执行卡"):
            self.assertIn(k, t, k)
        for code in ("VIDEO_PROMPT_EXECUTION_DETAIL_INSUFFICIENT",
                     "VIDEO_PERFORMANCE_CONTRACT_INSUFFICIENT",
                     "VIDEO_ACTION_PHASE_INCOMPLETE",
                     "VIDEO_CAMERA_GRAMMAR_INSUFFICIENT",
                     "STORYBOARD_REFERENCE_ADMISSION_FAILED",
                     "VIDEO_REFERENCE_UNIQUE_UTILITY_UNPROVEN"):
            self.assertIn(code, t, code)

    def test_n13_says_detail_is_not_adjectives(self):
        """★ 这条是 skill 自己反复强调的，最容易被做成堆形容词。"""
        self.assertIn("堆形容词", self._tpl("n13_video"))

    def test_the_audit_checks_the_spine(self):
        t = self._tpl("n14_audit")
        self.assertIn("故事板骨架与导演级执行", t)
        self.assertIn("没有退化成只有一张起始图", t)

    def test_the_audit_category_list_includes_it(self):
        """★ 清单加了但枚举没加 —— 模型没地方归类，只能塞进别的类。"""
        t = self._tpl("n14_audit")
        i = t.index('"category"')
        self.assertIn("故事板骨架与导演级执行", t[i:i + 200])
        j = t.index('"checked"')
        self.assertIn("故事板骨架与导演级执行", t[j:j + 260])

    def test_the_required_fields_moved_down_a_level(self):
        """★ 只校验包一级的话，sheets 里每张都没提示词也算过 ——

        那时装配层一张任务都建不出来，而这不报错，只是没有故事板。
        """
        req = V.LLM_SPEC["n12"][2]
        self.assertIn("sbpkg[].sheets[]", req)
        # 末尾的 `!` = 值不许为空。只查键存在拦不住 `"storyboard_prompt": ""`
        # —— 那时装配层建不出任务，而这不报错，只是没有故事板。
        self.assertIn("sbpkg[].sheets[].storyboard_prompt!", req)
        self.assertNotIn("sbpkg[].storyboard_prompt", req)


if __name__ == "__main__":
    unittest.main()
