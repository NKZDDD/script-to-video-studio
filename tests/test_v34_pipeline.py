# -*- coding: utf-8 -*-
"""V3.4 一键跑到底：假模型 + 假服务商，从剧本走到视频文件。

这条测试要证明的不是「能跑」，是几件靠肉眼查不出来的编排性质：

  · 顺序对：资产图等所有集的资产提示词齐了才出；
    场景状态图 → 故事板 → 视频，每一步拿上一步当参考。
    顺序错了不报错 —— 只是参考图指不到文件，或者同一个角色出两张脸。
  · 「继续」就是再点一次：做过的一步都不重做，不重复花钱。
  · 一集失败不拖累别的集；一段失败不毒掉整集。
"""
import os
import shutil
import unittest

from core import pipeline_v34 as P, run_v34 as R
from core.executor import Job
from test_v34_run import EP1, EP2, PARAMS, FakeLLM, new_project


class FakeProv:
    id = "fake"

    def __init__(self):
        self.made = []

    def needs_url(self, *a, **k): return False
    def needs_bytes(self, *a, **k): return False
    def accepts_url(self, *a, **k): return False

    def generate_image(self, task, out, **kw):
        self.made.append(out)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        from PIL import Image
        Image.new("RGB", (8, 8), (30, 60, 90)).save(out)
        return {"provider": "fake", "model": "m"}

    def generate_video(self, task, out, **kw):
        self.made.append(out)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        open(out, "wb").write(b"mp4")
        return {"provider": "fake", "model": "m"}


class PipelineTests(unittest.TestCase):

    def setUp(self):
        self.pj = new_project()
        self.llm = FakeLLM()
        self.prov = FakeProv()
        import core.produce as _P
        self._orig = _P.build_provider
        _P.build_provider = lambda *a, **k: self.prov
        # 出图 worker 在 produce 的命名空间里解析 build_provider，
        # patch core.stages 上的同名属性不生效（踩过）。

    def tearDown(self):
        import core.produce as _P
        _P.build_provider = self._orig
        shutil.rmtree(self.pj.root, ignore_errors=True)

    def _run(self, **kw):
        job = Job("pipeline", 1, 1, project_root=self.pj.root)
        chain = [{"provider": "fake", "api_key": "k", "model": "m"}]
        return P.run(job, self.pj, llm_factory=lambda: self.llm,
                     provider_factory=lambda kind: chain,
                     params=PARAMS, concurrency=1, ep_concurrency=1,
                     seg_concurrency=1, **kw), job

    # ---------------------------------------------------------------- 全链路

    def test_runs_from_script_to_video(self):
        """★ 一次跑完：文字 15 个环节 + 四类出图出片。"""
        res, job = self._run()
        self.assertEqual(res["status"], "done", f"没跑完：{res}")
        outs = {os.path.basename(p) for p in self.prov.made}
        self.assertTrue(any(p.endswith(".mp4") for p in self.prov.made), "没出视频")
        t = self.pj.tasks()
        for kind in ("asset_tasks", "scstate_tasks", "storyboard_tasks", "video_tasks"):
            self.assertTrue(t[kind], f"{kind} 是空的")
            for task in t[kind]:
                self.assertTrue(os.path.isfile(self.pj.p(*task["output"].split("/"))),
                                f"{task['key']} 的产物没落盘")

    def test_production_order_assets_then_scstate_then_board_then_video(self):
        """★ 顺序错了不报错，只是参考图指不到文件、或者同一角色出两张脸。"""
        self._run()
        made = self.prov.made
        def first(frag):
            return next(i for i, p in enumerate(made) if frag in p)
        self.assertLess(first("固定资产"), first("场景状态图"), "资产图没排在场景状态图前")
        self.assertLess(first("场景状态图"), first("故事板"), "场景状态图没排在故事板前")
        self.assertLess(first("故事板"), first("分段视频"), "故事板没排在视频前")

    def test_assets_wait_for_every_episode(self):
        """★ 资产图必须等所有集的资产提示词齐了 —— 否则同一角色会出两张脸。"""
        steps = P.plan(self.pj)
        self.assertEqual([s["stage"] for s in steps][:2], ["n1", "n2"])
        R.run_stage(self.pj, "n1", llm=self.llm, params=PARAMS, log=lambda *a: None)
        steps = P.plan(self.pj)
        labels = [s["label"] for s in steps]
        last_prompt = max(i for i, s in enumerate(steps) if s["stage"] == "n4b")
        asset_img = next(i for i, s in enumerate(steps) if s["stage"] == "p1")
        self.assertLess(last_prompt, asset_img,
                        f"资产图排在了某一集的资产提示词前面：{labels[asset_img]}")
        # 两集的 n4b 都要在前面
        self.assertEqual(sum(1 for s in steps if s["stage"] == "n4b"), 2)

    # ---------------------------------------------------------------- 续跑

    def test_rerun_redoes_nothing(self):
        """★「继续」就是再点一次同一个按钮，做过的一步都不重做。"""
        self._run()
        calls, made = len(self.llm.sent), len(self.prov.made)
        res, job = self._run()
        self.assertEqual(res["status"], "done")
        self.assertEqual(len(self.llm.sent), calls, "文字环节又跑了一遍")
        self.assertEqual(len(self.prov.made), made, "又出了一遍图")
        states = {v.get("state") for v in job.items.values()}
        self.assertIn("skipped", states)

    def test_deleting_one_image_only_redoes_that_one(self):
        """删掉一张图再点一次，只补那一张。"""
        self._run()
        t = self.pj.tasks()
        victim = t["storyboard_tasks"][0]
        os.remove(self.pj.p(*victim["output"].split("/")))
        before = len(self.prov.made)
        self._run()
        self.assertEqual(len(self.prov.made) - before, 1, "补了不止那一张")

    # ---------------------------------------------------------------- 隔离

    def test_one_bad_episode_does_not_stop_the_others(self):
        """★ 集与集之间独立：一集的某个环节失败，别的集照常跑完。"""
        self.llm.fail_on = {("n5", EP2)}
        res, job = self._run(include_produce=False, include_deliver=False)
        self.assertEqual(res["stuck_episodes"], [EP2])
        # EP01 的后续环节照常做完 —— 标签里的环节号是阿拉伯数字
        done = [k for k, v in job.items.items()
                if v.get("state") == "ok" and k.startswith(EP1)]
        self.assertTrue(any("第13环节" in k for k in done),
                        f"EP01 没能跑到最后一个文字环节：{done}")
        # EP02 从失败那一步之后全部跳过，不是继续硬跑
        skipped = [k for k, v in job.items.items()
                   if v.get("state") == "skipped" and k.startswith(EP2)]
        self.assertGreaterEqual(len(skipped), 5, "EP02 失败后没有停下")

    def test_plan_rejects_unknown_episode_with_a_readable_message(self):
        R.run_stage(self.pj, "n1", llm=self.llm, params=PARAMS, log=lambda *a: None)
        with self.assertRaises(ValueError) as cm:
            P.plan(self.pj, only_episodes=["EP99"])
        msg = str(cm.exception)
        self.assertIn("EP99", msg)
        self.assertIn("当前切出了", msg)

    def test_only_one_episode_narrows_everything(self):
        R.run_stage(self.pj, "n1", llm=self.llm, params=PARAMS, log=lambda *a: None)
        steps = P.plan(self.pj, only_episodes=[EP1])
        eps = {s["episode"] for s in steps if s.get("episode")}
        self.assertEqual(eps, {EP1})


if __name__ == "__main__":
    unittest.main()
