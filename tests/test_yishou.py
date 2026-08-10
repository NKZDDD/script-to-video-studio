# -*- coding: utf-8 -*-
import unittest

from core.apiutil import ApiError, TASK_FATAL
from core.providers import REGISTRY, resolve_id
from core.providers.base import VideoTask
from core.providers.yishou import YishouProvider


class YishouTests(unittest.TestCase):
    def test_registered_and_aliased(self):
        self.assertIn("yishou", REGISTRY)
        for alias in ("oneapi", "one-api", "weijin", "weijinapi"):
            self.assertEqual(resolve_id(alias), "yishou")

    def test_video_only_and_url_only(self):
        p = YishouProvider()
        self.assertEqual(tuple(p.capabilities()["supports"]), ("video",))
        # 参考图/音频没有上传接口，只收公网链接 —— 声明错了会让参考图被静默丢掉
        self.assertTrue(p.needs_url("", "video"))
        self.assertTrue(p.accepts_url("", "video"))
        self.assertFalse(p.needs_bytes(""))

    def test_models_left_empty_on_purpose(self):
        """模型清单由 GET /v1/models 给，写死会过期 —— 前端拿到空列表才会去拉。"""
        video = YishouProvider().capabilities()["video"]
        self.assertEqual(video["models"], [])
        self.assertEqual(video["default_model"], "")

    def test_rejects_empty_model(self):
        p = YishouProvider(api_key="k")
        with self.assertRaises(ApiError) as raised:
            p.generate_video(VideoTask(prompt="x", model=""), "out.mp4")
        self.assertEqual(raised.exception.kind, TASK_FATAL)
        self.assertIn("/v1/models", str(raised.exception))

    def test_rejects_local_refs_before_paying(self):
        p = YishouProvider(api_key="k")
        task = VideoTask(prompt="x", refs=["data:image/png;base64,abc"], model="video-model-720p")
        with self.assertRaises(ApiError) as raised:
            p.generate_video(task, "out.mp4")
        self.assertEqual(raised.exception.kind, TASK_FATAL)
        self.assertIn("公网", str(raised.exception))

    def test_upload_rejects_oversize(self):
        p = YishouProvider(api_key="k")
        with self.assertRaises(ApiError):
            p.upload_video("不存在的文件.mp4")


if __name__ == "__main__":
    unittest.main()
