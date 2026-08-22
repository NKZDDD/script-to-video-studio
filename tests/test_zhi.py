# -*- coding: utf-8 -*-
"""智（zhi168.it.com）的请求形状与鉴权。

这家和别家差异最大的三点，各有一组测试锁住：

  · 鉴权用 X-API-Key 头（不是 Bearer）
  · 模型是账号分配的**数字 ID**（model_code 整数），填名字要当场说清
  · 素材全收 *_urls 公网数组；图片的 size 要换算成 aspect_ratio
"""
import unittest

from core.apiutil import ApiError
from core.providers import REGISTRY, resolve_id
from core.providers.base import ImageTask, VideoTask
from core.providers.zhi import ZhiProvider, _ratio_of, model_code

P = ZhiProvider()
U = ["https://cdn/1.jpg", "https://cdn/2.jpg"]
V = ["https://cdn/motion.mp4"]
A = ["https://cdn/voice.mp3"]


class AuthTests(unittest.TestCase):

    def test_uses_the_x_api_key_header(self):
        """★ 这家文档明确 X-API-Key，不是 Bearer —— 发错头就是 401。"""
        h = P.session._headers()
        self.assertEqual(h.get("X-API-Key"), P.session.api_key)
        self.assertNotIn("Authorization", h)

    def test_other_providers_still_use_bearer(self):
        from core.providers.octopus import OctopusProvider
        h = OctopusProvider().session._headers()
        self.assertEqual(h.get("Authorization"), "Bearer ")
        self.assertNotIn("X-API-Key", h)


class ModelCodeTests(unittest.TestCase):

    def test_numeric_models_parse_to_int(self):
        self.assertEqual(model_code("37"), 37)
        self.assertEqual(model_code(" 12 "), 12)

    def test_a_name_is_rejected_with_instructions(self):
        """★ 模型框是自由输入，填错要当场说清去哪查 —— 422 排查毫无线索。"""
        with self.assertRaises(ApiError) as ctx:
            model_code("gpt-image-2")
        self.assertIn("数字 ID", str(ctx.exception))
        self.assertIn("available-models", str(ctx.exception))

    def test_an_empty_model_is_rejected_too(self):
        with self.assertRaises(ApiError):
            model_code("")


class VideoBodyTests(unittest.TestCase):

    def body(self, **kw):
        return P.build_video_body(VideoTask(**kw))

    def test_shape_matches_the_doc(self):
        b = self.body(prompt="x", model="37", refs=[U[0]],
                      extra={"videos": V, "audios": A})
        self.assertEqual(b, {
            "model_code": 37,
            "prompt": "x",
            "aspect_ratio": "9:16",       # VideoTask 默认
            "resolution": "720p",          # 空 resolution 的默认
            "duration_seconds": 15,        # VideoTask 默认时长
            "reference_image_urls": [U[0]],
            "video_urls": V,
            "audio_urls": A,
        })

    def test_with_audio_is_never_sent(self):
        """文档：默认 false，要配音用 audio_urls —— 不该自动发 true。"""
        b = self.body(prompt="x", model="37")
        self.assertNotIn("with_audio", b)

    def test_duration_is_clamped_to_1_300(self):
        """按提交时长计费 —— 夹住，别让一次手滑烧穿积分。"""
        self.assertEqual(self.body(prompt="x", model="37", duration=999)["duration_seconds"], 300)
        self.assertEqual(self.body(prompt="x", model="37", duration=1)["duration_seconds"], 1)

    def test_no_reference_fields_when_no_materials(self):
        b = self.body(prompt="x", model="37")
        for k in ("reference_image_urls", "video_urls", "audio_urls"):
            self.assertNotIn(k, b)

    def test_resolution_defaults_to_720p(self):
        b = self.body(prompt="x", model="37", resolution="1080p")
        self.assertEqual(b["resolution"], "1080p")


class ImageBodyTests(unittest.TestCase):

    def body(self, **kw):
        kw.setdefault("prompt", "一段足够长的提示词")
        return P.build_image_body(ImageTask(**kw))

    def test_shape_matches_the_doc(self):
        b = self.body(model="12", size="1024x1536", refs=U)
        self.assertEqual(b, {
            "model_code": 12,
            "prompt": "一段足够长的提示词",
            "aspect_ratio": "9:16",        # 竖图
            "image_count": 1,              # 异步接口固定 1
            "reference_image_urls": U,
        })

    def test_size_becomes_an_aspect_ratio(self):
        self.assertEqual(_ratio_of("1024x1536"), "9:16")
        self.assertEqual(_ratio_of("1536x1024"), "16:9")
        self.assertEqual(_ratio_of("1024x1024"), "1:1")
        self.assertEqual(_ratio_of("garbage"), "1:1")

    def test_reference_images_capped_at_eight(self):
        many = [f"https://cdn/{i}.jpg" for i in range(12)]
        b = self.body(model="12", refs=many)
        self.assertEqual(len(b["reference_image_urls"]), 8)


class RegistryTests(unittest.TestCase):

    def test_registered_and_aliased(self):
        self.assertIn("zhi", REGISTRY)
        for alias in ("智", "zhi168", "zhi168.it.com"):
            self.assertEqual(resolve_id(alias), "zhi")

    def test_capabilities_leave_models_dynamic(self):
        """模型按账号分配 —— capabilities 静态列表必须留空，靠 note 引导手填。"""
        cap = P.capabilities()
        self.assertEqual(cap["video"]["models"], [])
        self.assertEqual(cap["image"]["models"], [])
        self.assertIn("数字 ID", cap["video"]["notes"])
        self.assertIn("数字 ID", cap["image"]["notes"])

    def test_list_models_reads_the_account_endpoint(self):
        """模型清单从 available-models 拉（带账号 Key），返回数字 ID 字符串。"""
        got = {}

        class S:
            def request(inner, method, path, **kw):        # noqa: N805
                got["path"] = path
                return [{"id": 37, "capability": "video"},
                        {"id": 12, "capability": "image"}]
        P.session, old = S(), P.session
        try:
            self.assertEqual(P.list_models(), ["12", "37"])
        finally:
            P.session = old
        self.assertEqual(got["path"], "/api/v1/available-models")


if __name__ == "__main__":
    unittest.main()
