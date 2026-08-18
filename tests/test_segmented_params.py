# -*- coding: utf-8 -*-
"""按段跑的环节，要真跑一遍。

用户实跑撞到：环节7 八段全部失败，每段都是

    name 'params' is not defined

`run_segmented` 里调 `system_prompt(pj, params)`，而 `params` 根本不在这个
函数的签名里。它藏得住是因为两层：

  · 那句话在 `one()` 里，被 `except Exception` 兜住 —— 每段记一条失败，
    整个流程继续往下走，进程不崩
  · 卡片报「没见过的错误 …… 查清楚之后点开始重试」，
    于是人去重试，八段又是同一句话

而**测试里从来没有真的跑过这个函数** —— 只测了它的调用方和 build_user。
这个用例就是补这一刀：塞一个假 LLM 进去，把整条路走通。
"""
import shutil
import unittest

from core import diagnose, stages as S
from test_v34_run import new_project


class FakeLLM:
    """只记下收到的 system，别的什么都不干。"""

    model = "fake"

    def __init__(self):
        self.systems = []

    def json_call(self, system, user, **kw):
        self.systems.append(system)
        return {"shots": [{"id": "X", "shot_list": [{"positions": "左"}],
                           "character_space_note": "n"}]}


class RunSegmentedTests(unittest.TestCase):

    def setUp(self):
        self.pj = new_project()
        self.segs = [{"id": "EP01-SEG01"}, {"id": "EP01-SEG02"}]

    def tearDown(self):
        shutil.rmtree(self.pj.root, ignore_errors=True)

    def _run(self, **over):
        kw = dict(stage_id="s7", out_name="s7_shots", key="shots",
                  segs=self.segs, done_ids=set(), llm=FakeLLM(),
                  build_user=lambda seg: "u", params={"duration": 15},
                  required=["shots[]"], log=lambda *_: None, episode="EP01",
                  cancel=None, seg_concurrency=1)
        kw.update(over)
        return S.run_segmented(self.pj, **kw), kw["llm"]

    def test_every_segment_actually_goes_through(self):
        """★ 这一条就是那个 bug —— 以前跑到这里抛 NameError。"""
        (result, failed, cancelled), llm = self._run()
        self.assertEqual(failed, [], f"有段失败了：{failed}")
        self.assertEqual(cancelled, [])
        self.assertEqual([c["id"] for c in result["shots"]],
                         ["EP01-SEG01", "EP01-SEG02"])
        self.assertEqual(len(llm.systems), 2)

    def test_the_project_settings_reach_the_system_prompt(self):
        """★ 光不报错不够 —— 设定得真的进到 system 里。

        漏传 params 的另一种长相是**静默降级**：每段都跑通、什么都不报，
        只是字幕规则、旁白、对白语言全按缺省走。那个比崩掉更难发现。
        """
        _, llm = self._run()
        self.assertTrue(all(s.strip() for s in llm.systems), "system 是空的")
        self.assertEqual(llm.systems[0], S.system_prompt(self.pj, {"duration": 15}))

    def test_params_is_not_optional(self):
        """★ 不给默认值 —— 漏传要当场 TypeError，不能悄悄用 None。"""
        import inspect
        p = inspect.signature(S.run_segmented).parameters["params"]
        self.assertIs(p.default, inspect.Parameter.empty)

    def test_both_incremental_stages_pass_it(self):
        """环节7 和环节8 是仅有的两个调用方，都得传。"""
        import inspect
        src = inspect.getsource(S)
        self.assertEqual(src.count("run_segmented("), 3, "调用方数量变了，这条要跟着改")
        for fn in ("run_s7_incremental", "run_s8_incremental"):
            body = inspect.getsource(getattr(S, fn))
            self.assertIn("params=params", body, fn)

    def test_one_broken_segment_does_not_stop_the_others(self):
        """原有行为不能被上面几条改掉。"""
        class Flaky(FakeLLM):
            def json_call(self, system, user, **kw):
                if len(self.systems) == 0:
                    self.systems.append(system)
                    raise RuntimeError("这一段炸了")
                return super().json_call(system, user, **kw)

        (result, failed, _), _ = self._run(llm=Flaky())
        self.assertEqual(failed, ["EP01-SEG01"])
        self.assertEqual([c["id"] for c in result["shots"]], ["EP01-SEG02"])


class AppBugIsNotRetryableTests(unittest.TestCase):
    """程序自己的 bug 要和「模型/网络出问题」分开报。

    这次的卡片写的是「查清楚之后点开始重试」—— 而重试八段得到的是
    同一句 `name 'params' is not defined`。**一次都不该重。**
    """

    def test_our_own_mistakes_are_recognised_by_type(self):
        for exc in (NameError("name 'params' is not defined"),
                    UnboundLocalError("x"),
                    AttributeError("'NoneType' object has no attribute 'get'"),
                    ImportError("no module named boto3"),
                    TypeError("f() missing 1 required keyword-only argument: 'params'"),
                    TypeError("f() got an unexpected keyword argument 'parms'")):
            self.assertTrue(diagnose.is_app_bug(exc), repr(exc))

    def test_external_failures_are_not_swept_in(self):
        """★ 别拦过头 —— 真该重试的还得重试。"""
        for exc in (RuntimeError("HTTP 524 A timeout occurred"),
                    ValueError("JSON 输出校验失败"),
                    TypeError("'<' not supported between instances of str and int"),
                    OSError("connection reset")):
            self.assertFalse(diagnose.is_app_bug(exc), repr(exc))

    def test_the_card_says_do_not_retry(self):
        d = diagnose.build(NameError("name 'params' is not defined"), stage="stage:s7")
        self.assertEqual(d["code"], "APP_BUG")
        self.assertFalse(d["resumable"])
        self.assertIn("别重试", " ".join(d["fix"]))
        self.assertFalse(diagnose.should_failover(d), "换服务商也是同一个错")

    def test_a_traceback_is_kept(self):
        """★ 「name 'params' is not defined」不说在哪一行 —— 光这一句没法修。"""
        try:
            raise NameError("name 'params' is not defined")
        except NameError as exc:
            d = diagnose.build(exc, stage="stage:s7")
        self.assertIn("Traceback", d["raw"])
        self.assertIn("test_segmented_params.py", d["raw"])

    def test_the_batch_runner_does_not_retry_it(self):
        import inspect

        from core import executor
        src = inspect.getsource(executor.run_batch)
        self.assertIn("diagnose.is_app_bug(exc)", src)
        self.assertIn("kind = TASK_FATAL", src)


if __name__ == "__main__":
    unittest.main()
