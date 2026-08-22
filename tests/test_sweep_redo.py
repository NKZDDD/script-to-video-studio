# -*- coding: utf-8 -*-
"""条件补跑：上一趟「条件不具备」的，输入落盘之后自动重派。

用户报告的失灵场景：就绪即派只在**派单那一刻**检测一次 —— 一条任务
那会儿输入没齐、被标成「条件不具备，没发请求」，之后上游补完了，
它不会自己复活。用户原话：「我不去再次点开始/继续，只有之前的几个
满足条件的在制作，我点一下开始/重试就会多出几十个可以制作的」。

sweep_redo 就是把「再点一次开始」的那次全盘重扫自动化。这个文件
钉死它的判定语义：

  · **捞谁** —— 产物没有、输入**现在**在磁盘上、不是本趟真报过错的。
  · **不捞谁** —— 本趟真报过错的（服务商拒绝的提示词五秒后再发
    还是被拒，和泵「版本不涨不重试」同一条规矩）；上一趟的旧账
    除外 —— 本趟开头那一轮已经按磁盘重派过它们，成败都翻过篇。
  · **链式** —— 按生产顺序扫，前一步这一轮补出来的文件，后一步
    同一轮立刻接着用，一条链一次跑通。
  · **收敛** —— 重派要么落盘（下次不再捞到）、要么落一条新的失败
    记录（下次进「真报过错」被排除）；上限只防记录没落上的极端情况。

reconcile_produce_steps 钉死补跑后的终态校准 —— 中间那轮只知道自己
那一轮的事，两个方向都可能骗人：failed 升回 ok（人白紧张）、
ok 降回 failed（「做完了」是假的）。
"""
import os
import shutil
import tempfile
import unittest

from core import diagnose, probe
from core.relay import Relay, reconcile_produce_steps, sweep_redo

EPOCH = "2026-08-23 12:00:00"
OLD = "2026-08-22 09:00:00"          # 上一趟的旧账
NEW = "2026-08-23 12:05:00"          # 本趟的新记录


class _PJ:
    """sweep 只需要 tasks() 和 p()；产物文件真往临时目录里写。"""

    def __init__(self, root):
        self.root = root
        self.tasks_by_key: dict = {}

    def p(self, *parts):
        return os.path.join(self.root, *parts)

    def tasks(self):
        return self.tasks_by_key


def _t(key, out, refs=()):
    return {"key": key, "output": out,
            "reference_images": [{"file_ref": r} for r in refs]}


def _step(label, produce, task_key, batch=""):
    return {"kind": "produce", "label": label, "stage": task_key,
            "produce": produce, "task_key": task_key, "batch": batch or label}


class _FakeJob:
    """reconcile 只用 items / set_item。"""

    def __init__(self):
        self.items: dict = {}

    def set_item(self, key, **kw):
        self.items.setdefault(key, {}).update(kw)


