# -*- coding: utf-8 -*-
"""无限画布：模型清单和逐模型约束**从实拉来**，不照文档抄。

2026-09-01 实拉 `GET /v1/models`，和上一版照文档写的差了好几处，
每一处的失败都是静默的：

  · 只声明了 2 个模型，实际 5 个 —— 另外三个页面上根本选不到
  · 声明了 `21:9`，而**一个模型都不支持**
  · 比例和时长原来全局一份，实际逐模型不同 —— 按全局那份填，
    2.0 系列选 20 秒、2.5gs 选 10 秒都会被拒，而那两个值是**我们给的候选**
"""
import io
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
        self.assertIn("31 条", str(c.exception))
        self.assertIn("这个模型没单独声明", str(c.exception))

    def _sent(self, **kw):
        from unittest import mock
        sent = {}

        def fake(self, method, path, **k):
            if path == "/v1/assets":
                return {"asset_id": "a1"}
            sent.clear()
            sent.update(k.get("json_body") or {})
            return {"id": "t1", "video_url": "https://x/v.mp4"}

        p = WuxianhuabuProvider(api_key="k")
        t = VideoTask(prompt="正文", refs=["https://x/a.png"],
                      duration=kw.pop("duration", 10), ratio=kw.pop("ratio", "9:16"),
                      model=kw.pop("model"), resolution=kw.pop("resolution", ""))
        with mock.patch.object(type(p.session), "request", fake),              mock.patch.object(type(p.session), "save_item", lambda *a, **k: None):
            p.generate_video(t, "o.mp4", log=lambda *a: None)
        return sent

    def test_an_explicit_resolution_wins(self):
        """★ 页面上选的清晰度盖过表里的。

        `task.resolution` 一直是一等公民（八家 provider 在读它），可页面上
        从来没有地方能填 —— 它只从服务商配置里取。于是换一个需要别的清晰度
        的模型时只能去改配置文件。用户原话（2026-09-01）：「像无限画布这种有
        清晰度作为参数内容的也需要把参数展示出来并且可选，否则我新增模型的
        时候需要不同的清晰度时不能选择」。

        优先级：**选的 > 表里记的 > 从名字认的 > 不填**。
        「不填」是有意义的一档 —— 让平台用它自己的默认，比我们蒙一个强。
        """
        # 表外的新模型：选什么发什么
        self.assertEqual(self._sent(model="全新模型", resolution="1080p")["resolution"],
                         "1080p")
        # 表外 + 没选 + 名字看不出 → 不填，别蒙
        self.assertNotIn("resolution", self._sent(model="全新模型"))
        # 表里的：没选就用表里的
        self.assertEqual(self._sent(model="seedance-2.5-hf")["resolution"], "480p")

    def test_a_resolution_the_model_does_not_have_is_refused(self):
        """选了这个模型没有的那一档 → 当场停。

        发出去多半是「片子出得来、清晰度不是你要的」，而且不报错。
        """
        with self.assertRaises(ApiError) as c:
            self._sent(model="seedance-2.5-hf-720p", resolution="480p")
        self.assertIn("只有 720p", str(c.exception))

    def test_the_page_offers_resolution_as_free_input(self):
        """★ 清晰度框是**自由输入 + 候选**，不是纯下拉。

        纯下拉会回到同一个坑：表外的新模型要 1080p，而候选里只有这家现有
        模型的 480p/720p —— 「选不了」正是要修的那件事。
        """
        import os
        import re
        page = io.open(os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "web", "index.html"), encoding="utf-8").read()
        m = re.search(r"const res = \(selected\.resolutions[\s\S]{0,700}?: '';", page)
        self.assertIsNotNone(m, "找不到清晰度那个框")
        blk = m.group(0)
        self.assertIn('<input class="v-res"', blk, "又变回纯下拉了")
        self.assertIn("datalist", blk)
        # 这家没声明 resolutions 时不摆空框 —— 空框比没有更糟
        self.assertIn(": ''", blk)

    def _run(self, model, n_img=1, n_vid=0, n_aud=0, duration=None):
        from unittest import mock
        lim = MODELS.get(model) or {}
        d = duration if duration is not None else (
            lim["durations"][0] if lim.get("durations") else 10)
        p = WuxianhuabuProvider(api_key="k")
        t = VideoTask(
            prompt="正文", duration=d, ratio="9:16", model=model,
            refs=[f"https://x/{i}.png" for i in range(n_img)],
            extra={"video_refs": [f"https://x/{i}.mp4" for i in range(n_vid)],
                   "audio_refs": [f"https://x/{i}.wav" for i in range(n_aud)]})

        def fake(self, m, path, **kw):
            return ({"asset_id": "a"} if path == "/v1/assets"
                    else {"id": "t1", "video_url": "https://cdn/out.mp4"})

        with mock.patch.object(type(p.session), "request", fake),              mock.patch.object(type(p.session), "save_item", lambda *a, **k: None):
            p.generate_video(t, "o.mp4", log=lambda *a: None)

    def test_the_total_asset_cap_is_checked(self):
        """★ 三类**加起来**还有一个总数，它声明了而我们一直没判。

        图 30 + 视频 10 + 音频 10 各自都不超，加起来正好 50 —— 再多一条，
        分类那三道都放行，只有总数这一道拦得住。
        """
        self.assertIn("max_assets", MODELS["seedance-2.5gs 720p"])
        self._run("seedance-2.5gs 720p", 30, 10, 10)          # 正好 50，放行
        with self.assertRaises(ApiError) as c:
            self._run("seedance-2.5gs 720p", 30, 10, 11)
        self.assertIn("一共 51", str(c.exception))
        self.assertIn("总数上限 50", str(c.exception))

    def test_undeclared_limits_are_not_invented(self):
        """★ 它没声明的数，表里就不许有。

        第一版我给全部五个模型都填了 30/10/10 —— 而实际只有
        `seedance-2.5gs 720p` 声明了这三个数，另外四个只声明了
        `max_reference_assets: 50`，还有两个连这个都没有。
        填一个「看起来合理」的数和照文档抄没有区别：它会显示在页面上、
        会拿去拦人，而没有任何东西背书。
        """
        declared_per_type = {"seedance-2.5gs 720p"}
        for m, v in MODELS.items():
            for k in ("max_images", "max_videos", "max_audios",
                      "min_images", "max_prompt"):
                if m not in declared_per_type:
                    self.assertNotIn(k, v, f"{m} 的 {k} 它没声明，不该出现在表里")
        # 只有这两个模型声明了总数
        self.assertEqual(
            {m for m, v in MODELS.items() if "max_assets" in v},
            {"seedance-2.5gs 720p", "seedance-2.0-r-720P", "seedance-2.0-F-r-720P"})

    def test_the_error_says_where_the_number_came_from(self):
        """★ 报错要分清这个上限是**模型声明的**还是**整家的兜底**。

        前者改不了（只能换模型），后者可能只是我们没拿到它的声明 ——
        指错了人会去做一件解决不了问题的事。
        """
        with self.assertRaises(ApiError) as c:
            self._run("seedance-2.5gs 720p", n_img=31)
        self.assertIn("seedance-2.5gs 720p 声明的", str(c.exception))
        with self.assertRaises(ApiError) as c:
            self._run("seedance-2.0-r-720P", n_img=31)
        self.assertIn("这个模型没单独声明", str(c.exception))

    def test_the_unverified_resolution_is_marked_as_such(self):
        """`seedance-2.5-hf` 自己什么都没声明 —— 480p 是文档里写的。

        不标出来的话，「我们写的」看起来就像「它说的」。
        """
        notes = WuxianhuabuProvider().capabilities()["video"]["notes"]
        self.assertIn("实拉没有背书", notes)


if __name__ == "__main__":
    unittest.main()
