# -*- coding: utf-8 -*-
"""「满足条件就开始干、没做出来就继续重试」要在**用户真走的那条路上**生效。

用户原话（2026-08-25）：「就是上次说的满足条件就开始干，没做出图片，
满足条件继续重试好像还没有生效」。

定性：`core/relay.py::sweep_redo` 一直只在「一键跑到底」的 produce 块收摊
之后被调用（`pipeline.py` / `pipeline_v34.py`）。而「生产」页那三个「开始」
走 `/api/generate` → `run_chain`，`deps_of` 只对资产给，故事板和视频
**连 relay 都没有**：参考图没齐也照样派出去，撞成一条「失败」——
而它不是失败，是条件不具备，面板上两者长得一样。

而材料导入完，程序提示的正是「去『生产』页出图出片」，恰好是没有补跑那条。
"""
import inspect
import tempfile
import unittest

from core import pipeline as PU, pipeline_v34 as PV
from core.store import Project


def _proj(system: str):
    pj = Project(tempfile.mkdtemp())
    pj.init_dirs()
    pj.save_meta({"project_name": "x", "system": system})
    return pj


class ProduceOnlyPlanTests(unittest.TestCase):
    """只跑生产：材料导入模式下 LLM 环节的中间产物压根不存在。"""

    def test_cinematic_drops_every_llm_step(self):
        steps = PV.plan(_proj("v34"), include_llm=False,
                        include_deliver=False)
        self.assertTrue(steps)
        self.assertEqual({s["kind"] for s in steps}, {"produce"})

    def test_general_drops_every_llm_step(self):
        steps = PU.plan(_proj("v61"), include_llm=False,
                        include_deliver=False)
        self.assertTrue(steps)
        self.assertEqual({s["kind"] for s in steps}, {"produce"})

    def test_the_freeze_step_goes_too(self):
        """★ n0 是分析那一段的准备（冻结设置）—— 材料模式没有分析这一段。"""
        labels = [s["kind"] for s in PV.plan(_proj("v34"), include_llm=False)]
        self.assertNotIn("freeze", labels)

    def test_the_full_plan_is_unchanged(self):
        """★ 默认必须一模一样 —— 这个开关只在勾上时改变行为。"""
        for mod, sid in ((PV, "v34"), (PU, "v61")):
            pj = _proj(sid)
            self.assertEqual([s["label"] for s in mod.plan(pj)],
                             [s["label"] for s in mod.plan(pj,
                                                           include_llm=True)])

    def test_produce_only_keeps_the_production_order(self):
        """★ 顺序就是依赖顺序：资产 → 场景状态 → 故事板 → 视频。
        乱了的话下游拿不到上游的参考图，而 relay 会把它们全判成「在等」。"""
        got = [s["stage"] for s in PV.plan(_proj("v34"), include_llm=False,
                                           include_deliver=False)]
        self.assertEqual(got, list(PV.V.PRODUCE_ORDER))


class SweepInGenerateTests(unittest.TestCase):
    """`/api/generate` 现在也有 relay + 条件补跑。"""

    def _src(self):
        from server import app as A
        src = inspect.getsource(A.api_post)
        i = src.index('/api/generate')
        return src[i:i + 12000]

    def test_it_declares_every_kind(self):
        """★ 漏登记哪一类，等它产物的下游会误判成「没人会做它」而立刻开跑。"""
        blk = self._src()
        for tk in ("asset_tasks", "scstate_tasks", "storyboard_tasks",
                   "video_tasks"):
            self.assertIn(tk, blk)

    def test_it_passes_ready_of(self):
        """★ 没有 ready_of 的话，参考图没齐也照样发请求 ——
        撞出来的那条「失败」其实是「条件不具备」，人分不出要修还是在等。"""
        self.assertIn("ready_of=relay.ready_of", self._src())

    def test_it_marks_the_other_kinds_finished(self):
        """★ 这一趟只跑一类。别的三类不标成收摊的话，
        本类会一直等一个永远不会开始的批次。"""
        self.assertIn("relay.finished(_bk)", self._src())

    def test_it_sweeps_after_the_batch(self):
        self.assertIn("sweep_redo", self._src())

    def test_it_has_an_epoch(self):
        """★ 补跑靠 epoch 区分新旧失败记录 —— 少了它，上一趟的旧账会
        把这一趟该补的全排除掉（`_fresh_hard_errors`）。"""
        self.assertIn("epoch=epoch", self._src())

    def test_the_final_status_is_recomputed_from_disk(self):
        """★ 补跑之后还用批次的 left 判状态，会把补出来的报成失败。"""
        blk = self._src()
        self.assertIn("left = [t for t in items", blk)
