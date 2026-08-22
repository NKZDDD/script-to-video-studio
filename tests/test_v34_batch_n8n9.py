# -*- coding: utf-8 -*-
"""V3.4 第八/九环节按场分批的专项测试。

分批要钉住的事，每一条都对应一种**不报错的错**：

  · 合并不覆盖 —— 每批只写自己那几场，直接存盘会把前面几批冲掉。
  · 断点续跑 —— 每批落盘，再点一次只补没做的那几场（不调模型、不花钱）。
  · 编号不撞 —— 模型每批从 001 重编，程序改成全局续号
    （和 n3 并发时各集都从 SC01 编起是同一个坑）。
  · 时间轴偏移 —— 每批从 0 铺起，合并时程序加偏移；模型自己从偏移
    接着铺的（不听话但没错）不二次偏移。
  · 跨批引用 —— PREV_CVS / LAST_SHOT 给模型的是已落盘的真实编号，
    合并改号时不能把指向旧行的引用改走。
  · 输入裁剪 —— 导演设计 / CVS 只发本批那几场的，别的场不发
    （发全量的话分批只省了输出，输入还是超）。
"""
import copy
import shutil
import tempfile
import unittest

from core import pipeline_v34 as P, run_v34 as R
from core.store import Project

EP1 = "EP01"
SCENES = ["SC01", "SC02", "SC03", "SC04"]
PARAMS = {"project_code": "V34", "duration": 15, "image_size": "1024x1536"}


def quiet(*a, **k):
    pass


class QueueLLM:
    """按调用顺序吐排好的产物，记下每次发出去的正文。

    不像 test_v34_run 的 FakeLLM 按环节现编 —— 分批测试要精确控制
    「第几批吐什么」，特别是模型重启编号这种不听话的行为。
    """

    model = "fake"

    def __init__(self, responses=()):
        self.responses = [copy.deepcopy(r) for r in responses]
        self.sent = []

    def json_call(self, system, user, **kw):
        self.sent.append(user)
        assert self.responses, "多调了一次模型（测试里没排这一次的产物）"
        return copy.deepcopy(self.responses.pop(0))


def cvs(cid, scene, mark=""):
    return {"cvs_id": cid, "scene_id": scene, "story_time": "d1" + mark,
            "location_id": "S001", "characters": [], "props": [],
            "relational_blocking": [], "forbidden_state": []}


def n8_batch(scenes, prev_cid=None, mark=""):
    """第八环节一批的假产物：每场一条 CVS，跨场 VT 归后一批写。"""
    rows = [cvs(f"CVS_{EP1}_{s}_01", s, mark) for s in scenes]
    vt = []
    if prev_cid:                            # 跨场 VT：上一批末尾 → 本批第一条
        vt.append({"vt_id": "VT_x", "source_cvs": prev_cid,
                   "target_cvs": f"CVS_{EP1}_{scenes[0]}_01",
                   "trigger_event": "EV1", "irreversible_result": "r"})
    for a, b in zip(scenes, scenes[1:]):    # 批内场与场之间的 VT
        vt.append({"vt_id": "VT_y", "source_cvs": f"CVS_{EP1}_{a}_01",
                   "target_cvs": f"CVS_{EP1}_{b}_01",
                   "trigger_event": "EV1", "irreversible_result": "r"})
    return {"cvs": rows, "vt": vt,
            "camera_free_check": f"第{mark}批没有镜头字段"}


def shot(sid, scene, cvs_id):
    return {"shot_id": sid, "scene_id": scene, "source_cvs": cvs_id,
            "shot_size": "中景", "camera_position_xyz": [0, 0, 0],
            "screen_direction": "左", "estimated_duration": 7.5,
            "dramatic_function": "f"}


def tr(tid, a, b):
    return {"transition_id": tid, "from_shot": a, "to_shot": b,
            "mechanism": "NATIVE_CUT", "cinematic_grammar": "动作匹配",
            "execution_mode": "MODEL_NATIVE_ONLY"}


def timing(sid, start, end, tr_id=""):
    row = {"shot_id": sid, "start": start, "end": end}
    if tr_id:
        row["outgoing_transition_id"] = tr_id
    return row


