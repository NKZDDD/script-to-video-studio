# -*- coding: utf-8 -*-
"""坤鸡 2026-08-19 新文档：分组 Key、香蕉(Gemini原生)、veo 视频。

这些断言锁的都是**发错不报错、只是结果不对**的地方 ——
最典型的是拿 1K 分组的 Key 去要 4K：不报错，悄悄给你一张 1K。
"""
import unittest

from core.apiutil import ApiError, TASK_FATAL
from core.providers.base import ImageTask, VideoTask
from core.providers.kunji import (BANANA_MODELS, VIDEO_DURATIONS,
                                  KunjiProvider, parse_keys)

MULTI = "1k=sk-aaa;4k=sk-bbb;high=sk-ccc"


def _stub(p, reply):
    seen = {}

    def fake(method, path, json_body=None, files=None, retries=3, timeout=None):
        seen.update(path=path, body=json_body, files=files,
                    key=p.session.api_key)
        return reply

    p.session.request = fake
    p.session.save_item = lambda i, d: d
    p.session.poll = lambda *a, **k: "https://x/v.mp4"
    return seen


class KeyGroupTests(unittest.TestCase):
    """★ 4K 是按**令牌分组**给的，不是模型的区别（文档：1K 分组最高 1K）。"""

    def test_single_key_still_works(self):
        self.assertEqual(parse_keys("sk-only"), {"default": "sk-only"})

    def test_multi_key_by_tier(self):
        p = KunjiProvider(api_key=MULTI)
        self.assertEqual(p.key_for("1K"), "sk-aaa")
        self.assertEqual(p.key_for("4K"), "sk-bbb")
        self.assertEqual(p.key_for("1K", "high"), "sk-ccc")

    def test_unknown_tier_falls_back(self):
        p = KunjiProvider(api_key=MULTI)
        self.assertEqual(p.key_for("1024x1536"), "sk-aaa")   # 像素写法 → default
        self.assertEqual(KunjiProvider(api_key="sk-x").key_for("4K"), "sk-x")

    def test_requesting_4k_switches_the_key(self):
        """拿 1K 分组的 Key 要 4K 会被静默降级 —— 所以必须按档位切。"""
        p = KunjiProvider(api_key=MULTI)
        seen = _stub(p, {"data": [{"b64_json": "aGk="}]})
        p.generate_image(ImageTask(prompt="猫", size="4K", model="gpt-image-2"), "o.png")
        self.assertEqual(seen["key"], "sk-bbb")


class BananaTests(unittest.TestCase):
    def test_model_goes_in_the_path_not_the_body(self):
        """香蕉是 Gemini 原生格式：模型名在 URL 里，body 里没有 model 字段。"""
        p = KunjiProvider(api_key="sk-x")
        seen = _stub(p, {"candidates": [{"content": {"parts": [
            {"inline_data": {"mime_type": "image/png", "data": "aGk="}}]}}]})
        p.generate_image(ImageTask(prompt="苹果", size="4K",
                                   model="gemini-3-pro-image-preview"), "o.png")
        self.assertEqual(seen["path"],
                         "/v1beta/models/gemini-3-pro-image-preview:generateContent")
        self.assertNotIn("model", seen["body"])
        cfg = seen["body"]["generationConfig"]["imageConfig"]
        self.assertEqual(cfg["imageSize"], "4K")          # K 必须大写
        self.assertNotIn("aspectRatio", cfg)              # 没给比例就省略，别传 auto

    def test_ratio_is_sent_only_when_given(self):
        p = KunjiProvider(api_key="sk-x")
        seen = _stub(p, {"candidates": [{"content": {"parts": [
            {"inline_data": {"data": "aGk="}}]}}]})
        p.generate_image(ImageTask(prompt="x", size="2K", model=BANANA_MODELS[0],
                                   extra={"ratio": "21:9"}), "o.png")
        self.assertEqual(
            seen["body"]["generationConfig"]["imageConfig"]["aspectRatio"], "21:9")

    def test_wide_ratios_are_banana2_only(self):
        p = KunjiProvider(api_key="sk-x")
        _stub(p, {})
        with self.assertRaises(ApiError) as raised:
            p.generate_image(ImageTask(prompt="x", size="1K",
                                       model="gemini-3-pro-image-preview",
                                       extra={"ratio": "8:1"}), "o.png")
        self.assertEqual(raised.exception.kind, TASK_FATAL)
        # 香蕉2 放行
        seen = _stub(p, {"candidates": [{"content": {"parts": [
            {"inline_data": {"data": "aGk="}}]}}]})
        p.generate_image(ImageTask(prompt="x", size="1K",
                                   model="gemini-3.1-flash-image-preview",
                                   extra={"ratio": "8:1"}), "o.png")
        self.assertEqual(
            seen["body"]["generationConfig"]["imageConfig"]["aspectRatio"], "8:1")

    def test_banana_accepts_urls_but_gpt_image_does_not(self):
        """香蕉的 file_data 收公网链接；gpt-image-2 的 edits 只收文件字节。"""
        p = KunjiProvider()
        self.assertTrue(p.accepts_url("gemini-3-pro-image-preview", "image"))
        self.assertFalse(p.accepts_url("gpt-image-2", "image"))
        self.assertTrue(p.needs_bytes("gpt-image-2"))
        self.assertFalse(p.needs_bytes("gemini-3-pro-image-preview"))


