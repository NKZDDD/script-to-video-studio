# -*- coding: utf-8 -*-
"""改写中途停下来的原因，必须跟着那个失败一起出现在页面上。

用户问：「这个为什么就尝试 2 次呢」。

实际轮数是 5（`soften_rounds` 没填 → `DEFAULT_ROUNDS`），而它停在第 2 版。
页面上唯一留下的是上一轮改写成功那条「已改写第 2 版」—— 于是看起来像
「它只肯试两次」，而**没有一个字解释为什么停**。

提前停有三条路，以前三条都只写一行运行日志（job 的内存态，关掉就没了）：

  ① 这一轮改回来的东西没通过验收 → 扔掉，照原错抛出
  ② 这一次的失败没被认成审核问题 → 一轮都不改
  ③ 轮数用完，每轮都还是被拒

原因挂在**异常**上而不是自己记一条：`diagnose.record` 对同一个
(stage, target) 只保留最新，而这个异常紧接着会被上层记成一条失败 ——
自己先记一条只会被那条盖掉。
"""
import shutil
import tempfile
import unittest

from core import soften as S
from core.apiutil import ApiError
from core.store import Project


def rejection(msg="content policy violation: 儿童形象"):
    return ApiError(msg, status=400)


def boom(exc):
    """一个只会抛的 gen。"""
    def gen(_p):
        raise exc
    return gen


class FakeLLM:
    """要它改就给一版；`give=None` 表示改不出来。

    `vary=True` 时每轮多加一点字 —— **每轮返回同一段文本的话，第二轮就会
    被验收拦在「和上一版一模一样，没有推进」上**，于是永远走不到
    「轮数用完」那一支。（第一次写这个夹具就掉进去了。）
    """

    def __init__(self, give=None, vary=False):
        self.give = give
        self.vary = vary
        self.calls = 0

    def chat(self, system, user, **kw):
        self.calls += 1
        if self.give is None:
            return ""
        return self.give + ("　补充一点细节" * self.calls if self.vary else "")


