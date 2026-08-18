# -*- coding: utf-8 -*-
"""V5.6 位置状态门控：人物不许无事件瞬移。

V5.6 第 14 章把这个列为两类高风险失败之一，原话：

  「人物在前一状态坐在发布会桌后，下一状态没有起身、绕行或穿越事件，
    却直接出现在观众区争吵。这不是 Camera 变化，而是 World Truth
    被静默改写。」

为什么必须由程序拦：这类错**画面上是好看的**。三个人整整齐齐同框，
构图甚至更漂亮。它只在把整集连起来看的时候才露馅 —— 上一段还坐着，
下一段人已经在对面了。人工一集一集验收，基本抓不到。

而模型有很强的动机这么干：为了三人同框、为了露全身、为了做对峙构图，
把人挪到房间中央是最省事的解法。
"""
import io
import os
import shutil
import unittest

from core import gates_v34 as G
from test_v34_run import new_project

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def pos(zone="B", anchor="DESK_01", posture="SEATED", support="CHAIR_03",
        yaw=90):
    return {"zone": zone, "anchor_id": anchor, "posture_class": posture,
            "support_binding_id": support, "orientation_yaw_deg": yaw}


def cvs(cid, *chars):
    return {"cvs_id": cid, "characters": list(chars)}


def who(cid, **kw):
    return {"character_id": cid, "world_position_state": pos(**kw)}


