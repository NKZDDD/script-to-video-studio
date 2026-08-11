# -*- coding: utf-8 -*-
"""V3.4 任务装配：把各环节产物变成出图出片那一层唯一读的 tasks.json。

四类任务。相对 V6.1 多出来的是 scstate 那一类 —— 故事板不再直接拿一堆
原子资产当参考，而是先合成一张场景状态图再参考它。

这里钉的都是「装配错了不报错、只是少」的那一类：
少一条任务看着像本来就没有，参考图指不到文件要到出图那一刻才发现。
"""
import os
import shutil
import unittest

from core import run_v34 as R
from test_v34_run import (EP1, EP2, PARAMS, SEGS, FakeLLM, new_project)


def quiet(*a, **k):
    pass


class TaskBuildTests(unittest.TestCase):

    def setUp(self):
        self.pj = new_project()
        self.llm = FakeLLM()
        for sid in ("n1", "n2"):
            R.run_stage(self.pj, sid, llm=self.llm, params=PARAMS, log=quiet)
        for sid in ("n3", "n4", "n4b", "n5", "n6", "n7", "n8", "n9", "n10"):
            R.run_stage(self.pj, sid, llm=self.llm, params=PARAMS,
                        episode=EP1, log=quiet)
        for sid in ("n11", "n12", "n13"):
            R.run_segment_stage(self.pj, sid, llm=self.llm, params=PARAMS,
                                episode=EP1, log=quiet)

    def tearDown(self):
        shutil.rmtree(self.pj.root, ignore_errors=True)

    def test_four_task_kinds_are_built(self):
        t = R.build_tasks(self.pj, PARAMS)
        for k in ("asset_tasks", "scstate_tasks", "storyboard_tasks", "video_tasks"):
            self.assertIn(k, t)
        self.assertEqual(t["system"], "v34",
                         "没标体系的话，V6.1 的生产页会去读这份 tasks.json")

    def test_every_task_has_the_fields_produce_layer_reads(self):
        """★ produce.py 只认这几个字段。缺一个就是跑到那一步才炸。"""
        t = R.build_tasks(self.pj, PARAMS)
        for kind in ("asset_tasks", "scstate_tasks", "storyboard_tasks"):
            for task in t[kind]:
                for f in ("key", "prompt_ref", "reference_images", "params", "output"):
                    self.assertIn(f, task, f"{kind} 的 {task.get('key')} 缺 {f}")
        for task in t["video_tasks"]:
            for f in ("key", "prompt_ref", "storyboard_ref", "params", "output"):
                self.assertIn(f, task)

    def test_one_task_per_segment(self):
        t = R.build_tasks(self.pj, PARAMS)
        self.assertEqual({x["key"] for x in t["storyboard_tasks"]}, set(SEGS))
        self.assertEqual({x["key"] for x in t["video_tasks"]}, set(SEGS))

    def test_video_points_at_its_own_storyboard(self):
        """★ 视频拿故事板当参考。指错段的话，画面和台词对不上但不报错。"""
        t = R.build_tasks(self.pj, PARAMS)
        sb = {x["key"]: x["output"] for x in t["storyboard_tasks"]}
        for v in t["video_tasks"]:
            self.assertEqual(v["storyboard_ref"], sb[v["key"]])

    def test_storyboard_refs_resolve_to_scstate_files(self):
        """★ 故事板的参考图应该指到场景状态图，不是指到空。"""
        self.pj.save_stage("n11_scstate", {"scstates": [
            {"scstate_id": "SCST_A", "seg_id": SEGS[0], "source_cvs": "CVS1",
             "reference_assets": ["C001"], "prompt": "p"}]}, EP1)
        self.pj.save_stage("n12_storyboard", {"sbpkg": [
            {"sbpkg_id": "PKG", "seg_id": SEGS[0], "kf": [],
             "reference_order": [{"image_n": 1, "asset_id": "SCST_A"}],
             "storyboard_prompt": "p"}]}, EP1)
        t = R.build_tasks(self.pj, PARAMS)
        sb = next(x for x in t["storyboard_tasks"] if x["key"] == SEGS[0])
        self.assertTrue(sb["reference_images"][0]["file_ref"],
                        "故事板引用了场景状态图，却指不到文件")
        self.assertIn("SCST_A", sb["reference_images"][0]["file_ref"])

    def test_unknown_reference_stays_in_the_list_with_empty_file_ref(self):
        """★ 认不出的引用不许悄悄删掉。

        删了数量看着是对的，反而看不出少了一张；留着空 file_ref，
        出图那一层会因为「声明几张就必须解析出几张」停下并报清缺哪张。
        """
        self.pj.save_stage("n12_storyboard", {"sbpkg": [
            {"sbpkg_id": "PKG", "seg_id": SEGS[0], "kf": [],
             "reference_order": [{"image_n": 1, "asset_id": "根本不存在"}],
             "storyboard_prompt": "p"}]}, EP1)
        t = R.build_tasks(self.pj, PARAMS)
        sb = next(x for x in t["storyboard_tasks"] if x["key"] == SEGS[0])
        self.assertEqual(len(sb["reference_images"]), 1, "被悄悄删掉了")
        self.assertEqual(sb["reference_images"][0]["file_ref"], "")

    def test_scstate_tasks_dedupe_by_state_not_by_segment(self):
        """★ 场景状态图按状态去重，不按段。

        V3.4 里 SCSTATE 编号不含段号 —— 同一场戏跨几段而世界状态没变时，
        本来就该复用同一张。不去重的话同一张图付几次钱，
        而且几条任务写同一个文件、后一条覆盖前一条，不报错只是白花钱。
        """
        t = R.build_tasks(self.pj, PARAMS)
        keys = [x["key"] for x in t["scstate_tasks"]]
        self.assertEqual(len(keys), len(set(keys)), f"排了重复的场景状态图：{keys}")
        outs = [x["output"] for x in t["scstate_tasks"]]
        self.assertEqual(len(outs), len(set(outs)), "两条任务写同一个文件")

    def test_no_two_tasks_write_the_same_file(self):
        """★ 所有类别一起看：不许有两条任务写同一个输出。"""
        t = R.build_tasks(self.pj, PARAMS)
        seen = {}
        for kind in ("asset_tasks", "scstate_tasks", "storyboard_tasks", "video_tasks"):
            for task in t[kind]:
                prev = seen.get(task["output"])
                self.assertIsNone(
                    prev, f"{kind}/{task['key']} 和 {prev} 写同一个文件："
                          f"{task['output']}")
                seen[task["output"]] = f"{kind}/{task['key']}"

    def test_asset_without_a_prompt_produces_no_task(self):
        """判了 must 却没写提示词的资产，不会进任务 —— 但那属于上游的账。"""
        self.pj.save_stage("n4b_asset_prompts", {"asset_prompts": []}, EP1)
        t = R.build_tasks(self.pj, PARAMS)
        self.assertEqual(t["asset_tasks"], [])

    def test_prompt_files_land_on_disk(self):
        """★ 出图那一层按路径读文件，不是从 tasks.json 里读正文。

        落盘还有一个用处：人能在页面上直接改这一条，改完立刻生效。
        """
        n = R.write_prompt_files(self.pj, EP1)
        self.assertGreater(n, 0)
        t = R.build_tasks(self.pj, PARAMS)
        for kind in ("asset_tasks", "scstate_tasks", "storyboard_tasks", "video_tasks"):
            for task in t[kind]:
                p = self.pj.p(*task["prompt_ref"].split("/"))
                self.assertTrue(os.path.isfile(p),
                                f"{task['key']} 的提示词文件不在：{task['prompt_ref']}")


