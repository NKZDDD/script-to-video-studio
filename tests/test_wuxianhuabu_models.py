# -*- coding: utf-8 -*-
"""无限画布：模型清单和逐模型约束**从实拉来**，不照文档抄。

2026-09-01 实拉 `GET /v1/models`，和上一版照文档写的差了好几处，
每一处的失败都是静默的：

  · 只声明了 2 个模型，实际 5 个 —— 另外三个页面上根本选不到
  · 声明了 `21:9`，而**一个模型都不支持**
  · 比例和时长原来全局一份，实际逐模型不同 —— 按全局那份填，
    2.0 系列选 20 秒、2.5gs 选 10 秒都会被拒，而那两个值是**我们给的候选**
"""
import unittest

from core.apiutil import ApiError
from core.providers.base import VideoTask
from core.providers.wuxianhuabu import MODELS, RATIOS, WuxianhuabuProvider

# 实拉那一份的关键数（2026-09-01，GET /v1/models 的 capability_schema）。
# 抄在这里是为了「哪天有人手改了 MODELS，这里会亮」——
# 亮了就该重新实拉一次，而不是把这份跟着改。
LIVE = {
    "seedance-2.5-hf-720p":  {"ratios": ["16:9", "9:16", "4:3", "1:1"], "dur": (4, 30)},
    "seedance-2.5gs 720p":   {"ratios": ["16:9", "9:16", "1:1", "4:3", "3:4"], "dur": (15, 30)},
    "seedance-2.0-r-720P":   {"ratios": ["16:9", "9:16", "1:1", "4:3", "3:4"], "dur": (4, 15)},
    "seedance-2.0-F-r-720P": {"ratios": ["16:9", "9:16", "1:1", "4:3", "3:4"], "dur": (4, 15)},
}


