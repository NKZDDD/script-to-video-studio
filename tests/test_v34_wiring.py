# -*- coding: utf-8 -*-
"""两套体系并存的接线：项目记住自己用哪套，服务端和前端跟着走。

体系是建项目时定死的。选错了只能重建项目重跑 —— 前面花的钱全白花。
所以这里钉的是「不会走错路」：老项目不许被当成新体系、
新项目不许被 V6.1 的编排接手。
"""
import os
import re
import shutil
import unittest

from core import pipeline_v34 as P, run_v34 as R
from core.store import Project, list_projects
from test_v34_run import EP1, PARAMS, FakeLLM, new_project

WEB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "web", "index.html")
APP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "server", "app.py")


class SystemTagTests(unittest.TestCase):

    def setUp(self):
        self.pj = new_project()

    def tearDown(self):
        shutil.rmtree(self.pj.root, ignore_errors=True)

    def test_old_projects_fall_back_to_v61(self):
        """★ 老项目 meta 里没有 system —— 它们本来就是 V6.1 跑出来的。

        判成 v34 的话会把已有产物全看成「还没做」，然后重跑一遍花第二份钱。
        """
        from server.app import system_of
        meta = self.pj.meta()
        self.assertNotIn("system", meta)
        self.assertEqual(system_of(self.pj), "v61")

    def test_unknown_system_falls_back_to_v61(self):
        from server.app import _system_of
        for bad in ("", None, "v99", "V3.4", "  "):
            self.assertEqual(_system_of(bad), "v61", repr(bad))
        self.assertEqual(_system_of("v34"), "v34")
        self.assertEqual(_system_of("V34"), "v34")

    def test_project_list_exposes_the_system(self):
        self.pj.save_meta(dict(self.pj.meta(), system="v34"))
        base = os.path.dirname(self.pj.root)
        row = next(p for p in list_projects(base) if p["root"] == self.pj.root)
        self.assertEqual(row["system"], "v34")

    def test_list_defaults_missing_system_to_v61(self):
        base = os.path.dirname(self.pj.root)
        row = next(p for p in list_projects(base) if p["root"] == self.pj.root)
        self.assertEqual(row["system"], "v61")


class PreviewTests(unittest.TestCase):
    """点「开始」之前先看清会做什么、花多少次调用。"""

    def setUp(self):
        self.pj = new_project()
        self.pj.save_meta(dict(self.pj.meta(), system="v34"))
        self.llm = FakeLLM()

    def tearDown(self):
        shutil.rmtree(self.pj.root, ignore_errors=True)

    def test_preview_counts_segment_calls_not_stages(self):
        """★ 逐段环节按段算调用次数。

        一集十几段时，「还要跑 1 个环节」和「还要跑 13 次调用」
        差着一个数量级 —— 按环节算会让人以为很便宜。
        """
        q = lambda *a, **k: None
        for sid in ("n1", "n2"):
            R.run_stage(self.pj, sid, llm=self.llm, params=PARAMS, log=q)
        for sid in ("n3", "n4", "n4b", "n5", "n6", "n7", "n8", "n9", "n10"):
            R.run_stage(self.pj, sid, llm=self.llm, params=PARAMS,
                        episode=EP1, log=q)
        pv = P.preview(self.pj, only_episodes=[EP1])
        self.assertEqual(pv["system"], "v34")
        # n11/n12/n13 每个 2 段 = 6 次，加 n14 一次
        self.assertEqual(pv["llm_calls"], 7, pv["todo"])
        self.assertTrue(any("2 次调用" in x for x in pv["todo"]),
                        f"逐段环节没标出调用次数：{pv['todo']}")

    def test_preview_lists_done_steps_as_skipped(self):
        q = lambda *a, **k: None
        R.run_stage(self.pj, "n1", llm=self.llm, params=PARAMS, log=q)
        pv = P.preview(self.pj)
        self.assertTrue(any("源解析" in x for x in pv["skip"]),
                        f"跑过的环节没算进跳过：{pv['skip']}")


class FrontEndWiringTests(unittest.TestCase):
    """前端不能再写死 V6.1 的环节表 —— 写死的话 V3.4 项目会显示成 12 个环节。"""

    def setUp(self):
        self.html = open(WEB, encoding="utf-8").read()

    def test_stage_list_comes_from_the_project_system(self):
        self.assertIn("function sysStages()", self.html)
        self.assertIn("function projSystem()", self.html)
        # 除了 sysStages 内部的兜底，别处不该再直接读 BOOT.stages
        uses = [l for l in self.html.splitlines()
                if "BOOT.stages" in l and "s.stages" not in l]
        self.assertEqual(uses, [], f"还有地方写死 V6.1 环节表：{uses}")

    def test_per_episode_check_uses_the_stage_object(self):
        """V3.4 的环节表自带 scope；按 id 猜「只有 s1 是全剧级」会全判错。"""
        self.assertNotIn("perEpisode(id)", self.html)
        self.assertIn("s.scope", self.html)

    def test_create_form_lets_you_pick_and_warns_it_is_final(self):
        self.assertIn('id="spSystem"', self.html)
        self.assertIn("system: $('#spSystem').value", self.html)
        self.assertIn("建完不能改", self.html)


class BackEndWiringTests(unittest.TestCase):

    def setUp(self):
        self.src = open(APP, encoding="utf-8").read()

    def test_run_and_preview_both_branch_on_the_system(self):
        """★ 只改一个入口的话，另一个会用错编排把产物写坏。"""
        for anchor in ("/api/pipeline/preview", "/api/pipeline/run"):
            i = self.src.find(anchor)
            self.assertGreater(i, 0, anchor)
            chunk = self.src[i:i + 2600]
            self.assertIn("pipeline_v34", chunk, f"{anchor} 没有按体系分流")

    def test_bootstrap_ships_both_stage_tables(self):
        self.assertIn('"systems"', self.src)
        self.assertIn("V34.STAGES", self.src)


if __name__ == "__main__":
    unittest.main()
