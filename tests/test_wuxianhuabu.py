# -*- coding: utf-8 -*-
import base64
import unittest

from core.providers import REGISTRY, resolve_id
from core.providers.base import VideoTask
from core.providers.wuxianhuabu import (MAX_AUDIOS, MAX_IMAGES, MAX_VIDEOS,
                                        WuxianhuabuProvider)


class WuxianhuabuTests(unittest.TestCase):
    def test_registered_as_independent_provider(self):
        self.assertIn("wuxianhuabu", REGISTRY)
        self.assertNotEqual(resolve_id("videogogo"), "ake")
        self.assertEqual(resolve_id("videogogo"), "wuxianhuabu")

    def test_models_and_mixed_reference_limits(self):
        """清单不写死条数。

        原来这条断言 `models == ["seedance-2.5-hf-720p", "seedance-2.5-hf"]` ——
        那是照文档写的两个，而 2026-09-01 实拉 `/v1/models` 是**五个**。
        把条数钉死等于「平台上新，测试红，然后人去改测试」，
        而该改的是清单（逐模型约束见 tests/test_wuxianhuabu_models.py）。
        这里只钉「文档里那两个还在」和整家的接口上限。
        """
        cap = WuxianhuabuProvider().capabilities()["video"]
        for m in ("seedance-2.5-hf-720p", "seedance-2.5-hf"):
            self.assertIn(m, cap["models"])
        self.assertEqual(cap["default_model"], "seedance-2.5-hf-720p")
        self.assertEqual(cap["durations"], list(range(4, 31)))
        self.assertEqual(cap["max_refs"], MAX_IMAGES)
        self.assertEqual(cap["max_video_refs"], MAX_VIDEOS)
        self.assertEqual(cap["max_audio_refs"], MAX_AUDIOS)

    def test_upload_and_create_wire_format(self):
        p = WuxianhuabuProvider(api_key="k")
        calls = []

        def fake(method, path, **kwargs):
            calls.append((method, path, kwargs))
            if path == "/v1/assets":
                self.assertEqual(kwargs["raw_body"], b"image-bytes")
                return {"asset_id": "asset-image-1"}
            return {"id": "job-1", "status": "completed",
                    "download_url": "https://cdn.example/out.mp4"}

        p.session.request = fake
        p.session.save_item = lambda *_: None
        ref = "data:image/png;base64," + base64.b64encode(b"image-bytes").decode()
        p.generate_video(VideoTask(
            prompt="x", refs=[ref, "https://cdn.example/ref.jpg"], duration=30,
            ratio="9:16", model="seedance-2.5-hf-720p",
            extra={"video_refs": ["video-asset"], "audio_refs": ["audio-asset"]}),
            "out.mp4")
        create = next(x for x in calls if x[1] == "/v1/videos")
        body = create[2]["json_body"]
        self.assertEqual(body["seconds"], 30)
        self.assertEqual(body["resolution"], "720p")
        self.assertEqual(body["reference_images"],
                         ["asset-image-1", "https://cdn.example/ref.jpg"])
        self.assertEqual(body["reference_videos"], ["video-asset"])
        self.assertEqual(body["reference_audios"], ["audio-asset"])
        self.assertTrue(body["generate_audio"])
        self.assertFalse(body["watermark"])
        self.assertNotIn("size", body)
        self.assertIn("Idempotency-Key", create[2]["headers"])

    def test_model_controls_resolution(self):
        p = WuxianhuabuProvider(api_key="k")
        captured = {}
        p.session.request = lambda method, path, **kwargs: (
            captured.update(kwargs["json_body"]) or
            {"id": "j", "download_url": "https://cdn.example/o.mp4"})
        p.session.save_item = lambda *_: None
        p.generate_video(VideoTask(prompt="x", duration=4, model="seedance-2.5-hf"),
                         "out.mp4")
        self.assertEqual(captured["resolution"], "480p")


if __name__ == "__main__":
    unittest.main()
