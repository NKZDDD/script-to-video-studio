# -*- coding: utf-8 -*-
"""V3.4 执行层：拿假模型把 n1 → n14 整条跑一遍。

这个测试的价值不在「跑通了」，在于它钉住几件靠肉眼看不出来的事：

  · 依赖按范围去正确的目录取 —— 取错了拿到空字典，模板里那个占位符变成
    `{}`，模型看到空输入通常会自己编而不是报错。
  · 逐集环节只吃本集正文 —— 发全剧的话 40 集要发 40 遍，钱翻几十倍，
    而且模型会拿别的集的情节填这一集。
  · 逐段环节按段存盘、一段失败不毒掉整集、重跑只补没做的。
  · 产物落在对的地方：全剧级在项目根，逐集/逐段在集目录。
"""
import copy
import os
import re
import shutil
import tempfile
import unittest

from core import run_v34 as R, system_v34 as V
from core.store import Project, write_text

SCRIPT = ("第一集开场\n甲走进病房。\n乙拿出文件。\n"
          "第二集开场\n甲在雨里跌倒。\n乙冷笑。\n")


class FakeLLM:
    """按环节必需字段现编一份合法产物，并记下每次实际发出去的正文。"""

    model = "fake"

    def __init__(self):
        self.sent = []          # (stage_id, user)
        # (环节id, 集号) → 这一次调用抛错。按 id 判，别在正文里搜关键词猜 ——
        # 模板之间互相引用（n4 里就写着「见第五环节的 SPATIAL」），
        # 关键词匹配会误伤别的环节。
        self.fail_on = set()

    def json_call(self, system, user, required=None, log=None, cancel=None,
                  on_usage=None, **kw):
        stage = self._which(user)
        self.sent.append((stage, user))
        for sid, ep in self.fail_on:
            if stage == sid and (not ep or f"只处理 {ep}" in user
                                 or f"只做这一段】{ep}" in user):
                raise RuntimeError(f"{sid} 在 {ep} 上炸了（测试注入）")
        if on_usage:
            # 真的 LLM 会回传用量。假的不回传的话，「每次调用都记账」
            # 那条测试就变成在测夹具而不是测代码。
            on_usage({"model": self.model, "prompt_tokens": 100,
                      "completion_tokens": 50, "seconds": 0.1})
        # 必须深拷贝：浅拷贝时两段共用同一个内层 dict，
        # 后一段写进去的 seg_id 会把前一段的覆盖掉 —— 看着像「只跑了一段」。
        return copy.deepcopy(_FIXTURES[stage])

    def user_for(self, stage_id):
        """某个环节最后一次实际发出去的正文。"""
        hit = [u for s, u in self.sent if s == stage_id]
        assert hit, f"{stage_id} 没被调用过"
        return hit[-1]

    @staticmethod
    def _which(user):
        """从正文里认出这是哪个环节 —— 靠模板标题，不靠调用顺序。"""
        # 标题可能带后缀，比如「第四环节（下）｜资产生产提示词编译」，
        # 所以只认 ｜ 后面那截，不去猜前面写了什么。
        m = re.search(r"^#[^｜\n]*｜(.+)$", user, re.M)
        title = m.group(1).strip() if m else ""
        for sid, (tpl, _, _) in V.LLM_SPEC.items():
            head = open(os.path.join(_PROMPTS, tpl + ".md"),
                        encoding="utf-8").readline()
            if title and title in head:
                return sid
        raise AssertionError(f"认不出这是哪个环节：{user[:80]!r}")


_PROMPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "prompts")

EP1, EP2 = "EP01", "EP02"
SEGS = [f"{EP1}-SEG01", f"{EP1}-SEG02"]

