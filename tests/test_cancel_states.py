# -*- coding: utf-8 -*-
"""取消/熔断之后，没跑到的步骤不许留在 pending。

`pending` 的字面意思是「还没轮到，等会儿会跑」。整个 job 已经停了还这么显示，
人会以为它还在排队 —— 于是干等，或者以为程序卡住了。

实跑截出来的状态长这样，交错得毫无规律：

    EP01 第12环节 故事板包编译      cancelled
    EP01 第13环节 视频执行计划       pending      ← 循环 return 掉了，没进 do_llm
    资产图生产                     cancelled
    EP01 第14环节 漏洞审计          pending      ← 同上
    排序拼接与交付                  cancelled

原因是 run_episode 和 tail 循环在取消时直接 return/break，
剩下的步骤一个都没标。
"""
import unittest

from core import pipeline_v34 as P
from core.executor import Job


def job(**kw):
    j = Job("pipeline", total=0, concurrency=1)
    for k, v in kw.items():
        setattr(j, k, v)
    return j


STEPS = [{"label": "第12环节 故事板"}, {"label": "第13环节 视频提示词"},
         {"label": "资产图生产"}, {"label": "第14环节 审计"}]


class MarkStoppedTests(unittest.TestCase):

    def test_pending_becomes_cancelled(self):
        j = job(cancelled=True)
        for s in STEPS:
            j.set_item(s["label"], state="pending")
        P._mark_stopped(j, STEPS)
        for s in STEPS:
            self.assertEqual(j.items[s["label"]]["state"], "cancelled", s["label"])
            self.assertEqual(j.items[s["label"]]["msg"], "已取消")

    def test_untouched_steps_are_marked_too(self):
        """★ 从来没 set_item 过的也要标 —— 那正是留在 pending 的那些。"""
        j = job(cancelled=True)
        P._mark_stopped(j, STEPS)
        self.assertTrue(all(j.items[s["label"]]["state"] == "cancelled"
                            for s in STEPS))

    def test_abort_says_it_was_a_breaker_not_a_cancel(self):
        """★ 熔断（余额不足/密钥失效）和人点了取消是两回事。

        显示成「已取消」会让人以为是自己按的，然后去找哪儿按错了。
        """
        j = job(aborted=True, cancelled=True)   # abort_with 会连带置 cancelled
        P._mark_stopped(j, STEPS)
        self.assertEqual(j.items[STEPS[0]["label"]]["msg"], "熔断停止")

    def test_finished_steps_are_never_overwritten(self):
        """★ 已经 ok / failed / skipped 的是事实，盖掉等于篡改结果。"""
        j = job(cancelled=True)
        j.set_item("第12环节 故事板", state="ok")
        j.set_item("资产图生产", state="failed", msg="出图失败")
        j.set_item("第14环节 审计", state="skipped", msg="已经做过了，跳过")
        P._mark_stopped(j, STEPS)
        self.assertEqual(j.items["第12环节 故事板"]["state"], "ok")
        self.assertEqual(j.items["资产图生产"]["msg"], "出图失败")
        self.assertEqual(j.items["第14环节 审计"]["state"], "skipped")
        # 只有那个 pending 的被改了
        self.assertEqual(j.items["第13环节 视频提示词"]["state"], "cancelled")


class WiringTests(unittest.TestCase):

    def setUp(self):
        import io, os
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.src = io.open(os.path.join(root, "core", "pipeline_v34.py"),
                           encoding="utf-8").read()

    def test_both_loops_mark_the_rest(self):
        """★ 两个循环都要接 —— 只接一个，另一个照样留 pending。"""
        self.assertEqual(self.src.count("_mark_stopped(job,"), 2)

    def test_the_tail_loop_also_checks_cancelled_not_only_aborted(self):
        """原来的 tail 循环只判 aborted，取消时会继续往下走一圈。"""
        i = self.src.index("for i, s in enumerate(tail):")
        self.assertIn("job.aborted or job.cancelled", self.src[i:i + 120])


if __name__ == "__main__":
    unittest.main()
