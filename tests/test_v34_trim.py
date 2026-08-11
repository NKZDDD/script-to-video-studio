# -*- coding: utf-8 -*-
"""按环节裁剪上游产物：只发这一步真正要的部分。

起因是实跑的一次失败。环节1 吐出 2.8 万字，环节2 的提示词 3.1 万字里
92% 就是它 —— 而环节2 连剧本都不吃，只吃这一份。
结果大输入 + 大输出同时发生，把网关的上限试出来了：
环节1 能过（小输入大输出），环节2 断在中途，三次重试三次断。

第二笔账更贵：n1_truth 有 4 个下游、其中 3 个是逐集的。
一集重复发 3 遍同一份东西，40 集就是 340 万字纯重复。

裁剪的风险是反的：裁多了**不报错**，只是模型少了输入、答得差一点 ——
那正是这个项目里最难查的一类问题。所以这里钉两头：
该裁的裁到了，不该裁的一个字都没少。
"""
import shutil
import unittest

from core import run_v34 as R, system_v34 as V
from core.stages import jd
from test_v34_run import EP1, PARAMS, FakeLLM, new_project

TRUTH = {
    "project_name": "某剧", "story_type": "都市情感",
    "cultural_setting": "雅加达", "dialogue_language": "印尼语",
    "worldview": "现实", "era": "当代", "main_conflict": "遗产争夺",
    "entities": [{"entity_id": "E001", "canonical_name": "甲",
                  "aliases": ["阿甲"]}],
    "events": [{"event_id": "EV001", "action": "摔化验单", "result": "对方沉默",
                "state_deltas": [{"affected_entity_id": "E001",
                                  "state_dimension": "外观",
                                  "result_value": "领口散开"}]}],
    "story_truth": {"objective_facts": ["甲有病"], "hidden_truth": []},
    "open_design": [{"design_question": "病房什么样"}],
    "reality_threads": [{"thread_id": "RT_MAIN"}],
    "episode_ranges": [{"episode": "EP01", "start_line": 1, "end_line": 120}],
}


