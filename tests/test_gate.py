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

    def test_video_body_is_inputs_plus_metadata_not_flat(self):
        """★ 锁请求体形状。以前发的是扁平字段，文档里没有一个在那个位置。

        文档 4.1/4.3：inputs[] 每条素材独立一项、各带 format；视频参数在
        metadata{} 里。而文档第 3 条原话是「未被模型声明的参数会被**静默丢弃**
        或被下游拒绝」—— 发扁平的后果是任务建得起来、task_id 也拿得到，
        但提示词和参考图很可能一个都没进去。片子出得来，只是跟你要的没关系。
        """
        p = GateProvider(api_key="k")
        seen = {}
        p.session.request = lambda method, path, **kw: (
            seen.update({"method": method, "path": path, **kw}) or
            ({"task_id": "job-1"} if path.endswith("create_task") else
             {"taskId": "job-1", "status": "success", "results": [
                 {"parameters": [{"name": "video_url", "format": "url",
                                  "value": "https://cdn.example/video.mp4"}]}]}))
        p.session.save_item = lambda *_: None
        p.generate_video(VideoTask(
            prompt="一只橘猫", refs=["https://cdn.example/a.png"], duration=30,
            ratio="9:16", resolution="720p", model="seedance-2.5",
            extra={"video_refs": ["https://cdn.example/b.mp4"],
                   "audio_refs": ["https://cdn.example/c.mp3"]}),
            "out.mp4", log=lambda *_: None, poll_interval=0)
        body = p.build_video_body(VideoTask(
            prompt="一只橘猫", refs=["https://cdn.example/a.png"], duration=30,
            ratio="9:16", resolution="720p", model="seedance-2.5",
            extra={"video_refs": ["https://cdn.example/b.mp4"],
                   "audio_refs": ["https://cdn.example/c.mp3"]}), "seedance-2.5")
        self.assertEqual(body["inputs"], [
            {"name": "prompt", "value": "一只橘猫", "format": "text"},
            {"name": "image_url", "value": "https://cdn.example/a.png",
             "format": "reference_image"},
            {"name": "video_url", "value": "https://cdn.example/b.mp4",
             "format": "reference_video"},
            {"name": "audio_url", "value": "https://cdn.example/c.mp3",
             "format": "reference_audio"}])
        self.assertEqual(body["metadata"]["duration"], 30)
        self.assertEqual(body["metadata"]["ratio"], "9:16")
        # 编出来的扁平字段一个都不许再出现
        for dead in ("prompt", "duration", "ratio", "resolution",
                     "image_url", "video_url", "audio_url"):
            self.assertNotIn(dead, body, f"{dead} 不该在顶层")

    def test_query_endpoint_is_post_get_result_not_v1_videos(self):
        """★ /v1/videos/{id} 是编的，而它在 Gate（litellm 搭的）上是 OpenAI 直通路由。

        实遇：我们 GET 它，Gate 把 task_id 转发去了 api.openai.com/v1/videos/{uuid}，
        超时回 500，日志刷了一屏 litellm.APIConnectionError。
        """
        p = GateProvider(api_key="k")
        paths = []
        def fake(method, path, **kw):
            paths.append((method, path, kw.get("json_body")))
            if path.endswith("create_task"):
                return {"task_id": "job-9"}
            return {"taskId": "job-9", "status": "success", "results": [
                {"result": {"content": {"video_url": "https://cdn/v.mp4"}}}]}
        p.session.request = fake
        p.session.save_item = lambda *_: None
        p.generate_video(VideoTask(prompt="x", duration=10, ratio="16:9",
                                   resolution="720p", model="seedance-2.0-mini"),
                         "out.mp4", log=lambda *_: None, poll_interval=0)
        method, path, body = paths[-1]
        self.assertEqual((method, path), ("POST", "/api/multimodal/get_result"))
        self.assertEqual(body, {"model": "seedance-2.0-mini", "taskId": "job-9"})
        self.assertFalse([p_ for _, p_, _ in paths if "/v1/videos" in p_],
                         "还在打 /v1/videos —— 那会被转发去 OpenAI")

    def test_schema_declared_unsupported_params_are_refused(self):
        """Schema 原话：宁可显式拒绝也不静默丢弃。我们照做。"""
        p = GateProvider(api_key="k")
        with self.assertRaises(ApiError) as c:
            p.build_video_body(VideoTask(prompt="x", duration=10, model="seedance-2.0-mini",
                                         ratio="16:9", resolution="720p",
                                         extra={"camera_fixed": True}), "seedance-2.0-mini")
        self.assertIn("camera_fixed", str(c.exception))
        # 2.5 系支持它，不该被拦
        b = p.build_video_body(VideoTask(prompt="x", duration=10, model="seedance-2.5",
                                         ratio="16:9", resolution="720p",
                                         extra={"camera_fixed": True}), "seedance-2.5")
        self.assertTrue(b["metadata"]["camera_fixed"])

    def test_limits_follow_the_live_schema_per_model(self):
        p = GateProvider(api_key="k")
        # 2.0 系 9 图，2.5 系 30 图
        with self.assertRaises(ApiError):
            p.build_video_body(VideoTask(
                prompt="x", refs=[f"https://cdn/{i}.png" for i in range(10)],
                duration=10, ratio="16:9", resolution="720p",
                model="seedance-2.0-mini"), "seedance-2.0-mini")
        p.build_video_body(VideoTask(
            prompt="x", refs=[f"https://cdn/{i}.png" for i in range(10)],
            duration=10, ratio="16:9", resolution="720p",
            model="seedance-2.5"), "seedance-2.5")
        # 2.0 系只给音频、不给图/视频 -> Schema 的 x-requires-any-of
        with self.assertRaises(ApiError):
            p.build_video_body(VideoTask(
                prompt="x", duration=10, ratio="16:9", resolution="720p",
                model="seedance-2.0-mini",
                extra={"audio_refs": ["https://cdn/a.mp3"]}), "seedance-2.0-mini")

    def test_first_last_frame_uses_the_documented_formats(self):
        p = GateProvider(api_key="k")
        b = p.build_video_body(VideoTask(
            prompt="x", refs=["https://cdn/a.png", "https://cdn/b.png"],
            duration=10, ratio="16:9", resolution="720p", model="seedance-2.5",
            extra={"first_last": True}), "seedance-2.5")
        self.assertEqual([i["format"] for i in b["inputs"][1:]],
                         ["first_frame", "last_frame"])

    def test_video_rejects_local_reference_before_paid_request(self):
        with self.assertRaises(ApiError):
            GateProvider(api_key="k").generate_video(
                VideoTask(prompt="x", refs=[r"C:\local\a.png"], duration=15,
                          model="seedance-2.5"), "out.mp4")


if __name__ == "__main__":
    unittest.main()
