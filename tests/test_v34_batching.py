# -*- coding: utf-8 -*-
"""n3 和 n4b 分批跑。

这两个环节是「全剧一次」，而输出量随剧的长度线性涨。涨过这条线路一次能
吐完的量之后就**再也跑不过去**了：重试写的是同一批东西，每次断在差不多
的地方，钱花掉、进度是零。实跑连撞三次 —— 最后一次是 n3 断流一次、
HTTP 524 一次，后面 6 个环节全线连带停摆。

分批不改环节图：n3 还是 n3、还在原位、产物文件名不变、下游读法不变。
变的只有一件事 —— 一个环节内部分几次调用，每次存盘。

这份用例守三件事，全是「不报错、只是少」那一类：
  · 合并不能变成覆盖（前几批白跑，只表现为「越跑越少」）
  · 断在中间要能接着跑，不能从头再来
  · 分批之后跨集的因果链不能断（编号、entry_state、伏笔回收）

**这里的假 LLM 会读分批指令**。读不懂指令的假模型测不出分批 ——
它对每一批都回同一份全剧产物，于是怎么切都"对"。
"""
import copy
import re
import shutil
import unittest

from core import run_v34 as R
from test_v34_run import EP1, EP2, PARAMS, _FIXTURES, new_project


class Batch3LLM:
    """n3 的假模型：按「只做 EPxx」那句话，只回这一集的场次。"""

    model = "fake"

    def __init__(self):
        self.calls = []

    def json_call(self, system, user, required=None, log=None, cancel=None,
                  on_usage=None, **kw):
        self.calls.append(user)
        m = re.search(r"只做 (EP\d+)", user)
        eps = [m.group(1)] if m else [EP1, EP2]
        i = len(self.calls)
        return {"scope": "full_series",
                "scenes": [{"scene_id": f"SC{i:02d}", "episode": e,
                            "objective": "o", "turn": "t", "outcome": "r",
                            "entry_state": "en", "exit_state": "ex"}
                           for e in eps],
                "beats": [{"beat_id": f"SC{i:02d}-B1", "scene_id": f"SC{i:02d}",
                           "meaningful_change": "c", "change_kind": "knowledge",
                           "state_delta": "d", "shot_need": "s"}],
                "episode_arcs": [{"episode": e, "arc": "a"} for e in eps],
                "boundary_note": f"第 {i} 批的判断"}


class Batch4bLLM:
    """n4b 的假模型：只为「这一批要写的」那几条回提示词。

    要写的那几条是全量记录（带 appearance），引用来的和目录行不是 ——
    照着这个区分，就能测出「这一批到底被要求写了几条」。
    """

    model = "fake"

    def __init__(self):
        self.calls = []

    @staticmethod
    def _asked(user: str) -> list:
        ids = []
        for chunk in user.split('"asset_id"')[1:]:
            m = re.match(r'\s*:\s*"([^"]+)"', chunk)
            if m and "appearance" in chunk[:600]:
                ids.append(m.group(1))
        return ids

    def json_call(self, system, user, required=None, log=None, cancel=None,
                  on_usage=None, **kw):
        self.calls.append(user)
        return {"asset_prompts": [
            {"asset_id": aid, "reference_assets": [], "reference_role_map": {},
             "output_spec": {}, "filename": f"{aid}.png", "prompt": "p"}
            for aid in self._asked(user)]}


def _series_project():
    """跑到 n2 为止的项目 —— 而且 episodes.json 是真切出来的。"""
    pj = new_project()
    pj.save_stage("n1_truth", copy.deepcopy(_FIXTURES["n1"]), "")
    pj.save_stage("n2_rules", copy.deepcopy(_FIXTURES["n2"]), "")
    from core import episodes as _eps
    _eps.build(pj, PARAMS["script"], copy.deepcopy(_FIXTURES["n1"]))
    return pj


# ====================================================================== 合并

