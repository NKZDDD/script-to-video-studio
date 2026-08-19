# -*- coding: utf-8 -*-
"""就绪即派：参考图齐了就能做，不等同层里那条慢的。

以前是分层跑 —— 层内并发、层间串行。正确但是慢：

    第1层  A(30秒)  B(1秒)  C(1秒)  …  30 项
    第2层  D(依赖 B)

`D` 只依赖 `B`，`B` 一秒就好了，可 `D` 要等 `A` 跑完 —— 等的是**整层**，
不是自己真正依赖的那几条。4K 出图、被审核挡了要改写重试几轮的，
一条就能把后面全拖住。

改成按条记依赖之后，`B` 一完成 `D` 立刻开跑，`A` 还在跑也不影响。

两个不能丢的性质：
  · 依赖顺序照旧 —— 参考图没好绝不开跑（这是分层当初要解决的问题）
  · 上游没做成时下游**当场标失败并说清等的是谁**，不再花一次调用去撞空文件
"""
import threading
import time
import unittest

from core import produce as P
from core.executor import Job, run_batch


def _t(key, refs=()):
    return {"key": key,
            "reference_images": [{"image_n": i + 1, "asset_id": r}
                                 for i, r in enumerate(refs)],
            "output": f"x/{key}.png"}


class DepsTests(unittest.TestCase):
    """依赖表：只记这一批里的。"""

    def test_it_lists_what_each_task_waits_for(self):
        deps = P.asset_deps([_t("A"), _t("B"), _t("C", ["A", "B"])])
        self.assertEqual(deps["C"], ["A", "B"])
        self.assertEqual(deps["A"], [])

    def test_references_outside_this_batch_are_not_waited_for(self):
        """★ 已经出好的不在这一批里 —— 等它就是永远等。"""
        deps = P.asset_deps([_t("C", ["A", "ALREADY_DONE"])] + [_t("A")])
        self.assertEqual(deps["C"], ["A"])

    def test_a_self_reference_is_reported_as_a_cycle(self):
        """自己把自己当参考图 = 死等。成环检查历来把它当单元素环报出来，

        这是对的 —— 那本身就是环节4 写错了，得让人看见。
        （执行器里还有一道 `d != key` 的兜底，防的是绕过这一层的调用。）
        """
        with self.assertRaises(P.AssetDependencyCycleError):
            P.asset_deps([_t("A", ["A"])])

    def test_a_cycle_is_reported_before_anything_runs(self):
        """★ 换了调度方式不代表环能跑了。

        环的表现会变成「谁都不就绪」—— 那种卡住比报错难查得多，
        所以照旧在派任务前就报出成员。
        """
        with self.assertRaises(P.AssetDependencyCycleError) as cm:
            P.asset_deps([_t("A", ["B"]), _t("B", ["A"])])
        self.assertIn("A", str(cm.exception))
        self.assertIn("B", str(cm.exception))


class _Run:
    """记录每条任务的开工/完工时刻，用来验证真的没有整层等待。"""

    def __init__(self, slow=(), fail=()):
        self.slow, self.fail = set(slow), set(fail)
        self.at: dict = {}
        self.lock = threading.Lock()

    def worker(self, task, log, cancel):
        key = task["key"]
        with self.lock:
            self.at[key] = [time.time(), None]
        if key in self.fail:
            raise RuntimeError("这条炸了")
        time.sleep(0.25 if key in self.slow else 0.01)
        with self.lock:
            self.at[key][1] = time.time()
        return {"output": task["output"]}

    def started(self, key):
        return self.at[key][0]

    def finished(self, key):
        return self.at[key][1]


def _job(n, conc=4):
    return Job("asset", n, conc, project_root="")


def _go(tasks, run, conc=4):
    job = _job(len(tasks), conc)
    deps = P.asset_deps(tasks)
    run_batch(job, tasks, run.worker, key_of=lambda t: t["key"],
              max_retry=0, deps_of=deps.get)
    return job


