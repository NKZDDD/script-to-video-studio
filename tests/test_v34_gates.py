# -*- coding: utf-8 -*-
"""出图出片之前的三道硬闸门。

V3.4 对这几条的措辞是硬的：「出现下列任一项，不得交付生产执行，必须回编」、
「不得以『出现概率不大』为理由跳过」、「阻断 Prompt，不得猜图继续」。
所以运行时一律硬拦，**没有继续按钮**。

放行走第 0 章冻结的用户授权 —— 一次显式的改配置动作，留在冻结记录里，
不是运行时随手点一下。这和 V3.4 对降级的态度一致：
「若用户以后授权外部剪辑，必须修改项目配置和执行模式，不能静默切换」。
"""
import os
import shutil
import unittest

from core import gates_v34 as G, pipeline_v34 as P, run_v34 as R
from core.executor import Job
from test_v34_run import EP1, PARAMS, SEGS, FakeLLM, new_project
from test_v34_pipeline import FakeProv


def quiet(*a, **k):
    pass


class AuditGateTests(unittest.TestCase):

    def setUp(self):
        self.pj = new_project()

    def tearDown(self):
        shutil.rmtree(self.pj.root, ignore_errors=True)

    def test_no_audit_yet_is_not_a_failure(self):
        """审计本身是 soft 的 —— 没审过不该反过来拦住生产。"""
        self.assertEqual(G.audit_gate(self.pj), [])

    def test_block_finding_stops_production(self):
        self.pj.save_stage("n14_audit", {"findings": [
            {"severity": "BLOCK", "where": "n12 的 SBPKG_EP01-SEG01",
             "what": "参考图第 2 张编号错位", "how_to_fix": "改成 Image 2 = C005"}],
            "verdict": "FIX_FIRST"}, "")
        bad = G.audit_gate(self.pj)
        self.assertEqual(len(bad), 1)
        self.assertIn("编号错位", bad[0])
        self.assertIn("改成 Image 2", bad[0], "报文里没写怎么改")

    def test_warn_alone_does_not_stop(self):
        """WARN 是「人看一眼决定」，不该拦住无人值守的批量。"""
        self.pj.save_stage("n14_audit", {"findings": [
            {"severity": "WARN", "where": "x", "what": "y"}], "verdict": "READY"}, "")
        self.assertEqual(G.audit_gate(self.pj), [])

    def test_fix_first_verdict_stops_even_without_block_findings(self):
        self.pj.save_stage("n14_audit", {"findings": [], "verdict": "FIX_FIRST",
                                         "verdict_reason": "第 3 段缺退出状态"}, "")
        bad = G.audit_gate(self.pj)
        self.assertEqual(len(bad), 1)
        self.assertIn("第 3 段缺退出状态", bad[0])


class CoverageGateTests(unittest.TestCase):
    """故事板只画半身，镜头一拉远就看见下半身 —— 那块没定义过的话，
    模型只能自己想：鞋子换一双、背面衣服变成另一款，而且不报错。"""

    def setUp(self):
        self.pj = new_project()

    def tearDown(self):
        shutil.rmtree(self.pj.root, ignore_errors=True)

    def _plan(self, window):
        self.pj.save_stage("n13_video", {"video_plan": [
            {"seg_id": SEGS[0], "windows": [window],
             "reference_order": [{"image_n": 1, "asset_id": "SBPKG"}],
             "video_prompt": "p"}]}, "")

    def test_covered_passes(self):
        self._plan({"window_id": "W01", "visual_coverage_status": "COVERED"})
        self.assertEqual(G.coverage_gate(self.pj), [])

    def test_missing_verdict_is_blocked(self):
        """★ 没给结论也算没过 —— 「会不会显露没定义的区域」必须有答案。"""
        self._plan({"window_id": "W01"})
        bad = G.coverage_gate(self.pj)
        self.assertEqual(len(bad), 1)
        self.assertIn("没给覆盖结论", bad[0])

    def test_needs_supplemental_but_none_given_is_blocked(self):
        """判定要补覆盖图，参考图里却只有故事板 —— 等于没补。"""
        self._plan({"window_id": "W01",
                    "visual_coverage_status": "SUPPLEMENTAL_REFERENCE_REQUIRED"})
        bad = G.coverage_gate(self.pj)
        self.assertIn("没有那张覆盖图", bad[0])

    def test_needs_supplemental_with_reference_passes(self):
        self.pj.save_stage("n13_video", {"video_plan": [
            {"seg_id": SEGS[0],
             "windows": [{"window_id": "W01",
                          "visual_coverage_status": "SUPPLEMENTAL_REFERENCE_REQUIRED"}],
             "reference_order": [{"image_n": 1, "asset_id": "SBPKG"},
                                 {"image_n": 2, "asset_id": "C001"}],
             "video_prompt": "p"}]}, "")
        self.assertEqual(G.coverage_gate(self.pj), [])

    def test_camera_constrained_without_a_path_is_blocked(self):
        """说了机位要受限却不写怎么走，等于没限制。"""
        self._plan({"window_id": "W01",
                    "visual_coverage_status": "CAMERA_CONSTRAINED"})
        self.assertIn("不写等于没限制", G.coverage_gate(self.pj)[0])

    def test_camera_constrained_with_a_path_passes(self):
        self._plan({"window_id": "W01", "visual_coverage_status": "CAMERA_CONSTRAINED",
                    "camera_path_world": "固定在 [4.2,0.8,1.6]，不后退不环绕"})
        self.assertEqual(G.coverage_gate(self.pj), [])