def n9_first_window():
    """第九环节第一批（SC01/SC02，15 秒时窗）：模型守规矩从 001 编起。"""
    return {"shots": [shot("SH_EP01_001", "SC01", "CVS_EP01_SC01_01"),
                      shot("SH_EP01_002", "SC02", "CVS_EP01_SC02_01")],
            "transitions": [tr("TR_EP01_001", "SH_EP01_001", "SH_EP01_002")],
            "timing_plan": [timing("SH_EP01_001", 0.0, 7.5, "TR_EP01_001"),
                            timing("SH_EP01_002", 7.5, 15.0)],
            "shot_count_rationale": "第一批：两场各一镜",
            "transition_summary": "第一批：一个切"}


def n9_second_window_restarted():
    """第九环节第二批（SC03/SC04）：模型**重启编号**（又从 001 起），
    跨批转场的 from_shot 用上一批最后一镜的真实编号。"""
    return {"shots": [shot("SH_EP01_001", "SC03", "CVS_EP01_SC03_01"),
                      shot("SH_EP01_002", "SC04", "CVS_EP01_SC04_01")],
            "transitions": [tr("TR_EP01_001", "SH_EP01_002", "SH_EP01_001")],
            "timing_plan": [timing("SH_EP01_001", 0.0, 7.5, "TR_EP01_001"),
                            timing("SH_EP01_002", 7.5, 15.0)],
            "shot_count_rationale": "第二批：两场各一镜",
            "transition_summary": "第二批：一个切"}


def n9_last_window():
    """第九环节收尾批（只剩 SC04）：重启编号，跨批转场用真实的 SH_EP01_003。"""
    return {"shots": [shot("SH_EP01_001", "SC04", "CVS_EP01_SC04_01")],
            "transitions": [tr("TR_EP01_001", "SH_EP01_003", "SH_EP01_001")],
            "timing_plan": [timing("SH_EP01_001", 0.0, 7.5, "TR_EP01_001")],
            "shot_count_rationale": "收尾批：一场一镜",
            "transition_summary": "收尾批：一个切"}


def seeded(scenes=SCENES, duration=30):
    """直接把上游产物写到盘上 —— 这里测分批驱动，不重跑全链路。"""
    root = tempfile.mkdtemp(prefix="v34-batch-")
    pj = Project(root)
    pj.init_dirs()
    pj.save_meta({"project_code": "V34", "episode": EP1, "params": {}})
    pj.save_stage("n3_narrative", {
        "scenes": [{"scene_id": s, "episode": EP1, "objective": "o",
                    "turn": "t", "outcome": "r", "entry_state": "e",
                    "exit_state": "x"} for s in scenes],
        "beats": [{"beat_id": f"{s}-B1", "scene_id": s,
                   "meaningful_change": "c", "change_kind": "knowledge",
                   "state_delta": "d", "shot_need": "n"} for s in scenes]})
    pj.save_stage("episodes", {"episodes": [
        {"episode": EP1, "duration_sec": duration}]})
    pj.save_stage("n4_assets", {"assets": [{"asset_id": "C001", "name": "甲"}]})
    pj.save_stage("n5_spatial", {"spatial_masters": [{"spatial_id": "SP001"}]})
    pj.save_stage("n6_ledger", {"ledger": [{"event_id": "EV1"}]})
    pj.save_stage("n7_directing", {
        "scene_directing": [{"scene_id": s, "intent": f"走位-{s}"}
                            for s in scenes]}, EP1)
    return pj


def seeded_with_cvs(**kw):
    """第九环节的夹具：上游到 n8 为止都是齐的（每场一条 CVS）。"""
    pj = seeded(**kw)
    pj.save_stage("n8_cvs", {
        "cvs": [cvs(f"CVS_{EP1}_{s}_01", s) for s in SCENES],
        "vt": [], "camera_free_check": "ok"}, EP1)
    return pj


class TempCase(unittest.TestCase):

    def setUp(self):
        self.pj = seeded()
        self.llm = QueueLLM()

    def tearDown(self):
        shutil.rmtree(self.pj.root, ignore_errors=True)


# ================================================================ 纯函数

