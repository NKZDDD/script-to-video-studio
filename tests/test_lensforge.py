# -*- coding: utf-8 -*-
import unittest

from core.providers import REGISTRY, resolve_id
from core.providers.base import ImageTask, VideoTask
from core.providers.lensforge import (
    IMAGE_MODELS, VIDEO_MODELS, LensForgeProvider, _ratio_of, _tier_of)

OFFERS = [
    {"id": "off-20", "videoDisplayName": "渠道5-seedance2.0", "imageDisplayName": "无",
     "supportsVideo": True, "supportsImage": False,
     "videoDurations": list(range(5, 16)), "videoResolutions": ["480p", "720p"],
     "maxReferenceImages": 9, "maxReferenceVideos": 0, "maxReferenceAudios": 3},
    {"id": "off-30", "videoDisplayName": "渠道5-seedance2.5 满血", "imageDisplayName": "无",
     "supportsVideo": True, "supportsImage": False,
     "videoDurations": list(range(5, 31)), "videoResolutions": ["480p", "720p"],
     "maxReferenceImages": 30, "maxReferenceVideos": 0, "maxReferenceAudios": 10},
    {"id": "off-vref", "videoDisplayName": "渠道3-seedance2.0（支持视频参考）",
     "imageDisplayName": "无", "supportsVideo": True, "supportsImage": False,
     "videoDurations": list(range(5, 16)), "videoResolutions": ["720p"],
     "maxReferenceImages": 9, "maxReferenceVideos": 3, "maxReferenceAudios": 3},
    {"id": "off-img", "videoDisplayName": "无", "imageDisplayName": "gpt-image-2",
     "supportsVideo": False, "supportsImage": True,
     "maxReferenceImages": 9},
]


def _provider(calls):
    """目录与提交/轮询全部走桩，不联网。"""
    p = LensForgeProvider(api_key="test-key")

    def fake(method, path, **kwargs):
        calls.append((method, path, kwargs))
        if path.endswith("/catalog/offerings"):
            return {"items": OFFERS}
        if path == "/v1/images/generations":
            return {"id": "img-1", "data": [{"url": "https://cdn.example/out.png"}]}
        if path == "/api/v1/client/tasks":
            return {"task": {"id": "task-1"}}
        raise AssertionError(f"没有桩的请求：{method} {path}")

    p.session.request = fake
    p.session.poll = lambda *a, **k: "https://cdn.example/out.mp4"
    p.session.save_item = lambda *a, **k: None
    return p