class ObjectCountGateTests(unittest.TestCase):
    """遮挡、离画、装进容器都不改变存在数量。对不上账的典型症状是
    道具被遮挡之后复制成两个，或者离画之后凭空消失。"""

    def setUp(self):
        self.pj = new_project()

    def tearDown(self):
        shutil.rmtree(self.pj.root, ignore_errors=True)

    def _track(self, lock):
        self.pj.save_stage("n6_ledger", {"ledger": [], "prop_tracking": [
            {"instance_id": "PI001", "timeline": [], "count_lock": lock}]}, "")

    def test_matching_reconciliation_passes(self):
        self._track({"active_total": 2, "reconciliation": "可见1 + 部分0 + 遮挡1 + 画外0 = 2"})
        self.assertEqual(G.object_count_gate(self.pj), [])

    def test_arithmetic_that_does_not_add_up_is_blocked(self):
        self._track({"active_total": 2, "reconciliation": "可见1 + 部分0 + 遮挡0 + 画外0 = 2"})
        bad = G.object_count_gate(self.pj)
        self.assertEqual(len(bad), 1)
        self.assertIn("对账对不上", bad[0])

    def test_total_disagreeing_with_the_sum_is_blocked(self):
        self._track({"active_total": 3, "reconciliation": "可见1 + 遮挡1 = 2"})
        self.assertIn("对账对不上", G.object_count_gate(self.pj)[0])

    def test_declaring_a_total_without_detail_is_blocked(self):
        self._track({"active_total": 2, "reconciliation": ""})
        self.assertIn("没给对账明细", G.object_count_gate(self.pj)[0])

    def test_no_count_lock_at_all_is_not_a_failure(self):
        """没写对账表不算错；写了就得对得上。"""
        self._track({})
        self.assertEqual(G.object_count_gate(self.pj), [])


class AuthorizationTests(unittest.TestCase):
    """放行走第 0 章冻结的用户授权，不是运行时按钮。"""

    def setUp(self):
        self.pj = new_project()
        self.pj.save_stage("n14_audit", {"findings": [
            {"severity": "BLOCK", "where": "x", "what": "y", "how_to_fix": "z"}]}, "")

    def tearDown(self):
        shutil.rmtree(self.pj.root, ignore_errors=True)

    def test_blocked_until_authorized(self):
        self.assertIn("audit_block", G.check_all(self.pj))
        G.authorize(self.pj, "audit_block", "这条是误报，人工看过图了")
        self.assertNotIn("audit_block", G.check_all(self.pj))

    def test_authorization_records_who_and_why(self):
        """★ 三个月后回头看，要知道当初为什么放行。"""
        r = G.authorize(self.pj, "audit_block", "误报，已人工确认")
        self.assertEqual(r["why"], "误报，已人工确认")
        self.assertTrue(r["at"])
        cap = self.pj.meta()["capability"]["authorizations"]
        self.assertIn("audit_block", cap)

    def test_authorization_requires_a_reason(self):
        with self.assertRaises(ValueError):
            G.authorize(self.pj, "audit_block", "  ")

    def test_unknown_gate_is_refused(self):
        with self.assertRaises(ValueError) as cm:
            G.authorize(self.pj, "随便什么", "x")
        self.assertIn("没有这道闸门", str(cm.exception))

    def test_authorizing_one_gate_does_not_open_the_others(self):
        self.pj.save_stage("n13_video", {"video_plan": [
            {"seg_id": SEGS[0], "windows": [{"window_id": "W01"}],
             "reference_order": [], "video_prompt": "p"}]}, "")
        G.authorize(self.pj, "audit_block", "误报")
        left = G.check_all(self.pj)
        self.assertNotIn("audit_block", left)
        self.assertIn("visual_coverage", left, "放行一道把别的也放开了")


class PipelineBlockingTests(unittest.TestCase):

    def setUp(self):
        self.pj = new_project()
        self.pj.save_meta(dict(self.pj.meta(), system="v34"))
        self.llm = FakeLLM()
        self.prov = FakeProv()
        import core.produce as _P
        self._orig = _P.build_provider
        _P.build_provider = lambda *a, **k: self.prov

    def tearDown(self):
        import core.produce as _P
        _P.build_provider = self._orig
        shutil.rmtree(self.pj.root, ignore_errors=True)

    def _run(self):
        job = Job("pipeline", 1, 1, project_root=self.pj.root)
        res = P.run(job, self.pj, llm_factory=lambda: self.llm,
                    provider_factory=lambda k: [{"provider": "fake", "api_key": "k",
                                                 "model": "m"}],
                    params=PARAMS, concurrency=1, ep_concurrency=1,
                    seg_concurrency=1)
        return res, job

    def test_a_block_finding_stops_all_production(self):
        """★ 拦住的是**出图出片**，不是文字环节 —— 文字已经跑完了，
        拦它没意义；要拦的是接下来要花钱的那几步。"""
        self.llm.fail_on = set()
        # 让审计报一条 BLOCK
        import test_v34_run as F
        F._FIXTURES["n14"] = {"findings": [
            {"severity": "BLOCK", "where": "n12", "what": "参考图编号错位",
             "how_to_fix": "按上传顺序改"}], "verdict": "FIX_FIRST"}
        try:
            res, job = self._run()
            self.assertEqual(res["status"], "error")
            self.assertFalse(self.prov.made, "被拦下了却还是出了图")
            msgs = [v.get("msg", "") for v in job.items.values()
                    if v.get("state") == "failed"]
            self.assertTrue(any("审计" in m or "编号错位" in m for m in msgs), msgs)
        finally:
            F._FIXTURES["n14"] = {"findings": []}

    def test_clean_run_is_not_blocked(self):
        res, job = self._run()
        self.assertEqual(res["status"], "done", res)
        self.assertTrue(self.prov.made)


if __name__ == "__main__":
    unittest.main()