class CvsSceneTests(unittest.TestCase):

    def test_reads_scene_field_first_then_digs_the_id(self):
        """scene_id 不是必需字段，模型经常不写 —— 编号里的 SC 号是兜底。
        只认字段的话老产物全部归不上场，分批续跑每场都当没做过。"""
        self.assertEqual(R._cvs_scene({"scene_id": "SC02"}), "SC02")
        self.assertEqual(R._cvs_scene({"cvs_id": "CVS_EP01_SC03_01"}), "SC03")
        self.assertIsNone(R._cvs_scene({"cvs_id": "CVS1"}))
        self.assertIsNone(R._cvs_scene(None))

    def test_scene_hit_accepts_bare_and_prefixed_ids(self):
        """n3 并发后场次号带集号前缀，模型在编号里常只写裸 SC 号。
        have 永远来自本集的产物（SC 号本集内唯一），跨集不在此列。"""
        self.assertTrue(R._scene_hit("EP01-SC01", {"SC01"}))
        self.assertTrue(R._scene_hit("SC01", {"EP01-SC01"}))
        self.assertTrue(R._scene_hit("EP01-SC01", {"EP01-SC02", "SC01"}))


class MergeCvsTests(unittest.TestCase):

    def test_merges_instead_of_overwriting(self):
        """★ 这一批只写自己那几场，直接存盘会把前面几批冲掉。
        prev 先过一遍合并 —— 真实流程里上一批的 VT 编号已经续过号。"""
        prev = R.merge_cvs({}, n8_batch(["SC01", "SC02"], mark="-1"), EP1)
        fresh = n8_batch(["SC03", "SC04"], prev_cid="CVS_EP01_SC02_01",
                         mark="-2")
        merged = R.merge_cvs(prev, fresh, EP1)
        self.assertEqual(len(merged["cvs"]), 4)
        self.assertEqual(len(merged["vt"]), 3)
        # VT 编号全局续号：每批都从 01 编起必然撞号
        self.assertEqual([v["vt_id"] for v in merged["vt"]],
                         ["VT_EP01_001", "VT_EP01_002", "VT_EP01_003"])
        cross = merged["vt"][1]
        self.assertEqual(cross["source_cvs"], "CVS_EP01_SC02_01")
        self.assertEqual(cross["target_cvs"], "CVS_EP01_SC03_01")

    def test_same_scene_rerun_overwrites_in_place(self):
        prev = n8_batch(["SC01", "SC02"], mark="-1")
        rerun = n8_batch(["SC02", "SC03"], mark="-2")     # SC02 重跑
        merged = R.merge_cvs(prev, rerun, EP1)
        self.assertEqual([c["cvs_id"] for c in merged["cvs"]],
                         ["CVS_EP01_SC01_01", "CVS_EP01_SC02_01",
                          "CVS_EP01_SC03_01"])
        self.assertEqual(merged["cvs"][1]["story_time"], "d1-2")  # 盖成新的

    def test_cross_scene_collision_is_renamed_and_refs_follow(self):
        """撞号且不同场的 CVS 改成带场次号的唯一编号，本批引用跟着改。"""
        prev = {"cvs": [cvs("CVS1", "SC01")], "vt": []}
        fresh = {"cvs": [cvs("CVS1", "SC02")],
                 "vt": [{"vt_id": "VT1", "source_cvs": "CVS1",
                         "target_cvs": "CVS1", "trigger_event": "EV1",
                         "irreversible_result": "r"}]}
        merged = R.merge_cvs(prev, fresh, EP1)
        self.assertEqual([c["cvs_id"] for c in merged["cvs"]],
                         ["CVS1", "CVS1_SC02"])
        self.assertEqual(merged["vt"][0]["source_cvs"], "CVS1_SC02")

    def test_keep_refs_holds_the_reference_to_previous_batch(self):
        """★ keep_refs 里的编号是上一批末尾 CVS 的真实编号（PREV_CVS 给过
        模型），指向它的跨场 VT 引用不能被改号改走。"""
        prev = {"cvs": [cvs("CVS1", "SC01")], "vt": []}
        fresh = {"cvs": [cvs("CVS1", "SC02")],
                 "vt": [{"vt_id": "VT1", "source_cvs": "CVS1",
                         "target_cvs": "CVS_EP01_SC02_01",
                         "trigger_event": "EV1", "irreversible_result": "r"}]}
        merged = R.merge_cvs(prev, fresh, EP1, keep_refs={"CVS1"})
        self.assertEqual(merged["vt"][0]["source_cvs"], "CVS1")

    def test_camera_free_check_accumulates_by_line(self):
        """一句话按行累加 —— 后一批盖掉前批等于把前面的确认丢了。"""
        prev = {"cvs": [], "vt": [], "camera_free_check": "第一批没问题"}
        fresh = {"cvs": [], "vt": [], "camera_free_check": "第二批没问题"}
        merged = R.merge_cvs(prev, fresh, EP1)
        self.assertIn("第一批没问题", merged["camera_free_check"])
        self.assertIn("第二批没问题", merged["camera_free_check"])


