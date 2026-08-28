# -*- coding: utf-8 -*-
"""巨轮：**一个服务、五种请求格式**，选模型即选格式。

这些断言锁的都是「发错不报错、只是结果不对」的地方。最要命的三条：

  · grok 格式**不认 seconds**，比例分辨率必须在 extra 里 —— 放顶层不报错，
    只是全部按默认出，你以为选了 9:16，出来是 16:9。
  · `sd2.5` 传错时长平台**悄悄改成 30**，`dubai_sd25_170` 却直接 400 ——
    同一份代码在两个模型上表现完全不同，本类统一先纠正。
  · 参考素材必须公网可达，本机图发过去会被丢掉照样出片 —— 脸就不是本人了。
"""
import unittest

from core.apiutil import ApiError, TASK_FATAL
from core.providers import REGISTRY, resolve_id
from core.providers.base import ImageTask, VideoTask
from core.providers.julun import (IMAGE_MODELS, SPEC, VIDEO_MODELS,
                                  JulunProvider, fit_duration, spec_of)

URLS = ["https://cdn/a.jpg", "https://cdn/b.jpg"]


def _stub(p, reply=None, replies=None):
    """replies: path 前缀 → 返回值。seen 记最后一次请求。"""
    seen = {}

    def fake(method, path, json_body=None, params=None, files=None,
             retries=3, timeout=None):
        seen.update(method=method, path=path, body=json_body, files=files)
        if replies:
            for pre, val in replies.items():
                if path.startswith(pre):
                    return val
        return reply if reply is not None else {}

    p.session.request = fake
    p.session.save_item = lambda i, d, *a, **k: d
    return seen


def _vt(**kw):
    kw.setdefault("prompt", "一个人走在雨里")
    return VideoTask(**kw)


class RegistryTests(unittest.TestCase):
    def test_registered_and_aliased(self):
        self.assertIn("julun", REGISTRY)
        for a in ("julun.cc", "巨轮", "jl"):
            self.assertEqual(resolve_id(a), "julun")

    def test_builtin_order_lists_it(self):
        """exe 里扫不到目录时按这张表加载，漏了这一家会整家缺席且不报错。"""
        from core.providers import _BUILTIN_ORDER
        self.assertIn("julun", _BUILTIN_ORDER)

    def test_capabilities_shape(self):
        cap = JulunProvider().capabilities()
        for k in ("id", "name", "supports", "image", "video"):
            self.assertIn(k, cap)
        self.assertEqual(cap["video"]["specs"]["sd2.5"]["format"], "openai_refs")
        self.assertEqual(len(cap["video"]["models"]), 17)

    def test_model_names_are_verbatim(self):
        """模型名带空格、中文和**全角括号**，手打必错。"""
        self.assertIn("grok-imagine-video-1.5（按次）", VIDEO_MODELS)   # 全角（）
        self.assertNotIn("grok-imagine-video-1.5(按次)", VIDEO_MODELS)  # 半角是错的
        self.assertIn("Quality V4 · 480p/720p (可@图/视频/音频)", VIDEO_MODELS)
        self.assertIn("SD2.0 1080P 933", VIDEO_MODELS)
        self.assertEqual(IMAGE_MODELS, ["doubao-seedream-5-0-260128"])

    def test_ref_mode_is_url_for_everything(self):
        p = JulunProvider()
        for m in ("sd2.5", "SD2.0 Fast", "minimax-h3 2k"):
            self.assertTrue(p.needs_url(m, "video"))
            self.assertTrue(p.accepts_url(m, "video"))
            self.assertFalse(p.needs_bytes(m))


