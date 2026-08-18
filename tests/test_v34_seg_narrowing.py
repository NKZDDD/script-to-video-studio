# -*- coding: utf-8 -*-
"""逐段环节别把整集的东西都发过去。

实跑量出来的（一集 17 段）：

    n11  逐段  单段输入 101,342 token
    n12  逐段  单段输入  52,962 token
    n13  逐段  单段输入  62,829 token

n11 是**逐段**环节，一段却收了十万 token —— 同一份整集镜头表、状态表、
空间主表被发了 17 遍。代价有两层：钱按段翻倍；更要命的是输入越大，
模型吐第一个字之前想得越久，中转站看不到数据就在 125 秒切断。
那一晚三条 524 的报错里写着「等了 127 秒」「等了 125 秒」，正是这个。

裁剪靠一条 ID 链，四层都对得上：

    n10_segs.included_shots  →  n9_shots.shot_id
    n9_shots.source_cvs      →  n8_cvs.cvs_id
    n8_cvs.spatial_id        →  n5_spatial.spatial_masters.spatial_id

**接不上就整份发。** 裁错比多发严重得多：模型看不到本段真正要用的镜头，
就会自己编一个 —— 而这不报错。
"""
import shutil
import unittest

from core import run_v34 as R
from test_v34_run import EP1, new_project

SEG = f"{EP1}-SEG02"

N10 = {"segs": [
    {"seg_id": f"{EP1}-SEG01", "included_shots": ["SH1"],
     "entry_cvs": "CV1", "exit_cvs": "CV1"},
    {"seg_id": SEG, "included_shots": ["SH2", "SH3"],
     "entry_cvs": "CV2", "exit_cvs": "CV3"},
]}
N9 = {"shots": [
    {"shot_id": "SH1", "source_cvs": "CV1"},
    {"shot_id": "SH2", "source_cvs": "CV2"},
    {"shot_id": "SH3", "source_cvs": "CV3"},
    {"shot_id": "SH4", "source_cvs": "CV4"},
], "transitions": [
    {"transition_id": "T1", "from_shot": "SH1", "to_shot": "SH2"},   # 跨段
    {"transition_id": "T2", "from_shot": "SH2", "to_shot": "SH3"},   # 本段
], "timing_plan": [
    {"shot_id": "SH2", "start": 0, "end": 3},
    {"shot_id": "SH4", "start": 9, "end": 12},
]}
N8 = {"cvs": [
    {"cvs_id": "CV1", "spatial_id": "SP1"},
    {"cvs_id": "CV2", "spatial_id": "SP2"},
    {"cvs_id": "CV3", "spatial_id": "SP2"},
    {"cvs_id": "CV4", "spatial_id": "SP9"},
], "vt": [
    {"vt_id": "VT1", "source_cvs": "CV2", "target_cvs": "CV3"},
    {"vt_id": "VT9", "source_cvs": "CV4", "target_cvs": "CV4"},
]}
N5 = {"spatial_masters": [{"spatial_id": "SP1"}, {"spatial_id": "SP2"},
                          {"spatial_id": "SP9"}],
      "loc_views": [{"view_id": "V1", "spatial_id": "SP2"},
                    {"view_id": "V9", "spatial_id": "SP9"}]}


class ContextTests(unittest.TestCase):

    def setUp(self):
        self.pj = new_project()
        for name, obj in (("n10_segs", N10), ("n9_shots", N9), ("n8_cvs", N8)):
            self.pj.save_stage(name, obj, EP1)

    def tearDown(self):
        shutil.rmtree(self.pj.root, ignore_errors=True)

    def test_the_chain_resolves(self):
        ctx = R._seg_context(self.pj, EP1, SEG)
        self.assertEqual(ctx["shots"], {"SH2", "SH3"})
        self.assertEqual(ctx["cvs"], {"CV2", "CV3"})
        self.assertEqual(ctx["spatial"], {"SP2"})

    def test_an_unknown_segment_gives_up(self):
        self.assertIsNone(R._seg_context(self.pj, EP1, "EP01-SEG99"))

    def test_a_segment_without_included_shots_gives_up(self):
        """★ 模型没填这个字段时必须整份发，不能裁成空的。"""
        self.pj.save_stage("n10_segs", {"segs": [{"seg_id": SEG}]}, EP1)
        self.assertIsNone(R._seg_context(self.pj, EP1, SEG))

    def test_ids_that_do_not_match_give_up(self):
        self.pj.save_stage("n9_shots", {"shots": [{"shot_id": "别的"}]}, EP1)
        self.assertIsNone(R._seg_context(self.pj, EP1, SEG))

    def test_giving_up_says_so(self):
        """★ 悄悄退回整集发的话，日志上看不出为什么这一段特别大。"""
        said = []
        R._seg_context(self.pj, EP1, "EP01-SEG99", log=said.append)
        self.assertTrue(said)
        self.assertIn("整集", said[0])