class LensForgeTests(unittest.TestCase):
    def test_registered_and_aliased(self):
        self.assertIn("lensforge", REGISTRY)
        for alias in ("lf", "LF", "lensforge"):
            self.assertEqual(resolve_id(alias), "lensforge")

    def test_capabilities_follow_live_catalog(self):
        cap = LensForgeProvider().capabilities()
        self.assertEqual(cap["video"]["models"], VIDEO_MODELS)
        self.assertEqual(cap["image"]["models"], IMAGE_MODELS)
        self.assertIn("渠道5-seedance2.5 满血", VIDEO_MODELS)
        self.assertTrue(cap["video"]["reference_video_urls_supported"])
        self.assertEqual(cap["video"]["max_refs"], 30)
        self.assertEqual(cap["video"]["model_options"]["渠道3-seedance2.0（支持视频参考）"]["max_video_refs"], 3)
        self.assertEqual(cap["image"]["max_refs"], 6)
        self.assertEqual(LensForgeProvider.ref_mode, "bytes")

    def test_catalog_rows_use_readable_names_not_uuids(self):
        """★ /v1/models 回 UUID；下拉必须拿到目录里的展示名。"""
        p = LensForgeProvider(api_key="test-key")
        p._offers = OFFERS + [{"id": "off-test", "videoDisplayName": "测试通道",
                               "imageDisplayName": "测试出图",
                               "supportsVideo": True, "supportsImage": True}]
        models = {row["id"]: row["type"] for row in p.catalog_rows()}
        self.assertEqual(models["渠道5-seedance2.0"], "video")
        self.assertEqual(models["gpt-image-2"], "image")
        self.assertNotIn("测试通道", models)
        self.assertNotIn("测试出图", models)
        self.assertFalse([m for m in models if m.startswith("off-")])

    def test_pulled_catalog_keeps_readable_names(self):
        from core import model_catalog as MC
        p = LensForgeProvider()
        p._offers = OFFERS
        cap = MC.apply_catalog(p.capabilities(), {"ok": True, "rows": p.catalog_rows()}, "测试")
        self.assertEqual(cap["video"]["models"], VIDEO_MODELS)
        self.assertEqual(cap["image"]["models"], IMAGE_MODELS)
        self.assertNotIn("off-20", cap["video"]["models"])

    def test_filter_catalog_rewrites_uuids_for_older_clients(self):
        """旧客户端只会用 /v1/models 的 UUID；filter_catalog 必须换成展示名。"""
        from core import model_catalog as MC
        p = LensForgeProvider(api_key="test-key")
        p._offers = OFFERS
        uuids = ["off-20", "off-30", "off-vref", "off-img"]
        rows = p.filter_catalog([{"id": u} for u in uuids])
        self.assertEqual({r["id"] for r in rows}, set(VIDEO_MODELS) | set(IMAGE_MODELS))
        cap = MC.apply_catalog(p.capabilities(),
                               {"ok": True, "models": uuids, "rows": rows, "strict": True},
                               "测试")
        self.assertEqual(cap["video"]["models"], VIDEO_MODELS)
        self.assertEqual(cap["image"]["models"], IMAGE_MODELS)
        self.assertFalse([m for m in cap["video"]["models"] if m in uuids])

    def test_size_maps_to_ratio_and_tier(self):
        self.assertEqual(_ratio_of("1024x1536", ["1:1", "2:3", "3:2"]), "2:3")
        self.assertEqual(_ratio_of("1536x1024", ["1:1", "2:3", "3:2"]), "3:2")
        self.assertEqual(_tier_of("1024x1024", ["1k", "2k", "4k"]), "1k")
        self.assertEqual(_tier_of("1536x1024", ["1k", "2k", "4k"]), "2k")
        self.assertEqual(_tier_of("3840x2160", ["1k", "2k", "4k"]), "4k")

    def test_video_submits_native_task_with_materials_in_order(self):
        calls = []
        p = _provider(calls)
        p.generate_video(VideoTask(
            prompt="@Image1 的角色走动", refs=["https://cdn.example/a.png",
                                              "https://cdn.example/b.png"],
            duration=10, ratio="9:16", resolution="720p", model="渠道5-seedance2.0"), "out.mp4")
        body = next(k["json_body"] for m, path, k in calls if path == "/api/v1/client/tasks")
        self.assertEqual(body["kind"], "video")
        self.assertEqual(body["offeringId"], "off-20")
        self.assertEqual(body["duration"], 10)
        self.assertEqual(body["ratio"], "9:16")
        self.assertEqual(body["resolution"], "720p")
        self.assertTrue(body["generateAudio"])
        self.assertEqual(body["materials"], [
            {"type": "image", "role": "reference_image", "url": "https://cdn.example/a.png"},
            {"type": "image", "role": "reference_image", "url": "https://cdn.example/b.png"}])
        headers = next(k["headers"] for m, path, k in calls if path == "/api/v1/client/tasks")
        self.assertTrue(headers["Idempotency-Key"])
        self.assertGreaterEqual(len(headers["Idempotency-Key"]), 8)

    def test_duration_outside_catalog_is_sent_as_chosen(self):
        # 本地不再按目录档位夹取/拒绝，用户选的秒数原样发出，超范围交给服务商裁决。
        calls = []
        p = _provider(calls)
        p.generate_video(VideoTask(prompt="x", duration=20, model="渠道5-seedance2.0"), "out.mp4")
        body = next(k["json_body"] for m, path, k in calls if path == "/api/v1/client/tasks")
        self.assertEqual(body["duration"], 20)

    def test_resolution_outside_catalog_fails(self):
        p = _provider([])
        with self.assertRaisesRegex(Exception, "不支持 1080p"):
            p.generate_video(VideoTask(prompt="x", duration=5, resolution="1080p",
                                       model="渠道5-seedance2.0"), "out.mp4")

    def test_reference_video_and_audio_send_measured_duration(self):
        calls = []
        p = _provider(calls)
        import core.providers.lensforge as mod
        old = mod._measured_seconds
        mod._measured_seconds = lambda url: 3.5
        try:
            p.generate_video(VideoTask(
                prompt="x", refs=["https://cdn.example/a.png"], duration=5,
                resolution="720p", model="渠道3-seedance2.0（支持视频参考）",
                extra={"video_refs": ["https://cdn.example/ref.mp4"],
                       "audio_refs": ["https://cdn.example/beat.mp3"]}), "out.mp4")
        finally:
            mod._measured_seconds = old
        body = next(k["json_body"] for m, path, k in calls if path == "/api/v1/client/tasks")
        self.assertEqual(body["materials"][1], {
            "type": "video", "role": "reference_video",
            "url": "https://cdn.example/ref.mp4", "durationSeconds": 3.5})
        self.assertEqual(body["materials"][2], {
            "type": "audio", "role": "reference_audio",
            "url": "https://cdn.example/beat.mp3", "durationSeconds": 3.5})

    def test_unmeasurable_reference_duration_is_an_error_not_a_guess(self):
        import core.providers.lensforge as mod

        def boom(url):
            raise mod.ApiError("read failed", kind="task_fatal")

        old = mod._measured_seconds
        mod._measured_seconds = boom
        try:
            p = _provider([])
            with self.assertRaisesRegex(Exception, "read failed"):
                p.generate_video(VideoTask(
                    prompt="x", duration=5, resolution="720p",
                    model="渠道3-seedance2.0（支持视频参考）",
                    extra={"video_refs": ["https://cdn.example/ref.mp4"]}), "out.mp4")
        finally:
            mod._measured_seconds = old

    def test_too_many_reference_images_fails_instead_of_dropping(self):
        p = _provider([])
        refs = [f"https://cdn.example/{i}.png" for i in range(10)]
        with self.assertRaisesRegex(Exception, "最多 9 张"):
            p.generate_video(VideoTask(prompt="x", refs=refs, duration=5,
                                       model="渠道5-seedance2.0"), "out.mp4")

    def test_video_model_without_reference_video_rejects_it(self):
        p = _provider([])
        with self.assertRaisesRegex(Exception, "不支持视频参考"):
            p.generate_video(VideoTask(prompt="x", duration=5, model="渠道5-seedance2.0",
                                       extra={"video_refs": ["https://cdn.example/r.mp4"]}),
                             "out.mp4")

    def test_image_posts_offering_id_and_mapped_size(self):
        calls = []
        p = _provider(calls)
        p.generate_image(ImageTask(prompt="cat", refs=["https://cdn.example/a.png"],
                                   size="1024x1536", model="gpt-image-2"), "out.png")
        body = next(k["json_body"] for m, path, k in calls if path == "/v1/images/generations")
        self.assertEqual(body["model"], "off-img")
        self.assertEqual(body["n"], 1)
        self.assertEqual(body["ratio"], "2:3")
        self.assertEqual(body["resolution"], "2k")
        self.assertEqual(body["images"], ["https://cdn.example/a.png"])

    def test_image_rejects_second_output(self):
        p = _provider([])
        with self.assertRaisesRegex(Exception, "n 只能是 1"):
            p.generate_image(ImageTask(prompt="cat", n=2, model="gpt-image-2"), "out.png")


if __name__ == "__main__":
    unittest.main()
