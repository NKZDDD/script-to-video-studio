# -*- coding: utf-8 -*-
"""V6.1「上游边界锚定与人物空间一致性强化」在五份模板里的落地。

V6.0 的主题是图像减压。实跑之后暴露出它减过了头：故事板被吃掉大半，
视频开始拿**上一条视频的尾帧**当参考 —— 整段没有任何东西持有
Camera/Blocking/Time 权威，模型只能自己编。

V6.1 是针对这个的修正，两条新规则：

  19. 任何 AI 视频生成帧**只能作为 QC 证据**，禁止注册为下一 SEG 的
      Reference / Temporal Primary / Canonical 入口。边界锚点改成在两条
      视频生产**之前**编译（BNDPLAN / BNDANCHOR）。
  20. **图像减压不得降低一致性维度。** 六维（Identity、LOOK/CT、
      Spatial、Position/Blocking、State、Prop）是不可降级底座。

参数名的改动一句话说明方向：
`adaptive_minimum_sufficient_execution_set`（最小充分）
→ `adaptive_authority_complete_nonconflicting_set`（权威完整）。
"""
import io
import os
import unittest

from core import settings as ST, system_v34 as V

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _tpl(name):
    return io.open(os.path.join(ROOT, "prompts", f"{name}.md"),
                   encoding="utf-8").read()


class BoundaryTests(unittest.TestCase):
    """★ 这一组就是用户实跑撞到的那个。"""

    def test_generated_frames_are_forbidden_by_policy(self):
        self.assertEqual(
            ST.FIXED_DERIVED["generated_video_frame_reference_policy"], "forbidden")

    def test_the_v60_segbound_field_is_gone(self):
        """V6.0 的 `approved_video_boundary_reuse` 被 V6.1 整条废掉。

        留着的话页面上会有一个「过 QC 后可作为下一段入口」的开关，
        而 skill 已经明令禁止 —— 那个开关一打开就是回到 bug 里。
        """
        self.assertNotIn("approved_video_boundary_reuse", ST.BY_KEY)

    def test_the_replacement_is_a_precompiled_boundary(self):
        f = ST.BY_KEY["canonical_boundary_policy"]
        self.assertIn("canonical_cut_pair", f["options"])
        self.assertIn("两条视频生产之前", f["why"].replace("**", ""))


class DimensionFloorTests(unittest.TestCase):

    def test_the_policy_name_is_not_minimum_sufficient(self):
        """★ 这条测试钉的是**方向**，被改过两次名字，方向一直没变。

        V6.0  `minimum_sufficient`                 能删就删
        V6.1  `adaptive_authority_complete…`       六维都有来源才允许删
        V6.2  `mandatory_storyboard_plus_selective_effective_supplemental`
                                                  故事板必给，只有补图是挑的

        「最小充分」和后两者是**相反**的判据。名字可以再改，
        但不许改回「能删就删」——那个方向一打开就是回到 bug 里。
        """
        v = ST.FIXED_DERIVED["video_reference_policy"]
        self.assertNotIn("minimum_sufficient", v)
        self.assertIn("mandatory_storyboard", v)

    def test_the_storyboard_spine_is_mandatory(self):
        """★ V6.2 的主题：视频不许退化成只有一张起始图。"""
        self.assertEqual(ST.FIXED_DERIVED["storyboard_video_reference_policy"],
                         "mandatory_temporal_spine")

    def test_the_gate_is_required(self):
        self.assertEqual(ST.FIXED_DERIVED["reference_dimension_coverage_gate"],
                         "required")

    def test_deleting_an_image_does_not_delete_the_position_contract(self):
        """★ 最容易做错的一条：不出图 ≠ 没有位置合同。"""
        self.assertEqual(ST.FIXED_DERIVED["position_contract_policy"],
                         "immutable_without_authorized_movement")
        for name in ("n11_scstate", "n12_storyboard", "n13_video"):
            self.assertIn("不等于删", _tpl(name), name)