class NarrowTests(unittest.TestCase):

    def setUp(self):
        self.ctx = {"shots": {"SH2", "SH3"}, "cvs": {"CV2", "CV3"},
                    "spatial": {"SP2"}}

    def _n(self, name, obj):
        return R._narrow_join(name, obj, self.ctx)

    def test_shots_are_cut_to_this_segment(self):
        """★ 这就是那十万 token 的大头之一。"""
        out = self._n("n9_shots", N9)
        self.assertEqual([s["shot_id"] for s in out["shots"]], ["SH2", "SH3"])

    def test_a_transition_that_crosses_the_boundary_is_dropped(self):
        """跨段那条属于相邻段 —— 发过来只会让模型以为要接一个看不到的镜头。"""
        out = self._n("n9_shots", N9)
        self.assertEqual([t["transition_id"] for t in out["transitions"]], ["T2"])

    def test_the_timing_plan_follows_the_shots(self):
        out = self._n("n9_shots", N9)
        self.assertEqual([t["shot_id"] for t in out["timing_plan"]], ["SH2"])

    def test_cvs_are_cut(self):
        out = self._n("n8_cvs", N8)
        self.assertEqual([c["cvs_id"] for c in out["cvs"]], ["CV2", "CV3"])
        self.assertEqual([v["vt_id"] for v in out["vt"]], ["VT1"])

    def test_spatial_masters_are_cut_to_the_places_this_segment_uses(self):
        out = self._n("n5_spatial", N5)
        self.assertEqual([s["spatial_id"] for s in out["spatial_masters"]], ["SP2"])
        self.assertEqual([v["view_id"] for v in out["loc_views"]], ["V1"])

    def test_products_that_are_not_on_the_chain_are_untouched(self):
        """★ 资产表故意不裁 —— 裁掉会让跨集复用的角色在本段「消失」，
        模型于是重新发明一个 ID。"""
        a4 = {"assets": [{"asset_id": "C001"}, {"asset_id": "C002"}]}
        self.assertEqual(self._n("n4_assets", a4), a4)

    def test_no_context_means_no_cutting(self):
        self.assertEqual(R._narrow_join("n9_shots", N9, None), N9)

    def test_cutting_everything_away_falls_back_to_everything(self):
        """★ 最重要的一条：空数组比多发严重得多。

        下游看到「这一段没有任何镜头」，然后自己编 —— 而这不报错。
        """
        out = R._narrow_join("n9_shots", N9, {"shots": {"没有这个"},
                                              "cvs": set(), "spatial": set()})
        self.assertEqual(len(out["shots"]), 4, "一条都没留下时要整份保留")


class WiringTests(unittest.TestCase):
    """真的走一遍 mapping，别只测那两个函数。"""

    def setUp(self):
        self.pj = new_project()
        for name, obj in (("n10_segs", N10), ("n9_shots", N9),
                          ("n8_cvs", N8)):
            self.pj.save_stage(name, obj, EP1)
        self.pj.save_stage("n5_spatial", N5, "")
        self.pj.save_stage("n4_assets", {"assets": [{"asset_id": "C001"}]}, "")

    def tearDown(self):
        shutil.rmtree(self.pj.root, ignore_errors=True)

    def test_a_segment_stage_gets_the_cut_down_version(self):
        m = R.mapping(self.pj, "n13", {"duration": 15},
                      R.deps_data(self.pj, "n13", EP1), EP1, SEG)
        blob = m[R.V.placeholder_of("n9_shots")]
        self.assertIn("SH2", blob)
        self.assertNotIn("SH4", blob, "别的段的镜头不该发过来")

    def test_an_episode_stage_still_gets_everything(self):
        """★ 别裁过头：整集级环节要看得见整集。"""
        m = R.mapping(self.pj, "n10", {"duration": 15},
                      R.deps_data(self.pj, "n10", EP1), EP1)
        self.assertIn("SH4", m[R.V.placeholder_of("n9_shots")])


if __name__ == "__main__":
    unittest.main()
