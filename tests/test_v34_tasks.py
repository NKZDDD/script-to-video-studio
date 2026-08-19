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

    def test_one_video_task_per_segment(self):
        self.assertEqual({x["key"] for x in R.build_tasks(
            self.pj, PARAMS)["video_tasks"]}, set(SEGS))

    def test_a_segment_gets_one_storyboard_task_per_sheet(self):
        """★ V6.2：一个段落是 1..N 张**有序** Sheet，不再是一张。

        V6.1 时代这里断言的是「一段一张」。那条不变量正是坑的来源：
        模板要求「每张最多 3 格、更多用有序续页」，而装配层只给一个输出位置，
        模型只能把 6 格挤进一张 —— 实遇 EP01-SEG06 就是这样。
        """
        t = R.build_tasks(self.pj, PARAMS)
        per_seg: dict = {}
        for x in t["storyboard_tasks"]:
            per_seg.setdefault(x["segment"], []).append(x)
        self.assertEqual(set(per_seg), set(SEGS))
        for seg, rows in per_seg.items():
            self.assertEqual(len(rows), 2, f"{seg} 的两张 Sheet 没都建出任务")
            self.assertEqual([r["sheet_id"] for r in rows],
                             ["SHEET_A", "SHEET_B"])
            self.assertEqual(len({r["key"] for r in rows}), 2,
                             "两张的 key 撞了 —— 会互相覆盖同一个文件")
            self.assertEqual(len({r["output"] for r in rows}), 2,
                             "两张的输出路径撞了 —— 后一张覆盖前一张，不报错")

    def test_video_carries_the_whole_ordered_spine(self):
        """★ V6.2 的核心：视频要拿**整条**有序骨架，不是一张。

        只给第一张的话，模型不知道这一段先发生什么后发生什么 ——
        而画面照样出得来，看着正常。
        """
        t = R.build_tasks(self.pj, PARAMS)
        by_seg: dict = {}
        for x in t["storyboard_tasks"]:
            by_seg.setdefault(x["segment"], []).append(x["output"])
        for v in t["video_tasks"]:
            got = [s["file_ref"] for s in v["storyboard_refs"]]
            self.assertEqual(got, by_seg[v["key"]],
                             "视频拿到的骨架和这一段的故事板对不上")
            self.assertEqual([s["order"] for s in v["storyboard_refs"]], [1, 2],
                             "顺序丢了 —— 模型会把后段当前段")

    def test_the_old_single_ref_field_still_points_somewhere(self):
        """老页面和老台账还在读 storyboard_ref，别让它变成空。"""
        t = R.build_tasks(self.pj, PARAMS)
        for v in t["video_tasks"]:
            self.assertTrue(v["storyboard_ref"])
            self.assertEqual(v["storyboard_ref"], v["storyboard_refs"][0]["file_ref"])

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
        self.pj.save_stage("n4b_asset_prompts", {"asset_prompts": []}, "")
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


class SeriesWideAssetTests(unittest.TestCase):
    """资产库全剧一份 —— 这一层错了，同一个角色会出两张脸。

    这个类原来叫 CrossEpisodeTests，测的是「第二集要跳过第一集写过的资产」。
    那套跨集去重连同它的竞争一起删了：资产表和资产提示词改成**全剧级**之后，
    同一个角色天然只有一份定义，不需要去重，也就没有东西可抢。
    """

    def setUp(self):
        self.pj = new_project()
        self.llm = FakeLLM()
        for sid in ("n1", "n2", "n3", "n4", "n4b"):
            R.run_stage(self.pj, sid, llm=self.llm, params=PARAMS, log=quiet)

    def tearDown(self):
        shutil.rmtree(self.pj.root, ignore_errors=True)

    def test_assets_live_at_the_project_root_not_under_an_episode(self):
        """★ 放错目录不会报错，只会让下游读到空字典然后自己编。"""
        self.assertTrue(os.path.isfile(self.pj.stage_path("n4_assets")))
        self.assertFalse(os.path.isfile(self.pj.stage_path("n4_assets", EP1)))
        self.assertTrue(os.path.isfile(self.pj.stage_path("n4b_asset_prompts")))

    def test_each_asset_gets_exactly_one_task(self):
        t = R.build_tasks(self.pj, PARAMS)
        keys = [x["key"] for x in t["asset_tasks"]]
        self.assertEqual(len(keys), len(set(keys)), "同一个资产排了两条任务")
        self.assertIn("C001", keys)

    def test_prompt_files_are_written_once_regardless_of_episode(self):
        """★ 资产提示词按集写的话，40 集会把同一批文件重写 40 遍。

        更糟的是产物在项目根下，按集读时 40 集里只有一集读得到，
        另外 39 集写 0 个文件而且不报错。
        """
        before = R.write_prompt_files(self.pj, EP1)
        self.assertGreater(before, 0, "第一集一个提示词文件都没写出来")
        self.assertEqual(R.write_prompt_files(self.pj, EP2), before,
                         "换一集就写不出资产提示词了 —— 说明还在按集读")


if __name__ == "__main__":
    unittest.main()
