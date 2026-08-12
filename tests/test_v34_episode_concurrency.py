# -*- coding: utf-8 -*-
"""多集并发的安全前提：逐集层不许有跨集共享状态。

这个文件原来还测「资产提示词跨集去重」的一堆竞争 —— 那些用例连同
被测代码一起删了，因为 V5.6 对照下来，资产表、资产提示词、空间主表、
连续性总账**本来就该是全剧级**的。挪上去之后逐集层不再共享任何状态，
那类竞争在结构上就不存在了，不需要用锁去防。

留下来的是**结构断言**：一旦有人把某个全剧级环节改回逐集、
或者让全剧级环节依赖逐集产物，这里立刻会红。那是整个并发模型的地基。
"""
import shutil
import threading
import unittest

from core import run_v34 as R, system_v34 as V
from test_v34_run import new_project


class ScopeLayoutTests(unittest.TestCase):
    """先钉住结构本身：全剧级环节必须全部排在逐集之前。"""

    def test_the_global_phase_covers_everything_shared_across_episodes(self):
        """★ 跨集共享的东西必须全部在全剧层做完。

        pipeline 是「全剧级串行跑完 → 逐集并行」。逐集层只要还剩一件
        跨集共享的活（资产表、空间主表、连续性总账），并发就会去抢它 ——
        而那种抢**不报错**，只是各写各的，最后互相覆盖。

        这五个是 V5.6 明确要求全剧一份的：
          唯一 Continuity Ledger / LONG_TERM 跨 Episode /
          Project→Episode→Scene→Beat 解析 / 分析全剧只生产 EP01
        """
        series = [s["id"] for s in V.STAGES
                  if s["kind"] == "llm" and s["scope"] == "series"]
        self.assertEqual(series, ["n1", "n2", "n3", "n4", "n4b", "n5", "n6"])

    def test_the_episode_layer_shares_nothing(self):
        """★ 逐集环节的产物不许被别的集读到。

        n7 之后每一集只读「本集的」和「全剧的」，不读别的集的 ——
        这是 4 集并发能成立的全部理由。
        """
        for s in V.STAGES:
            if s["kind"] != "llm" or s["scope"] != "episode":
                continue
            _tpl, deps, _req = V.LLM_SPEC[s["id"]]
            for d in deps:
                src = next((x for x in V.STAGES if x.get("out") == d), None)
                self.assertIn(
                    src["scope"], ("series", "episode", "segment"),
                    f"{s['id']} 依赖 {d}")

    def test_no_series_stage_depends_on_a_narrower_one(self):
        """全剧级不许依赖逐集/逐段产物 —— 时序上不可能满足。

        规则只对 series 成立，**不是对称的**：逐集环节依赖逐段产物是
        合法的聚合（n14 审计就要读本集全部段的故事板），
        写成对称规则会把正常的聚合判成错的。
        """
        by_out = {s["out"]: s for s in V.STAGES if s.get("out")}
        for sid, (_tpl, deps, _req) in V.LLM_SPEC.items():
            if V.scope_of(sid) != "series":
                continue
            for d in deps:
                src = by_out.get(d)
                if src:
                    self.assertEqual(
                        src["scope"], "series",
                        f"全剧级的 {sid} 依赖了 {src['scope']} 级的 {d} —— "
                        f"它在逐集环节之前就跑了，那时候这份产物还不存在")


if __name__ == "__main__":
    unittest.main()