class RenumberShiftMergeTests(unittest.TestCase):

    def test_renumber_rewrites_batch_internal_refs(self):
        fresh = {"shots": [shot("SH_EP01_001", "SC03", "CVS_EP01_SC03_01"),
                           shot("SH_EP01_002", "SC04", "CVS_EP01_SC04_01")],
                 "transitions": [tr("TR_EP01_001", "SH_EP01_001",
                                    "SH_EP01_002")],
                 "timing_plan": [timing("SH_EP01_001", 0.0, 7.5, "TR_EP01_001"),
                                 timing("SH_EP01_002", 7.5, 15.0)]}
        R._renumber_batch_shots(fresh, EP1, 3, 2)
        self.assertEqual([s["shot_id"] for s in fresh["shots"]],
                         ["SH_EP01_003", "SH_EP01_004"])
        t = fresh["transitions"][0]
        self.assertEqual((t["transition_id"], t["from_shot"], t["to_shot"]),
                         ("TR_EP01_002", "SH_EP01_003", "SH_EP01_004"))
        self.assertEqual([r["shot_id"] for r in fresh["timing_plan"]],
                         ["SH_EP01_003", "SH_EP01_004"])
        self.assertEqual(fresh["timing_plan"][0]["outgoing_transition_id"],
                         "TR_EP01_002")

    def test_renumber_protects_the_cross_batch_reference(self):
        """★ 模型重启编号时，上一批最后一镜的真实编号会撞上本批旧号。
        跨批转场的 from_shot 指的是旧行 —— protect 保住它；
        本批自己那条的时间行不受 protect 影响（否则会静默丢行）。"""
        fresh = {"shots": [shot("SH_EP01_001", "SC03", "CVS_EP01_SC03_01"),
                           shot("SH_EP01_002", "SC04", "CVS_EP01_SC04_01")],
                 "transitions": [tr("TR_EP01_001", "SH_EP01_002",
                                    "SH_EP01_001")],
                 "timing_plan": [timing("SH_EP01_001", 0.0, 7.5),
                                 timing("SH_EP01_002", 7.5, 15.0)]}
        R._renumber_batch_shots(fresh, EP1, 3, 2, protect={"SH_EP01_002"})
        t = fresh["transitions"][0]
        self.assertEqual(t["from_shot"], "SH_EP01_002")   # 上一批的，保住
        self.assertEqual(t["to_shot"], "SH_EP01_003")     # 本批的，照改
        self.assertEqual([r["shot_id"] for r in fresh["timing_plan"]],
                         ["SH_EP01_003", "SH_EP01_004"])

    def test_shift_moves_absolute_fields_only(self):
        """镜内的相对量（dialogue_start 这类）不挪 —— 挪了反而错。"""
        fresh = {"timing_plan": [
            {"shot_id": "SH_EP01_001", "start": 0.0, "end": 7.5,
             "transition_time_range": [0.0, 0.4],
             "dialogue_start": 0.3, "dialogue_end": 2.0}]}
        R._shift_batch_timing(fresh, 15.0)
        row = fresh["timing_plan"][0]
        self.assertEqual(row["start"], 15.0)
        self.assertEqual(row["end"], 22.5)
        self.assertEqual(row["transition_time_range"], [15.0, 15.4])
        self.assertEqual(row["dialogue_start"], 0.3)

    def test_no_double_shift_when_model_continued_from_offset(self):
        """模型没听、自己从偏移接着铺的（首镜 start ≈ offset）不再加偏移。"""
        fresh = {"timing_plan": [timing("SH_EP01_001", 15.0, 22.5)]}
        R._shift_batch_timing(fresh, 15.0, quiet)
        self.assertEqual(fresh["timing_plan"][0]["start"], 15.0)

    def test_merge_shots_drops_timing_rows_of_foreign_shots(self):
        """模型偶尔会把上一批最后一镜的时间行重写一份（时间坐标是它自己
        批里的）—— 不能盖掉已落盘的那行。"""
        prev = {"shots": [shot("SH_EP01_001", "SC01", "CVS_EP01_SC01_01")],
                "transitions": [],
                "timing_plan": [timing("SH_EP01_001", 0.0, 7.5)],
                "shot_count_rationale": "第一批"}
        fresh = {"shots": [shot("SH_EP01_003", "SC03", "CVS_EP01_SC03_01")],
                 "transitions": [],
                 "timing_plan": [timing("SH_EP01_001", 99.0, 999.0),
                                 timing("SH_EP01_003", 7.5, 15.0)],
                 "shot_count_rationale": "第二批"}
        merged = R.merge_shots(prev, fresh)
        self.assertEqual(len(merged["timing_plan"]), 2)
        row = next(r for r in merged["timing_plan"]
                   if r["shot_id"] == "SH_EP01_001")
        self.assertEqual((row["start"], row["end"]), (0.0, 7.5))
        self.assertIn("第一批", merged["shot_count_rationale"])
        self.assertIn("第二批", merged["shot_count_rationale"])