class FormatDispatchTests(unittest.TestCase):
    def test_every_model_maps_to_a_known_format(self):
        known = {"metadata", "url_media", "openai_refs", "grok", "simple"}
        for m in VIDEO_MODELS:
            self.assertIn(SPEC[m][0], known, m)

    def test_unknown_model_falls_back_not_crash(self):
        self.assertEqual(spec_of("以后新加的模型")[0], "url_media")

    def test_metadata_body(self):
        p = JulunProvider()
        path, body = p.build_video_body(
            _vt(model="sd-2-c6", refs=URLS, duration=8, ratio="1:1"))
        self.assertEqual(path, "/v1/videos")
        meta = body["metadata"]
        self.assertEqual(meta["duration"], 8)
        self.assertEqual(meta["ratio"], "1:1")
        self.assertEqual(meta["content"], URLS)
        self.assertNotIn("seconds", body)
        self.assertNotIn("image_urls", body)

    def test_metadata_wraps_video_and_audio_with_role(self):
        p = JulunProvider()
        _, body = p.build_video_body(_vt(
            model="sd-2-c6", refs=URLS[:1], duration=8,
            extra={"video_refs": ["https://cdn/v.mp4"],
                   "audio_refs": ["https://cdn/a.wav"]}))
        content = body["metadata"]["content"]
        self.assertEqual(content[0], URLS[0])
        self.assertEqual(content[1]["role"], "reference_video")
        self.assertEqual(content[1]["video_url"]["url"], "https://cdn/v.mp4")
        self.assertEqual(content[2]["role"], "reference_audio")

    def test_url_media_body(self):
        p = JulunProvider()
        _, body = p.build_video_body(_vt(
            model="seedance-2.5-deal", refs=URLS, duration=12, ratio="16:9",
            extra={"audio_refs": ["https://cdn/a.wav"]}))
        self.assertEqual(body["seconds"], 12)
        self.assertEqual(body["image_urls"], URLS)
        self.assertEqual(body["audio_urls"], ["https://cdn/a.wav"])
        self.assertNotIn("metadata", body)
        self.assertNotIn("duration", body)

    def test_openai_refs_body(self):
        p = JulunProvider()
        _, body = p.build_video_body(_vt(model="sd2-c7", refs=URLS, duration=10,
                                         ratio="16:9"))
        self.assertEqual(body["duration"], 10)
        self.assertEqual(body["aspect_ratio"], "16:9")
        self.assertEqual(body["image_refs"], URLS)
        self.assertNotIn("seconds", body)
        self.assertNotIn("image_urls", body)

    def test_grok_body_has_no_seconds_and_puts_ratio_in_extra(self):
        """★ 放顶层不报错，只是比例分辨率全按默认走。"""
        p = JulunProvider()
        _, body = p.build_video_body(_vt(
            model="grok-imagine-video-1.5（按次）", refs=URLS,
            duration=6, ratio="9:16", resolution="480p"))
        self.assertNotIn("seconds", body)
        self.assertEqual(body["duration"], 6)
        self.assertEqual(body["extra"]["aspect_ratio"], "9:16")
        self.assertEqual(body["extra"]["resolution"], "480p")
        self.assertNotIn("aspect_ratio", body)
        self.assertNotIn("resolution", body)

    def test_grok_single_ref_uses_input_reference(self):
        p = JulunProvider()
        _, body = p.build_video_body(_vt(
            model="grok-imagine-video-1.5-preview", refs=URLS[:1], duration=6))
        self.assertEqual(body["input_reference"], URLS[0])
        self.assertNotIn("reference_images", body["extra"])

    def test_grok_multi_ref_carries_role(self):
        p = JulunProvider()
        _, body = p.build_video_body(_vt(
            model="grok-imagine-video-1.5-preview", refs=URLS, duration=6))
        imgs = body["extra"]["reference_images"]
        self.assertEqual([i["role"] for i in imgs],
                         ["reference_image", "reference_image"])

    def test_grok_first_last_frame_pairs_roles(self):
        p = JulunProvider()
        _, body = p.build_video_body(_vt(
            model="grok-imagine-video-1.5-preview", refs=URLS, duration=6,
            extra={"first_last": True}))
        imgs = body["extra"]["reference_images"]
        self.assertEqual([i["role"] for i in imgs], ["first_frame", "last_frame"])
        self.assertNotIn("input_reference", body)

    def test_h3_uses_its_own_endpoint(self):
        p = JulunProvider()
        path, body = p.build_video_body(_vt(
            model="minimax-h3 768p", refs=URLS[:1], duration=6))
        self.assertEqual(path, "/v1/video/generations")
        self.assertEqual(body["seconds"], 6)
        self.assertEqual(body["image_urls"], URLS[:1])