class StoryboardSheetTests(unittest.TestCase):
    """★ 用户实跑撞到 16 格。V6.1 上限是 3。"""

    def test_the_cap_is_three_not_nine(self):
        t = _tpl("n12_storyboard")
        self.assertIn("{{STORYBOARD_MAX_KF_PER_SHEET}}", t)
        self.assertIn("每张 Sheet 最多", t)
        self.assertNotIn("不要凑满 3×3", t, "V5.6 的 9 格上限还留着")

    def test_dense_layouts_are_named_and_forbidden(self):
        """光说「最多 3 格」不够 —— 要点名禁止九宫格和缩人排版，

        否则模型会用「一格里放三个动作阶段」绕过去。
        """
        t = _tpl("n12_storyboard")
        self.assertIn("九宫格", t)
        self.assertIn("多个动作阶段塞进同一格", t)

    def test_kf_materialization_tiers_are_listed(self):
        t = _tpl("n12_storyboard")
        for tier in ("TEXT_CANON_ONLY", "VISUAL_ENTRY_ANCHOR",
                     "VISUAL_RESULT_ANCHOR", "VISUAL_HIGH_RISK_ANCHOR",
                     "VISUAL_BOUNDARY_ANCHOR"):
            self.assertIn(tier, t, tier)
        self.assertIn("默认是 `TEXT_CANON_ONLY`", t)

    def test_the_two_carriers_are_equal_under_v62(self):
        """★ V6.1 这条是「**不许默认**让图片模型一次生成三格」——

        逐张独立锚点是正路，三格排版是例外。V6.2 第 19 章把两者拉平了：
        有序多张 Sheet 和有序独立 KF 锚点是**等价载体**，按模型实测挑，
        而必须满足的是「覆盖完整关键时间推进」。

        所以那句禁令没了，换成这条 —— 钉住的是「两种都合法、都必须有序」。
        """
        t = _tpl("n12_storyboard")
        self.assertIn("ORDERED_CONTINUATION_SHEETS", t)
        self.assertIn("ORDERED_KF_ANCHORS", t)
        self.assertIn("等价载体", t)
        self.assertNotIn("不许默认让图片模型一次生成三格", t,
                         "V6.2 已经把两种载体拉平，这句禁令留着会和上面那张表打架")

    def test_the_three_panel_cap_itself_is_still_there(self):
        """★ 别把上限一起丢了：每张最多 3 格是 V6.0 定的，V6.2 没动。"""
        self.assertIn("每张 Sheet 最多", _tpl("n12_storyboard"))


class ScstateLogicalFirstTests(unittest.TestCase):

    def test_scstate_defaults_to_a_logical_contract(self):
        t = _tpl("n11_scstate")
        self.assertIn("默认不出图", t)
        self.assertIn("{{SCSTATE_MATERIALIZATION_POLICY}}", t)
        for d in ("LOGICAL_ONLY", "DEFER_TO_VIDEO", "VISUAL_ANCHOR_REQUIRED"):
            self.assertIn(d, t, d)

    def test_playing_it_safe_is_not_a_trigger(self):
        """★ 「保险起见出一张」是图片层过度承载的来源，要点名否掉。"""
        self.assertIn("不是合法触发条件", _tpl("n11_scstate"))

    def test_zone_coherent_slicing_replaces_cramming(self):
        t = _tpl("n11_scstate")
        self.assertIn("OFF-FRAME ACTIVE", t)
        self.assertIn("不许为了同框把人移过来", t)


class AssetTierTests(unittest.TestCase):

    def test_the_new_tiers_are_in_the_template(self):
        t = _tpl("n4_assets")
        for d in ("logical_only", "defer_to_video", "visual_anchor_required",
                  "existing_canonical"):
            self.assertIn(d, t, d)

    def test_the_code_honours_them(self):
        """★ 模板判出来了、程序不认，等于没判 —— 照样写提示词。

        而那一步本来就顶着输出上限，白写的部分直接把它推过线。
        """
        from core import run_v34 as R
        for d in ("logical_only", "defer_to_video", "existing_canonical", "skip"):
            self.assertIn(d, R.NO_IMAGE_DECISIONS, d)


class ViewCoverageTests(unittest.TestCase):

    def test_views_are_demand_driven(self):
        t = _tpl("n5_spatial")
        self.assertIn("{{REDUNDANCY_OVERLAP_HEURISTIC}}", t)
        self.assertIn("REDUNDANT_VIEW_REJECTED", t)

    def test_focal_length_alone_is_not_a_new_view(self):
        """★ 不点名的话，模型会用「换个焦段」凑出一堆几乎一样的图。"""
        t = _tpl("n5_spatial")
        self.assertIn("只是焦段不同", t)
        self.assertIn("只是轻微横移", t)


class WiringTests(unittest.TestCase):
    """占位符写进模板但填不上，会原样出现在提示词里。"""

    def test_every_setting_placeholder_is_fillable(self):
        for p in ST.PLACEHOLDERS:
            self.assertIn(p, V.COMMON_PLACEHOLDERS, p)

    def test_the_common_list_is_derived_not_hand_copied(self):
        """★ 手抄一份迟早和字段表对不上，然后校验就成了摆设。"""
        import inspect
        src = inspect.getsource(V)
        self.assertIn("_setting_placeholders", src)


if __name__ == "__main__":
    unittest.main()
