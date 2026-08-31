# -*- coding: utf-8 -*-
import unittest

from core.apiutil import ApiError
from core.providers import REGISTRY, resolve_id
from core.providers.base import ImageTask, VideoTask
from core.providers.gate import GateProvider, _image_shape


class GateTests(unittest.TestCase):
    def test_registered_as_independent_provider(self):
        self.assertIn("gate", REGISTRY)
        self.assertEqual(resolve_id("astralmind"), "gate")
        self.assertEqual(GateProvider().session._proxies(), {"http": "", "https": ""})
        self.assertEqual(GateProvider(proxy="http://127.0.0.1:7890").session._proxies(),
                         {"http": "http://127.0.0.1:7890",
                          "https": "http://127.0.0.1:7890"})

    def test_seedance_25_and_20_limits_are_separate(self):
        video = GateProvider().capabilities()["video"]
        v25 = video["model_options"]["seedance-2.5"]
        v20 = video["model_options"]["seedance-2.0-standard"]
        self.assertEqual((min(v25["durations"]), max(v25["durations"])), (4, 30))
        self.assertEqual(v25["max_refs"], 30)
        self.assertEqual((min(v20["durations"]), max(v20["durations"])), (4, 15))
        self.assertEqual(v20["max_refs"], 9)

    def test_live_model_response_is_normalized(self):
        p = GateProvider(api_key="k")
        p.session.request = lambda *_, **__: {
            "data": [{"model_group": "seedance-2.5"},
                     {"model_group": "nano-banana-2"},
                     {"model_group": "seedance-2.5"}]}
        self.assertEqual(p.list_models(), ["nano-banana-2", "seedance-2.5"])

    def test_image_schema_adapts_n_size_and_references(self):
        p = GateProvider(api_key="k")
        captured = {}
        p.session.request = lambda method, path, **kwargs: (
            captured.update({"method": method, "path": path, **kwargs}) or
            {"data": [{"url": "https://cdn.example/image.png"}]})
        p.session.save_item = lambda *_: None
        p.generate_image(ImageTask(prompt="x", refs=["data:image/png;base64,eA=="],
                                   size="1024x1536", model="seedream-5-0-pro"),
                         "out.png")
        body = captured["json_body"]
        self.assertEqual(captured["path"], "/v1/images/generations")
        self.assertEqual(body["image"], "data:image/png;base64,eA==")
        self.assertEqual(body["size"], "1024x1536")
        self.assertNotIn("n", body)
        self.assertEqual(captured["retries"], 1)

    def test_special_image_model_shapes(self):
        self.assertEqual(_image_shape("qwen-image-2.0-pro", "1024x1536"),
                         {"size": "1024*1536"})
        self.assertEqual(_image_shape("seedream-4-5", "1024x1536"), {"size": "2K"})
        self.assertEqual(_image_shape("kling-image-o3", "1024x1536"),
                         {"resolution": "1K", "aspect_ratio": "2:3"})

    def test_unsupported_reference_is_not_silently_dropped(self):
        with self.assertRaises(ApiError):
            GateProvider(api_key="k").generate_image(
                ImageTask(prompt="x", refs=["data:image/png;base64,eA=="],
                          model="gpt-image-2"), "out.png")

    def test_video_wire_format_uses_plain_url_arrays(self):
        p = GateProvider(api_key="k")
        captured = {}
        p.session.request = lambda method, path, **kwargs: (
            captured.update({"method": method, "path": path, **kwargs}) or
            {"id": "job-1", "url": "https://cdn.example/video.mp4"})
        p.session.save_item = lambda *_: None
        p.generate_video(VideoTask(
            prompt="x", refs=["https://cdn.example/a.png"], duration=30,
            ratio="9:16", resolution="720p", model="seedance-2.5",
            extra={"video_refs": ["https://cdn.example/b.mp4"],
                   "audio_refs": ["https://cdn.example/c.mp3"]}), "out.mp4")
        body = captured["json_body"]
        self.assertEqual(captured["path"], "/api/multimodal/create_task")
        self.assertEqual(body["duration"], 30)
        self.assertEqual(body["image_url"], ["https://cdn.example/a.png"])
        self.assertEqual(body["video_url"], ["https://cdn.example/b.mp4"])
        self.assertEqual(body["audio_url"], ["https://cdn.example/c.mp3"])
        self.assertEqual(captured["retries"], 1)

    def test_video_rejects_local_reference_before_paid_request(self):
        with self.assertRaises(ApiError):
            GateProvider(api_key="k").generate_video(
                VideoTask(prompt="x", refs=[r"C:\local\a.png"], duration=15,
                          model="seedance-2.5"), "out.mp4")


if __name__ == "__main__":
    unittest.main()