class Merge3Tests(unittest.TestCase):

    def test_a_later_batch_does_not_wipe_an_earlier_one(self):
        """★ 直接存盘会把前面几集排好的场次全冲掉，而且不报错。"""
        a = {"scenes": [{"scene_id": "SC01", "episode": EP1}],
             "beats": [{"beat_id": "SC01-B1", "scene_id": "SC01"}]}
        b = {"scenes": [{"scene_id": "SC02", "episode": EP2}],
             "beats": [{"beat_id": "SC02-B1", "scene_id": "SC02"}]}
        m = R.merge_narrative(a, b)
        self.assertEqual([s["scene_id"] for s in m["scenes"]], ["SC01", "SC02"])
        self.assertEqual([x["beat_id"] for x in m["beats"]],
                         ["SC01-B1", "SC02-B1"])

    def test_rerunning_one_episode_replaces_just_that_episode(self):
        a = {"scenes": [{"scene_id": "SC01", "objective": "旧"},
                        {"scene_id": "SC02", "objective": "别动我"}]}
        m = R.merge_narrative(a, {"scenes": [{"scene_id": "SC01", "objective": "新"}]})
        self.assertEqual([s["objective"] for s in m["scenes"]], ["新", "别动我"])

    def test_episode_arcs_merge_by_episode(self):
        m = R.merge_narrative({"episode_arcs": [{"episode": EP1, "arc": "a"}]},
                              {"episode_arcs": [{"episode": EP2, "arc": "b"}]})
        self.assertEqual(len(m["episode_arcs"]), 2)

    def test_the_boundary_note_accumulates(self):
        """★ 它记的是「哪几处边界是判断题」，每一批各有各的判断。"""
        m = R.merge_narrative({"boundary_note": "第一集的判断"},
                              {"boundary_note": "第二集的判断"})
        self.assertIn("第一集的判断", m["boundary_note"])
        self.assertIn("第二集的判断", m["boundary_note"])

    def test_the_same_note_is_not_repeated(self):
        m = R.merge_narrative({"boundary_note": "同一句"}, {"boundary_note": "同一句"})
        self.assertEqual(m["boundary_note"], "同一句")

    def test_rows_without_an_id_are_dropped_not_crashed_on(self):
        m = R.merge_narrative({}, {"scenes": [{"episode": EP1}, {"scene_id": "SC01"}]})
        self.assertEqual([s["scene_id"] for s in m["scenes"]], ["SC01"])

    def test_the_scope_stays_full_series(self):
        """下游按全剧级读这份产物，scope 不能变成某一集。"""
        self.assertEqual(R.merge_narrative({}, {"scope": "EP01"})["scope"],
                         "full_series")


# ====================================================================== n3

class Split3Tests(unittest.TestCase):

    def setUp(self):
        self.pj = _series_project()

    def tearDown(self):
        shutil.rmtree(self.pj.root, ignore_errors=True)

    def test_nothing_done_yet(self):
        self.assertEqual(R.n3_split(self.pj), ([], [EP1, EP2]))

    def test_one_episode_done(self):
        self.pj.save_stage("n3_narrative",
                           {"scenes": [{"scene_id": "SC01", "episode": EP1}]}, "")
        self.assertEqual(R.n3_split(self.pj), ([EP1], [EP2]))

    def test_a_scene_without_an_episode_does_not_count(self):
        """★ 没标集号的场次不能让整集算做过 —— 那一集会永远排不出来。"""
        self.pj.save_stage("n3_narrative", {"scenes": [{"scene_id": "SC01"}]}, "")
        self.assertEqual(R.n3_split(self.pj), ([], [EP1, EP2]))


