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
        self.assertEqual(_duration("wan3.0-video", 40), 30)
        self.assertEqual(_duration("grok-imagine-video-1.5", 2), 4)
        self.assertEqual(_duration("wan-3.0", 60), 60)
        self.assertEqual(_limits("wan3.0-video"), (10, 5, 5))
        self.assertEqual(_limits("wan3.0-image"), (10, 0, 5))
        self.assertEqual(_limits("grok-imagine-video-1.5"), (7, 0, 0))

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