class CrossEpisodeTests(unittest.TestCase):
    """资产库全剧共享 —— 这一层错了，同一个角色会出两张脸。"""

    def setUp(self):
        self.pj = new_project()
        self.llm = FakeLLM()
        for sid in ("n1", "n2"):
            R.run_stage(self.pj, sid, llm=self.llm, params=PARAMS, log=quiet)

    def tearDown(self):
        shutil.rmtree(self.pj.root, ignore_errors=True)

    def test_second_episode_skips_assets_already_written(self):
        """★ 不过滤的话 40 集会把同一个角色的提示词重写 40 遍。"""
        for sid in ("n3", "n4", "n4b"):
            R.run_stage(self.pj, sid, llm=self.llm, params=PARAMS,
                        episode=EP1, log=quiet)
        for sid in ("n3", "n4"):
            R.run_stage(self.pj, sid, llm=self.llm, params=PARAMS,
                        episode=EP2, log=quiet)
        todo, skipped = R.assets_to_write(self.pj, EP2)
        self.assertEqual([a["asset_id"] for a in skipped], ["C001"],
                         "EP01 写过的资产没被跳过")
        self.assertEqual(todo, [])

    def test_first_episode_writes_everything(self):
        for sid in ("n3", "n4"):
            R.run_stage(self.pj, sid, llm=self.llm, params=PARAMS,
                        episode=EP1, log=quiet)
        todo, skipped = R.assets_to_write(self.pj, EP1)
        self.assertEqual([a["asset_id"] for a in todo], ["C001"])
        self.assertEqual(skipped, [])

    def test_assets_merge_across_episodes_first_definition_wins(self):
        """同一个 asset_id 在两集里都出现时，用先出现的定义 —— 否则会换脸。"""
        for sid in ("n3", "n4", "n4b"):
            for ep in (EP1, EP2):
                R.run_stage(self.pj, sid, llm=self.llm, params=PARAMS,
                            episode=ep, log=quiet)
        self.pj.save_stage("n4_assets", {"assets": [
            {"asset_id": "C001", "family": "CHAR", "name": "后来改的名字",
             "decision": "must", "reference_assets": [], "used_by_segs": []}]}, EP2)
        t = R.build_tasks(self.pj, PARAMS)
        keys = [x["key"] for x in t["asset_tasks"]]
        self.assertEqual(keys.count("C001"), 1, "同一个资产排了两条任务")


if __name__ == "__main__":
    unittest.main()