class Run3Tests(unittest.TestCase):

    def setUp(self):
        self.pj = _series_project()
        self.llm = Batch3LLM()

    def tearDown(self):
        shutil.rmtree(self.pj.root, ignore_errors=True)

    def _run(self, llm=None):
        return R.run_n3_batched(self.pj, llm=llm or self.llm, params=PARAMS,
                                log=lambda *a: None)

    def test_one_call_per_episode(self):
        """★ 这就是分批本身。"""
        self._run()
        self.assertEqual(len(self.llm.calls), 2, "两集应该两次调用")

    def test_each_batch_is_told_which_episode_it_is(self):
        self._run()
        self.assertIn(f"只做 {EP1}", self.llm.calls[0])
        self.assertIn(f"只做 {EP2}", self.llm.calls[1])

    def test_each_batch_only_carries_its_own_script(self):
        """★ 每批都发全剧剧本就等于没分批 —— 输入那一半照样超。"""
        self._run()
        self.assertNotEqual(self.llm.calls[0], self.llm.calls[1])

    def test_the_second_batch_sees_what_the_first_one_did(self):
        """★ 分批最大的风险：后面几集看不到前面排了什么。

        看不到的后果全都不报错 —— 编号重头开始、entry_state 接不上、
        前面留的伏笔再也没人收。
        """
        self._run()
        self.assertIn("已经排好的场次", self.llm.calls[1])
        self.assertIn("SC01", self.llm.calls[1])

    def test_the_first_batch_says_there_is_nothing_before_it(self):
        self._run()
        self.assertIn("第一批", self.llm.calls[0])

    def test_every_episode_lands_in_one_product(self):
        out = self._run()
        self.assertEqual({s["episode"] for s in out["scenes"]}, {EP1, EP2})
        saved = self.pj.stage_data("n3_narrative", "")
        self.assertEqual({s["episode"] for s in saved["scenes"]}, {EP1, EP2})

    def test_a_break_halfway_keeps_the_finished_episodes(self):
        """★ 断在第二集，第一集必须留下 —— 不然分批毫无意义。"""
        class Flaky(Batch3LLM):
            def json_call(self, system, user, **kw):
                if f"只做 {EP2}" in user:
                    raise RuntimeError("这一集炸了")
                return super().json_call(system, user, **kw)

        with self.assertRaises(RuntimeError):
            self._run(Flaky())
        saved = self.pj.stage_data("n3_narrative", "")
        self.assertEqual({s["episode"] for s in saved["scenes"]}, {EP1})

    def test_running_again_only_does_what_is_left(self):
        """★ 续跑：接着第二集，不重排第一集。"""
        self.pj.save_stage("n3_narrative",
                           {"scenes": [{"scene_id": "SC01", "episode": EP1}]}, "")
        self._run()
        self.assertEqual(len(self.llm.calls), 1, "第一集不该再排一遍")
        self.assertIn(f"只做 {EP2}", self.llm.calls[0])

    def test_everything_done_means_no_call_at_all(self):
        """全排过了就别再花钱。"""
        self.pj.save_stage("n3_narrative", {"scenes": [
            {"scene_id": "SC01", "episode": EP1},
            {"scene_id": "SC02", "episode": EP2}]}, "")
        self._run()
        self.assertEqual(self.llm.calls, [])

    def test_a_project_without_episodes_falls_back_to_one_call(self):
        """★ 分批是为了跑得过去，不是强制换做法。老项目不能因此跑不动。"""
        from core import episodes as _eps
        self.pj.save_stage(_eps.FILE[:-5], {"episodes": []})
        self._run()
        self.assertEqual(len(self.llm.calls), 1)
        self.assertIn("一次处理全剧", self.llm.calls[0])


# ====================================================================== n4b