_FIXTURES = {
    "n1": {"project_name": "t", "entities": [{"entity_id": "E001", "aliases": ["甲"]}],
           "events": [{"event_id": "EV1", "story_time": "d1"}],
           "story_truth": {"objective_facts": ["x"]},
           "reality_threads": [{"thread_id": "RT_MAIN"}],
           "episode_ranges": [
               {"episode": EP1, "start_anchor": "第一集开场", "duration_sec": 30},
               {"episode": EP2, "start_anchor": "第二集开场", "duration_sec": 30}],
           "visual_tone": {"compressed": "冷白"}},
    "n2": {"characters": [{"character_id": "E001", "long_term_motive": "m",
                           "relationships": [], "arc": "a",
                           "physical_limits": [], "performance_boundary": "b"}],
           "world_rules": [], "cultural_rules": {}},
    # 叙事结构现在是全剧一次做完的，所以假产物也要横跨两集 ——
    # 只给一集的话，「裁成本集」那条测试永远是绿的（裁不裁都一样）。
    "n3": {"scope": "full_series",
           "scenes": [{"scene_id": "SC01", "episode": "EP01", "objective": "o",
                       "turn": "t", "outcome": "r", "entry_state": "e",
                       "exit_state": "x"},
                      {"scene_id": "SC02", "episode": "EP02", "objective": "o",
                       "turn": "t", "outcome": "r", "entry_state": "e",
                       "exit_state": "x"}],
           "beats": [{"beat_id": "SC01-B1", "scene_id": "SC01",
                      "meaningful_change": "c", "change_kind": "knowledge",
                      "state_delta": "d", "shot_need": "n"},
                     {"beat_id": "SC02-B1", "scene_id": "SC02",
                      "meaningful_change": "c", "change_kind": "knowledge",
                      "state_delta": "d", "shot_need": "n"}]},
    "n4": {"assets": [{"asset_id": "C001", "family": "CHAR", "name": "甲",
                       "decision": "must", "decision_reason": "r",
                       "parent_asset_id": "", "reference_assets": [],
                       "identity_anchors": "a", "appearance": "p",
                       "output_spec": "four_view", "dependency_order": 1}],
           "costume_contracts": [], "prop_specs": [], "prop_instances": [],
           "production_order": ["C001"]},
    "n4b": {"asset_prompts": [
        {"asset_id": "C001", "filename": "C001_PROMPT.txt", "family": "CHAR",
         "output_spec": "four_view", "size": "1024x1536",
         "reference_assets": [], "reference_role_map": [],
         "prompt": "资产名称：甲。四视图。"}]},
    "n5": {"spatial_masters": [{"spatial_id": "SP001", "world_origin": "o",
                                "axis": {}, "unit": "meter", "zones": [],
                                "anchors": [], "routes": [], "landmarks": [],
                                "fixed_structures": []}],
           "loc_views": []},
    "n6": {"ledger": [{"event_id": "EV1", "affected_entity": "C001",
                       "state_dimension": "外观", "result_value": "v",
                       "activation_event": "a", "persistence_class": "CROSS_SCENE"}]},
    "n7": {"scene_directing": [], "beat_directing": [],
           "blocking": [{"character_id": "C001", "zone": "A", "anchor": "X",
                         "root_xyz": [0, 0, 0], "body_orientation_yaw": 0}],
           "performance_intent": []},
    "n8": {"cvs": [{"cvs_id": "CVS1", "story_time": "d1", "location_id": "S001",
                    "characters": [], "props": [], "relational_blocking": [],
                    "forbidden_state": []}],
           "vt": [{"vt_id": "VT1", "source_cvs": "CVS1", "target_cvs": "CVS1",
                   "trigger_event": "EV1", "irreversible_result": "r"}]},
    "n9": {"shots": [{"shot_id": "SH1", "source_cvs": "CVS1", "shot_size": "中景",
                      "camera_position_xyz": [0, 0, 0], "screen_direction": "左",
                      "estimated_duration": 2.0, "dramatic_function": "f"}],
           "transitions": [{"transition_id": "TR1", "from_shot": "SH1",
                            "to_shot": "SH1", "mechanism": "NATIVE_CUT",
                            "cinematic_grammar": "动作匹配",
                            "execution_mode": "MODEL_NATIVE_ONLY"}],
           "timing_plan": []},
    "n10": {"segs": [{"seg_id": s, "duration": 15, "included_shots": ["SH1"],
                      "entry_cvs": "CVS1", "exit_cvs": "CVS1",
                      "model_native_transition_ids": ["TR1"],
                      "boundary_rationale": "b"} for s in SEGS]},
    "n11": {"scstates": [{"scstate_id": "SCST1", "source_cvs": "CVS1",
                          "reference_assets": ["C001"], "prompt": "p"}]},
    "n12": {"sbpkg": [{"sbpkg_id": "PKG", "kf": [], "reference_order": [],
                       "storyboard_prompt": "p"}]},
    "n13": {"video_plan": [{"seg_id": SEGS[0], "windows": [],
                            "reference_order": [], "video_prompt": "p"}]},
    "n14": {"findings": []},
}

