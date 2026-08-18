# -*- coding: utf-8 -*-
"""闸门不能比它实现的规则更严。

实跑：EP01 的四步生产（资产图/场景状态图/故事板/视频）**全部被拦**，
理由是「审计的 BLOCK 级发现 12 处」。查下来其中两类是我们自己判错的：

  · 道具对账 4 条 —— 模型写的算式是对的，是我们的解析算错了
  · 位置门控 3 条 —— 转个身、换个姿态被判成「无事件瞬移」，
    而模板恰恰把「表演、视线、手势」列为允许改变

闸门误报的代价比漏报更隐蔽：人看到一屏 BLOCK，最后干脆把整道闸门放行，
那比没有闸门更糟。所以拦不准的时候宁可不拦。
"""
import shutil
import unittest

from core import gates_v34 as G
from test_v34_run import new_project


class PropCountTests(unittest.TestCase):
    """`reconciliation` 是一段自由文本，不是一个算式。

    原来的写法：把整段里所有数字抓出来，假设最后一个是总数、
    前面的加起来等于它。而模型会在等号后面继续解释，也会写区间。
    """

    def setUp(self):
        self.pj = new_project()

    def tearDown(self):
        shutil.rmtree(self.pj.root, ignore_errors=True)

    def _check(self, rec, total):
        self.pj.save_stage("n6_ledger", {"prop_tracking": [
            {"instance_id": "PI001",
             "count_lock": {"active_total": total, "reconciliation": rec}}]}, "")
        return G.object_count_gate(self.pj)

    def test_the_real_one_that_was_misjudged(self):
        """★ 实跑原文。算式 1 + 1 = 2 是对的，等号后面是解释。"""
        self.assertEqual(self._check(
            "一把青菜1 + 野葱1 = 2；成交后摊位库存减少2，林南桥持有2，不得回到摊位", 2), [])

    def test_ranges_are_not_guessed_at(self):
        """★ 另一条实跑原文。「0至1」是区间，一项出两个数，怎么加都不对。"""
        self.assertEqual(self._check(
            "明确可见0至1 + 部分可见0至1 + 遮挡0 + 画外0至1 = 1；唯一实体，不得复制", 1), [])

    def test_a_clean_equation_still_passes(self):
        self.assertEqual(self._check("1 + 0 + 0 + 1 = 2", 2), [])

    def test_a_wrong_equation_is_still_caught(self):
        """★ 别放过头：真加错了必须拦 —— 这道闸门本来就是防道具凭空多出来。"""
        bad = self._check("明确可见1 + 部分可见1 + 遮挡0 + 画外1 = 3", 2)
        self.assertTrue(bad, "3 ≠ 2，该拦")
        self.assertIn("对账对不上", bad[0])

    def test_a_total_that_disagrees_with_the_equation_is_caught(self):
        self.assertTrue(self._check("1 + 1 = 2", 3))

    def test_no_detail_at_all_is_still_caught(self):
        self.assertTrue(self._check("唯一实体，不得复制", 1))

    def test_no_equation_is_not_guessed_at(self):
        """算不出来就不拦 —— 我们对格式的假设不该变成硬性拦截。"""
        self.assertEqual(self._check("全程 1 件，被遮挡时仍然是 1 件", 1), [])

    def test_no_reconciliation_field_is_left_alone(self):
        self.pj.save_stage("n6_ledger", {"prop_tracking": [
            {"instance_id": "PI009", "count_lock": {}}]}, "")
        self.assertEqual(G.object_count_gate(self.pj), [])


def pos(**kw):
    base = {"zone": "B", "anchor_id": "DESK_01", "posture_class": "SEATED",
            "support_binding_id": "CHAIR_03", "orientation_yaw_deg": 90}
    base.update(kw)
    return base


def kf(kid, state, ev="NONE"):
    return {"kf_id": kid, "authorized_movement_event_id": ev,
            "entity_position_state": [dict(state, id="C001")]}


class PoseIsNotMovementTests(unittest.TestCase):
    """转身、换姿态不是瞬移。

    n12 模板原文：`authorized_movement_event_id = NONE` 时，这一格
    **只能改变**机位投影、表演、视线、手势、动作阶段；
    **不能改变**人物真实的 Zone、Anchor、支撑关系。

    代码一度把 posture_class 和 orientation_yaw_deg 也算进「不能改变」，
    比模板更严 —— 而转身正属于模板明确允许的那一类。
    """

    def setUp(self):
        self.pj = new_project()

    def tearDown(self):
        shutil.rmtree(self.pj.root, ignore_errors=True)

    def _run(self, a, b, ev="NONE"):
        # 没建 episodes.json 的项目里 _episodes 只产出 ""，产物就存在那一格
        self.pj.save_stage("n12_storyboard",
                           {"sbpkg": [{"seg_id": "EP01-SEG02",
                                       "kf": [kf("KF02", a), kf("KF03", b, ev)]}]},
                           "")
        return G.position_gate(self.pj)

    def test_turning_around_is_allowed(self):
        """★ 实跑被拦的就是这个：orientation_yaw_deg 变了。"""
        self.assertEqual(self._run(pos(orientation_yaw_deg=90),
                                   pos(orientation_yaw_deg=270)), [])

    def test_changing_posture_alone_is_allowed(self):
        """★ 姿态是「动作阶段」，模板列在允许改变里。"""
        self.assertEqual(self._run(pos(posture_class="SEATED"),
                                   pos(posture_class="LEANING")), [])

    def test_changing_zone_is_still_blocked(self):
        """★ 别放过头：这才是这道闸门要拦的 —— 没有事件却换了位置。"""
        bad = self._run(pos(zone="B"), pos(zone="C"))
        self.assertTrue(bad)
        self.assertIn("zone", bad[0])

    def test_changing_anchor_is_still_blocked(self):
        self.assertTrue(self._run(pos(anchor_id="DESK_01"),
                                  pos(anchor_id="DOOR_01")))

    def test_releasing_support_is_still_blocked(self):
        """★ 起身要有事件。空字符串是「没有支撑」，不是「没写」。"""
        bad = self._run(pos(support_binding_id="CHAIR_03"),
                        pos(support_binding_id=""))
        self.assertTrue(bad, "坐着变成没支撑 = 起身了，得有事件")

    def test_an_authorized_move_passes(self):
        self.assertEqual(self._run(pos(zone="B"), pos(zone="C"), ev="VT_001"), [])

    def test_a_missing_field_is_not_treated_as_a_change(self):
        """★ 「没写」和「写了空」是两件事。老产物缺字段不该被判成移动。"""
        a = pos()
        b = {k: v for k, v in pos().items() if k != "support_binding_id"}
        self.assertEqual(self._run(a, b), [])

    def test_none_and_empty_mean_the_same_thing(self):
        """★ 这一格写 ""、下一格写 "NONE" 不是解除了支撑 —— 不归一会一屏误报。"""
        self.assertEqual(self._run(pos(support_binding_id=""),
                                   pos(support_binding_id="NONE")), [])


if __name__ == "__main__":
    unittest.main()