class CvsPositionTests(unittest.TestCase):
    """相邻 CVS 之间：位置变了必须有一条批准这个人移动的过渡。"""

    def setUp(self):
        self.pj = new_project()

    def tearDown(self):
        shutil.rmtree(self.pj.root, ignore_errors=True)

    def _save(self, cvs_list, vt=()):
        self.pj.save_stage("n8_cvs", {"cvs": cvs_list, "vt": list(vt)}, "")

    def test_unchanged_position_is_fine(self):
        self._save([cvs("A1", who("C001")), cvs("A2", who("C001"))])
        self.assertEqual(G.position_gate(self.pj), [])

    def test_teleport_without_any_event_is_blocked(self):
        """★ 坐在桌后 → 下一状态直接在观众区。没有起身、没有路线。"""
        self._save([
            cvs("A1", who("C001", zone="B", anchor="DESK_01")),
            cvs("A2", who("C001", zone="C", anchor="AISLE_01",
                          posture="STANDING", support="")),
        ])
        bad = G.position_gate(self.pj)
        self.assertEqual(len(bad), 1, bad)
        self.assertIn("C001", bad[0])
        self.assertIn("无事件瞬移", bad[0])
        self.assertIn("机位变化不能当理由", bad[0])

    def test_a_transition_that_does_not_name_the_mover_does_not_count(self):
        """★ 有过渡但没把这个人列进 authorized_movers —— 等于没批准他。

        这一条最容易漏：同一时刻常有多人，批准了甲不等于批准了乙。
        """
        self._save(
            [cvs("A1", who("C001"), who("C005")),
             cvs("A2", who("C001"),
                 who("C005", zone="C", anchor="AISLE_01",
                     posture="STANDING", support=""))],
            [{"source_cvs": "A1", "target_cvs": "A2",
              "authorized_movers": ["C001"],
              "release_support_action": "起身", "route_id": "R1",
              "barrier_or_portal_crossing": "DESK_01_GAP_E",
              "completion_condition": "站定"}])
        bad = G.position_gate(self.pj)
        self.assertTrue(any("C005" in b for b in bad), bad)
        self.assertFalse(any("C001" in b and "瞬移" in b for b in bad), bad)

    def test_a_complete_authorized_move_passes(self):
        self._save(
            [cvs("A1", who("C001", zone="B", posture="SEATED",
                           support="CHAIR_03")),
             cvs("A2", who("C001", zone="C", anchor="AISLE_01",
                           posture="STANDING", support=""))],
            [{"source_cvs": "A1", "target_cvs": "A2",
              "authorized_movers": ["C001"],
              "release_support_action": "向后推椅、起身完成",
              "route_id": "R1",
              "barrier_or_portal_crossing": "DESK_01_GAP_E",
              "completion_condition": "在过道站定"}])
        self.assertEqual(G.position_gate(self.pj), [])

    def test_standing_up_without_releasing_support_is_blocked(self):
        """★ 镜头拉远也取消不了座椅关系。

        不解除支撑就是「人还坐着，同时又站在别处」。
        """
        self._save(
            [cvs("A1", who("C001", posture="SEATED", support="CHAIR_03")),
             cvs("A2", who("C001", posture="STANDING", support=""))],
            [{"source_cvs": "A1", "target_cvs": "A2",
              "authorized_movers": ["C001"], "completion_condition": "站定"}])
        bad = G.position_gate(self.pj)
        self.assertTrue(any("解除支撑" in b for b in bad), bad)

    def test_crossing_zones_needs_a_route_and_a_portal(self):
        """不写路线就等于允许穿墙穿桌。"""
        self._save(
            [cvs("A1", who("C001", zone="B", posture="STANDING", support="")),
             cvs("A2", who("C001", zone="C", posture="STANDING", support=""))],
            [{"source_cvs": "A1", "target_cvs": "A2",
              "authorized_movers": ["C001"], "completion_condition": "站定"}])
        bad = "　".join(G.position_gate(self.pj))
        self.assertIn("Route", bad)
        self.assertIn("Portal", bad)

    def test_a_move_without_a_completion_condition_is_blocked(self):
        # 用**真的换了位置**来触发（换 anchor）。以前这里用的是转个朝向，
        # 而朝向不算移动 —— 模板把「表演、视线、手势」列为允许改变。
        self._save(
            [cvs("A1", who("C001", anchor="DESK_01", posture="STANDING", support="")),
             cvs("A2", who("C001", anchor="DOOR_01", posture="STANDING", support=""))],
            [{"source_cvs": "A1", "target_cvs": "A2",
              "authorized_movers": ["C001"]}])
        bad = "　".join(G.position_gate(self.pj))
        self.assertIn("完成条件", bad)

    def test_a_character_appearing_for_the_first_time_is_not_a_teleport(self):
        """这一状态才进场的人没有「上一位置」，不该被判成瞬移。"""
        self._save([cvs("A1", who("C001")),
                    cvs("A2", who("C001"), who("C009", zone="C"))])
        self.assertEqual(G.position_gate(self.pj), [])

    def test_missing_fields_are_not_treated_as_changes(self):
        """★ 没写不等于改了。

        把「字段缺失」当成「位置变了」会让闸门在数据不全时疯狂误报，
        然后人就会去把整道闸门关掉 —— 那比没有闸门更糟。
        """
        self._save([cvs("A1", {"character_id": "C001"}),
                    cvs("A2", {"character_id": "C001"})])
        self.assertEqual(G.position_gate(self.pj), [])

    def test_one_cvs_alone_has_nothing_to_compare(self):
        self._save([cvs("A1", who("C001"))])
        self.assertEqual(G.position_gate(self.pj), [])

    def test_a_scene_cut_is_not_a_teleport(self):
        """★ 换场不是瞬移。

        实跑报了 18 处「无事件瞬移」，一半是 SC01→SC02、SC04→SC05 这种：
        不同时间、不同地点，人当然在别处 —— 那是剪辑。

        V5.6 举的例子（坐在发布会桌后 → 直接在观众区争吵）发生在
        **同一场发布会之内**。要求跨场次也给移动事件是无理的，
        而误报的代价很实在：真问题被一屏噪音淹掉，人干脆整道闸门放行。
        """
        self._save([
            dict(cvs("A1", who("C001", zone="B", anchor="DESK_01")),
                 scene_id="EP01-SC01"),
            dict(cvs("A2", who("C001", zone="C", anchor="AISLE_01",
                               posture="STANDING", support="")),
                 scene_id="EP01-SC02"),
        ])
        self.assertEqual(G.position_gate(self.pj), [])

    def test_within_one_scene_it_still_blocks(self):
        """写了 scene_id 不是免检牌 —— 同一场之内照样逐项对账。"""
        self._save([
            dict(cvs("A1", who("C001", zone="B", anchor="DESK_01")),
                 scene_id="EP01-SC01"),
            dict(cvs("A2", who("C001", zone="C", anchor="AISLE_01",
                               posture="STANDING", support="")),
                 scene_id="EP01-SC01"),
        ])
        bad = G.position_gate(self.pj)
        self.assertEqual(len(bad), 1, bad)
        self.assertIn("无事件瞬移", bad[0])

    def test_without_scene_ids_it_compares_conservatively(self):
        """没写场次号就照旧比 —— 宁可误报，也别让漏写字段变成免检。"""
        self._save([
            cvs("A1", who("C001", zone="B")),
            dict(cvs("A2", who("C001", zone="C")), scene_id="EP01-SC02"),
        ])
        self.assertTrue(G.position_gate(self.pj))


