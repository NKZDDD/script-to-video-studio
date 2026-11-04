# -*- coding: utf-8 -*-
import unittest

from core.providers import REGISTRY, resolve_id
from core.providers.ake import AkeProvider, _duration, _limits
from core.providers.base import VideoTask


class AkeTests(unittest.TestCase):
    def test_registered_and_aliased(self):
        self.assertIn("ake", REGISTRY)
        for alias in ("snumom", "阿珂", "ako"):
            self.assertEqual(resolve_id(alias), "ake")

    def test_capabilities_match_unified_document(self):
        cap = AkeProvider().capabilities()["video"]
        self.assertIn("wan3.0-video", cap["models"])
        self.assertIn("grok-imagine-video-1.5", cap["models"])
        self.assertEqual(cap["max_refs"], 10)
        self.assertEqual(cap["model_options"]["grok-imagine-video-1.5"]["max_refs"], 7)
        self.assertEqual(AkeProvider.ref_mode, "url")

    def test_model_limits(self):
        self.assertEqual(_duration("wan3.0-video", 40), 40)
        self.assertEqual(_duration("grok-imagine-video-1.5", 2), 2)
        self.assertEqual(_duration("wan-3.0", 60), 60)
        self.assertEqual(_limits("wan3.0-video"), (10, 5, 5))
        self.assertEqual(_limits("wan3.0-image"), (10, 0, 5))
        self.assertEqual(_limits("grok-imagine-video-1.5"), (7, 0, 0))

    def test_hyphenated_wan_catalog_and_request_keep_ten_images(self):
        from core.model_catalog import apply_catalog
        # /v1/models 只返回 ID，新旧拼写必须一致，不能落入未知模型的 4 图分支。
        for model in ("wan-3.0-video", "wan-3.0-video-prime", "wan3.0-video-nsfw"):
            with self.subTest(model=model):
                p = AkeProvider(api_key="test-key")
                captured = {}
                def fake(method, path, **kwargs):
                    self.assertEqual((method, path), ("POST", "/v1/videos"))
                    captured.update(kwargs["json_body"])
                    return {"id": "job-1", "metadata": {"url": "https://cdn.example/out.mp4"}}
                p.session.request = fake
                p.session.save_item = lambda *_: None
                refs = [f"https://cdn.example/{i}.png" for i in range(10)]
                videos = [f"https://cdn.example/{i}.mp4" for i in range(5)]
                audios = [f"https://cdn.example/{i}.mp3" for i in range(5)]
                p.generate_video(VideoTask(prompt="test", model=model, refs=refs,
                    duration=60, extra={"video_refs": videos, "audio_refs": audios,
                                        "prompt_extend": False}), "out.mp4", log=lambda *_: None)
                self.assertEqual(captured["model"], model)
                self.assertEqual(captured["seconds"], "60")
                self.assertEqual([r["url"] for r in captured["reference_images"]], refs)
                self.assertEqual([r["url"] for r in captured["reference_videos"]], videos)
                self.assertEqual([r["url"] for r in captured["reference_audios"]], audios)
                self.assertIs(captured["prompt_extend"], False)
                cap = apply_catalog(p.capabilities(), {"rows": [{"id": model}]}, "test")["video"]
                effective = dict(cap, **cap.get("model_options", {}).get(model, {}))
                self.assertIn(model, cap["models"])
                self.assertEqual(effective["max_refs"], 10)

    def test_wan_aliases_preserve_other_reference_limits(self):
        from unittest.mock import Mock
        self.assertEqual(_limits("wan-3.0"), (9, 3, 3))
        self.assertEqual(_limits("unknown-model"), (4, 0, 0))
        for model in ("wan-3.0-video", "wan-3.0-video-prime"):
            with self.subTest(model=model):
                p = AkeProvider(api_key="test-key")
                p.session.request = Mock()
                with self.assertRaisesRegex(Exception, "最多 10 张"):
                    p.generate_video(VideoTask(prompt="test", model=model,
                        refs=[f"https://cdn.example/{i}.png" for i in range(11)]), "out.mp4")
                p.session.request.assert_not_called()
        for model in ("wan3.0-image", "wan3.0-image-prime"):
            with self.subTest(model=model):
                self.assertEqual(_limits(model), (10, 0, 5))
                p = AkeProvider(api_key="test-key")
                p.session.request = Mock()
                with self.assertRaisesRegex(Exception, "不支持参考视频"):
                    p.generate_video(VideoTask(prompt="test", model=model,
                        extra={"video_refs": ["https://cdn.example/v.mp4"]}), "out.mp4")
                p.session.request.assert_not_called()

    def test_h3_sends_all_references_without_local_count_limit(self):
        from core.model_catalog import apply_catalog
        for model in ("minimax_h3", "minimax_h3-fast", "H3", "h3-pro"):
            with self.subTest(model=model):
                p = AkeProvider(api_key="test-key")
                captured = {}
                def fake(method, path, **kwargs):
                    captured.update(kwargs["json_body"])
                    return {"id": "job-1", "metadata": {"url": "https://cdn.example/out.mp4"}}
                p.session.request = fake
                p.session.save_item = lambda *_: None
                refs = [f"https://cdn.example/{i}.png" for i in range(16)]
                p.generate_video(VideoTask(prompt="test", model=model, refs=refs), "out.mp4")
                self.assertEqual([r["url"] for r in captured["reference_images"]], refs)
                cap = apply_catalog(p.capabilities(), {"rows": [{"id": model, "max_images": 4}]}, "test")
                effective = dict(cap["video"], **cap["video"]["model_options"][model])
                self.assertTrue(effective["refs_unlimited"])
                self.assertIsNone(effective["max_refs"])

    def test_other_video_models_keep_documented_limit(self):
        p = AkeProvider(api_key="test-key")
        with self.assertRaisesRegex(Exception, "最多 7 张"):
            p.generate_video(VideoTask(prompt="test", model="grok-imagine-video-1.5",
                refs=[f"https://cdn.example/{i}.png" for i in range(8)]), "out.mp4")

    def test_request_uses_object_reference_arrays(self):
        p = AkeProvider(api_key="k")
        captured = {}

        def fake(method, path, **kwargs):
            captured.update(kwargs["json_body"])
            return {"id": "job-1", "metadata": {"url": "https://cdn.example/out.mp4"}}

        p.session.request = fake
        p.session.save_item = lambda *_: None
        p.generate_video(VideoTask(
            prompt="图1的人物参考视频1", refs=["https://cdn.example/char.jpg"],
            duration=15, ratio="9:16", resolution="720P", model="wan3.0-video",
            extra={"video_refs": ["https://cdn.example/motion.mp4"],
                   "audio_refs": ["https://cdn.example/voice.mp3"],
                   "video_durations": [5], "image_roles": ["reference_image"]}),
            "out.mp4")
        self.assertEqual(captured["seconds"], "15")
        self.assertEqual(captured["size"], "720P")
        self.assertEqual(captured["aspect_ratio"], "9:16")
        self.assertEqual(captured["reference_images"], [{
            "url": "https://cdn.example/char.jpg", "role": "reference_image"}])
        self.assertEqual(captured["reference_videos"], [{
            "url": "https://cdn.example/motion.mp4", "duration": 5}])
        self.assertEqual(captured["reference_audios"], [{
            "url": "https://cdn.example/voice.mp3"}])

    def test_missing_public_reference_fails_instead_of_dropping(self):
        p = AkeProvider(api_key="k")
        with self.assertRaisesRegex(Exception, "只收公网 URL"):
            p.generate_video(VideoTask(prompt="x", refs=["data:image/png;base64,abc"],
                                       model="wan3.0-video"), "out.mp4")


if __name__ == "__main__":
    unittest.main()
