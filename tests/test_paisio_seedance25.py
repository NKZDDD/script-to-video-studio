# -*- coding: utf-8 -*-
import unittest

from core.apiutil import ApiError, TASK_FATAL
from core.providers.base import VideoTask
from core.providers.paisio import (PAISIO_SEEDANCE25_DURATIONS,
                                   PAISIO_SEEDANCE25_MODELS,
                                   PAISIO_SEEDANCE25_RATIOS, PaisioProvider,
                                   SEEDANCE25_DURATIONS, SEEDANCE25_MODELS,
                                   SEEDANCE25_RATIOS)


class PaisioSeedance25Tests(unittest.TestCase):
    def test_capabilities_declare_model_specific_limits(self):
        video = PaisioProvider().capabilities()["video"]
        for model in SEEDANCE25_MODELS:
            self.assertIn(model, video["models"])
            options = video["model_options"][model]
            self.assertEqual(options["durations"], list(range(4, 30)))
            self.assertEqual(options["ratios"], SEEDANCE25_RATIOS)
            self.assertEqual(options["max_refs"], 30)
            self.assertEqual(options["max_video_refs"], 10)
            self.assertEqual(options["max_audio_refs"], 10)
        # 旧模型仍保持原来的参数，不能被2.5的上限污染。
        self.assertEqual(video["durations"], [4, 5, 8, 10, 12, 15])
        self.assertEqual(video["max_refs"], 9)

    def test_prefixed_models_use_the_verified_four_to_fifteen_limits(self):
        video = PaisioProvider().capabilities()["video"]
        for model in PAISIO_SEEDANCE25_MODELS:
            self.assertIn(model, video["models"])
            options = video["model_options"][model]
            self.assertEqual(options["durations"], PAISIO_SEEDANCE25_DURATIONS)
            self.assertEqual(options["ratios"], PAISIO_SEEDANCE25_RATIOS)
            self.assertEqual(options["max_refs"], 9)
            self.assertEqual(options["max_video_refs"], 3)
            self.assertEqual(options["max_audio_refs"], 3)

    def test_seedance25_uses_documented_request_fields(self):
        task = VideoTask(
            prompt="让人物保持一致并自然运动",
            refs=["https://cdn.example/1.png", "https://cdn.example/2.png"],
            duration=29, ratio="21:9", model="seedance-2.5-720p",
            extra={
                "video_refs": ["https://cdn.example/motion.mp4"],
                "audio_refs": ["https://cdn.example/voice.wav"],
            },
        )
        body = PaisioProvider._seedance25_body(task, task.model)
        self.assertEqual(body, {
            "model": "seedance-2.5-720p",
            "prompt": "让人物保持一致并自然运动",
            "duration": 29,
            "aspect_ratio": "21:9",
            "image_url": "https://cdn.example/1.png",
            "extra_images": ["https://cdn.example/2.png"],
            "extra_videos": ["https://cdn.example/motion.mp4"],
            "extra_audios": ["https://cdn.example/voice.wav"],
        })
        self.assertNotIn("metadata", body)
        self.assertNotIn("images", body)

    def test_prefixed_model_dispatches_to_new_fields_and_keeps_prompt_exactly(self):
        provider = PaisioProvider(api_key="k")
        captured = {}
        provider.session.request = lambda method, path, **kwargs: (
            captured.update({"method": method, "path": path, **kwargs}) or
            {"task_id": "job-1", "video_url": "https://cdn.example/out.mp4"})
        provider.session.save_item = lambda *_: None
        prompt = "@Image1 在窗台晒太阳，环境严格参考@Image2"
        provider.generate_video(VideoTask(
            prompt=prompt,
            refs=["https://cdn.example/cat.png", "https://cdn.example/room.png"],
            duration=15, ratio="9:16", model="paisio-seedance-2.5-480p"),
            "out.mp4")
        body = captured["json_body"]
        self.assertEqual(captured["path"], "/v1/videos")
        self.assertEqual(body["prompt"], prompt)
        self.assertEqual(body["image_url"], "https://cdn.example/cat.png")
        self.assertEqual(body["extra_images"], ["https://cdn.example/room.png"])
        self.assertEqual(body["aspect_ratio"], "9:16")
        self.assertNotIn("images", body)
        self.assertNotIn("metadata", body)
        self.assertNotIn("@图", body["prompt"])

    def test_prefixed_model_rejects_thirty_seconds_before_paid_request(self):
        task = VideoTask(prompt="x", duration=30, ratio="9:16",
                         model="paisio-seedance-2.5-480p")
        with self.assertRaisesRegex(ApiError, "4-15秒"):
            PaisioProvider._seedance25_body(task, task.model)

    def test_prompt_over_document_limit_is_not_silently_truncated(self):
        task = VideoTask(prompt="字" * 2501, duration=15, ratio="9:16",
                         model="paisio-seedance-2.5-480p")
        with self.assertRaisesRegex(ApiError, "最多2500字符"):
            PaisioProvider._seedance25_body(task, task.model)

    def test_seedance25_rejects_invalid_limits_before_request(self):
        task = VideoTask(prompt="x", refs=["data:image/png;base64,abc"],
                         duration=30, ratio="2:1", model=SEEDANCE25_MODELS[0],
                         extra={"videos": ["v"] * 11, "audios": ["a"] * 11})
        with self.assertRaises(ApiError) as raised:
            PaisioProvider._seedance25_body(task, task.model)
        self.assertEqual(raised.exception.kind, TASK_FATAL)
        message = str(raised.exception)
        self.assertIn("4-29秒", message)
        self.assertIn("视频素材最多10条", message)
        self.assertIn("音频素材最多10条", message)
        self.assertIn("公网", message)

    def test_provider_marks_only_seedance25_as_url_only(self):
        provider = PaisioProvider()
        self.assertTrue(provider.needs_url("seedance-2.5-480p", "video"))
        self.assertTrue(provider.needs_url("paisio-seedance-2.5-480p", "video"))
        self.assertFalse(provider.needs_url("sd2-720p", "video"))

    def test_legacy_video_payload_is_unchanged(self):
        task = VideoTask(prompt="legacy", refs=["data:image/png;base64,abc"],
                         duration=15, ratio="9:16", model="sd2-720p")
        body = PaisioProvider._legacy_video_body(task, task.model)
        self.assertEqual(body["model"], "sd2-720p")
        self.assertEqual(body["images"], task.refs)
        self.assertEqual(body["metadata"]["ratio"], "9:16")
        self.assertIn("@图1", body["prompt"])


if __name__ == "__main__":
    unittest.main()
