# -*- coding: utf-8 -*-
import unittest

from core.providers import REGISTRY, resolve_id
from core.providers.base import ImageTask, VideoTask
from core.providers.valeclouds import ValeCloudsProvider, VIDEO_MODELS

PNG_1X1 = ("data:image/png;base64,"
           "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQ"
           "DwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


class ValeCloudsTests(unittest.TestCase):
    def test_registered_and_aliased(self):
        self.assertIn("valeclouds", REGISTRY)
        for alias in ("ny", "NY", "vale", "valeclouds"):
            self.assertEqual(resolve_id(alias), "valeclouds")

    def test_base_url_accepts_pasted_v1_suffix(self):
        p = ValeCloudsProvider(api_key="k", base_url="https://valeclouds.com/v1")
        self.assertEqual(p.session.base_url, "https://valeclouds.com")

    def test_capabilities_declare_both_media(self):
        cap = ValeCloudsProvider().capabilities()
        self.assertEqual(cap["supports"], ["image", "video"])
        self.assertIn("seedance-2.5-official", cap["video"]["models"])
        self.assertEqual(set(cap["video"]["models"]), set(VIDEO_MODELS))
        self.assertIn("gpt-image-2.5-sunburst", cap["image"]["models"])

    def test_video_quotes_then_creates_with_idempotency_key(self):
        p = ValeCloudsProvider(api_key="k")
        calls = []

        def fake(method, path, **kwargs):
            calls.append((method, path, kwargs))
            if path == "/v1/video/quotes":
                return {"id": "quote_1", "amount": 3}
            return {"id": "job-1", "url": "https://cdn.example/out.mp4"}

        p.session.request = fake
        p.session.save_item = lambda *a, **k: None
        p.generate_video(VideoTask(
            prompt="图1的女人抬头", refs=["https://cdn.example/a.png"],
            duration=12, ratio="9:16", resolution="720P",
            model="seedance-2.5-official"), "out.mp4")

        self.assertEqual([c[1] for c in calls], ["/v1/video/quotes", "/v1/videos"])
        quote_body = calls[0][2]["json_body"]
        self.assertEqual(quote_body["duration"], 12)
        self.assertEqual(quote_body["ratio"], "9:16")
        self.assertEqual(quote_body["resolution"], "720p")
        self.assertEqual(quote_body["referenceImages"], ["https://cdn.example/a.png"])

        create_body = calls[1][2]["json_body"]
        self.assertEqual(create_body["quote_id"], "quote_1")
        self.assertEqual(create_body, dict(quote_body, quote_id="quote_1"))
        key = calls[1][2]["headers"]["Idempotency-Key"]
        self.assertGreaterEqual(len(key), 8)
        self.assertLessEqual(len(key), 200)

    def test_local_reference_is_uploaded_before_create(self):
        p = ValeCloudsProvider(api_key="k")
        calls = []

        def fake(method, path, **kwargs):
            calls.append((method, path, kwargs))
            if path == "/v1/video/assets/image":
                return {"url": "https://cdn.example/uploaded.png", "asset_id": 1}
            if path == "/v1/video/quotes":
                return {"id": "quote_1"}
            return {"id": "job-1", "url": "https://cdn.example/out.mp4"}

        p.session.request = fake
        p.session.save_item = lambda *a, **k: None
        p.generate_video(VideoTask(prompt="x", refs=[PNG_1X1],
                                   model="seedance-len"), "out.mp4")

        upload = calls[0]
        self.assertEqual(upload[1], "/v1/video/assets/image")
        self.assertEqual(upload[2]["headers"]["Content-Type"], "image/png")
        self.assertTrue(upload[2]["raw_body"])
        self.assertFalse(upload[2]["submit"])
        self.assertEqual(calls[1][2]["json_body"]["referenceImages"],
                         ["https://cdn.example/uploaded.png"])

    def test_video_reference_over_limit_fails_instead_of_dropping(self):
        p = ValeCloudsProvider(api_key="k")
        with self.assertRaisesRegex(Exception, "最多 9 个参考图"):
            p.generate_video(VideoTask(
                prompt="x", model="seedance2.0-official",
                refs=[f"https://cdn.example/{i}.png" for i in range(10)]), "out.mp4")

    def test_image_without_reference_uses_generations(self):
        p = ValeCloudsProvider(api_key="k")
        captured = {}

        def fake(method, path, **kwargs):
            captured.update(path=path, **kwargs)
            return {"data": [{"url": "https://cdn.example/img.png"}]}

        p.session.request = fake
        p.session.save_item = lambda *a, **k: None
        p.generate_image(ImageTask(prompt="猫", model="gpt-image-2.5-sunburst",
                                   size="1024x1024"), "out.png")

        self.assertEqual(captured["path"], "/v1/images/generations")
        self.assertEqual(captured["json_body"]["size"], "1024x1024")
        self.assertEqual(captured["json_body"]["n"], 1)

    def test_image_with_reference_uses_multipart_edits(self):
        p = ValeCloudsProvider(api_key="k")
        captured = {}

        def fake(method, path, **kwargs):
            captured.update(path=path, **kwargs)
            return {"data": [{"url": "https://cdn.example/img.png"}]}

        p.session.request = fake
        p.session.save_item = lambda *a, **k: None
        p.generate_image(ImageTask(prompt="猫", model="gpt-image-2.5-sunburst",
                                   refs=[PNG_1X1]), "out.png")

        self.assertEqual(captured["path"], "/v1/images/edits")
        kinds = [name for name, _ in captured["files"]]
        self.assertIn("image", kinds)
        image = dict(captured["files"])["image"]
        self.assertEqual(image[2], "image/png")


if __name__ == "__main__":
    unittest.main()