class KeyframePositionTests(unittest.TestCase):
    """相邻关键帧之间：Camera may reframe; entities may not reblock."""

    def setUp(self):
        self.pj = new_project()

    def tearDown(self):
        shutil.rmtree(self.pj.root, ignore_errors=True)

    def _save(self, kfs):
        self.pj.save_stage("n12_storyboard",
                           {"sbpkg": [{"seg_id": "EP01-SEG01", "kf": kfs}]},
                           "")

    def _kf(self, kid, event="NONE", **kw):
        return {"kf_id": kid, "authorized_movement_event_id": event,
                "entity_position_state": [dict(pos(**kw), id="C001")]}

    def test_reframing_without_moving_is_fine(self):
        self._save([self._kf("KF01"), self._kf("KF02")])
        self.assertEqual(G.position_gate(self.pj), [])

    def test_reblocking_with_no_movement_event_is_blocked(self):
        """★ 换机位不等于可以重新安排人站哪。"""
        self._save([self._kf("KF01", zone="B"),
                    self._kf("KF02", zone="C", anchor="AISLE_01")])
        bad = G.position_gate(self.pj)
        self.assertEqual(len(bad), 1, bad)
        self.assertIn("KF02", bad[0])
        self.assertIn("重新取景", bad[0])
        self.assertIn("同框", bad[0], "没说清模型为什么会犯这个错")

    def test_moving_with_an_authorized_event_is_allowed(self):
        self._save([self._kf("KF01", zone="B"),
                    self._kf("KF02", event="VT_EP01_03", zone="C",
                             anchor="AISLE_01")])
        self.assertEqual(G.position_gate(self.pj), [])

    def test_the_literal_string_none_does_not_count_as_an_event(self):
        """模型很可能写 "NONE" / "none" —— 那是「没有」，不是一个事件号。"""
        for none in ("NONE", "none", "None", "", "  "):
            self._save([self._kf("KF01", zone="B"),
                        self._kf("KF02", event=none, zone="C")])
            self.assertTrue(G.position_gate(self.pj), repr(none))


class GateWiringTests(unittest.TestCase):

    def setUp(self):
        self.pj = new_project()

    def tearDown(self):
        shutil.rmtree(self.pj.root, ignore_errors=True)

    def test_the_gate_runs_as_part_of_check_all(self):
        """★ 不接进 check_all 的闸门等于没写。"""
        self.assertIn("position_state", G.GATES)
        self.pj.save_stage("n8_cvs", {"cvs": [
            cvs("A1", who("C001", zone="B")),
            cvs("A2", who("C001", zone="C")),
        ], "vt": []}, "")
        self.assertIn("position_state", G.check_all(self.pj))

    def test_authorizing_it_lets_the_run_continue(self):
        self.pj.save_stage("n8_cvs", {"cvs": [
            cvs("A1", who("C001", zone="B")),
            cvs("A2", who("C001", zone="C")),
        ], "vt": []}, "")
        G.authorize(self.pj, "position_state", "这一段是回忆闪回，位置本来就断开")
        self.assertNotIn("position_state", G.check_all(self.pj))

    def test_authorizing_still_requires_a_reason(self):
        with self.assertRaises(ValueError):
            G.authorize(self.pj, "position_state", "   ")


class TemplateTests(unittest.TestCase):
    """闸门拦得住但模板不教，等于每次都被拦。"""

    def _tpl(self, name):
        return io.open(os.path.join(ROOT, "prompts", f"{name}.md"),
                       encoding="utf-8").read()

    def test_spatial_master_registers_barriers_portals_and_supports(self):
        """route_id / barrier_or_portal_crossing 得有地方可指。"""
        t = self._tpl("n5_spatial")
        for k in ("barriers", "portals", "supports", "minimum_action_time"):
            self.assertIn(k, t, k)

    def test_cvs_carries_the_world_position_state_block(self):
        t = self._tpl("n8_cvs")
        for k in ("world_position_state", "posture_class", "support_binding_id",
                  "authorized_movers", "release_support_action"):
            self.assertIn(k, t, k)

    def test_cvs_spells_out_the_forbidden_chain(self):
        """V5.6 给了合法链和禁止链，两个都要写进模板。"""
        t = self._tpl("n8_cvs")
        self.assertIn("直接在前排过道争吵", t)
        self.assertIn("镜头拉远也取消不了座椅关系", t)

    def test_scstate_declares_previous_state_and_authorized_movers(self):
        t = self._tpl("n11_scstate")
        for k in ("previous_scstate", "unchanged_position_states",
                  "authorized_movers", "forbidden_position_delta"):
            self.assertIn(k, t, k)

    def test_storyboard_carries_the_two_mandatory_sentences(self):
        """V5.6 要求这两句原样进提示词。"""
        t = self._tpl("n12_storyboard")
        self.assertIn("CAMERA MAY REFRAME; ENTITIES MAY NOT REBLOCK.", t)
        self.assertIn("DO NOT MOVE SUBJECTS FOR VISIBILITY OR COMPOSITION.", t)
        self.assertIn("authorized_movement_event_id", t)
        self.assertIn("遮挡不是瞬移许可证", t)

    def test_ledger_treats_position_as_a_state(self):
        t = self._tpl("n6_ledger")
        self.assertIn("position_tracking", t)
        self.assertIn("INHERITED", t)

    def test_directing_writes_the_full_move(self):
        t = self._tpl("n7_directing")
        for k in ("blocking_change", "release_support", "route_id",
                  "barrier_or_portal_crossing"):
            self.assertIn(k, t, k)
        self.assertIn("为了构图", t, "没说清哪些理由不算移动理由")