PARAMS = {"project_code": "V34", "duration": 15, "image_size": "1024x1536",
          "script": SCRIPT}


def new_project():
    root = tempfile.mkdtemp(prefix="v34-run-")
    pj = Project(root)
    pj.init_dirs()
    pj.save_meta({"project_code": "V34", "episode": EP1, "params": {}})
    write_text(pj.p("01_剧本与分段", "原始剧本.txt"), SCRIPT)
    return pj


class RunTests(unittest.TestCase):

    def setUp(self):
        self.pj = new_project()
        self.llm = FakeLLM()

    def tearDown(self):
        shutil.rmtree(self.pj.root, ignore_errors=True)

    def _run_series_and_episode(self, ep):
        quiet = lambda *a, **k: None
        # 全剧层：叙事结构、资产、空间、总账都在这里一次做完
        for sid in ("n1", "n2", "n3", "n4", "n4b", "n5", "n6"):
            R.run_stage(self.pj, sid, llm=self.llm, params=PARAMS, log=quiet)
        # 逐集层：从导演设计开始才分集
        for sid in ("n7", "n8", "n9", "n10"):
            R.run_stage(self.pj, sid, llm=self.llm, params=PARAMS,
                        episode=ep, log=quiet)

    # ---------------------------------------------------------------- 基本跑通

    def test_full_chain_runs_and_lands_in_the_right_place(self):
        """★ 全剧级产物在项目根，逐集的在集目录。放错了下游取到空字典。"""
        self._run_series_and_episode(EP1)
        self.assertTrue(os.path.isfile(self.pj.stage_path("n1_truth")),
                        "全剧级产物没落在项目根")
        self.assertFalse(os.path.isfile(self.pj.stage_path("n1_truth", EP1)))
        # 资产表、空间主表、连续性总账都改成全剧一份了（V5.6：唯一 Ledger）
        self.assertTrue(os.path.isfile(self.pj.stage_path("n4_assets")),
                        "资产表没落在项目根 —— 它是全剧一份的")
        self.assertFalse(os.path.isfile(self.pj.stage_path("n4_assets", EP1)))
        self.assertTrue(os.path.isfile(self.pj.stage_path("n7_directing", EP1)),
                        "逐集产物没落在集目录")
        self.assertFalse(os.path.isfile(self.pj.stage_path("n7_directing")))

    def test_episode_split_happens_right_after_n1(self):
        R.run_stage(self.pj, "n1", llm=self.llm, params=PARAMS, log=lambda *a: None)
        from core import episodes as _eps
        self.assertEqual(_eps.ids(self.pj), [EP1, EP2])

    # ---------------------------------------------------------------- 依赖

    def test_missing_dependency_is_refused_with_a_readable_name(self):
        """缺前置直接停，并说清缺的是哪个环节 —— 不是「KeyError」。"""
        with self.assertRaises(RuntimeError) as cm:
            R.run_stage(self.pj, "n4", llm=self.llm, params=PARAMS, episode=EP1,
                        log=lambda *a: None)
        self.assertIn("前置还没跑", str(cm.exception))
        self.assertIn("环节", str(cm.exception))

    def test_dependencies_are_read_from_the_right_scope(self):
        """★ 全剧级依赖不带集号去取，逐集级带 —— 取错就是空字典。"""
        self._run_series_and_episode(EP1)
        data = R.deps_data(self.pj, "n4", EP1)
        self.assertTrue(data["n1_truth"], "全剧级依赖取成空的了")
        self.assertTrue(data["n3_narrative"], "逐集依赖取成空的了")
        self.assertEqual(data["n1_truth"]["project_name"], "t")

    # ---------------------------------------------------------------- 提示词

    def test_per_episode_stage_only_gets_its_own_episode(self):
        """★ 逐集环节拿到全剧产物时必须裁成本集。

        叙事结构改成全剧一份之后，n7 收到的是**全剧的场次**。
        不裁的话它会把别的集的戏排进这一集 —— 而且不报错。
        """
        self._run_series_and_episode(EP2)
        # 直接看裁剪函数：在提示词正文里 parse JSON 太脆，
        # 一个大括号位置变了断言就碎，测的也不是真正想钉的东西。
        whole = self.pj.stage_data("n3_narrative", "")
        got = R._narrow_episode("n3_narrative", whole, EP2)
        eps = {s.get("episode") for s in got.get("scenes", [])}
        self.assertEqual(eps, {EP2},
                         f"本集的导演设计会收到别的集的场次：{eps}")
        self.assertTrue(whole.get("scenes"), "假模型没产出场次，这条测了个寂寞")
        self.assertLess(len(got["scenes"]), len(whole["scenes"]), "根本没裁")

    def test_series_stage_gets_the_whole_script(self):
        R.run_stage(self.pj, "n1", llm=self.llm, params=PARAMS, log=lambda *a: None)
        user = self.llm.user_for("n1")
        self.assertIn("第一集开场", user)
        self.assertIn("第二集开场", user)

    def test_no_unfilled_placeholders_go_out(self):
        """★ 填不上的占位符会原样发出去，模型看到大括号会假装那里有内容。"""
        self._run_series_and_episode(EP1)
        for _, user in self.llm.sent:   # (stage, user)
            left = re.findall(r"\{\{(\w+)\}\}", user)
            self.assertEqual(left, [], f"这些占位符没填上就发出去了：{left}")

    def test_params_whitelist_keeps_junk_out_of_the_prompt(self):
        """config 里删掉的旧旋钮还留着时，不许跟着发给模型当指令。"""
        dirty = dict(PARAMS, shots_min=5, concurrency=9, r2_secret="绝不能外泄")
        R.run_stage(self.pj, "n1", llm=self.llm, params=dirty, log=lambda *a: None)
        user = self.llm.user_for("n1")
        head = user.split("【完整剧本】")[0]
        for k in ("shots_min", "concurrency", "绝不能外泄"):
            self.assertNotIn(k, head, f"{k} 漏进提示词了")

    # ---------------------------------------------------------------- 逐段

    def test_segment_stage_runs_once_per_segment(self):
        self._run_series_and_episode(EP1)
        before = len(self.llm.sent)
        R.run_segment_stage(self.pj, "n11", llm=self.llm, params=PARAMS,
                            episode=EP1, log=lambda *a: None)
        self.assertEqual(len(self.llm.sent) - before, len(SEGS),
                         "逐段环节的调用次数应该等于段数")
        got = R.done_segments(self.pj, "n11", EP1)
        self.assertEqual(got, set(SEGS))

    def test_segment_prompt_says_which_segment(self):
        """★ 不说清只做哪一段，模型会把整集的段都编一遍。"""
        self._run_series_and_episode(EP1)
        R.run_segment_stage(self.pj, "n11", llm=self.llm, params=PARAMS,
                            episode=EP1, log=lambda *a: None)
        users = [u for s, u in self.llm.sent if s == "n11"]
        self.assertIn(SEGS[0], users[0])
        self.assertIn("只做这一段", users[0])
        self.assertNotIn(SEGS[1], users[0].split("【只做这一段】")[-1])

    def test_segment_context_is_narrowed_to_this_segment(self):
        """★ 逐段环节不许把整集的按段产物全发过去。

        不裁的话，做 SEG01 会把这一集全部段落的装箱、场景状态图、故事板一起发：
        一集十几段就是十几倍输入，钱翻几倍；更糟的是模型会串段 ——
        看到别的段的内容，把那边的动作写进这一段。
        """
        self._run_series_and_episode(EP1)
        R.run_segment_stage(self.pj, "n11", llm=self.llm, params=PARAMS,
                            episode=EP1, log=lambda *a: None)
        first = [u for s, u in self.llm.sent if s == "n11"][0]
        segs_block = first.split("【本集 SEG 装箱（第十环节）】")[-1]
        self.assertIn(SEGS[0], segs_block)
        self.assertNotIn(SEGS[1], segs_block, "把别的段的装箱也发过去了")

    def test_episode_wide_context_is_not_narrowed(self):
        """反过来：整集共享的资产表、空间主表不该被裁 —— 那是这一段要用的上下文。"""
        self._run_series_and_episode(EP1)
        R.run_segment_stage(self.pj, "n11", llm=self.llm, params=PARAMS,
                            episode=EP1, log=lambda *a: None)
        first = [u for s, u in self.llm.sent if s == "n11"][0]
        self.assertIn("C001", first, "资产表被裁没了")
        self.assertIn("SP001", first, "空间主表被裁没了")

    def test_rerun_only_fills_the_missing_segments(self):
        """★ 续跑不重复花钱：做过的段跳过。"""
        self._run_series_and_episode(EP1)
        R.run_segment_stage(self.pj, "n11", llm=self.llm, params=PARAMS,
                            episode=EP1, log=lambda *a: None)
        n = len(self.llm.sent)
        R.run_segment_stage(self.pj, "n11", llm=self.llm, params=PARAMS,
                            episode=EP1, log=lambda *a: None)
        self.assertEqual(len(self.llm.sent), n, "已完成的段又跑了一遍")

    def test_one_bad_segment_does_not_poison_the_episode(self):
        """★ 一段失败只影响那一段，其余照常存盘。"""
        self._run_series_and_episode(EP1)

        boom = SEGS[1]
        real = self.llm.json_call

        def flaky(system, user, **kw):
            if f"【只做这一段】{boom}" in user:
                raise RuntimeError("这一段炸了")
            return real(system, user, **kw)

        self.llm.json_call = flaky
        _, failed, cancelled = R.run_segment_stage(
            self.pj, "n11", llm=self.llm, params=PARAMS, episode=EP1,
            log=lambda *a: None)
        self.assertEqual(failed, [boom])
        self.assertEqual(cancelled, [])
        self.assertEqual(R.done_segments(self.pj, "n11", EP1), {SEGS[0]},
                         "好的那一段没存下来")

    def test_segment_stage_refuses_when_there_are_no_segments(self):
        self._run_series_and_episode(EP1)
        self.pj.save_stage("n10_segs", {"segs": []}, EP1)
        with self.assertRaises(RuntimeError) as cm:
            R.run_segment_stage(self.pj, "n11", llm=self.llm, params=PARAMS,
                                episode=EP1, log=lambda *a: None)
        self.assertIn("第十环节", str(cm.exception))

    def test_scope_mixups_are_refused(self):
        with self.assertRaises(ValueError):
            R.run_stage(self.pj, "n11", llm=self.llm, params=PARAMS, episode=EP1)
        with self.assertRaises(ValueError):
            R.run_segment_stage(self.pj, "n4", llm=self.llm, params=PARAMS,
                                episode=EP1)

    # ---------------------------------------------------------------- 记账

    def test_every_call_is_billed(self):
        """跑完不知道花了多少，是这类流水线最容易失控的地方。"""
        from core import ledger
        self._run_series_and_episode(EP1)
        rows = [r for r in ledger.load(self.pj.root) if r.get("kind") == "llm"]
        per = {}
        for r in rows:
            per[r["stage"]] = per.get(r["stage"], 0) + 1
        # n3 是**按集分批**的：两集 = 两次调用 = 两条账。
        # 这里不写死总数 —— 写死的话，以后夹具多加一集就得来改这个数字，
        # 而真正要守的是「每一次调用都记了账」，不是「一共 11 次」。
        want = {sid: 1 for sid in ("n1", "n2", "n4", "n4b", "n5", "n6",
                                   "n7", "n8", "n9", "n10")}
        want["n3"] = len(_FIXTURES["n1"]["episode_ranges"])
        self.assertEqual(per, want, "有调用没记账，或者分批次数不对")