class BatchPlanningTests(TempCase):

    def test_n8_batches_two_per_batch_and_big_scenes_alone(self):
        """普通的两场一批；六拍以上的大场单独一批（劈成两半接不上）。"""
        self.assertEqual(R._n8_batches(self.pj, SCENES),
                         [["SC01", "SC02"], ["SC03", "SC04"]])
        n3 = self.pj.stage_data("n3_narrative")
        n3["beats"] = ([dict(n3["beats"][0], scene_id="SC02",
                             beat_id=f"SC02-B{i}") for i in range(6)]
                       + [dict(n3["beats"][0], scene_id=s, beat_id=f"{s}-B1")
                          for s in ("SC01", "SC03")])
        self.pj.save_stage("n3_narrative", n3)
        self.assertEqual(R._n8_batches(self.pj, ["SC01", "SC02", "SC03"]),
                         [["SC01"], ["SC02"], ["SC03"]])

    def test_n9_windows_pack_by_estimated_seconds(self):
        """一批约一个 SEG 容器的秒数；没存本集秒数的按两场一批兜底。"""
        batches = R._n9_windows(self.pj, EP1, PARAMS, SCENES)
        self.assertEqual([b for b, _ in batches],
                         [["SC01", "SC02"], ["SC03", "SC04"]])
        self.assertAlmostEqual(batches[0][1], 15.0)
        old = seeded(duration=0)
        try:
            self.assertEqual(R._n9_windows(old, EP1, PARAMS, SCENES),
                             [(["SC01", "SC02"], 0.0),
                              (["SC03", "SC04"], 0.0)])
        finally:
            shutil.rmtree(old.root, ignore_errors=True)

    def test_n8_scene_split_digs_scene_from_cvs_id(self):
        self.pj.save_stage("n8_cvs", {"cvs": [
            cvs("CVS_EP01_SC01_01", ""), cvs("CVS_EP01_SC02_01", ""),
            cvs("CVS_EP01_03_01", "SC03")]}, EP1)   # 前两条没写 scene_id
        done, todo = R.n8_scene_split(self.pj, EP1)
        self.assertEqual(done, ["SC01", "SC02", "SC03"])
        self.assertEqual(todo, ["SC04"])

    def test_n8_scene_split_matches_bare_ids_against_prefixed_scenes(self):
        pj = seeded(scenes=[f"{EP1}-{s}" for s in SCENES])
        try:
            pj.save_stage("n8_cvs", {"cvs": [
                cvs(f"CVS_{EP1}_{s}_01", "") for s in ("SC01", "SC02")]}, EP1)
            done, todo = R.n8_scene_split(pj, EP1)
            self.assertEqual(done, [f"{EP1}-SC01", f"{EP1}-SC02"])
            self.assertEqual(todo, [f"{EP1}-SC03", f"{EP1}-SC04"])
        finally:
            shutil.rmtree(pj.root, ignore_errors=True)

    def test_n9_scene_split_walks_source_cvs_back_to_scene(self):
        """镜头的 scene_id 不是必需字段 —— 沿 source_cvs 对回 CVS 归场。"""
        self.pj.save_stage("n8_cvs", {
            "cvs": [cvs(f"CVS_{EP1}_{s}_01", s) for s in SCENES], "vt": []},
            EP1)
        self.pj.save_stage("n9_shots", {"shots": [
            shot("SH_EP01_001", "", "CVS_EP01_SC01_01"),   # 没写 scene_id
            shot("SH_EP01_002", "SC02", "CVS_EP01_SC02_01")]}, EP1)
        done, todo = R.n9_scene_split(self.pj, EP1)
        self.assertEqual(done, ["SC01", "SC02"])
        self.assertEqual(todo, ["SC03", "SC04"])


