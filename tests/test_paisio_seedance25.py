# -*- coding: utf-8 -*-
import unittest

from core.apiutil import ApiError, TASK_FATAL
from core.providers.base import VideoTask
from core.providers.paisio import (PaisioProvider, SEEDANCE25_DURATIONS,
                                   SEEDANCE25_MODELS, SEEDANCE25_RATIOS,
                                   is_seedance25)


class PaisioSeedance25Tests(unittest.TestCase):
    def test_capabilities_declare_model_specific_limits(self):
        video = PaisioProvider().capabilities()["video"]
        for model in SEEDANCE25_MODELS:
            self.assertIn(model, video["models"])
            options = video["model_options"][model]
            self.assertEqual(options["durations"], list(range(4, 31)))
            self.assertEqual(options["ratios"], SEEDANCE25_RATIOS)
            self.assertEqual(options["max_refs"], 30)
            self.assertEqual(options["max_video_refs"], 10)
            self.assertEqual(options["max_audio_refs"], 10)
        # 旧模型仍保持原来的参数，不能被2.5的上限污染。
        self.assertEqual(video["durations"], [4, 5, 8, 10, 12, 15])
        self.assertEqual(video["max_refs"], 9)

    def test_seedance25_uses_documented_request_fields(self):
        task = VideoTask(
            prompt="让人物保持一致并自然运动",
            refs=["https://cdn.example/1.png", "https://cdn.example/2.png"],
            duration=30, ratio="21:9", model="paisio-seedance-2.5-720p",
            extra={
                "video_refs": ["https://cdn.example/motion.mp4"],
                "audio_refs": ["https://cdn.example/voice.wav"],
            },
        )
        body = PaisioProvider._video_body(task, task.model)
        self.assertEqual(body, {
            "model": "paisio-seedance-2.5-720p",
            "prompt": "让人物保持一致并自然运动",
            "duration": 30,
            "aspect_ratio": "21:9",
            "image_url": "https://cdn.example/1.png",
            "extra_images": ["https://cdn.example/2.png"],
            "extra_videos": ["https://cdn.example/motion.mp4"],
            "extra_audios": ["https://cdn.example/voice.wav"],
        })
        self.assertNotIn("metadata", body)
        self.assertNotIn("images", body)

    def test_seedance25_rejects_invalid_limits_before_request(self):
        task = VideoTask(prompt="x", refs=["data:image/png;base64,abc"],
                         duration=31, ratio="2:1", model="paisio-seedance-2.5-720p",
                         extra={"videos": ["v"] * 11, "audios": ["a"] * 11})
        with self.assertRaises(ApiError) as raised:
            PaisioProvider._video_body(task, task.model)
        self.assertEqual(raised.exception.kind, TASK_FATAL)
        message = str(raised.exception)
        self.assertIn("4-30秒", message)
        self.assertIn("视频素材最多10条", message)
        self.assertIn("音频素材最多10条", message)
        self.assertIn("公网", message)

    def test_the_4_1_model_refuses_reference_video_and_audio(self):
        """模型广场把 seedance2.5-4-1-720p 标成 **10/0/0** —— 它不收参考视频和音频。

        发过去只会被忽略，不报错。与其静默丢掉、让人以为运镜参考生效了，
        不如在提交前就说清楚。
        """
        task = VideoTask(prompt="x", refs=["https://cdn/1.png"], duration=15,
                         ratio="9:16", model="seedance2.5-4-1-720p",
                         extra={"videos": ["https://cdn/m.mp4"]})
        with self.assertRaises(ApiError) as raised:
            PaisioProvider._video_body(task, task.model)
        self.assertIn("不支持参考视频", str(raised.exception))

    def test_the_4_1_model_allows_up_to_ten_images(self):
        task = VideoTask(prompt="x", refs=[f"https://cdn/{i}.png" for i in range(10)],
                         duration=30, ratio="9:16", model="seedance2.5-4-1-720p")
        body = PaisioProvider._video_body(task, task.model)
        self.assertEqual(len(body["extra_images"]) + 1, 10)

    def test_provider_marks_only_seedance25_as_url_only(self):
        provider = PaisioProvider()
        self.assertTrue(provider.needs_url("seedance2.5-26-480p", "video"))
        self.assertFalse(provider.needs_url("sd2-720p", "video"))

    # ---- 2026-08-28：鹤改名之后补的三条回归 --------------------------------
    #
    # 背景：2.5 的能力以前挂在「名字在 SEEDANCE25_MODELS 里」这个精确判定上，
    # 鹤新增 paisio-seedance-2.5-* 之后六处一起漏判。其中三处是**静默**的，
    # 下面三条各盯一处 —— 判定改成家族匹配（is_seedance25）之后才有意义。

    def test_the_family_check_covers_every_known_spelling(self):
        """鹤对同一个 2.5 用过四种写法，认漏一种就整套能力降级。"""
        for m in ("seedance2.5-4-1-720p", "seedance2.5-26-480p",
                  "paisiodance-2.5-720p", "paisio-seedance-2.5-480p",
                  "doubao-seedance-2-5-720p", "sd2.5-720p-standard"):
            self.assertTrue(is_seedance25(m), m)
        # 刻意不认的：2.0 系和按次分组，body 形状不一样
        for m in ("sd2-720p", "seedance2-4-1-720p", "paisio-seedance-2-mini-480p",
                  "paisio-seedance-2.0-720p", ""):
            self.assertFalse(is_seedance25(m), m)

    def test_new_spelling_still_demands_public_urls(self):
        """★ 静默失败之一：漏判 → 参考图按 data URI 发 → 2.5 丢掉它，
        照样出片、照样计费，只是脸不对，而且不报错。"""
        provider = PaisioProvider()
        for m in ("paisio-seedance-2.5-480p", "paisio-seedance-2.5-720p",
                  "doubao-seedance-2-5-720p", "sd2.5-720p-standard"):
            self.assertTrue(provider.needs_url(m, "video"), m)

    def test_new_spelling_takes_the_25_body_not_the_legacy_one(self):
        """★ 静默失败之二：漏判 → 走旧的 metadata+images 形状。

        这里不发真请求，只截下 generate_video 攒出来的 body。
        """
        captured = {}

        class _FakeSession:
            def request(self, method, path, json_body=None, **kw):
                captured["path"] = path
                captured["body"] = json_body
                return {"data": [{"url": "https://cdn/out.mp4"}]}

            def save_item(self, url, dest):
                captured["saved"] = url

        provider = PaisioProvider()
        provider.session = _FakeSession()
        task = VideoTask(prompt="x", refs=["https://cdn/1.png"], duration=30,
                         ratio="9:16", model="paisio-seedance-2.5-480p")
        provider.generate_video(task, "out.mp4", log=lambda *a, **k: None)
        self.assertEqual(captured["body"]["duration"], 30)      # 旧 body 会是字符串+metadata
        self.assertEqual(captured["body"]["aspect_ratio"], "9:16")
        self.assertEqual(captured["body"]["image_url"], "https://cdn/1.png")
        self.assertNotIn("metadata", captured["body"])
        self.assertNotIn("images", captured["body"])

    def test_every_declared_25_model_gets_the_full_duration_range(self):
        """页面上的时长下拉就是从 model_options 取的 —— 用户「选不到 30 秒」
        的直接原因是这里少了它的名字。"""
        video = PaisioProvider().capabilities()["video"]
        for m in video["models"]:
            if is_seedance25(m):
                self.assertEqual(max(video["model_options"][m]["durations"]), 30, m)

    def test_every_model_uses_the_documented_body(self):
        """★ **所有视频模型同一套请求体。** 文档原话（提交视频生成任务）：
        「视频生成请求体。所有视频模型使用相同的参数格式。」

        原来按模型分两条路，旧模型那条自己造了一套：`images` 数组、
        `metadata.ratio` / `modeType`（文档里没有 metadata），而且**往正文
        末尾追加 `@图1..N`** —— 文档的引用语法是 `@Image1` / `@Img1`，
        压根没有 `@图N` 这个写法。

        后果就是用户报的「提示词失效 / 参考图失效」：材料正文已经用
        `@Image1..N` 写好了身份映射，末尾又被追加一串 `@图1..5`，同一个
        请求里两套编号；而参考图塞在一个服务商不认的字段里。片子出得来，
        用的参考图和正文说的对不上，一处都不报错。
        """
        prompt = "@Image1 与 @Image2 对话"
        refs = ["https://x/a.png", "https://x/b.png", "https://x/c.png"]
        for model in ("sd2-720p", "paisio-seedance-2.5-720p"):
            b = PaisioProvider._video_body(VideoTask(prompt=prompt, refs=refs, duration=10, ratio='9:16', model=model), model)
            b.pop("_note", None)
            self.assertEqual(b["prompt"], prompt, f"{model}: 正文被改写了")
            self.assertEqual(b["image_url"], refs[0], model)
            self.assertEqual(b["extra_images"], refs[1:], model)
            self.assertIn("aspect_ratio", b, model)
            self.assertNotIn("images", b, f"{model}: 文档里没有 images 这个字段")
            self.assertNotIn("metadata", b, f"{model}: 文档里没有 metadata")
