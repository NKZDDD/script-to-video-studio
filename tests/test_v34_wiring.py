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

    def test_new_projects_default_to_v34_not_v61(self):
        """★ 建项目时没传 system，该给现在在用的那套。

        和「读老项目缺字段回落 v61」是两回事，用同一个默认值就会出现
        「批量建剧整批建成了旧体系」而且一声不吭 —— 批量那个接口原本就这么漏的。
        """
        from server.app import _new_system, _system_of
        for blank in ("", None, "  "):
            self.assertEqual(_new_system(blank), "v34", repr(blank))
            self.assertEqual(_system_of(blank), "v61", repr(blank))
        self.assertEqual(_new_system("v61"), "v61", "明确指定 v61 还是要听")

    def test_batch_create_records_the_system(self):
        """★ 批量建剧和单个建剧必须落同一套体系。"""
        src = open(APP, encoding="utf-8").read()
        blk = src[src.index('"/api/project/create_batch"'):
                  src.index('"/api/project/create"')]
        self.assertIn('"system"', blk,
                      "批量建剧没写 system —— 建出来的剧会被当成旧体系")
        self.assertIn("_new_system", blk)

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



class TemplateRegistryTests(unittest.TestCase):
    """模板注册表要认全两套。

    只认 V6.1 的话，V3.4 那 15 份模板在设置页里**看不见也改不了** ——
    而模板正是这套体系里最需要改的东西（换风格、换模型、加约束）。
    这个洞是打包自检发现的：模板文件明明在 exe 里，接口不认。
    """

    def test_catalog_lists_every_template_on_disk(self):
        """★ 目录里有几份 .md，接口就该认得几份。"""
        from core import prompts as P
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        on_disk = {f[:-3] for f in os.listdir(os.path.join(root, "prompts"))
                   if f.endswith(".md") and not f.endswith("_adapter.md")}
        known = {x["name"] for x in P.catalog()}
        self.assertFalse(on_disk - known,
                         f"这些模板改不了：{sorted(on_disk - known)}")

    def test_every_template_is_tagged_with_its_system(self):
        from core import prompts as P
        by_sys = {}
        for x in P.catalog():
            by_sys.setdefault(x["system"], []).append(x["name"])
        self.assertEqual(len(by_sys.get("v34", [])), 15)
        self.assertEqual(len(by_sys.get("v61", [])), 8)
        self.assertEqual(by_sys.get(""), ["_common"], "_common 是两套共用的")

    def test_v34_templates_can_be_read_and_saved(self):
        """★ 光列出来不算数 —— 打开和保存也得走得通。"""
        from core import prompts as P
        r = P.read("n9_shots", scope="global")
        self.assertTrue(r["text"].strip())
        self.assertEqual(r["stage_no"], 9)
        self.assertFalse(P.check("n9_shots", r["text"])["errors"])

    def test_required_vars_come_from_the_dependency_graph(self):
        """★ V3.4 的必需占位符从环节依赖推导，不再手抄一张表。

        手抄的表迟早和依赖表对不上，然后这道校验就成了摆设。
        """
        from core import prompts as P, system_v34 as V34
        for sid, (tpl, deps, _req) in V34.LLM_SPEC.items():
            want = [V34.placeholder_of(d) for d in deps]
            self.assertEqual(P.required_vars(tpl), want, tpl)

    def test_builtin_templates_all_pass_their_own_check(self):
        """★ 内置模板拿自己去验自己，必须全过。

        过不了说明模板正文和依赖表已经对不上 —— 用户什么都没改就存不进去。
        """
        from core import prompts as P, stages as S
        bad = {}
        for it in P.catalog():
            errs = P.check(it["name"], S.load_prompt(it["name"]))["errors"]
            if errs:
                bad[it["name"]] = errs
        self.assertFalse(bad, bad)

    def test_the_page_only_lists_the_current_systems_templates(self):
        """两套加起来 24 份，环节号还会撞（n1 和 s1 都是「环节1」）。"""
        src = open(WEB, encoding="utf-8").read()
        self.assertIn("x.system === sys", src,
                      "模板下拉没按体系过滤，会让人改错那份")

if __name__ == "__main__":
    unittest.main()