class VeoTests(unittest.TestCase):
    def test_single_image_uses_string_field(self):
        p = KunjiProvider(api_key="sk-x")
        seen = _stub(p, {"id": "t1", "status": "completed", "url": "https://x/v.mp4"})
        p.generate_video(VideoTask(prompt="推进", refs=["https://c/a.jpg"],
                                   duration=4, model="veo-3.1-fast-generate-preview"),
                         "o.mp4")
        self.assertEqual(seen["body"]["image_url"], "https://c/a.jpg")
        self.assertNotIn("image_urls", seen["body"])

    def test_first_last_uses_array(self):
        p = KunjiProvider(api_key="sk-x")
        seen = _stub(p, {"id": "t1", "status": "completed", "url": "https://x/v.mp4"})
        p.generate_video(VideoTask(prompt="过渡", refs=["https://c/a.jpg", "https://c/b.jpg"],
                                   duration=8, model="veo-3.1-generate-preview"), "o.mp4")
        self.assertEqual(len(seen["body"]["image_urls"]), 2)
        self.assertNotIn("image_url", seen["body"])

    def test_multi_ref_is_locked_to_eight_seconds_and_16_9(self):
        """★ 文档：多参考图传 4/6 秒或 9:16 会**生成失败**。失败不扣费，但白等一轮。"""
        p = KunjiProvider(api_key="sk-x")
        seen = _stub(p, {"id": "t1", "status": "completed", "url": "https://x/v.mp4"})
        p.generate_video(VideoTask(prompt="一致", refs=[f"https://c/{i}.jpg" for i in range(3)],
                                   duration=4, ratio="9:16",
                                   model="veo-3.1-generate-preview-ref"), "o.mp4")
        self.assertEqual(seen["body"]["duration"], 8)
        self.assertEqual(seen["body"]["aspect_ratio"], "16:9")

    def test_multi_ref_requires_the_ref_model(self):
        p = KunjiProvider(api_key="sk-x")
        _stub(p, {})
        with self.assertRaises(ApiError) as raised:
            p.generate_video(VideoTask(prompt="x", refs=[f"https://c/{i}.jpg" for i in range(3)],
                                       duration=8, model="veo-3.1-fast-generate-preview"),
                             "o.mp4")
        self.assertEqual(raised.exception.kind, TASK_FATAL)

    def test_duration_snaps_to_allowed(self):
        p = KunjiProvider(api_key="sk-x")
        seen = _stub(p, {"id": "t1", "status": "completed", "url": "https://x/v.mp4"})
        p.generate_video(VideoTask(prompt="x", duration=5,
                                   model="veo-3.1-fast-generate-preview"), "o.mp4")
        self.assertIn(seen["body"]["duration"], VIDEO_DURATIONS)

    def test_provider_now_declares_video(self):
        """以前这家只声明 image —— 视频能力在页面上根本看不到。"""
        cap = KunjiProvider().capabilities()
        self.assertIn("video", cap["supports"])
        self.assertEqual(cap["video"]["resolutions"], ["720p"])


if __name__ == "__main__":
    unittest.main()
