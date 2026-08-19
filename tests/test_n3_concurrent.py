# -*- coding: utf-8 -*-
"""n3 按集并发 —— 前提是编号不撞。

n3 原本是「一次处理全剧」（模板第一句就是这么写的）。它跑不过去之后
按用户的要求改成按集分批，那时是**顺序**跑，靠模板里一句
「新场次的编号接着往下走」让模型自己续号。

并发之后这一条不成立了：各批看不到前面，21 集各自从 SC01 编起。
而 `merge_narrative` 是**按 `scene_id` 合并**的 —— 同号就覆盖：

    21 集并发跑完，可能只剩一集的场次，而且不报错。

所以并发的前提是程序自己保证编号唯一（`stamp_episode`），
不能指望模型记得加前缀。
"""
import shutil
import threading
import unittest

from core import run_v34 as R
from test_v34_run import EP1, EP2, PARAMS, _FIXTURES, new_project


def _scene(sid, ep):
    return {"scene_id": sid, "episode": ep, "objective": "o", "turn": "t",
            "outcome": "r", "entry_state": "en", "exit_state": "ex"}


class StampTests(unittest.TestCase):
    """打集号前缀。"""

    def test_a_bare_id_gets_the_episode_prefix(self):
        """★ 这就是撞号的解法。"""
        out = R.stamp_episode(EP1, {"scenes": [_scene("SC01", EP1)]})
        self.assertEqual(out["scenes"][0]["scene_id"], "EP01-SC01")

    def test_beats_follow_their_scene(self):
        out = R.stamp_episode(EP1, {
            "scenes": [_scene("SC01", EP1)],
            "beats": [{"beat_id": "SC01-B1", "scene_id": "SC01"}]})
        b = out["beats"][0]
        self.assertEqual(b["scene_id"], "EP01-SC01")
        self.assertEqual(b["beat_id"], "EP01-SC01-B1")

    def test_an_already_prefixed_id_is_left_alone(self):
        """★ 模型照模板写对了的，不许再套一层。"""
        out = R.stamp_episode(EP1, {
            "scenes": [_scene("EP01-SC01", EP1)],
            "beats": [{"beat_id": "EP01-SC01-B1", "scene_id": "EP01-SC01"}]})
        self.assertEqual(out["scenes"][0]["scene_id"], "EP01-SC01")
        self.assertEqual(out["beats"][0]["beat_id"], "EP01-SC01-B1")

    def test_a_beat_that_does_not_start_with_its_scene_id_still_gets_prefixed(self):
        """别的编号规则也不能撞号。"""
        out = R.stamp_episode(EP1, {
            "scenes": [_scene("SC01", EP1)],
            "beats": [{"beat_id": "B0001", "scene_id": "SC01"}]})
        self.assertEqual(out["beats"][0]["beat_id"], "EP01-B0001")

    def test_two_episodes_no_longer_collide(self):
        """★ **这一条是整件事的重点。**

        两集都从 SC01 编起，打完前缀之后合并，两集的场次都还在。
        """
        a = R.stamp_episode(EP1, {"scenes": [_scene("SC01", EP1)]})
        b = R.stamp_episode(EP2, {"scenes": [_scene("SC01", EP2)]})
        merged = R.merge_narrative(R.merge_narrative({}, a), b)
        self.assertEqual([s["scene_id"] for s in merged["scenes"]],
                         ["EP01-SC01", "EP02-SC01"])

    def test_without_stamping_they_would_collide(self):
        """★ 反过来钉一次：不打前缀就是会丢数据。

        这条不是在测代码，是在记住为什么必须打前缀 ——
        以后谁把 stamp_episode 去掉，这一条会告诉他后果。
        """
        a = {"scenes": [_scene("SC01", EP1)]}
        b = {"scenes": [_scene("SC01", EP2)]}
        merged = R.merge_narrative(R.merge_narrative({}, a), b)
        self.assertEqual(len(merged["scenes"]), 1, "同号只剩一条")
        self.assertEqual(merged["scenes"][0]["episode"], EP2, "先写的被顶掉了")

    def test_a_scene_without_an_id_is_skipped(self):
        out = R.stamp_episode(EP1, {"scenes": [{"episode": EP1}]})
        self.assertNotIn("scene_id", out["scenes"][0])

    def test_no_episode_means_no_change(self):
        obj = {"scenes": [_scene("SC01", "")]}
        self.assertEqual(R.stamp_episode("", obj)["scenes"][0]["scene_id"], "SC01")