class Worklist4bTests(unittest.TestCase):
    """发过去的那份清单：这一批全量，引用到的全量，其余一行。"""

    def setUp(self):
        self.pj = _series_project()

    def tearDown(self):
        shutil.rmtree(self.pj.root, ignore_errors=True)

    def _assets(self, n, **over):
        rows = []
        for i in range(1, n + 1):
            a = {"asset_id": f"A{i:03d}", "family": "PH", "name": f"n{i}",
                 "decision": "generate", "appearance": "x" * 50,
                 "reference_assets": [], "parent_asset_id": ""}
            a.update(over.get(f"A{i:03d}", {}))
            rows.append(a)
        self.pj.save_stage("n4_assets", {"assets": rows, "production_order": []}, "")
        return self.pj.stage_data("n4_assets", "")

    def test_a_referenced_asset_keeps_its_appearance(self):
        """★ 只给 ID 的话模型只能自己编一个外观 —— 跨集人脸就崩了，且不报错。"""
        a4 = self._assets(20, **{"A001": {"reference_assets": ["A020"]}})
        w = R._n4b_worklist(self.pj, a4, batch={"A001"})
        done = {a["asset_id"]: a for a in w["assets_already_done"]}
        self.assertIn("appearance", done["A020"], "被引用的那条要留全量")
        self.assertNotIn("appearance", done["A002"], "没被引用的只留一行")

    def test_a_parent_asset_keeps_its_appearance_too(self):
        a4 = self._assets(5, **{"A001": {"parent_asset_id": "A005"}})
        w = R._n4b_worklist(self.pj, a4, batch={"A001"})
        done = {a["asset_id"]: a for a in w["assets_already_done"]}
        self.assertIn("appearance", done["A005"])

    def test_a_referenced_asset_is_not_asked_to_be_rewritten(self):
        """★ 混进 assets[] 会被当成这一批的活：白写一遍，还覆盖定稿。"""
        a4 = self._assets(20, **{"A001": {"reference_assets": ["A020"]}})
        w = R._n4b_worklist(self.pj, a4, batch={"A001"})
        self.assertEqual([a["asset_id"] for a in w["assets"]], ["A001"])


class Run4bTests(unittest.TestCase):

    def setUp(self):
        self.pj = _series_project()
        self.pj.save_stage("n3_narrative", copy.deepcopy(_FIXTURES["n3"]), "")
        self.llm = Batch4bLLM()

    def tearDown(self):
        shutil.rmtree(self.pj.root, ignore_errors=True)

    def _assets(self, n):
        self.pj.save_stage("n4_assets", {"assets": [
            {"asset_id": f"A{i:03d}", "family": "PH", "name": f"n{i}",
             "decision": "generate", "appearance": "x" * 50,
             "reference_assets": [], "parent_asset_id": ""}
            for i in range(1, n + 1)], "production_order": []}, "")

    def _run(self, llm=None):
        return R.run_n4b_batched(self.pj, llm=llm or self.llm, params=PARAMS,
                                 log=lambda *a: None)

    def test_it_splits_into_batches(self):
        """★ 30 个资产不该挤在一次调用里。"""
        self._assets(30)
        self._run()
        self.assertEqual(len(self.llm.calls), 3, f"每批 {R._N4B_BATCH} 个")

    def test_a_small_project_still_takes_one_call(self):
        self._assets(3)
        self._run()
        self.assertEqual(len(self.llm.calls), 1)

    def test_each_batch_only_asks_for_its_own_assets(self):
        """★ 每批都发全量清单 = 没分批。"""
        self._assets(30)
        self._run()
        for user in self.llm.calls:
            self.assertLessEqual(len(Batch4bLLM._asked(user)), R._N4B_BATCH)

    def test_all_of_them_get_written_in_the_end(self):
        self._assets(30)
        out = self._run()
        self.assertEqual(len({p["asset_id"] for p in out["asset_prompts"]}), 30)

    def test_a_break_halfway_keeps_the_finished_batches(self):
        """★ 最后一批炸了，前两批必须留下。"""
        self._assets(30)

        class Flaky(Batch4bLLM):
            def json_call(self, system, user, **kw):
                if len(self.calls) >= 2:
                    raise RuntimeError("这一批炸了")
                return super().json_call(system, user, **kw)

        with self.assertRaises(RuntimeError):
            self._run(Flaky())
        saved = self.pj.stage_data("n4b_asset_prompts", "") or {}
        self.assertEqual(len(saved.get("asset_prompts") or []), R._N4B_BATCH * 2)

    def test_running_again_only_writes_what_is_missing(self):
        """★ 续跑不重写已经定稿的 —— 重写会让同一个角色前后两版。"""
        self._assets(30)
        self._run()
        self.llm.calls.clear()
        self._run()
        self.assertEqual(self.llm.calls, [])


if __name__ == "__main__":
    unittest.main()