# ================================================================ 整链

class N8BatchRunTests(TempCase):

    def test_batches_by_scene_with_scope_prev_cvs_and_narrowed_input(self):
        llm = QueueLLM([
            n8_batch(["SC01", "SC02"], mark="-1"),
            n8_batch(["SC03", "SC04"], prev_cid="CVS_EP01_SC02_01",
                     mark="-2")])
        R.run_stage(self.pj, "n8", llm=llm, params=PARAMS, episode=EP1,
                    log=quiet)
        self.assertEqual(len(llm.sent), 2)
        first, second = llm.sent
        self.assertIn("这一次只写", first)
        self.assertIn("SC01、SC02", first)
        self.assertIn("这是第一批", first)
        self.assertIn("走位-SC01", first)
        self.assertNotIn("走位-SC03", first)      # 输入只发本批那几场
        # 第二批：范围说明、上一批末尾 CVS 原文、只发自己那两场
        self.assertIn("SC03、SC04", second)
        self.assertIn("走位-SC03", second)
        self.assertNotIn("走位-SC01", second)
        self.assertIn("CVS_EP01_SC02_01", second)     # PREV_CVS 的真实编号
        self.assertIn("归后一批", second)              # 跨场 VT 的口径
        out = self.pj.stage_data("n8_cvs", EP1)
        self.assertEqual(len(out["cvs"]), 4)          # 合并，不是覆盖
        self.assertEqual([v["vt_id"] for v in out["vt"]],
                         ["VT_EP01_001", "VT_EP01_002", "VT_EP01_003"])
        cross = out["vt"][1]
        self.assertEqual(cross["source_cvs"], "CVS_EP01_SC02_01")
        self.assertEqual(cross["target_cvs"], "CVS_EP01_SC03_01")
        self.assertIn("第-1批", out["camera_free_check"])
        self.assertIn("第-2批", out["camera_free_check"])

    def test_rerun_skips_done_scenes_without_calling(self):
        llm = QueueLLM([
            n8_batch(["SC01", "SC02"], mark="-1"),
            n8_batch(["SC03", "SC04"], prev_cid="CVS_EP01_SC02_01",
                     mark="-2")])
        R.run_stage(self.pj, "n8", llm=llm, params=PARAMS, episode=EP1,
                    log=quiet)
        out = self.pj.stage_data("n8_cvs", EP1)
        again = QueueLLM()      # 没排任何产物 —— 再多调一次就 assert 炸
        R.run_stage(self.pj, "n8", llm=again, params=PARAMS, episode=EP1,
                    log=quiet)
        self.assertEqual(again.sent, [])
        self.assertEqual(self.pj.stage_data("n8_cvs", EP1), out)   # 没动

    def test_resume_picks_up_missing_scenes_only(self):
        """断在第二批：第一批已落盘，再点一次只补没写的那两场。"""
        self.pj.save_stage("n8_cvs", n8_batch(["SC01", "SC02"], mark="-1"),
                           EP1)
        llm = QueueLLM([n8_batch(["SC03", "SC04"],
                                 prev_cid="CVS_EP01_SC02_01", mark="-2")])
        R.run_stage(self.pj, "n8", llm=llm, params=PARAMS, episode=EP1,
                    log=quiet)
        self.assertEqual(len(llm.sent), 1)
        self.assertIn("SC03、SC04", llm.sent[0])
        self.assertIn("CVS_EP01_SC02_01", llm.sent[0])   # 续传从上一批末尾
        out = self.pj.stage_data("n8_cvs", EP1)
        self.assertEqual(len(out["cvs"]), 4)

    def test_single_scene_episode_is_not_batched(self):
        """一场（或没切出场次）的集没有分批的意义，走单次路径。"""
        pj = seeded(scenes=["SC01"])
        try:
            llm = QueueLLM([n8_batch(["SC01"])])
            R.run_stage(pj, "n8", llm=llm, params=PARAMS, episode=EP1,
                        log=quiet)
            self.assertEqual(len(llm.sent), 1)
            self.assertIn("一次做完，不分批", llm.sent[0])
            self.assertEqual(len(pj.stage_data("n8_cvs", EP1)["cvs"]), 1)
        finally:
            shutil.rmtree(pj.root, ignore_errors=True)

    def test_wholly_unattributable_output_skips_rerun(self):
        """★ 一条都归不上场的产物（老项目 / 模型没按模板编号）当整集
        已做完 —— 否则归不上的永远「没做完」，每点一次「继续」都
        重跑整集，白烧钱。部分归得上的才按场判。"""
        self.pj.save_stage("n8_cvs", {"cvs": [cvs("CVS1", ""), cvs("CVS2", "")],
                                      "vt": [],
                                      "camera_free_check": "x"}, EP1)
        llm = QueueLLM()      # 没排产物 —— 再调一次就 assert 炸
        R.run_stage(self.pj, "n8", llm=llm, params=PARAMS, episode=EP1,
                    log=quiet)
        self.assertEqual(llm.sent, [])


