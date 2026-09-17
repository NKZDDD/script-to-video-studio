# -*- coding: utf-8 -*-
"""无限画布：模型清单和逐模型约束**从实拉来**，不照文档抄。

2026-09-17 再拉 `GET /v1/models`，和 09-01 那次比 —— **五个模型一个都不在了**，
换成了现在这六个。所以这个文件真正盯的不是「这几个数对不对」，
而是「表里的每个数都得有实拉背书，没背书的键一个都不许有」。

这一批声明得比上一批少得多：大多数只声明了清晰度，时长和比例一个字没提。
「没提」= 不知道，**不是不限也不是不许** —— 那两项就不校验、照填的发。
编一个范围去夹的话，夹出来的值看着合法、其实不是人要的，而且不报错。
"""
import io
import unittest
from unittest import mock

from core.apiutil import ApiError
from core.providers.base import VideoTask
from core.providers.wuxianhuabu import MODELS, RATIOS, WuxianhuabuProvider

# 实拉那一份（2026-09-17，GET /v1/models 的 capability_schema 原样）。
# 抄在这里是为了「哪天有人手改了 MODELS，这里会亮」——
# 亮了就该重新实拉一次，而不是把这份跟着改。
LIVE = {
    "sd-2.5-480p-hg": {"resolutions": ["480p"], "videoReference": False,
                       "reference_types": ["IMAGE", "AUDIO"]},
    "sd-2.5-720p-hg": {"resolutions": ["720p"], "videoReference": False,
                       "reference_types": ["IMAGE", "AUDIO"]},
    "seedance-2.0-nt-480": {"resolutions": ["480p"], "videoReference": False},
    "seedance-2.0-nt-720": {"resolutions": ["720p"], "videoReference": False},
    "seedance2.5": {"durations": [30], "resolutions": ["720p"],
                    "aspect_ratios": ["16:9", "9:16", "1:1", "4:3", "3:4", "21:9"]},
    "seedance2.5-720-wd": {"resolutions": ["720p"]},
}