class EmptyScriptTests(unittest.TestCase):
    """★ 剧本没进去的时候必须停，而且要说对是哪儿的问题。

    真实故障：环节1 的提示词 3246 字，其中 3177 字是模板本身 ——
    剧本压根没进去。模型照着空输入吐了 215 token 的空壳，
    缺 `characters[]`，JSON 校验重试两次，三次调用全废。

    最糟的不是白花钱，是**报错指错了方向**：诊断给的是
    「拆剧本的模型没按格式回答 → 换个更强的模型 / 剧本太长先拆开」，
    而真实情况正好相反。照那条建议换十个模型也一样。
    """

    def setUp(self):
        self.pj = new_project()
        self.llm = FakeLLM()

    def tearDown(self):
        shutil.rmtree(self.pj.root, ignore_errors=True)

    def _params(self, script):
        return dict(PARAMS, script=script)

    def test_an_empty_script_stops_before_spending_anything(self):
        with self.assertRaises(RuntimeError) as cm:
            R.run_stage(self.pj, "n1", llm=self.llm, params=self._params(""),
                        log=lambda *a, **k: None)
        self.assertIn("剧本", str(cm.exception))
        self.assertEqual(self.llm.sent, [], "已经把空提示词发出去了")

    def test_a_whitespace_only_script_counts_as_empty(self):
        for blank in ("   ", "\n\n\t", ""):
            with self.assertRaises(RuntimeError):
                R.run_stage(self.pj, "n1", llm=self.llm,
                            params=self._params(blank), log=lambda *a, **k: None)

    def test_the_message_does_not_send_people_to_swap_models(self):
        """★ 这条报错的全部价值就是指对方向。"""
        with self.assertRaises(RuntimeError) as cm:
            R.run_stage(self.pj, "n1", llm=self.llm, params=self._params(""),
                        log=lambda *a, **k: None)
        msg = str(cm.exception)
        self.assertIn("原始剧本.txt", msg)
        from core import diagnose as D
        self.assertEqual(D.code_of(msg), "SCRIPT_EMPTY")
        entry = D.CATALOG["SCRIPT_EMPTY"]
        self.assertTrue(any("不要" in f and "更强的模型" in f
                            for f in entry["fix"]),
                        "没写清「别去换模型」—— 那正是上次被误导的方向")

    def test_stages_that_do_not_use_the_script_are_unaffected(self):
        """只查真的用到 {{SCRIPT}} 的环节，别拦住不相干的。"""
        self.assertTrue(R.needs_script("n1"))
        self.assertTrue(R.needs_script("n3"))
        for sid in ("n2", "n4", "n5", "n6", "n7"):
            self.assertFalse(R.needs_script(sid), sid)
        R.check_inputs(self.pj, "n2", self._params(""))     # 不该抛

    def test_a_real_script_passes_the_check(self):
        R.check_inputs(self.pj, "n1", self._params(SCRIPT))

if __name__ == "__main__":
    unittest.main()