class AuthorizeEndpointTests(unittest.TestCase):
    """闸门必须有页面出口。

    以前拦截文案写着「去项目设置里显式授权」，而那个地方**根本不存在** ——
    四道闸门任何一道判定有问题，页面上就走不下去，只能手改 project.json。
    闸门是加了，出口忘了开。
    """

    def setUp(self):
        self.pj = new_project()
        self.pj.save_meta(dict(self.pj.meta(), system="v34"))
        self.pj.save_stage("n8_cvs", {"cvs": [
            cvs("A1", who("C001", zone="B")),
            cvs("A2", who("C001", zone="C")),
        ], "vt": []}, "")

    def tearDown(self):
        shutil.rmtree(self.pj.root, ignore_errors=True)

    def _post(self, path, **body):
        from server.app import api_post
        return api_post(path, dict(body, project_root=self.pj.root))

    def test_gates_endpoint_reports_every_gate_not_only_the_blocking_ones(self):
        """通过的那几道也要列出来 —— 只报被拦的，人不知道另外几道查过没有。"""
        r = self._post("/api/gates")
        names = {g["gate"] for g in r["gates"]}
        self.assertEqual(names, set(G.GATES))
        pos = next(g for g in r["gates"] if g["gate"] == "position_state")
        self.assertTrue(pos["blocking"])
        self.assertTrue(pos["problems"])

    def test_authorize_then_the_gate_stops_blocking(self):
        self._post("/api/gates/authorize", gate="position_state",
                   why="这一段是闪回，位置本来就断开")
        r = self._post("/api/gates")
        pos = next(g for g in r["gates"] if g["gate"] == "position_state")
        self.assertFalse(pos["blocking"])
        self.assertEqual(r["blocking_count"], 0)

    def test_the_reason_and_time_are_kept(self):
        """★ 放行是会被忘掉的。忘了之后「为什么这集允许瞬移」没人答得上。"""
        self._post("/api/gates/authorize", gate="position_state", why="这一段是闪回")
        pos = next(g for g in self._post("/api/gates")["gates"]
                   if g["gate"] == "position_state")
        self.assertIn("闪回", pos["authorized"]["why"])
        self.assertTrue(pos["authorized"]["at"])

    def test_revoke_puts_the_gate_back(self):
        self._post("/api/gates/authorize", gate="position_state", why="先放过")
        self._post("/api/gates/authorize", gate="position_state", revoke=True)
        pos = next(g for g in self._post("/api/gates")["gates"]
                   if g["gate"] == "position_state")
        self.assertTrue(pos["blocking"])
        self.assertIsNone(pos["authorized"])

    def test_v61_projects_have_no_gates(self):
        self.pj.save_meta(dict(self.pj.meta(), system="v61"))
        self.assertEqual(self._post("/api/gates")["gates"], [])

    def test_the_block_message_points_at_somewhere_that_exists(self):
        """★ 文案指向的地方必须真的有。指向不存在的地方比不指更糟。"""
        msg = G.blocked_message({"position_state": ["随便一条"]})
        self.assertIn("闸门", msg)
        self.assertNotIn("项目设置里显式授权", msg)
        html = io.open(os.path.join(ROOT, "web", "index.html"),
                       encoding="utf-8").read()
        self.assertIn("出图出片前的闸门", html)
        self.assertIn("/api/gates/authorize", html)

if __name__ == "__main__":
    unittest.main()