class N9BatchRunTests(unittest.TestCase):

    def setUp(self):
        self.pj = seeded_with_cvs()

    def tearDown(self):
        shutil.rmtree(self.pj.root, ignore_errors=True)

    def _run(self, responses):
        llm = QueueLLM(responses)
        R.run_stage(self.pj, "n9", llm=llm, params=PARAMS, episode=EP1,
                    log=quiet)
        return llm

    def test_windows_merge_with_offset_and_cross_batch_transition(self):
        llm = self._run([n9_first_window(), n9_second_window_restarted()])
        self.assertEqual(len(llm.sent), 2)
        first, second = llm.sent
        self.assertIn("这是第一批", first)
        self.assertIn("SC01、SC02", first)
        self.assertIn("走位-SC01", first)
        self.assertNotIn("走位-SC03", first)
        # 第二批：上一批最后一镜的真实编号、接编口径、时间轴终点
        self.assertIn("上一批最后一镜的原文", second)
        self.assertIn("SH_EP01_002", second)
        self.assertIn("镜头编号从 SH_EP01_003 接着编", second)
        self.assertIn("转场编号从 TR_EP01_002 接着编", second)
        self.assertIn("第 15 秒", second)
        self.assertIn("走位-SC03", second)
        self.assertNotIn("走位-SC01", second)
        # CVS 块只发本批的（schema 示例里本来就写着 SC01 的样例编号，
        # 所以断言收紧到「本集标准视觉状态」那一节里没有 SC01 的行）
        cvs_block = second.split("【本集标准视觉状态（第八环节）】")[1] \
                          .split("【上一批最后一镜")[0]
        self.assertIn("CVS_EP01_SC03_01", cvs_block)
        self.assertNotIn("CVS_EP01_SC01_01", cvs_block)
        out = self.pj.stage_data("n9_shots", EP1)
        # 全局续号：模型第二批重启了编号，程序接上
        self.assertEqual([s["shot_id"] for s in out["shots"]],
                         ["SH_EP01_001", "SH_EP01_002",
                          "SH_EP01_003", "SH_EP01_004"])
        # 时间轴：批内从 0 铺、合并加偏移，首尾相接铺满本集
        rows = sorted(out["timing_plan"], key=lambda r: r["start"])
        self.assertEqual([r["start"] for r in rows], [0.0, 7.5, 15.0, 22.5])
        self.assertEqual([r["end"] for r in rows], [7.5, 15.0, 22.5, 30.0])
        # 跨批转场：from 是上一批最后一镜的**真实**编号（protect 保住的）
        cross = next(t for t in out["transitions"]
                     if t["transition_id"] == "TR_EP01_002")
        self.assertEqual(cross["from_shot"], "SH_EP01_002")
        self.assertEqual(cross["to_shot"], "SH_EP01_003")
        self.assertIn("第一批", out["shot_count_rationale"])
        self.assertIn("第二批", out["shot_count_rationale"])

    def test_rerun_skips_done_scenes_without_calling(self):
        self._run([n9_first_window(), n9_second_window_restarted()])
        out = self.pj.stage_data("n9_shots", EP1)
        again = QueueLLM()
        R.run_stage(self.pj, "n9", llm=again, params=PARAMS, episode=EP1,
                    log=quiet)
        self.assertEqual(again.sent, [])
        self.assertEqual(self.pj.stage_data("n9_shots", EP1), out)

    def test_resume_picks_up_missing_windows_only(self):
        """断点续跑：三场已落盘，再点一次只补剩下的那一场。
        剩一场时它独占一个时窗（权重变大），恰好验证「只剩一场」的批。"""
        disk = n9_first_window()
        disk["shots"].append(shot("SH_EP01_003", "SC03", "CVS_EP01_SC03_01"))
        disk["timing_plan"].append(timing("SH_EP01_003", 15.0, 22.5))
        self.pj.save_stage("n9_shots", disk, EP1)
        llm = self._run([n9_last_window()])
        self.assertEqual(len(llm.sent), 1)
        self.assertIn("SH_EP01_003", llm.sent[0])       # LAST_SHOT 续传
        self.assertIn("第 22.5 秒", llm.sent[0])        # 时间轴从断点接
        out = self.pj.stage_data("n9_shots", EP1)
        self.assertEqual(len(out["shots"]), 4)
        rows = sorted(out["timing_plan"], key=lambda r: r["start"])
        self.assertEqual(rows[-1]["end"], 30.0)         # 铺满本集
        # 收尾批的镜头接着编号，跨批转场的 from 是已落盘的真实编号
        self.assertEqual(out["shots"][3]["shot_id"], "SH_EP01_004")
        cross = next(t for t in out["transitions"]
                     if t["transition_id"] == "TR_EP01_002")
        self.assertEqual(cross["from_shot"], "SH_EP01_003")
        self.assertEqual(cross["to_shot"], "SH_EP01_004")

    def test_wholly_unattributable_output_skips_rerun(self):
        """同第八环节：scene_id 和 source_cvs 都对不上场号的产物当整集
        已做完，不重排。"""
        self.pj.save_stage("n9_shots", {"shots": [
            shot("SH1", "", "CVS9"), shot("SH2", "", "CVS9")]}, EP1)
        llm = QueueLLM()
        R.run_stage(self.pj, "n9", llm=llm, params=PARAMS, episode=EP1,
                    log=quiet)
        self.assertEqual(llm.sent, [])

    def test_batch_failure_keeps_earlier_batches(self):
        """第二批炸了：第一批是留下的，产物里看得到 —— 再点一次只补它。"""
        class FailSecond(QueueLLM):
            def json_call(self, system, user, **kw):
                if len(self.sent) == 1:
                    raise RuntimeError("第二批炸了（测试注入）")
                return super().json_call(system, user, **kw)

        llm = FailSecond([n9_first_window(), n9_second_window_restarted()])
        with self.assertRaises(RuntimeError):
            R.run_stage(self.pj, "n9", llm=llm, params=PARAMS, episode=EP1,
                        log=quiet)
        out = self.pj.stage_data("n9_shots", EP1)
        self.assertEqual([s["shot_id"] for s in out["shots"]],
                         ["SH_EP01_001", "SH_EP01_002"])   # 第一批还在