class SweepRedoTests(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.pj = _PJ(self.dir)
        self.relay = Relay(self.pj)
        self.calls: list = []          # (step_label, [task key, ...])
        self.logs: list = []

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    # ------------------------------------------------------------ 工具

    def _put(self, rel, size=1024):
        """往磁盘上放一个真产物（大于 probe 的最小字节数）。"""
        p = self.pj.p(*rel.split("/"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(b"x" * size)

    def _record(self, target, code="UPSTREAM_REJECTED", at=NEW, stage="asset"):
        diagnose.record(self.pj.root, {
            "code": code, "title": "x", "why": "", "where": "", "fix": [],
            "resume": "", "resumable": True, "scope": "task",
            "stage": stage, "target": target, "at": at, "raw": "",
        })

    def _run(self, steps, produce=None):
        """produce=None：重派的都做成（常态）；produce=set()：一个都做不成。

        todo_of 和真实管线同一条规矩：产物已在磁盘上的不进待办。
        """
        def todo_of(s):
            return [t for t in self.pj.tasks().get(s["task_key"]) or []
                    if not probe.have_output(self.pj.p(*t["output"].split("/")))]

        def run_step(s, pick):
            self.calls.append((s["label"], [t["key"] for t in pick]))
            for t in pick:
                if produce is None or t["key"] in produce:
                    self._put(t["output"])

        sweep_redo(self.pj, steps, self.relay, run_step=run_step,
                   todo_of=todo_of,
                   log=lambda s, m: self.logs.append((s["label"], m)),
                   epoch=EPOCH, should_stop=lambda: False)
        return run_step

    def _picked(self, label):
        return [k for lab, ks in self.calls if lab == label for k in ks]

    # ------------------------------------------------------------ 捞谁

    def test_input_landed_and_no_record_means_redo(self):
        """★ 这就是用户要的那条：条件不具备、没报错、输入后来齐了 → 自动重做。"""
        self.pj.tasks_by_key["asset_tasks"] = [
            _t("A", "x/A.png", refs=["x/up.png"])]
        self._put("x/up.png")                      # 上游后来补完了
        self._run([_step("资产图", "asset", "asset_tasks")])
        self.assertEqual(self._picked("资产图"), ["A"])
        self.assertTrue(any("自动补做" in m for _, m in self.logs))

    def test_input_still_missing_means_leave_it_alone(self):
        """输入还没齐的**不捞** —— 派出去就是花一次调用撞空文件。"""
        self.pj.tasks_by_key["asset_tasks"] = [
            _t("A", "x/A.png", refs=["x/up.png"])]
        self._run([_step("资产图", "asset", "asset_tasks")])
        self.assertEqual(self.calls, [])

    def test_already_done_means_not_picked(self):
        """产物在磁盘上的不捞 —— 重派是白撞。"""
        self.pj.tasks_by_key["asset_tasks"] = [_t("A", "x/A.png")]
        self._put("x/A.png")
        self._run([_step("资产图", "asset", "asset_tasks")])
        self.assertEqual(self.calls, [])

    # ------------------------------------------------------------ 不捞谁

    def test_fresh_hard_error_is_not_hammered(self):
        """★ 本趟真报过错的不再自动重试 —— 五秒后再发还是被拒。"""
        self.pj.tasks_by_key["asset_tasks"] = [_t("A", "x/A.png")]
        self._record("A", at=NEW)
        self._run([_step("资产图", "asset", "asset_tasks")])
        self.assertEqual(self.calls, [])

    def test_old_record_from_last_run_does_not_block(self):
        """★ 上一趟的旧账不拦本趟 —— 本趟开头那轮已经按磁盘重派过它了。"""
        self.pj.tasks_by_key["asset_tasks"] = [_t("A", "x/A.png")]
        self._record("A", at=OLD)
        self._run([_step("资产图", "asset", "asset_tasks")])
        self.assertEqual(self._picked("资产图"), ["A"])

    def test_ref_missing_is_exempt_even_when_fresh(self):
        """★ REF_MISSING 是「排早了撞空」，没花一分钱 —— 输入齐了就该重派。"""
        self.pj.tasks_by_key["asset_tasks"] = [
            _t("A", "x/A.png", refs=["x/up.png"])]
        self._put("x/up.png")
        self._record("A", code="REF_MISSING", at=NEW)
        self._run([_step("资产图", "asset", "asset_tasks")])
        self.assertEqual(self._picked("资产图"), ["A"])

    def test_failure_records_of_other_steps_do_not_block(self):
        """失败记录按 stage 过滤：别家的记录不拦这一步的补跑。"""
        self.pj.tasks_by_key["asset_tasks"] = [_t("A", "x/A.png")]
        self._record("V1", at=NEW, stage="video")
        self._run([_step("资产图", "asset", "asset_tasks")])
        self.assertEqual(self._picked("资产图"), ["A"])

    # ------------------------------------------------------------ 链式

    def test_chain_runs_through_in_one_round(self):
        """★ 前一步这一轮补出来的文件，后一步同一轮立刻接着用。"""
        self.pj.tasks_by_key["asset_tasks"] = [_t("A", "x/A.png")]
        self.pj.tasks_by_key["sb_tasks"] = [
            _t("SB", "x/SB.png", refs=["x/A.png"])]
        self._run([_step("资产图", "asset", "asset_tasks"),
                   _step("故事板", "storyboard", "sb_tasks")])
        self.assertEqual(self._picked("资产图"), ["A"])
        self.assertEqual(self._picked("故事板"), ["SB"])

    def test_deep_chain_runs_through_in_one_round(self):
        """A→B→C 三层：按生产顺序一轮通到底，不用一层跑一轮。"""
        self.pj.tasks_by_key["a"] = [_t("A", "x/A.png")]
        self.pj.tasks_by_key["b"] = [_t("B", "x/B.png", refs=["x/A.png"])]
        self.pj.tasks_by_key["c"] = [_t("C", "x/C.png", refs=["x/B.png"])]
        self._run([_step("一", "asset", "a"), _step("二", "asset", "b"),
                   _step("三", "asset", "c")])
        self.assertEqual(self._picked("一"), ["A"])
        self.assertEqual(self._picked("二"), ["B"])
        self.assertEqual(self._picked("三"), ["C"])
        # 一轮通完：第二轮没有任何可捞的，收工
        self.assertEqual(len(self.calls), 3)

    # ------------------------------------------------------------ 收敛

    def test_runaway_tasks_are_capped(self):
        """做不成的（又不落失败记录的极端情况）不会变成死循环。"""
        self.pj.tasks_by_key["asset_tasks"] = [_t("A", "x/A.png")]
        self._run([_step("资产图", "asset", "asset_tasks")],
                  produce=set())          # 永远做不成：既不落盘也不落记录
        self.assertEqual(len(self._picked("资产图")), 2)        # max_tries=2

    def test_should_stop_halts_immediately(self):
        self.pj.tasks_by_key["asset_tasks"] = [_t("A", "x/A.png")]
        sweep_redo(self.pj, [_step("资产图", "asset", "asset_tasks")],
                   self.relay,
                   run_step=lambda s, pick: self.calls.append(("x", [])),
                   todo_of=lambda s: self.pj.tasks().get(s["task_key"]) or [],
                   log=lambda s, m: None, epoch=EPOCH,
                   should_stop=lambda: True)
        self.assertEqual(self.calls, [])


class ReconcileTests(unittest.TestCase):

    def setUp(self):
        self.job = _FakeJob()

    def _todo(self, n):
        return [{"key": f"k{i}"} for i in range(n)]

    def test_failed_step_upgrades_to_ok_when_everything_landed(self):
        """★ 补跑把剩下的全做完了 → 升回 ok。不修的话整个 job 错报 error。"""
        s = _step("资产图", "asset", "asset_tasks")
        self.job.set_item("资产图", state="failed", msg="还有 3 项没做成")
        failed = ["资产图"]
        reconcile_produce_steps(self.job, failed, [s],
                                todo_of=lambda _: self._todo(0),
                                should_stop=lambda: False)
        self.assertEqual(self.job.items["资产图"]["state"], "ok")
        self.assertNotIn("资产图", failed)

    def test_ok_step_downgrades_to_failed_when_items_remain(self):
        """★ 补跑那轮全成了、步骤标了 ok，但初始轮真报错的还欠着 → 降回 failed。"""
        s = _step("资产图", "asset", "asset_tasks")
        self.job.set_item("资产图", state="ok", msg="1 项完成")
        failed = []
        reconcile_produce_steps(self.job, failed, [s],
                                todo_of=lambda _: self._todo(2),
                                should_stop=lambda: False)
        self.assertEqual(self.job.items["资产图"]["state"], "failed")
        self.assertIn("资产图", failed)

    def test_stopped_steps_are_left_alone(self):
        """skipped / cancelled / aborted 是事实，盖掉等于篡改结果。"""
        for st in ("skipped", "cancelled", "aborted", ""):
            s = _step(f"步-{st}", "asset", "asset_tasks")
            if st:
                self.job.set_item(f"步-{st}", state=st)
            failed = []
            reconcile_produce_steps(self.job, failed, [s],
                                    todo_of=lambda _: self._todo(5),
                                    should_stop=lambda: False)
            item = self.job.items.get(f"步-{st}") or {}
            self.assertEqual(item.get("state") or "", st)

    def test_failed_step_with_leftovers_stays_failed(self):
        s = _step("资产图", "asset", "asset_tasks")
        self.job.set_item("资产图", state="failed", msg="还有 3 项没做成")
        failed = ["资产图"]
        reconcile_produce_steps(self.job, failed, [s],
                                todo_of=lambda _: self._todo(3),
                                should_stop=lambda: False)
        self.assertEqual(self.job.items["资产图"]["state"], "failed")
        self.assertIn("资产图", failed)


if __name__ == "__main__":
    unittest.main()
