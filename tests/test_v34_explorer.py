# -*- coding: utf-8 -*-
"""产物页要能看见 v34 的东西。

读错体系不会报错，只会**什么都看不到** —— 资产库是空的、按段看是空的、
场景状态图那一整层在明细里根本不出现。人会以为是没跑成，
实际是页面在读另一套体系的文件名。
"""
import shutil
import unittest

from core import explorer, run_v34 as R
from test_v34_run import EP1, PARAMS, SEGS, FakeLLM, new_project


def quiet(*a, **k):
    pass


class V34ExplorerTests(unittest.TestCase):

    def setUp(self):
        self.pj = new_project()
        self.pj.save_meta(dict(self.pj.meta(), system="v34"))
        self.llm = FakeLLM()
        for sid in ("n1", "n2"):
            R.run_stage(self.pj, sid, llm=self.llm, params=PARAMS, log=quiet)
        for sid in ("n3", "n4", "n4b", "n5", "n6", "n7", "n8", "n9", "n10"):
            R.run_stage(self.pj, sid, llm=self.llm, params=PARAMS,
                        episode=EP1, log=quiet)
        for sid in ("n11", "n12", "n13"):
            R.run_segment_stage(self.pj, sid, llm=self.llm, params=PARAMS,
                                episode=EP1, log=quiet)
        R.write_prompt_files(self.pj, EP1)
        R.build_tasks(self.pj, PARAMS)

    def tearDown(self):
        shutil.rmtree(self.pj.root, ignore_errors=True)

    def test_system_is_detected(self):
        self.assertEqual(explorer.system_of(self.pj), "v34")

    def test_asset_library_is_not_empty(self):
        """★ 读 s4_assets 的话这里会是空的 —— 看着像没跑成。"""
        a = explorer.assets(self.pj)
        self.assertTrue(a["total"], "资产库空了，多半是读错了产物文件名")
        ids = [x["asset_id"] for x in a["items"]]
        self.assertIn("C001", ids)

    def test_asset_prompt_is_found_at_the_v34_path(self):
        a = explorer.assets(self.pj)
        item = next(x for x in a["items"] if x["asset_id"] == "C001")
        self.assertTrue(item["prompt"]["exists"], "资产提示词没找到")

    def test_segments_view_reads_v34_products(self):
        s = explorer.segments(self.pj, EP1)
        self.assertEqual([r["id"] for r in s["rows"]], SEGS)
        self.assertTrue(s["rows"][0]["compiled"], "视频计划没被算成已编译")

    def test_scstate_layer_is_visible(self):
        """★ V3.4 独有的那一层，页面上必须看得见 —— 否则出了图也不知道。"""
        s = explorer.segments(self.pj, EP1)
        self.assertTrue(any(r["scstates"] for r in s["rows"]),
                        "场景状态图那一层在按段看里完全不出现")
        self.assertTrue(any(x["prompt"]["exists"]
                            for r in s["rows"] for x in r["scstates"]),
                        "场景状态提示词读不到")

    def test_step_progress_uses_v34_stage_numbers(self):
        s = explorer.segments(self.pj, EP1)
        names = {x["name"] for x in s["steps"]}
        self.assertIn("场景状态图", names)
        self.assertNotIn("段落划分", names, "还在用 V6.1 的环节名")

    def test_task_detail_lists_four_kinds(self):
        """★ 少列一类的话，那一层出了图、花了钱，页面上看不见。"""
        t = explorer.tasks(self.pj, EP1)
        labels = [g["label"] for g in t["groups"]]
        self.assertEqual(labels, ["资产图", "场景状态图", "故事板", "分段视频"])
        self.assertTrue(t["has_tasks"])

    def test_view_does_not_crash_and_returns_both_panes(self):
        v = explorer.view(self.pj, EP1)
        self.assertTrue(v["assets"]["total"])
        self.assertTrue(v["segments"]["rows"])


class V61StillWorksTests(unittest.TestCase):
    """改成按体系分流之后，老项目不能坏。"""

    def setUp(self):
        self.pj = new_project()          # 没有 system 字段 = 老项目

    def tearDown(self):
        shutil.rmtree(self.pj.root, ignore_errors=True)

    def test_old_project_falls_back_to_v61(self):
        self.assertEqual(explorer.system_of(self.pj), "v61")

    def test_v61_paths_are_still_used(self):
        # 还没切集时资产表落在项目根（_merged_assets 会去那儿读），
        # 存进集目录反而读不到 —— 这一点两套体系一样
        self.pj.save_stage("s4_assets", {"assets": [
            {"asset_id": "C001", "category": "identity", "name": "甲",
             "decision": "must", "appearance": "x"}]})
        a = explorer.assets(self.pj)
        ids = [x["asset_id"] for x in a["items"]]
        self.assertIn("C001", ids)
        item = next(x for x in a["items"])
        self.assertIn("人物身份资产", item["image"]["rel"],
                      "老项目的资产图路径被换成了 V3.4 那套")

    def test_v61_task_kinds_unchanged(self):
        self.assertEqual([k[0] for k in explorer._task_kinds(self.pj)],
                         ["asset_tasks", "storyboard_tasks", "video_tasks"])


if __name__ == "__main__":
    unittest.main()
