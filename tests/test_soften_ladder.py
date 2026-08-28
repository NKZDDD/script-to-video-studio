# -*- coding: utf-8 -*-
"""改写阶梯要真的走完：四级 × 每级三次。

用户实遇（2026-08-27）：三条资产图分别停在「已自动改写第 1 / 2 / 3 版」，
而设置里给的是 12 轮。问「为什么这个失败不会跑完 12 次为什么会触发早停」。

原因：**我们自己那道「改写验收」不过，就直接放弃整条阶梯了** ——
哪怕那是第 1 轮、后面还有 11 轮没用。

    new = soften(...)
    if not new:        # 空 / 和上一版一样 / 只回了片段 / 短太多 / 身份映射被动过
        _gave_up(...); raise      # ← 整条阶梯就此结束

那是把两种失败搞混了：服务商拒绝走的是「同级再换一版，试满三次才降级」，
而改写本身没写好**也是「这一版没写好」，不是「这一级救不了」** ——
改写是生成动作，有随机性，同级再来一版常常就过了。

但也不能无限试（每轮都是一次真金白银的分析调用），所以连续 ATTEMPTS_PER_TIER
次都过不了才放弃 —— 那时候可以断定不是写法问题，是这份提示词改不动
（ID 和身份映射占了大半，任何改写都会碰到那几行）。
"""
import unittest

from core import soften
from core.apiutil import ApiError


class LadderTests(unittest.TestCase):

    def _run(self, ok_at, rounds=12, origin=None):
        """ok_at(round_no) -> 这一轮的改写过不过验收。返回调用计数。"""
        calls = {"gen": 0, "soft": 0, "rounds_seen": []}

        def gen(_p):
            calls["gen"] += 1
            raise ApiError("您的请求无法用于生成图像，已被拦截", status=400)

        real = soften.soften

        def fake(used, _why, **kw):
            calls["soft"] += 1
            n = kw["round_no"]
            calls["rounds_seen"].append(n)
            return (used + f"（第{n}版）") if ok_at(n) else ""

        soften.soften = fake
        try:
            soften.run_with_softening(
                gen, origin or ("原文" * 200), pj=None, llm=object(),
                kind="asset", key="X", rounds=rounds, log=lambda *a: None)
        except Exception:                                   # noqa: BLE001
            pass
        finally:
            soften.soften = real
        return calls

    def test_it_uses_every_round_when_rewrites_pass(self):
        """★ 服务商一直拒、改写每次都过 → 12 轮一轮不少。"""
        got = self._run(lambda n: True)
        self.assertEqual(got["soft"], 12)
        self.assertEqual(got["rounds_seen"], list(range(1, 13)))

    def test_one_bad_rewrite_does_not_end_the_ladder(self):
        """★ 这一条就是用户撞上的：第 2 轮没写好，以前就此结束。"""
        got = self._run(lambda n: n != 2)
        self.assertEqual(got["soft"], 12)

    def test_even_all_rewrites_failing_uses_every_round(self):
        """★ 用户原话（2026-08-27）：「下次遇到 400 这个问题，必须按照流程
        试满 12 次」。所以连一次都没过验收，也要把 12 轮走满 ——
        不设「连续 N 次就收手」那种上限。

        代价说清：过不了验收的那几轮各花一次分析调用，而且不发出图请求
        （没有可用的新版本可发）。这一条是用户明确要的取舍。
        """
        got = self._run(lambda n: False)
        self.assertEqual(got["soft"], 12)

    def test_the_streak_resets_after_a_good_one(self):
        """★ 三次是「连续」不是「累计」—— 累计的话前面偶尔失手几次，
        后面正常的轮数也被没收了。"""
        bad = {2, 4, 7}          # 分散的三次，中间都有通过的
        got = self._run(lambda n: n not in bad)
        self.assertEqual(got["soft"], 12)

    def test_the_ladder_covers_four_tiers(self):
        """★ 12 轮正好把四级走完 —— 少了就走不到最深那级。"""
        self.assertEqual(len(soften.TIERS), 4)
        self.assertEqual(soften.DEFAULT_ROUNDS,
                         len(soften.TIERS) * soften.ATTEMPTS_PER_TIER)
        self.assertEqual(soften.tier_of(1)[1], soften.TIERS[0][0])
        self.assertEqual(soften.tier_of(12)[1], soften.TIERS[-1][0])

    def test_a_rejected_version_is_not_carried_forward(self):
        """★ 扔掉的那一版不能当作下一轮的底稿 —— 它可能是个片段，
        接着改下去会一路缩水，而每一步单看都合格。"""
        seen = []

        def gen(p):
            seen.append(len(p))
            raise ApiError("已被拦截", status=400)

        real = soften.soften

        def fake(used, _why, **kw):
            n = kw["round_no"]
            return "" if n == 1 else used + "尾"

        soften.soften = fake
        try:
            soften.run_with_softening(gen, "原文" * 200, pj=None,
                                      llm=object(), kind="asset", key="X",
                                      rounds=4, log=lambda *a: None)
        except Exception:                                   # noqa: BLE001
            pass
        finally:
            soften.soften = real
        # 第 1 轮被扔 → 第 2 次发出去的还是原文那个长度
        self.assertEqual(seen[0], seen[1])

    def test_no_llm_still_gives_up_at_once(self):
        """没有分析引擎时一轮都不该改（也不该假装试了三次）。"""
        calls = {"n": 0}

        def gen(_p):
            calls["n"] += 1
            raise ApiError("已被拦截", status=400)

        with self.assertRaises(ApiError):
            soften.run_with_softening(gen, "原文" * 200, pj=None, llm=None,
                                      kind="asset", key="X", rounds=12,
                                      log=lambda *a: None)
        self.assertEqual(calls["n"], 1)
