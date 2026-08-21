# -*- coding: utf-8 -*-
"""出图出片全程就绪即派 —— 一条任务的输入齐了就开跑，不等整个阶段。

原来是阶段串行：全部资产出完 → 全部场景状态图出完 → 全部故事板出完 →
才开始第一条视频。而依赖是**逐段独立**的：`EP01-SEG01` 的故事板只需要它自己
那张场景状态图，不需要等另外 239 段。所以 SEG01 本可以在别的段还没开始
场景状态图的时候就把视频出完，实际却要等三轮全量。

两条不能丢的性质：

  · **顺序还是对的** —— 输入没齐绝不开跑（这是分阶段当初要解决的问题）
  · **等不到不自己报错** —— 照旧派出去，让出图那一层现有的硬停说清缺哪张。
    再造一条报错只会让同一件事在面板上有两种说法。
"""
import os
import shutil
import tempfile
import unittest

from core.relay import Relay


class _PJ:
    def __init__(self, root):
        self.root = root

    def p(self, *parts):
        return os.path.join(self.root, *parts)


def _task(out, refs=(), spine=(), sb="", aux=""):
    t = {"output": out,
         "reference_images": [{"image_n": i + 1, "asset_id": f"A{i}", "file_ref": r}
                             for i, r in enumerate(refs)]}
    if spine:
        t["storyboard_refs"] = [{"order": i + 1, "file_ref": s}
                                for i, s in enumerate(spine)]
    if sb:
        t["storyboard_ref"] = sb
    if aux:
        t["aux_reference"] = aux
    return t


class RelayTests(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.pj = _PJ(self.dir)
        self.relay = Relay(self.pj)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _make(self, rel):
        """造一张「像样的」产物文件（probe.have_output 认它）。"""
        full = self.pj.p(*rel.split("/"))
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 4000)

    def test_a_task_with_no_inputs_runs_at_once(self):
        """★ 没有输入的任务先跑是**对的**，不是乱序。"""
        ok, _ = self.relay.ready_of("storyboard")(_task("04/a.png"))
        self.assertTrue(ok)

    def test_it_waits_while_the_upstream_batch_is_still_running(self):
        """★ 这就是「顺序还是对的」那一半。"""
        self.relay.declare("scstate", [_task("03b/s1.png")])
        ok, what = self.relay.ready_of("storyboard")(
            _task("04/a.png", refs=["03b/s1.png"]))
        self.assertFalse(ok)
        self.assertIn("03b/s1.png", what)

    def test_it_runs_once_the_input_lands(self):
        self.relay.declare("scstate", [_task("03b/s1.png")])
        self._make("03b/s1.png")
        ok, _ = self.relay.ready_of("storyboard")(
            _task("04/a.png", refs=["03b/s1.png"]))
        self.assertTrue(ok)

    def test_it_stops_waiting_when_the_upstream_batch_is_done(self):
        """★ **这一条是等待的终点。** 但终点不是「派出去」。

        上游那一批跑完了、东西还是没有 → 不再等（不然要挂到超时上限），
        而返回的是 `None` = **条件不具备，别派**。

        用户原话（2026-08-21）：「他缺少实际条件他不能去做才对」。
        原来这里返回 True，让出图那层的硬停去报 —— 面板上留下的是一条
        「失败」，而它不是失败，是这一条还不能做。两者长得一样，
        人就分不出「这条要修」和「这条在等前面」。
        """
        self.relay.declare("scstate", [_task("03b/s1.png")])
        t = _task("04/a.png", refs=["03b/s1.png"])
        self.assertIs(self.relay.ready_of("storyboard")(t)[0], False,
                      "上游还在跑，应该是「等」")
        self.relay.finished("scstate")
        ok, why = self.relay.ready_of("storyboard")(t)
        self.assertIsNone(ok, "上游跑完了还是没有 —— 该判成条件不具备")
        self.assertIn("03b/s1.png", why, "得说清缺的是哪个文件")

    def test_an_input_nobody_produces_does_not_wait(self):
        """★ 没人会做它（比如 SP001 那种引错类别的）→ **当场判成不能做**。

        等一个不会有人做的东西，等到超时才说话，比当场说清糟得多。
        而「当场说清」不等于「派出去撞一次空」—— 见上一条。
        """
        ok, why = self.relay.ready_of("asset")(
            _task("02/a.png", refs=["02/SP001.png"]))
        self.assertIsNone(ok)
        self.assertIn("SP001", why)

    def test_the_ordered_spine_counts_as_input(self):
        """★ V6.2 的视频要整条有序骨架 —— 每一张都要等。"""
        self.relay.declare("storyboard", [_task("04/a.png"), _task("04/b.png")])
        self._make("04/a.png")
        t = _task("05/v.mp4", spine=["04/a.png", "04/b.png"])
        ok, what = self.relay.ready_of("video")(t)
        self.assertFalse(ok, "只齐了一张就放行 —— 骨架断了照样出片")
        self.assertIn("04/b.png", what)
        self._make("04/b.png")
        self.assertTrue(self.relay.ready_of("video")(t)[0])

    def test_the_legacy_single_ref_and_aux_count_too(self):
        self.relay.declare("storyboard", [_task("04/a.png")])
        self.relay.declare("asset", [_task("02/x.png")])
        t = _task("05/v.mp4", sb="04/a.png", aux="02/x.png")
        self.assertFalse(self.relay.ready_of("video")(t)[0])
        self._make("04/a.png")
        self.assertFalse(self.relay.ready_of("video")(t)[0], "aux 也要等")
        self._make("02/x.png")
        self.assertTrue(self.relay.ready_of("video")(t)[0])

    def test_http_inputs_are_not_waited_for(self):
        """外部链接我们等不了，也不该等。"""
        ok, _ = self.relay.ready_of("video")(
            _task("05/v.mp4", refs=["https://cdn/x.png"]))
        self.assertTrue(ok)

    def test_a_zero_byte_file_does_not_count_as_ready(self):
        """★ 不能用 isfile：0 字节和下了一半的都是「文件存在」。"""
        self.relay.declare("scstate", [_task("03b/s1.png")])
        full = self.pj.p("03b", "s1.png")
        os.makedirs(os.path.dirname(full), exist_ok=True)
        open(full, "wb").close()
        self.assertFalse(self.relay.ready_of("storyboard")(
            _task("04/a.png", refs=["03b/s1.png"]))[0])