class DurationTests(unittest.TestCase):
    def test_sd25_family_is_locked_to_30(self):
        """一个被平台悄悄改成 30，一个直接 400 —— 统一先纠正。"""
        said = []
        self.assertEqual(fit_duration("sd2.5", 10, log=said.append), 30)
        self.assertEqual(fit_duration("dubai_sd25_170", 15, log=said.append), 30)
        self.assertEqual(fit_duration("sd2.5", 30), 30)
        self.assertEqual(len(said), 2)      # 改了就要说，不能悄悄改

    def test_discrete_choices_snap_to_nearest(self):
        self.assertEqual(fit_duration("sd-2-c8", 4), 10)
        self.assertEqual(fit_duration("sd-2-c8", 15), 15)
        self.assertEqual(fit_duration("minimax-h3 2k", 8), 6)
        self.assertEqual(fit_duration("Quality V4 · 480p/720p (可@图/视频/音频)", 12), 10)

    def test_wan30th_allows_up_to_30(self):
        self.assertEqual(fit_duration("wan3.0th", 30), 30)
        self.assertEqual(fit_duration("wan3.0th", 31), 30)

    def test_range_models_clamp_and_say_so(self):
        said = []
        self.assertEqual(fit_duration("SD2.0 Fast", 99, log=said.append), 15)
        self.assertEqual(fit_duration("SD2.0 Fast", 1, log=said.append), 4)
        self.assertEqual(len(said), 2)

    def test_submitted_body_carries_the_corrected_duration(self):
        """纠正必须落到真正发出去的 body 里，不能只在日志里说说。"""
        p = JulunProvider()
        _, body = p.build_video_body(_vt(model="sd2.5", refs=URLS, duration=10))
        self.assertEqual(body["duration"], 30)


class LoudFailureTests(unittest.TestCase):
    """全是「不这么做就会悄悄出错片」的地方。"""

    def _fatal(self, fn):
        with self.assertRaises(ApiError) as c:
            fn()
        self.assertEqual(c.exception.kind, TASK_FATAL)
        return str(c.exception)

    def test_local_refs_refused_not_dropped(self):
        p = JulunProvider()
        msg = self._fatal(lambda: p.build_video_body(
            _vt(model="sd2.5", refs=["C:/tmp/a.png", URLS[0]])))
        self.assertIn("公网", msg)

    def test_1080p_933_requires_a_reference(self):
        p = JulunProvider()
        self._fatal(lambda: p.build_video_body(
            _vt(model="SD2.0 1080P 933", refs=[], duration=15)))
        # 有图就放行
        p.build_video_body(_vt(model="SD2.0 1080P 933", refs=URLS[:1], duration=15))

    def test_too_many_refs_refused_before_submitting(self):
        p = JulunProvider()
        msg = self._fatal(lambda: p.build_video_body(
            _vt(model="minimax-h3 2k", refs=URLS * 3, duration=6)))
        self.assertIn("参考图", msg)

    def test_media_kinds_the_model_cannot_take(self):
        p = JulunProvider()
        self._fatal(lambda: p.build_video_body(_vt(
            model="sd2-c7", refs=URLS, duration=10,
            extra={"video_refs": ["https://cdn/v.mp4"]})))
        self._fatal(lambda: p.build_video_body(_vt(
            model="SD2.0 Fast", refs=URLS, duration=10,
            extra={"audio_refs": ["https://cdn/a.wav"]})))

    def test_bad_ratio_refused(self):
        p = JulunProvider()
        msg = self._fatal(lambda: p.build_video_body(
            _vt(model="SD2.0 1080P 933", refs=URLS[:1], duration=15, ratio="1:1")))
        self.assertIn("比例", msg)

    def test_empty_prompt_refused(self):
        p = JulunProvider()
        self._fatal(lambda: p.build_video_body(_vt(prompt="  ", model="sd2.5")))

    def test_image_with_refs_refused(self):
        """出图接口只有文生图。丢掉参考图会出一张不相干的脸。"""
        p = JulunProvider()
        _stub(p)
        self._fatal(lambda: p.generate_image(
            ImageTask(prompt="一个人", refs=URLS), "out.png", log=lambda *_: None))


