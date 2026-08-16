# -*- coding: utf-8 -*-
"""故事板每张最多 3 格 —— 模板说了，程序还得真拦。

「格」= 一个 KF = 那张纸上的一个画格。一张 sheet 出一张图，
所以同一个 `sheet_id` 下挂 N 个 KF，就是要模型在一张图里画 N 个时刻。

实跑撞过 16 格：模型记不住 16 个时刻各自的世界状态，于是所有格子的
`source_scstate` 全填成第一个、道具状态和 CVS 打架、关键帧的时间和它的
来源对不上 —— 审计报的 7 条 BLOCK 里 5 条是这么来的。

**而画面本身是好看的**：16 个格子整整齐齐，人工一格一格看抓不到。
只有把整段连起来对照来源状态才露馅 —— 那时候图已经出完、钱已经花完。

内容不用减，拆续页就行。
"""
import shutil
import unittest

from core import gates_v34 as G, settings as ST
from test_v34_run import new_project


def _kf(n, sheet="SHEET_A"):
    return [{"kf_id": f"KF{i:02d}", "sheet_id": sheet} for i in range(1, n + 1)]


class DensityTests(unittest.TestCase):

    def setUp(self):
        self.pj = new_project()

    def tearDown(self):
        shutil.rmtree(self.pj.root, ignore_errors=True)

    def _save(self, pkgs):
        self.pj.save_stage("n12_storyboard", {"sbpkg": pkgs}, "")

    def test_three_is_fine(self):
        self._save([{"seg_id": "EP01-SEG01", "kf": _kf(3)}])
        self.assertEqual(G.sheet_density_gate(self.pj), [])

    def test_the_real_failure_is_caught(self):
        """★ 实跑那次：一张纸上 16 格。"""
        self._save([{"seg_id": "EP01-SEG01", "kf": _kf(16)}])
        bad = G.sheet_density_gate(self.pj)
        self.assertEqual(len(bad), 1, bad)
        self.assertIn("16 个关键帧", bad[0])
        # ★ 光说「超了」没用 —— 人会去删关键帧。要说清正确解法是拆页。
        self.assertIn("内容不用减", bad[0])
        self.assertIn("续页", bad[0])

    def test_splitting_into_sheets_passes(self):
        """★ 同样 8 个关键帧，拆成三张纸就合规 —— 内容一格没少。"""
        self._save([{"seg_id": "EP01-SEG01",
                     "kf": _kf(3, "SHEET_A") + _kf(3, "SHEET_B") + _kf(2, "SHEET_C")}])
        self.assertEqual(G.sheet_density_gate(self.pj), [])

    def test_a_missing_sheet_id_is_judged_conservatively(self):
        """★ 没写 sheet_id 就是「全挂在一张纸上」—— 实跑那次就是这样。

        当成「不知道、放过」的话，这道闸门对最常见的那种超格完全没用。
        """
        self._save([{"seg_id": "EP01-SEG01",
                     "kf": [{"kf_id": f"KF{i:02d}"} for i in range(1, 9)]}])
        bad = G.sheet_density_gate(self.pj)
        self.assertTrue(bad)
        self.assertIn("没写 sheet_id", bad[0])

    def test_each_over_stuffed_sheet_is_reported_separately(self):
        self._save([{"seg_id": "EP01-SEG01",
                     "kf": _kf(5, "SHEET_A") + _kf(5, "SHEET_B")}])
        self.assertEqual(len(G.sheet_density_gate(self.pj)), 2)

    def test_the_project_setting_wins(self):
        """项目里把上限调了就以项目为准 —— 不是写死 3。"""
        ST.save(self.pj, {"storyboard_max_kf_per_sheet": 6})
        self._save([{"seg_id": "EP01-SEG01", "kf": _kf(5)}])
        self.assertEqual(G.sheet_density_gate(self.pj), [])

    def test_nothing_produced_yet_is_not_a_failure(self):
        self.assertEqual(G.sheet_density_gate(self.pj), [])


class WiringTests(unittest.TestCase):
    """★ 不接进 check_all 的闸门等于没写。"""

    def setUp(self):
        self.pj = new_project()
        self.pj.save_stage("n12_storyboard",
                           {"sbpkg": [{"seg_id": "EP01-SEG01", "kf": _kf(9)}]}, "")

    def tearDown(self):
        shutil.rmtree(self.pj.root, ignore_errors=True)

    def test_it_runs_as_part_of_check_all(self):
        self.assertIn("sheet_density", G.GATES)
        self.assertIn("sheet_density", G.check_all(self.pj))

    def test_it_can_be_authorized_like_the_others(self):
        G.authorize(self.pj, "sheet_density", "这一段是快闪回忆，格子本来就密")
        self.assertNotIn("sheet_density", G.check_all(self.pj))

    def test_the_registry_has_exactly_one_source(self):
        """★ 加一道闸门只该改一处。

        以前 check_all 和 /api/gates 各写一份 gate→函数 的表，
        加这道闸门时端点那份就 KeyError 了 —— 而漏的要是 check_all 那份，
        新闸门会**根本不生效且不报错**。
        """
        self.assertEqual(set(G.GATES), set(G.CHECKS))
        import inspect

        from server import app
        self.assertNotIn("G.position_gate", inspect.getsource(app),
                         "端点里又抄了一份闸门函数表")

    def test_it_blocks_before_images_are_produced(self):
        """★ 拦在出图之前才有意义 —— 出完再说就只是通知。

        check_all 是在 pipeline 的 produce 之前调的，这里钉住它在闸门表里。
        """
        import inspect

        from core import pipeline_v34
        src = inspect.getsource(pipeline_v34.run)
        i, j = src.index("check_all"), src.index('s["kind"] == "produce"')
        self.assertLess(i, j, "闸门必须排在出图之前")


if __name__ == "__main__":
    unittest.main()