class WiringTests(unittest.TestCase):
    """两套体系都要接上，而且 finished 不能漏。"""

    def test_both_pipelines_run_produce_steps_concurrently(self):
        import inspect

        from core import pipeline as P61
        from core import pipeline_v34 as P34
        for mod in (P61, P34):
            src = inspect.getsource(mod._produce_all)
            self.assertIn("ThreadPoolExecutor", src)
            self.assertIn("调度出错了", src, "调度异常被吞掉了")

    def test_both_pass_ready_of(self):
        import inspect

        from core import pipeline as P61
        from core import pipeline_v34 as P34
        for mod in (P61, P34):
            self.assertIn("ready_of=relay.ready_of", inspect.getsource(mod.run))

    def test_both_report_the_batch_as_finished(self):
        """★ 漏了这一句，等它产物的任务会一直等到超时上限。"""
        import inspect

        from core import pipeline as P61
        from core import pipeline_v34 as P34
        for mod in (P61, P34):
            self.assertIn("relay.finished(", inspect.getsource(mod.run))

    def test_the_declare_happens_after_tasks_are_built(self):
        """★ 实际踩过：在 run 开头登记，那时 tasks.json 还没装配 ——

        登记出来是空的，于是下游把「还没生成的文件」当成「没人会做它」，
        立刻派出去撞空。
        """
        import inspect

        from core import pipeline as P61
        from core import pipeline_v34 as P34
        for mod in (P61, P34):
            src = inspect.getsource(mod.run)
            self.assertLess(src.index("build_tasks"), src.index("relay.declare"),
                            f"{mod.__name__}: 登记排在装配之前了")

    def test_the_executor_supports_the_gate(self):
        import inspect

        from core import executor as E
        self.assertIn("ready_of", inspect.getsource(E.run_batch))
        self.assertIn("ready_of=ready_of", inspect.getsource(E.run_chain))

    def test_waiting_on_a_cross_batch_input_is_not_treated_as_a_cycle(self):
        """★ 混在一起处理的话，「四类活并发、故事板比场景状态图先排到」

        这种完全正常的情况会被整批误杀成「互相引用成了环」。
        """
        import inspect

        from core import executor as E
        src = inspect.getsource(E.run_batch)
        i = src.index("crossing = [")
        j = src.index("互相引用成了环")
        self.assertLess(i, j, "先判成环、后判跨批等待 —— 顺序反了会误杀")


if __name__ == "__main__":
    unittest.main()