class DispatchTests(unittest.TestCase):

    def test_a_dependent_starts_as_soon_as_its_own_dep_is_done(self):
        """★ **这就是这次改动的全部意义。**

        D 只依赖 B。分层的话 D 要等 A（慢的那条）跑完；
        现在只等 B —— 所以 D 必须在 A 还没结束时就开工了。
        """
        tasks = [_t("A"), _t("B"), _t("D", ["B"])]
        run = _Run(slow=["A"])
        _go(tasks, run)
        self.assertLess(run.started("D"), run.finished("A"),
                        "D 等到 A 跑完才开工 —— 还是在按整层等")
        self.assertGreaterEqual(run.started("D"), run.finished("B"),
                                "D 在 B 出图之前就开工了 —— 依赖顺序破了")

    def test_the_dependency_order_still_holds(self):
        """★ 分层当初要解决的问题不许回来：参考图没好绝不开跑。"""
        tasks = [_t("A"), _t("B", ["A"]), _t("C", ["B"])]
        run = _Run()
        job = _go(tasks, run)
        self.assertGreaterEqual(run.started("B"), run.finished("A"))
        self.assertGreaterEqual(run.started("C"), run.finished("B"))
        self.assertEqual(job.counts().get("ok"), 3)

    def test_everything_independent_runs_together(self):
        """没有依赖的就该同时跑，不许被这套机制拖成串行。"""
        tasks = [_t(k) for k in "ABCD"]
        run = _Run(slow="ABCD")
        _go(tasks, run, conc=4)
        span = max(run.finished(k) for k in "ABCD") - min(run.started(k) for k in "ABCD")
        self.assertLess(span, 0.24 * 4, "四条独立任务跑成了串行")

    def test_a_dead_upstream_fails_its_dependent_without_calling_out(self):
        """★ 上游没做成，下游**不派**、当场标失败。

        以前会派 —— 那一条花一次调用去读一个不存在的参考图，
        报「参考图指不到文件」，人还得自己回头找是哪个上游没成。
        """
        tasks = [_t("A"), _t("B", ["A"])]
        run = _Run(fail=["A"])
        job = _go(tasks, run)
        self.assertNotIn("B", run.at, "B 被派出去了 —— 白花一次调用")
        self.assertEqual(job.items["B"]["state"], "failed")
        self.assertIn("A", job.items["B"]["msg"])

    def test_that_failure_says_what_to_do(self):
        tasks = [_t("A"), _t("B", ["A"])]
        job = _go(tasks, _Run(fail=["A"]))
        self.assertIn("参考图没做成", job.items["B"]["msg"])

    def test_a_sibling_of_a_dead_upstream_still_runs(self):
        """★ 一条上游死了不该连累无关的任务。"""
        tasks = [_t("A"), _t("B", ["A"]), _t("C")]
        job = _go(tasks, _Run(fail=["A"]))
        self.assertEqual(job.items["C"]["state"], "ok")

    def test_deep_chains_still_finish(self):
        tasks = [_t("A"), _t("B", ["A"]), _t("C", ["B"]), _t("D", ["C"])]
        job = _go(tasks, _Run(), conc=2)
        self.assertEqual(job.counts().get("ok"), 4)

    def test_it_never_hangs_when_nothing_can_run(self):
        """★ **最要紧的一条。** 谁都不就绪时必须报出来，绝不静默挂着。

        成环检查该拦住这种情况，但那是在上一层。这里再钉一道 ——
        「卡住」是最难查的失败方式，宁可报错。
        """
        tasks = [_t("A", ["B"]), _t("B", ["A"])]      # 绕过 asset_deps 的成环检查
        job = _job(2)
        run = _Run()
        run_batch(job, tasks, run.worker, key_of=lambda t: t["key"], max_retry=0,
                  deps_of=lambda k: {"A": ["B"], "B": ["A"]}[k])
        self.assertEqual(job.items["A"]["state"], "failed")
        self.assertIn("环", job.items["A"]["msg"])

    def test_without_deps_it_behaves_exactly_as_before(self):
        """不给 deps_of 就走老路 —— 故事板和视频那两类没有内部依赖。"""
        tasks = [_t(k) for k in "AB"]
        job = _job(2)
        run = _Run()
        run_batch(job, tasks, run.worker, key_of=lambda t: t["key"], max_retry=0)
        self.assertEqual(job.counts().get("ok"), 2)


class FailoverTests(unittest.TestCase):
    """换家补跑时，上一家已经出好的不在这一批里了。"""

    def test_deps_already_produced_by_the_previous_provider_are_not_waited_for(self):
        """★ 不过滤的话它们状态查出来是空，依赖它们的会被误报成环。

        这是这次改动最容易踩的坑：run_chain 换家时 tasks 只剩没做成的那些。
        """
        tasks = [_t("B", ["A"])]              # A 上一家已经出好了，不在这批里
        job = _job(1)
        run = _Run()
        run_batch(job, tasks, run.worker, key_of=lambda t: t["key"], max_retry=0,
                  deps_of=lambda k: ["A"])
        self.assertEqual(job.items["B"]["state"], "ok",
                         "把批外已完成的当成要等的了 —— 整批会卡死或误报成环")

    def test_run_chain_passes_deps_through(self):
        """传了不接 / 接了不传，都等于这次改动没生效。"""
        import inspect

        from core import executor as E
        self.assertIn("deps_of=deps_of", inspect.getsource(E.run_chain))

    def test_all_three_schedulers_use_it(self):
        import inspect

        from core import pipeline as P61
        from core import pipeline_v34 as P34
        self.assertIn("asset_deps", inspect.getsource(P61.run))
        self.assertIn("asset_deps", inspect.getsource(P34.run))
        import io
        import os
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        app = io.open(os.path.join(root, "server", "app.py"), encoding="utf-8").read()
        self.assertIn("S.asset_deps(pending)", app)


if __name__ == "__main__":
    unittest.main()