class ProjectionTests(unittest.TestCase):

    def test_asset_prompt_stage_only_gets_the_tone(self):
        """★ n4b 的模板标的是【视觉基调】，却收到整份故事真相。

        这是最大的一块浪费：一段基调用不到实体表、事件链和切集边界。
        """
        got = R.project_product("n4b", "n1_truth", TRUTH)
        self.assertIn("story_type", got)
        self.assertIn("cultural_setting", got)
        self.assertNotIn("entities", got)
        self.assertNotIn("events", got)
        self.assertNotIn("episode_ranges", got)
        self.assertLess(len(jd(got)), len(jd(TRUTH)) // 2)

    def test_rules_stage_keeps_the_plot_but_drops_the_visual_deltas(self):
        """★ 环节2 要靠事件写人物弧光，所以事件必须留。

        但逐事件的外观增量是第六环节账本要的东西 —— 人物动机和世界规则
        靠 action/result 就够。这一刀是环节2 那次断流的主要来源。
        """
        got = R.project_product("n2", "n1_truth", TRUTH)
        self.assertIn("events", got, "把事件裁掉，环节2 就没有剧情可依据了")
        self.assertEqual(got["events"][0]["action"], "摔化验单")
        self.assertNotIn("state_deltas", got["events"][0])
        self.assertNotIn("episode_ranges", got)

    def test_episode_ranges_are_dropped_after_splitting(self):
        """切集用的行号边界，切完就没人需要了。"""
        for sid in ("n2", "n3", "n4", "n4b"):
            self.assertNotIn("episode_ranges",
                             R.project_product(sid, "n1_truth", TRUTH), sid)

    def test_the_asset_system_still_gets_the_state_deltas(self):
        """★ 连续性状态资产就是从状态变化来的 —— 这一份不能裁。

        宁可少裁：裁错了模型不报错，只会漏掉本该建的状态资产。
        """
        got = R.project_product("n4", "n1_truth", TRUTH)
        self.assertIn("state_deltas", got["events"][0])
        self.assertIn("entities", got)
        self.assertIn("story_truth", got)

    def test_unlisted_combinations_are_sent_whole(self):
        """★ 默认必须是「不裁」。

        表里加错一条的代价是模型静默少了输入，所以没登记的一律整块发。
        """
        self.assertEqual(R.project_product("n7", "n5_spatial", TRUTH), TRUTH)
        self.assertEqual(R.project_product("n14", "n8_cvs", TRUTH), TRUTH)

    def test_the_original_object_is_not_mutated(self):
        """裁剪不能改到磁盘上那份产物 —— 下一个环节还要用完整的。"""
        before = jd(TRUTH)
        R.project_product("n2", "n1_truth", TRUTH)
        R.project_product("n4b", "n1_truth", TRUTH)
        self.assertEqual(jd(TRUTH), before)

    def test_missing_paths_do_not_blow_up(self):
        """产物字段缺失是常态（模型少写一个键），裁剪不能因此抛异常。"""
        self.assertEqual(R.project_product("n2", "n1_truth", {}), {})
        self.assertEqual(R.project_product("n2", "n1_truth", {"events": None}),
                         {"events": None})
        self.assertEqual(R.project_product("n2", "n1_truth", "不是字典"),
                         "不是字典")


class DropPathTests(unittest.TestCase):

    def test_top_level_key(self):
        self.assertEqual(R._drop_path({"a": 1, "b": 2}, "b"), {"a": 1})

    def test_per_row_key_in_a_list(self):
        obj = {"rows": [{"x": 1, "y": 2}, {"x": 3, "y": 4}]}
        self.assertEqual(R._drop_path(obj, "rows[].y"),
                         {"rows": [{"x": 1}, {"x": 3}]})

    def test_an_absent_path_changes_nothing(self):
        self.assertEqual(R._drop_path({"a": 1}, "zzz[].q"), {"a": 1})


class WiringTests(unittest.TestCase):

    def setUp(self):
        self.pj = new_project()
        self.llm = FakeLLM()
        q = lambda *a, **k: None
        R.run_stage(self.pj, "n1", llm=self.llm, params=PARAMS, log=q)

    def tearDown(self):
        shutil.rmtree(self.pj.root, ignore_errors=True)

    def test_mapping_applies_the_projection(self):
        """★ 光有函数不算 —— 得真的接在填占位符那一步上。"""
        data = R.deps_data(self.pj, "n4b", EP1)
        m = R.mapping(self.pj, "n4b", PARAMS, data, EP1)
        self.assertNotIn("episode_ranges", m["TRUTH"])

    def test_preview_shows_the_same_trimmed_input_as_the_real_run(self):
        """★ 预览和真跑必须一致，否则预览就是安慰剂。"""
        R.run_stage(self.pj, "n2", llm=self.llm, params=PARAMS, log=lambda *a, **k: None)
        for sid in ("n3", "n4"):
            R.run_stage(self.pj, sid, llm=self.llm, params=PARAMS, episode=EP1,
                        log=lambda *a, **k: None)
        pv = R.preview_prompt(self.pj, "n4b", PARAMS, EP1)
        self.assertEqual(pv["user"], R.build_user(self.pj, "n4b", PARAMS, EP1))

    def test_the_saving_is_reported(self):
        """★ 裁剪是「悄悄少发一部分」，省了多少必须看得见。

        看不见的话，裁错了也没人会去核对。
        """
        s = R.trim_saving(self.pj, "n2", "")
        self.assertIn("按环节裁剪", s)
        self.assertIn("TRUTH", s)
        self.assertIn("→", s)

    def test_stages_without_trimming_report_nothing(self):
        self.assertEqual(R.trim_saving(self.pj, "n1", ""), "")


class TableSanityTests(unittest.TestCase):
    """这张表本身要经得起核对 —— 它决定了什么被静默少发。"""

    def test_every_entry_points_at_a_real_dependency(self):
        """★ 环节和产物的组合必须在依赖表里真实存在。

        写错一个名字，那条裁剪永远不生效，而且一声不吭。
        """
        for (sid, out) in V.PRODUCT_NEEDS:
            self.assertIn(sid, V.LLM_SPEC, sid)
            self.assertIn(out, V.LLM_SPEC[sid][1],
                          f"{sid} 并不依赖 {out}，这条裁剪是死的")

    def test_keep_and_drop_are_not_mixed(self):
        """两种模式混写会让人以为 drop 也生效了，其实 keep 优先直接返回。"""
        for key, spec in V.PRODUCT_NEEDS.items():
            self.assertFalse(spec.get("keep") and spec.get("drop"), key)
            self.assertTrue(spec.get("keep") or spec.get("drop"), key)


if __name__ == "__main__":
    unittest.main()