class WuxianhuabuModelTests(unittest.TestCase):
    def test_the_list_is_exactly_what_was_pulled(self):
        """★ 清单 == 实拉。多一个是页面上给了会 404 的候选，
        少一个是能用的模型页面上根本没有。"""
        self.assertEqual(set(MODELS), set(LIVE))

    def test_every_model_matches_what_the_platform_declared(self):
        """★ 表里的每个数都得对得上实拉那一份。"""
        for m, live in LIVE.items():
            got = MODELS[m]
            self.assertEqual([got["resolution"]], live["resolutions"], m)
            if live.get("aspect_ratios"):
                self.assertEqual(got["ratios"], live["aspect_ratios"], m)
            if live.get("durations"):
                self.assertEqual(got["durations"], live["durations"], m)

    def test_what_it_did_not_declare_is_not_in_the_table(self):
        """★ 它没声明的键，表里一个都不许有。

        第一版（09-01 那批）我给全部五个模型都填了 30/10/10，而实际只有
        一个声明了这三个数。填一个「看起来合理」的数和照文档抄没有区别：
        它会显示在页面上、会拿去拦人，而没有任何东西背书。
        """
        for m, live in LIVE.items():
            got = MODELS[m]
            if not live.get("aspect_ratios"):
                self.assertNotIn("ratios", got, f"{m} 的比例它没声明")
            if not live.get("durations"):
                self.assertNotIn("durations", got, f"{m} 的时长它没声明")
            for k in ("max_images", "max_audios", "min_images",
                      "max_prompt", "max_assets"):
                self.assertNotIn(k, got, f"{m} 的 {k} 它没声明")

    def test_a_model_that_says_no_video_refs_gets_zero(self):
        """★ `videoReference: false` / `reference_types` 里没有 VIDEO ——
        **这是它明说的**，所以记 0；没提的那两个不动（不知道 ≠ 不允许）。"""
        for m in ("sd-2.5-480p-hg", "sd-2.5-720p-hg",
                  "seedance-2.0-nt-480", "seedance-2.0-nt-720"):
            self.assertEqual(MODELS[m]["max_videos"], 0, m)
        for m in ("seedance2.5", "seedance2.5-720-wd"):
            self.assertNotIn("max_videos", MODELS[m], m)

    def test_the_global_list_is_the_union_not_a_filter(self):
        """全局那份只说「这家总体收什么」，判合不合法要按模型。

        拿并集去判等于全放行。**`21:9` 这次在里面** —— 09-01 那批一个模型
        都不收，当时专门有条测试盯着别提供它；今天 `seedance2.5` 收。
        全局候选跟着实拉走，不跟着我们上次的结论走。
        """
        union = set()
        for v in MODELS.values():
            union |= set(v.get("ratios") or [])
        self.assertEqual(set(RATIOS), union)
        self.assertIn("21:9", RATIOS)

    def test_options_are_offered_per_model(self):
        """页面按选中的模型换候选，不给并集。

        **没声明的键不放进 model_options** —— 放了的话页面会把整家的并集
        当成这个模型的候选，人选到一个它不收的值，而那种拒绝要等跑到
        那一步才看得见。
        """
        opts = WuxianhuabuProvider().capabilities()["video"]["model_options"]
        for m, v in MODELS.items():
            self.assertIn(m, opts)
            self.assertEqual(opts[m]["resolutions"], [v["resolution"]])
            for key in ("ratios", "durations"):
                if v.get(key):
                    self.assertEqual(opts[m][key], v[key], m)
                else:
                    self.assertNotIn(key, opts[m], f"{m} 没声明 {key}，别给候选")

    def _fail(self, **kw):
        p = WuxianhuabuProvider(api_key="x")
        t = VideoTask(prompt=kw.pop("prompt", "正文"),
                      refs=kw.pop("refs", ["https://x/a.png"]),
                      duration=kw.pop("duration", 10),
                      ratio=kw.pop("ratio", "9:16"),
                      model=kw.pop("model"),
                      extra=kw.pop("extra", {}))
        with self.assertRaises(ApiError) as c:
            p.generate_video(t, "out.mp4", log=lambda *a: None)
        return str(c.exception)

    def test_out_of_range_is_refused_before_the_request(self):
        """★ 发之前就停，而且一次说全。

        一条一条报要跑好几趟；而这些在发请求之前就知道。
        """
        # seedance2.5 的时长**只有 30 这一个值**，不是 4–30
        msg = self._fail(model="seedance2.5", duration=15)
        self.assertIn("只能是 30 秒", msg)
        self.assertNotIn("30–30", msg, "单个值别写成区间，读起来像 15 落在里面")
        # 它声明的六个比例里没有 2:1
        self.assertIn("2:1", self._fail(model="seedance2.5",
                                        duration=30, ratio="2:1"))
        # 一次说全：时长和比例都错时两条都要出现
        both = self._fail(model="seedance2.5", duration=15, ratio="2:1")
        self.assertIn("秒", both)
        self.assertIn("2:1", both)

    def test_a_model_that_takes_no_video_refs_refuses_one(self):
        """★ 它明说不收视频参考 —— 发过去多半是静默忽略，
        片子出得来、少用了那段视频，没有一处会说话。"""
        msg = self._fail(model="sd-2.5-720p-hg",
                         extra={"video_refs": ["https://x/a.mp4"]})
        self.assertIn("参考视频", msg)
        self.assertIn("声明的", msg)

    def test_the_two_undeclared_ones_are_not_checked(self):
        """★ 没声明时长和比例的那几个 —— **这两项一个都不拦**。

        「没声明」是不知道，不是不限也不是不许。编一个范围去夹，
        夹出来的值看着合法、其实不是人要的，而且不报错。
        """
        sent = {}

        def fake(self, method, path, **kw):
            if path == "/v1/assets":
                return {"asset_id": "a1"}
            sent.clear()
            sent.update(kw.get("json_body") or {})
            return {"id": "t1", "video_url": "https://x/v.mp4"}

        p = WuxianhuabuProvider(api_key="k")
        t = VideoTask(prompt="正文", refs=["https://x/a.png"], duration=27,
                      ratio="21:9", model="sd-2.5-720p-hg")
        with mock.patch.object(type(p.session), "request", fake), \
                mock.patch.object(type(p.session), "save_item", lambda *a, **k: None):
            p.generate_video(t, "out.mp4", log=lambda *a: None)
        self.assertEqual(sent["seconds"], 27)
        self.assertEqual(sent["ratio"], "21:9")

    def test_an_unlisted_model_still_goes_out(self):
        """★ 那张表是**候选**，不是白名单。

        用户原话（2026-09-01）：「声明两个的时候会不会导致我填写其他模型名
        无法使用，这不是我想要的，因为会导致我新增模型的时候一定需要修改代码」。

        对。平台随时上新（09-01 到 09-17 整份换了一遍），写死白名单等于
        「平台上新，你就得改代码」。页面上模型框本来就是自由输入 + 候选，
        这一层也照办：表外的原样发，只在日志里说一声不校验。
        """
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
        with mock.patch.object(type(p.session), "request", fake), \
                mock.patch.object(type(p.session), "save_item", lambda *a, **k: None):
            p.generate_video(t, "out.mp4", log=logs.append)
        self.assertEqual(sent["model"], "seedance-3.0-ultra-1080P")
        self.assertEqual(sent["seconds"], 27)      # 表外的不按谁的区间削
        self.assertEqual(sent["ratio"], "21:9")    # 也不按并集拦
        self.assertEqual(sent.get("resolution"), "1080p")   # 名字里认出来的
        self.assertTrue([l for l in logs if "不校验" in l],
                        "表外的模型放行了，但日志没说这一趟没校验")

    def test_an_unnamed_resolution_is_left_out_of_the_body(self):
        """名字里认不出分辨率就**不填这个字段**。

        随手填一个的后果是「片子出得来、分辨率不是你要的」，而且不报错。
        """
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
        with mock.patch.object(type(p.session), "request", fake), \
                mock.patch.object(type(p.session), "save_item", lambda *a, **k: None):
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
        with mock.patch.object(type(p.session), "request", fake), \
                mock.patch.object(type(p.session), "save_item", lambda *a, **k: None):
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
        self.assertEqual(self._sent(model="seedance-2.0-nt-480")["resolution"],
                         "480p")

    def test_a_resolution_the_model_does_not_have_is_refused(self):
        """选了这个模型没有的那一档 → 当场停。

        发出去多半是「片子出得来、清晰度不是你要的」，而且不报错。
        """
        with self.assertRaises(ApiError) as c:
            self._sent(model="sd-2.5-720p-hg", resolution="480p")
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

        with mock.patch.object(type(p.session), "request", fake), \
                mock.patch.object(type(p.session), "save_item", lambda *a, **k: None):
            p.generate_video(t, "o.mp4", log=lambda *a: None)

    def test_the_total_asset_cap_is_checked(self):
        """★ 三类**加起来**还有一个总数这道检查。

        这一批模型没有一个声明它（09-01 那批有三个声明了 50）——
        所以这里拿一份**假造的声明**验代码路径还在：分类那三道各自放行、
        只有总数这一道拦得住。**不往真表里塞数**：表里的每个数都得有实拉背书。
        """
        fake_models = dict(MODELS)
        fake_models["测试用·声明了总数的模型"] = {
            "resolution": "720p", "max_images": 30, "max_videos": 10,
            "max_audios": 10, "max_assets": 50}
        with mock.patch.dict("core.providers.wuxianhuabu.MODELS",
                             fake_models, clear=True):
            self._run("测试用·声明了总数的模型", 30, 10, 10)      # 正好 50，放行
            with self.assertRaises(ApiError) as c:
                self._run("测试用·声明了总数的模型", 30, 10, 11)
        self.assertIn("一共 51", str(c.exception))
        self.assertIn("总数上限 50", str(c.exception))

    def test_the_error_says_where_the_number_came_from(self):
        """★ 报错要分清这个上限是**模型声明的**还是**整家的兜底**。

        前者改不了（只能换模型），后者可能只是我们没拿到它的声明 ——
        指错了人会去做一件解决不了问题的事。
        """
        fake_models = dict(MODELS)
        fake_models["测试用·声明了张数的模型"] = {"resolution": "720p",
                                                 "max_images": 30}
        with mock.patch.dict("core.providers.wuxianhuabu.MODELS",
                             fake_models, clear=True):
            with self.assertRaises(ApiError) as c:
                self._run("测试用·声明了张数的模型", n_img=31)
            self.assertIn("测试用·声明了张数的模型 声明的", str(c.exception))
        # 今天这批一个都没声明张数 —— 报错要说清这个 30 是整家的
        with self.assertRaises(ApiError) as c:
            self._run("seedance2.5-720-wd", n_img=31)
        self.assertIn("这个模型没单独声明", str(c.exception))

    def test_the_notes_say_which_parts_are_unchecked(self):
        """★ 这一批大多数只声明了清晰度 —— 页面上要说清哪几项没在校验。

        不说的话，「我们没拦」看起来就像「它允许」。
        """
        notes = WuxianhuabuProvider().capabilities()["video"]["notes"]
        self.assertIn("2026-09-17", notes)
        self.assertIn("不校验", notes)
        self.assertIn("30 秒", notes)


if __name__ == "__main__":
    unittest.main()