class PollTests(unittest.TestCase):
    """查询响应**套一层**、状态**大写**、进度是字符串 —— 通用轮询器认不出。"""

    def test_success_returns_result_url(self):
        p = JulunProvider()
        seen = _stub(p, replies={
            "/v1/videos/": {"code": "success",
                            "data": {"status": "SUCCESS", "progress": "100%",
                                     "result_url": "https://cdn/out.mp4"}},
            "/v1/videos": {"task_id": "t-1"},
        })
        meta = p.generate_video(_vt(model="sd2.5", refs=URLS, duration=30),
                                "out.mp4", log=lambda *_: None, poll_interval=0)
        self.assertEqual(meta["task_id"], "t-1")
        self.assertEqual(meta["source"], "https://cdn/out.mp4")
        self.assertEqual(seen["method"], "GET")

    def test_failure_is_reported_not_swallowed(self):
        p = JulunProvider()
        _stub(p, replies={
            "/v1/videos/": {"code": "success",
                            "data": {"status": "FAILURE", "fail_reason": "内容审核不通过"}},
            "/v1/videos": {"task_id": "t-2"},
        })
        with self.assertRaises(ApiError) as c:
            p.generate_video(_vt(model="sd2.5", refs=URLS, duration=30),
                             "out.mp4", log=lambda *_: None, poll_interval=0)
        self.assertIn("内容审核不通过", str(c.exception))

    def test_success_without_url_falls_back_to_content_endpoint(self):
        p = JulunProvider()
        _stub(p, replies={
            "/v1/videos/": {"code": "success", "data": {"status": "SUCCESS"}},
            "/v1/videos": {"task_id": "t-3"},
        })
        meta = p.generate_video(_vt(model="sd2.5", refs=URLS, duration=30),
                                "out.mp4", log=lambda *_: None, poll_interval=0)
        self.assertTrue(meta["source"].endswith("/v1/videos/t-3/content"))

    def test_missing_task_id_is_loud(self):
        p = JulunProvider()
        _stub(p, reply={"message": "ok"})
        with self.assertRaises(ApiError):
            p.generate_video(_vt(model="sd2.5", refs=URLS, duration=30),
                             "out.mp4", log=lambda *_: None, poll_interval=0)


class ImageTests(unittest.TestCase):
    def test_body_is_openai_shaped_and_n_is_one(self):
        p = JulunProvider()
        seen = _stub(p, reply={"data": [{"url": "https://cdn/i.png"}]})
        meta = p.generate_image(ImageTask(prompt="一只猫", size="1024x1536"),
                                "o.png", log=lambda *_: None)
        self.assertEqual(seen["path"], "/v1/images/generations")
        self.assertEqual(seen["body"]["n"], 1)          # 平台固定一次 1 张
        self.assertEqual(seen["body"]["size"], "1024x1536")
        self.assertEqual(seen["body"]["model"], IMAGE_MODELS[0])
        self.assertEqual(meta["source"], "https://cdn/i.png")

    def test_blank_size_is_omitted(self):
        p = JulunProvider()
        seen = _stub(p, reply={"data": [{"url": "https://cdn/i.png"}]})
        p.generate_image(ImageTask(prompt="一只猫", size="  "), "o.png",
                         log=lambda *_: None)
        self.assertNotIn("size", seen["body"])

    def test_no_image_in_response_is_loud(self):
        p = JulunProvider()
        _stub(p, reply={"data": []})
        with self.assertRaises(ApiError):
            p.generate_image(ImageTask(prompt="一只猫"), "o.png", log=lambda *_: None)


if __name__ == "__main__":
    unittest.main()