class LlmDoneTests(TempCase):

    def test_n8_done_requires_every_scene(self):
        """★ 产物文件在但只写了两场 —— 只看文件在不在会漏掉后两场。"""
        self.pj.save_stage("n8_cvs", n8_batch(["SC01", "SC02"]), EP1)
        self.assertFalse(P._llm_done(self.pj, "n8", EP1))
        self.pj.save_stage("n8_cvs", n8_batch(SCENES), EP1)
        self.assertTrue(P._llm_done(self.pj, "n8", EP1))

    def test_wholly_unattributable_output_counts_as_done(self):
        """一条都归不上场（老项目 / 没按模板编号）→ 整集已做完。"""
        self.pj.save_stage("n8_cvs", {"cvs": [cvs("CVS1", ""),
                                              cvs("CVS2", "")]}, EP1)
        self.assertTrue(P._llm_done(self.pj, "n8", EP1))
        self.pj.save_stage("n9_shots", {"shots": [
            shot("SH1", "", "CVS9")]}, EP1)
        self.assertTrue(P._llm_done(self.pj, "n9", EP1))

    def test_n9_done_requires_every_scene(self):
        self.assertFalse(P._llm_done(self.pj, "n9", EP1))   # 还没跑过
        self.pj.save_stage("n9_shots", {"shots": [
            shot("SH_EP01_001", "SC01", "CVS_EP01_SC01_01"),
            shot("SH_EP01_002", "SC02", "CVS_EP01_SC02_01")]}, EP1)
        self.assertFalse(P._llm_done(self.pj, "n9", EP1))
        self.pj.save_stage("n9_shots", {"shots": [
            shot(f"SH_EP01_{i:03d}", s, f"CVS_{EP1}_{s}_01")
            for i, s in enumerate(SCENES, 1)]}, EP1)
        self.assertTrue(P._llm_done(self.pj, "n9", EP1))


if __name__ == "__main__":
    unittest.main()