class Rewriter:
    """假模型：每集都从 SC01 编起（这正是要防的行为）。"""

    model = "fake"

    def __init__(self):
        self.lock = threading.Lock()
        self.seen = []

    def json_call(self, system, user, **kw):
        import re
        m = re.search(r"只做 (EP\d+)", user)
        ep = m.group(1) if m else EP1
        with self.lock:
            self.seen.append(ep)
        return {"scope": "full_series",
                "scenes": [_scene("SC01", ep), _scene("SC02", ep)],
                "beats": [{"beat_id": "SC01-B1", "scene_id": "SC01",
                           "meaningful_change": "c", "change_kind": "knowledge",
                           "state_delta": "d", "shot_need": "s"}],
                "episode_arcs": [{"episode": ep, "arc": "a"}]}


def _project():
    import copy

    from core import episodes as _eps
    pj = new_project()
    pj.save_stage("n1_truth", copy.deepcopy(_FIXTURES["n1"]), "")
    pj.save_stage("n2_rules", copy.deepcopy(_FIXTURES["n2"]), "")
    _eps.build(pj, PARAMS["script"], copy.deepcopy(_FIXTURES["n1"]))
    return pj


class ConcurrentTests(unittest.TestCase):

    def setUp(self):
        self.pj = _project()

    def tearDown(self):
        shutil.rmtree(self.pj.root, ignore_errors=True)

    def _run(self, workers, llm=None):
        return R.run_n3_batched(self.pj, llm=llm or Rewriter(), params=PARAMS,
                                log=lambda *a: None, concurrency=workers)

    def test_every_episode_survives_concurrency(self):
        """★ 并发跑完两集的场次都在 —— 不打前缀的话会只剩一集。"""
        out = self._run(4)
        self.assertEqual(sorted(s["scene_id"] for s in out["scenes"]),
                         ["EP01-SC01", "EP01-SC02", "EP02-SC01", "EP02-SC02"])
        self.assertEqual({s["episode"] for s in out["scenes"]}, {EP1, EP2})

    def test_what_lands_on_disk_matches(self):
        """★ 并发下的读-改-写要在锁里，不然后写的会挤掉先写的。"""
        self._run(4)
        saved = self.pj.stage_data("n3_narrative", "")
        self.assertEqual(len(saved["scenes"]), 4)

    def test_sequential_gives_the_same_result(self):
        """并发只改调度，产物该一样。"""
        out = self._run(1)
        self.assertEqual(sorted(s["scene_id"] for s in out["scenes"]),
                         ["EP01-SC01", "EP01-SC02", "EP02-SC01", "EP02-SC02"])

    def test_one_episode_failing_does_not_kill_the_others(self):
        """★ 一集失败不拖累别的集，排好的要留下来。"""
        class Flaky(Rewriter):
            def json_call(self, system, user, **kw):
                if f"只做 {EP2}" in user:
                    raise RuntimeError("这一集炸了")
                return super().json_call(system, user, **kw)

        with self.assertRaises(RuntimeError) as cm:
            self._run(4, Flaky())
        self.assertIn(EP2, str(cm.exception))
        saved = self.pj.stage_data("n3_narrative", "")
        self.assertEqual({s["episode"] for s in saved["scenes"]}, {EP1})

    def test_concurrency_is_capped_by_how_many_episodes_there_are(self):
        """两集不该开四个线程 —— 白占并发额度。"""
        import inspect
        src = inspect.getsource(R.run_n3_batched)
        self.assertIn("min(int(concurrency or 1), len(todo))", src)

    def test_the_pipeline_passes_a_concurrency(self):
        """★ 传了不接 / 接了不传，都等于并发没生效。"""
        import inspect

        from core import pipeline_v34 as P
        self.assertIn("concurrency=ep_concurrency", inspect.getsource(P.run))
        self.assertIn("concurrency=concurrency",
                      inspect.getsource(R.run_stage))


if __name__ == "__main__":
    unittest.main()