class WuxianhuabuModelTests(unittest.TestCase):
    def test_every_model_matches_what_the_platform_declared(self):
        """★ 声明 == 实拉。差一点就是页面给了一个会被拒的候选。"""
        for m, want in LIVE.items():
            self.assertIn(m, MODELS, f"{m} 没进清单 —— 页面上选不到")
            got = MODELS[m]
            self.assertEqual(got["ratios"], want["ratios"], m)
            self.assertEqual((min(got["durations"]), max(got["durations"])),
                             want["dur"], m)

    def test_no_model_offers_a_ratio_nobody_supports(self):
        """★ `21:9` 一个模型都不收 —— 原来它在全局候选里。

        选了它请求会被拒，或者网关自己挑一个 —— 后者出来的片子不是这个
        画幅，而任务标成功。
        """
        self.assertNotIn("21:9", RATIOS)
        for m, v in MODELS.items():
            self.assertNotIn("21:9", v["ratios"], m)

    def test_the_global_list_is_the_union_not_a_filter(self):
        """全局那份只说「这家总体收什么」，判合不合法要按模型。

        拿并集去判等于全放行 —— 2.5-hf-720p 会收下 3:4，而它不支持。
        """
        union = set()
        for v in MODELS.values():
            union |= set(v["ratios"])
        self.assertEqual(set(RATIOS), union)
        self.assertNotIn("3:4", MODELS["seedance-2.5-hf-720p"]["ratios"],
                         "2.5-hf-720p 实拉里没有 3:4")

    def test_options_are_offered_per_model(self):
        """页面按选中的模型换候选，不给并集。"""
        opts = WuxianhuabuProvider().capabilities()["video"]["model_options"]
        for m in MODELS:
            self.assertIn(m, opts)
            self.assertEqual(opts[m]["ratios"], MODELS[m]["ratios"])
            self.assertEqual(opts[m]["durations"], MODELS[m]["durations"])

    def test_out_of_range_is_refused_before_the_request(self):
        """★ 发之前就停，而且一次说全。

        一条一条报要跑好几趟；而这些在发请求之前就知道。
        """
        p = WuxianhuabuProvider(api_key="x")

        def go(**kw):
            t = VideoTask(prompt=kw.pop("prompt", "正文"),
                          refs=kw.pop("refs", ["https://x/a.png"]),
                          duration=kw.pop("duration", 10),
                          ratio=kw.pop("ratio", "9:16"),
                          model=kw.pop("model"))
            with self.assertRaises(ApiError) as c:
                p.generate_video(t, "out.mp4", log=lambda *a: None)
            return str(c.exception)

        # 2.0 系列上限 15 秒
        self.assertIn("4–15 秒", go(model="seedance-2.0-r-720P", duration=20))
        # 2.5gs 下限 15 秒
        self.assertIn("15–30 秒", go(model="seedance-2.5gs 720p", duration=10))
        # 2.5-hf-720p 不收 3:4
        self.assertIn("3:4", go(model="seedance-2.5-hf-720p", ratio="3:4"))
        # 2.5gs 至少要一张参考图
        self.assertIn("至少 1 张", go(model="seedance-2.5gs 720p",
                                     duration=20, refs=[]))
        # 2.5gs 提示词 8000 字上限
        self.assertIn("8000", go(model="seedance-2.5gs 720p", duration=20,
                                 prompt="字" * 8001))
        # 一次说全：时长和比例都错时两条都要出现
        msg = go(model="seedance-2.0-r-720P", duration=25, ratio="21:9")
        self.assertIn("秒", msg)
        self.assertIn("21:9", msg)

    def test_an_unlisted_model_still_goes_out(self):
        """★ 那张表是**候选**，不是白名单。

        用户原话（2026-09-01）：「声明两个的时候会不会导致我填写其他模型名
        无法使用，这不是我想要的，因为会导致我新增模型的时候一定需要修改代码」。

        对。平台随时上新（这次实拉就多出三个），写死白名单等于「平台上新，
        你就得改代码」。页面上模型框本来就是自由输入 + 候选（datalist），
        这一层也照办：表外的原样发，只在日志里说一声不校验。
        """
        from unittest import mock
        sent, logs = {}, []

        def fake(self, method, path, **kw):
            if path == "/v1/assets":
                return {"asset_id": "a1"}
            sent.clear()
            sent.update(kw.get("json_body") or {})
            return {"id": "t1", "video_url": "https://x/v.mp4"}

        p = WuxianhuabuProvider(api_key="k")
        t = VideoTask(prompt="正文", refs=["https://x/a.png"], duration=27,
                      ratio="21:9", model="seedance-3.0-ultra-1080P")
        with mock.patch.object(type(p.session), "request", fake),              mock.patch.object(type(p.session), "save_item", lambda *a, **k: None):
            p.generate_video(t, "out.mp4", log=logs.append)
        self.assertEqual(sent["model"], "seedance-3.0-ultra-1080P")
        self.assertEqual(sent["seconds"], 27)      # 表外的不按 4-30 削
        self.assertEqual(sent["ratio"], "21:9")    # 也不按并集拦
        self.assertEqual(sent.get("resolution"), "1080p")   # 名字里认出来的
        self.assertTrue([l for l in logs if "不校验" in l],
                        "表外的模型放行了，但日志没说这一趟没校验")

    def test_an_unnamed_resolution_is_left_out_of_the_body(self):
        """名字里认不出分辨率就**不填这个字段**。

        随手填一个的后果是「片子出得来、分辨率不是你要的」，而且不报错。
        """
        from unittest import mock
        sent = {}

        def fake(self, method, path, **kw):
            if path == "/v1/assets":
                return {"asset_id": "a1"}
            sent.clear()
            sent.update(kw.get("json_body") or {})
            return {"id": "t1", "video_url": "https://x/v.mp4"}

        p = WuxianhuabuProvider(api_key="k")
        t = VideoTask(prompt="正文", refs=[], duration=10, ratio="9:16",
                      model="一个看不出分辨率的名字")
        with mock.patch.object(type(p.session), "request", fake),              mock.patch.object(type(p.session), "save_item", lambda *a, **k: None):
            p.generate_video(t, "out.mp4", log=lambda *a: None)
        self.assertNotIn("resolution", sent)

    def test_the_interface_wide_caps_apply_to_unlisted_models_too(self):
        """★ 张数上限对表外的也判。

        30/10/10 是**整家的接口上限**，不是某个模型的脾气。超了服务商会截掉
        多的，而截掉的正是排在后面的那几张 —— 画面用错参考却标成功。
        """
        p = WuxianhuabuProvider(api_key="k")
        t = VideoTask(prompt="正文", refs=[f"https://x/{i}.png" for i in range(31)],
                      duration=10, ratio="9:16", model="全新的模型")
        with self.assertRaises(ApiError) as c:
            p.generate_video(t, "out.mp4", log=lambda *a: None)
        self.assertIn("31 张", str(c.exception))

    def test_the_unverified_resolution_is_marked_as_such(self):
        """`seedance-2.5-hf` 自己什么都没声明 —— 480p 是文档里写的。

        不标出来的话，「我们写的」看起来就像「它说的」。
        """
        notes = WuxianhuabuProvider().capabilities()["video"]["notes"]
        self.assertIn("实拉没有背书", notes)


if __name__ == "__main__":
    unittest.main()
