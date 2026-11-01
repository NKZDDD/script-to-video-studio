# -*- coding: utf-8 -*-
import base64
import unittest

from core.providers import REGISTRY, resolve_id
from core.providers import threefatai as tf
from core.providers.base import ImageTask, VideoTask
from core.providers.threefatai import (ThreeFatProvider, VIDEO_MODELS,
                                       _ratio_of, _tier_of)

PNG_1X1 = ("data:image/png;base64,"
           "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQ"
           "DwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
PNG_BYTES = base64.b64decode(PNG_1X1.split(",", 1)[1])


class _Resp:
    def __init__(self, content, ctype="image/png"):
        self.content = content
        self.headers = {"Content-Type": ctype}

    def raise_for_status(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class ThreeFatTests(unittest.TestCase):
    def test_registered_and_aliased(self):
        self.assertIn("threefatai", REGISTRY)
        for alias in ("threefat", "三胖", "sf"):
            self.assertEqual(resolve_id(alias), "threefatai")

    def test_capabilities_cover_documented_models(self):
        cap = ThreeFatProvider().capabilities()
        self.assertEqual(cap["supports"], ["image", "video"])
        self.assertEqual(set(cap["video"]["models"]), set(VIDEO_MODELS))
        self.assertIn("SD 2.5 · 特价按次", cap["video"]["models"])
        self.assertEqual(cap["image"]["default_model"], "GT Image 2.5")

    def test_pixel_size_maps_to_ratio_and_tier(self):
        ratios = tf._IMAGE_OPTIONS["GT Image 2.5"]["ratios"]
        tiers = tf._IMAGE_OPTIONS["GT Image 2.5"]["resolutions"]
        self.assertEqual(_ratio_of("1024x1536", ratios), "2:3")
        self.assertEqual(_ratio_of("1536x1024", ratios), "3:2")
        self.assertEqual(_tier_of("1024x1536", tiers), "2K")
        self.assertEqual(_tier_of("1024x1024", tiers), "1K")

    def test_video_sends_public_reference_urls_in_order(self):
        p = ThreeFatProvider(api_key="k")
        captured = {}

        def fake(method, path, **kwargs):
            captured.update(path=path, **kwargs)
            return {"id": "job-1", "video_url": "https://cdn.example/out.mp4"}

        p.session.request = fake
        p.session.save_item = lambda *a, **k: None
        p.generate_video(VideoTask(
            prompt="Image 1 与 Image 2", refs=["https://cdn.example/1.png",
                                               "https://cdn.example/2.png"],
            duration=10, ratio="9:16", resolution="720p",
            model="SD 2.5 · 特价按次"), "out.mp4")

        self.assertEqual(captured["path"], "/v1/videos")
        body = captured["json_body"]
        self.assertEqual(body["image_urls"], ["https://cdn.example/1.png",
                                              "https://cdn.example/2.png"])
        self.assertEqual(body["duration"], 10)
        self.assertEqual(len(body["external_id"]), 32)

    def test_local_and_public_references_are_all_uploaded_in_order(self):
        p = ThreeFatProvider(api_key="k")
        calls = []
        ids = iter(["file-1", "file-2"])

        def fake(method, path, **kwargs):
            calls.append((method, path, kwargs))
            if path == "/v1/files":
                return {"file_id": next(ids)}
            return {"id": "job-1", "video_url": "https://cdn.example/out.mp4"}

        p.session.request = fake
        p.session.save_item = lambda *a, **k: None
        original_get = tf.requests.get
        tf.requests.get = lambda url, **kw: _Resp(PNG_BYTES)
        try:
            p.generate_video(VideoTask(
                prompt="Image 1 / Image 2",
                refs=["https://cdn.example/char.png", PNG_1X1],
                model="SD 2.5 · 特价按次"), "out.mp4")
        finally:
            tf.requests.get = original_get

        self.assertEqual([c[1] for c in calls], ["/v1/files", "/v1/files", "/v1/videos"])
        body = calls[-1][2]["json_body"]
        self.assertEqual(body["image_ids"], ["file-1", "file-2"])
        self.assertNotIn("image_urls", body)

    def test_video_and_audio_references_are_inlined_as_base64(self):
        p = ThreeFatProvider(api_key="k")
        captured = {}

        def fake(method, path, **kwargs):
            captured.update(kwargs)
            return {"id": "job-1", "video_url": "https://cdn.example/out.mp4"}

        p.session.request = fake
        p.session.save_item = lambda *a, **k: None
        p.generate_video(VideoTask(
            prompt="x", model="SD 2.5 · 特价按次",
            extra={"video_refs": ["data:video/mp4;base64,AAAA"],
                   "audio_refs": ["data:audio/mpeg;base64,AAAA"]}), "out.mp4")

        self.assertEqual(captured["json_body"]["video_b64s"], ["AAAA"])
        self.assertEqual(captured["json_body"]["audio_b64s"], ["AAAA"])

    def test_gk_video_requires_first_frame_reference(self):
        p = ThreeFatProvider(api_key="k")
        with self.assertRaisesRegex(Exception, "必须带首帧参考图"):
            p.generate_video(VideoTask(prompt="x", model="GK-video-3.5"), "out.mp4")

    def test_image_maps_size_and_uses_generations(self):
        p = ThreeFatProvider(api_key="k")
        captured = {}

        def fake(method, path, **kwargs):
            captured.update(path=path, **kwargs)
            return {"data": [{"url": "https://cdn.example/img.png"}]}

        p.session.request = fake
        p.session.save_item = lambda *a, **k: None
        p.generate_image(ImageTask(prompt="猫", model="GT Image 2.5",
                                   size="1024x1536"), "out.png")

        self.assertEqual(captured["path"], "/v1/images/generations")
        body = captured["json_body"]
        self.assertEqual(body["ratio"], "2:3")
        self.assertEqual(body["resolution"], "2K")
        self.assertEqual(body["output_format"], "png")
        self.assertEqual(body["n"], 1)
        self.assertEqual(len(body["external_id"]), 32)


if __name__ == "__main__":
    unittest.main()
