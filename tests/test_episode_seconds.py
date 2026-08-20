# -*- coding: utf-8 -*-
"""每集多少秒：用户指定了就是硬的，不经过模型。

用户实遇：把「每集 3 分钟」写在**特殊要求**里，环节1 不听。

那是必然的 —— 特殊要求是给模型看的一句话，而环节1 的模板原则写着
「按剧情事件定秒数，不要按字数换算」。两句话打架时模型听自己那一条，
而且**不报错**，只是时长不是你要的。

所以这一条落在代码里：环节1 照旧按剧情给它的判断（`pacing_note` 留着），
程序在切集之后把秒数覆盖掉。
"""
import unittest

from core import episodes as E
from core import settings as S


def _res(*pairs):
    return {"episodes": [{"episode": ep, "duration_sec": sec, "chars": chars,
                          "pacing_note": "环节1 的理由"}
                         for ep, sec, chars in pairs],
            "issues": []}


class ForceTests(unittest.TestCase):

    def test_zero_means_do_not_touch(self):
        """★ 默认必须是 0 = 不指定 —— 现有项目的行为不许被这次改动改掉。"""
        out = E.force_seconds(_res(("EP01", 180, 3000), ("EP02", 240, 4000)), 0)
        self.assertEqual([e["duration_sec"] for e in out["episodes"]], [180, 240])
        self.assertNotIn("episode_seconds_forced", out)

    def test_a_number_overrides_every_episode(self):
        """★ 这就是那个硬性要求。"""
        out = E.force_seconds(_res(("EP01", 180, 3000), ("EP02", 240, 4000)), 120)
        self.assertEqual([e["duration_sec"] for e in out["episodes"]], [120, 120])
        self.assertEqual(out["episode_seconds_forced"], 120)

    def test_what_stage_one_wanted_is_kept(self):
        """★ 不留一份的话，「为什么每集都一样长」查不出来源。"""
        out = E.force_seconds(_res(("EP01", 180, 3000)), 120)
        self.assertEqual(out["episodes"][0]["duration_sec_by_stage1"], 180)

    def test_an_episode_already_at_that_length_is_not_marked(self):
        out = E.force_seconds(_res(("EP01", 120, 3000)), 120)
        self.assertNotIn("duration_sec_by_stage1", out["episodes"][0])

    def test_the_default_is_zero(self):
        f = next(x for x in S.FIELDS if x["key"] == "episode_seconds")
        self.assertEqual(f["default"], 0)
        self.assertEqual(f["source"], "settings")

    def test_the_field_says_special_notes_does_not_work(self):
        """★ 那正是用户踩的坑，说明里必须写出来。"""
        f = next(x for x in S.FIELDS if x["key"] == "episode_seconds")
        self.assertIn("特殊要求", f["why"])
        self.assertIn("重跑环节1", f["why"])


class DensityRecheckTests(unittest.TestCase):
    """覆盖秒数之后那条体检要按新秒数重算。"""

    def test_the_warning_uses_the_new_seconds(self):
        """★ 不重算的话 issues 里留的是按旧秒数算的比例 ——

        数字对不上，而人会照着它判断，比没有提醒更坏。
        """
        out = E.force_seconds(_res(("EP01", 600, 800)), 60)
        # 800 字撑 60 秒 = 每分钟 800 字，正常；撑 600 秒才是每分钟 80 字
        self.assertEqual(out["issues"], [], "按新秒数算是正常的，不该再报")

    def test_it_warns_when_the_forced_length_cannot_hold_the_text(self):
        out = E.force_seconds(_res(("EP01", 600, 9000)), 60)
        self.assertEqual(len(out["issues"]), 1)
        r = out["issues"][0]["reason"]
        self.assertIn("你在设置里指定的", r)
        self.assertIn("塞不下", r)
        self.assertIn("600", r, "没说环节1 本来想给多少")

    def test_it_warns_when_the_text_cannot_fill_the_forced_length(self):
        out = E.force_seconds(_res(("EP01", 120, 300)), 600)
        self.assertIn("撑不满", out["issues"][0]["reason"])

    def test_the_old_stale_warning_is_dropped(self):
        """★ 旧的那条（按环节1 秒数算的）必须清掉，不能两条并存。"""
        res = _res(("EP01", 600, 800))
        res["issues"] = [{"episode": "EP01", "reason": "正文 800 字要撑 600 秒 = "
                                                      "每分钟 80 字"}]
        out = E.force_seconds(res, 60)
        self.assertEqual(out["issues"], [])

    def test_other_issues_survive(self):
        """别把切集的报错一起清掉 —— 那些和时长无关。"""
        res = _res(("EP01", 600, 800))
        res["issues"] = [{"episode": "EP01", "reason": "在剧本里找不到这一行：xxx"}]
        out = E.force_seconds(res, 60)
        self.assertEqual(len(out["issues"]), 1)


class WiringTests(unittest.TestCase):

    def test_build_applies_it(self):
        import inspect
        src = inspect.getsource(E.build)
        self.assertIn("force_seconds(res", src)
        # 每集秒数现在从那份三量计划里取，不再直接读 episode_seconds ——
        # 因为它可能是「总时长 ÷ 集数」算出来的。
        self.assertIn("plan_lengths", src)
        self.assertIn('plan["per"]', src)

    def test_both_stage_one_templates_are_told(self):
        """★ 只在代码里覆盖、不告诉环节1 的话，它会按自己算的时长规划事件密度 ——

        然后秒数被改掉，事件密度和实际长度对不上。
        """
        import io
        import os
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for tpl in ("n1_truth", "s1_global"):
            text = io.open(os.path.join(root, "prompts", f"{tpl}.md"),
                           encoding="utf-8").read()
            # 换成了 {{LENGTH_PLAN}}：三个量（总时长/集数/每集）是互相决定的，
            # 单独发一个「每集多少秒」说不出「集数按算出来的、不按剧本章节」。
            self.assertIn("{{LENGTH_PLAN}}", text, tpl)
            self.assertIn("不听剧本里的章节数", text, tpl)

    def test_the_placeholder_resolves(self):
        """占位符要真能渲染出值 —— 渲染不出来的话模板里会留一个 {{...}}。"""
        self.assertIn("episode_seconds", {f["key"] for f in S.FIELDS})


if __name__ == "__main__":
    unittest.main()