class GiveUpTests(unittest.TestCase):

    PROMPT = ("资产名称：招娣受击\n"
              "Image 1 = C001 招娣\n"
              "MUST PRESERVE：年龄、面孔\n" + "描述" * 200)

    def setUp(self):
        self.pj = Project(tempfile.mkdtemp(prefix="soften-"))
        self.pj.init_dirs()
        self.logs = []

    def tearDown(self):
        shutil.rmtree(self.pj.root, ignore_errors=True)

    def _run(self, gen, llm, rounds=5):
        return S.run_with_softening(
            gen, self.PROMPT, pj=self.pj, llm=llm, kind="asset", key="ST001",
            rounds=rounds, log=self.logs.append)

    def _fix(self, exc):
        return "\n".join(getattr(exc, "extra_fix", None) or [])

    def _good(self):
        return self.PROMPT.replace("招娣受击", "招娣受挫") + "补一点字"

    # ---- ① 验收没过 ----

    def test_a_rejected_rewrite_no_longer_ends_the_ladder(self):
        """★ **2026-08-27 改了判断。** 用户原话：「下次遇到 400 这个问题，
        必须按照流程试满 12 次」。

        原来一版改写没过验收就放弃整条阶梯 —— 哪怕那是第 1 轮、后面还有
        11 轮没用。那是把两种失败搞混了：服务商拒绝走的是「同级再换一版、
        试满三次才降级」，而改写本身没写好也是「这一版没写好」，
        不是「这一级救不了」。

        现在轮数给多少就走满多少，报错里说清有几轮是卡在验收上、
        那几轮没发出图请求。
        """
        with self.assertRaises(ApiError) as e:
            self._run(boom(rejection()), FakeLLM("太短"))   # 必然过不了验收
        fix = self._fix(e.exception)
        self.assertIn("轮，每一轮都还是被拒", fix)
        self.assertIn("没通过验收", fix)
        self.assertIn("没发出图请求", fix)
        # 不该再出现「停在第 0 轮」那种「一次就收手」的说法
        self.assertNotIn("自动改写停在第 0 轮", fix)

    def test_it_still_names_the_acceptance_failure(self):
        """★ 卡在验收上和「服务商一直拒」是两回事，话要分得开 ——
        不然人会去改提示词，而真正的问题是改写回来的东西不合格。"""
        with self.assertRaises(ApiError) as e:
            self._run(boom(rejection()), FakeLLM("太短"))
        self.assertIn("没通过验收", self._fix(e.exception))

    # ---- ② 不是审核问题 ----

    def test_a_non_moderation_failure_says_so(self):
        """★ 以前这一条完全看不见。判错了的话（服务商这次只回一句笼统的

        「任务失败」），看起来就是「它只肯试两次」。
        """
        with self.assertRaises(ApiError) as e:
            self._run(boom(ApiError("connection reset by peer", status=0)),
                      FakeLLM("x" * 2000))
        self.assertIn("没被认成审核问题", self._fix(e.exception))

    def test_no_llm_says_so(self):
        with self.assertRaises(ApiError) as e:
            self._run(boom(rejection()), None)
        self.assertIn("没有可用的分析引擎", self._fix(e.exception))

    # ---- ③ 轮数用完 ----

    def test_using_every_round_says_that_instead(self):
        """★ 用完了就别说「还剩几轮没试」—— 那会让人去调轮数。"""
        with self.assertRaises(ApiError) as e:
            self._run(boom(rejection()), FakeLLM(self._good(), vary=True),
                      rounds=2)
        fix = self._fix(e.exception)
        self.assertIn("改写了 2 轮", fix)
        self.assertNotIn("还剩", fix)

    def test_zero_rounds_means_no_rewrite(self):
        """设置里填 0 就是关掉这个功能 —— 那也要说出来，别让人以为坏了。"""
        llm = FakeLLM(self._good())
        with self.assertRaises(ApiError) as e:
            self._run(boom(rejection()), llm, rounds=0)
        self.assertEqual(llm.calls, 0)
        self.assertIn("改写了 0 轮", self._fix(e.exception))

    # ---- 同一个坎 ----

    def test_the_same_rejection_every_round_is_called_out(self):
        """★ 儿童形象这类是**题材**，不是措辞 —— 再改十轮也过不了。

        不说的话人会去调轮数，而那是唯一不会起作用的旋钮。
        """
        with self.assertRaises(ApiError) as e:
            self._run(boom(rejection(
                "生成的图片可能违反了关于青少年与儿童形象适当描绘的防护限制")),
                FakeLLM(self._good(), vary=True), rounds=2)
        fix = self._fix(e.exception)
        self.assertIn("一模一样", fix)
        self.assertIn("题材", fix)
        self.assertIn("手动放图", fix)

    def test_a_changing_reason_is_not_called_a_wall(self):
        """★ 理由每轮都不一样时说「改措辞没用」是错的 —— 它在推进。"""
        self.assertFalse(S._same_wall(["血腥", "暴力"]))
        self.assertFalse(S._same_wall(["只有一条"]))
        self.assertFalse(S._same_wall([]))

    def test_only_digits_and_spaces_differing_still_counts_as_a_wall(self):
        """服务商常在判词里带请求号/时间 —— 那不算「理由变了」。"""
        self.assertTrue(S._same_wall([
            "policy violation req=1234 at 10:02",
            "policy violation req=9876 at 10:05"]))

    # ---- 成功那条路不受影响 ----

    def test_a_successful_rewrite_still_just_works(self):
        good = self._good()
        llm, seen = FakeLLM(good), []

        def gen(p):
            seen.append(p)
            if len(seen) == 1:
                raise rejection()
            return {"provider": "x", "model": "y"}

        self.assertEqual(self._run(gen, llm)["provider"], "x")
        self.assertEqual(len(seen), 2)
        self.assertEqual(seen[1], good, "第二次没用改写后那一版")

    def test_no_failure_means_no_rewrite_at_all(self):
        llm = FakeLLM("不该被用到")
        self.assertEqual(self._run(lambda p: {"ok": 1}, llm), {"ok": 1})
        self.assertEqual(llm.calls, 0)

    def test_an_exception_that_refuses_attributes_does_not_crash(self):
        """★ 挂不上属性就只剩日志 —— 不该因为「想多说一句」把整条弄崩。"""
        with self.assertRaises(OSError):
            self._run(boom(OSError("没法挂属性")), FakeLLM("太短"))


class CatalogTests(unittest.TestCase):

    def test_the_softened_title_no_longer_claims_it_was_produced(self):
        """★ 那条记录是在「改完、重发之前」写下的 —— 重发可能又被拒。

        用户实遇：页面上就这一条「已改写第 2 版」，而那一条其实没出图。
        """
        from core import diagnose as D
        self.assertNotIn("已经出图了", D.CATALOG["PROMPT_SOFTENED"]["title"])
        self.assertIn("不说重发成不成", D.CATALOG["PROMPT_SOFTENED"]["why"])

    def test_the_give_up_code_is_documented(self):
        """代号在记录里出现过，就得在目录里查得到 —— 否则搜不着。"""
        from core import diagnose as D
        self.assertIn("PROMPT_SOFTEN_GAVE_UP", D.CATALOG)
        self.assertIn("题材", D.CATALOG["PROMPT_SOFTEN_GAVE_UP"]["why"])


if __name__ == "__main__":
    unittest.main()
