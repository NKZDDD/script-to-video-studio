# -*- coding: utf-8 -*-
import unittest

from core.apiutil import DONE_STATES, FAIL_STATES, ApiError, TASK_FATAL
from core.providers import REGISTRY, resolve_id
from core.providers.base import ImageTask, VideoTask
from core.providers.xiaobalong import (DURATION_RULES, UPLOAD_LIMITS,
                                       XiaobalongProvider, _fit_duration)


def _stub(provider, reply):
    seen = {}

    def fake(method, path, json_body=None, files=None, retries=3, timeout=None):
        seen.update(method=method, path=path, body=json_body, files=files, retries=retries)
        return reply

    provider.session.request = fake
    provider.session.save_item = lambda item, dest: dest
    return seen


class XiaobalongTests(unittest.TestCase):
    def test_registered_and_aliased(self):
        self.assertIn("xiaobalong", REGISTRY)
        for alias in ("keik", "小霸龙", "xbl", "binghuo"):
            self.assertEqual(resolve_id(alias), "xiaobalong")

    def test_url_only_both_media(self):
        """图片只收公网URL、视频收URL或asset://，两边都不吃 data URI。"""
        p = XiaobalongProvider()
        self.assertTrue(p.needs_url("", "image"))
        self.assertTrue(p.needs_url("", "video"))
        self.assertFalse(p.needs_bytes(""))

    # -- 计费安全规则：创建 POST 只能发一次 ------------------------------
    def test_create_posts_never_retry(self):
        """文档硬规矩：图片和视频的创建 POST 都不得自动重试。retries=1 = 只发一次。"""
        p = XiaobalongProvider(api_key="k")
        seen = _stub(p, {"data": [{"url": "https://x/i.png"}]})
        p.generate_image(ImageTask(prompt="猫", model="image2"), "out.png")
        self.assertEqual(seen["retries"], 1)

        seen = _stub(p, {"id": "t1", "status": "completed",
                         "metadata": {"url": "https://api.keik.cc/v1/videos/t1/content"}})
        p.generate_video(VideoTask(prompt="走路", model="bh2.0-720p", duration=5), "out.mp4")
        self.assertEqual(seen["retries"], 1)

    def test_unknown_is_not_a_failure_state(self):
        """unknown 既不是完成也不是失败 —— 落进 FAIL_STATES 会让还在生成的付费任务被判死。"""
        self.assertNotIn("unknown", FAIL_STATES)
        self.assertNotIn("unknown", DONE_STATES)
        # 创建阶段 processing / 查询阶段 in_progress，两个都不能算完成或失败
        for s in ("processing", "in_progress", "queued"):
            self.assertNotIn(s, FAIL_STATES)
            self.assertNotIn(s, DONE_STATES)

    def test_video_body_shape(self):
        p = XiaobalongProvider(api_key="k")
        seen = _stub(p, {"id": "t1", "status": "completed",
                         "metadata": {"url": "https://api.keik.cc/v1/videos/t1/content"}})
        task = VideoTask(prompt="走路", refs=["https://a/1.jpg",
                                            "asset://xiaobalong/asset_x"],
                         duration=8, ratio="9:16", model="bh2.0-720p",
                         extra={"videos": ["asset://xiaobalong/asset_v"],
                                "audios": ["https://a/1.mp3"]})
        p.generate_video(task, "out.mp4")
        body = seen["body"]
        self.assertEqual(body["duration"], 8)              # 整数，不是字符串
        self.assertIsInstance(body["duration"], int)
        self.assertNotIn("seconds", body)
        self.assertEqual(body["aspect_ratio"], "9:16")
        # 素材必须是纯字符串数组，不能是 [{"url": …}]
        self.assertEqual(body["images"], ["https://a/1.jpg", "asset://xiaobalong/asset_x"])
        self.assertEqual(body["videos"], ["asset://xiaobalong/asset_v"])
        self.assertEqual(body["audios"], ["https://a/1.mp3"])
        self.assertNotIn("reference_videos", body)         # 和 videos 同发必须完全一致，索性不发

    def test_duration_falls_back_to_generic_range(self):
        """文档 r21 那两条白名单（quanneng2.0 / -9tu）的模型已下线，现在只剩通用 4–15。

        新模型的档位官方没给，**故意不猜** —— 猜错会按错的长度出片并计费，
        而越界只是 400（不结算）。所以这里断言 DURATION_RULES 是空的。
        """
        self.assertEqual(DURATION_RULES, {})
        self.assertEqual(_fit_duration("sd2.5-720p-301010", 30), 15)
        self.assertEqual(_fit_duration("sd2.5-720p-301010", 1), 4)
        self.assertEqual(_fit_duration("sd2-720p-933", 8), 8)

    def test_model_list_is_the_live_one_not_the_doc(self):
        """文档 r21 的 20 个视频模型 2026-08-16 实拉只剩 2 个还在 —— 别再照文档抄。"""
        from core.providers.xiaobalong import VIDEO_MODELS, VIDEO_PRICES
        for dead in ("bh2.0-720p", "quanneng2.0", "fd-Seedance 2.0 933", "doubaofast"):
            self.assertNotIn(dead, VIDEO_MODELS)
        for alive in ("sd2.5-720p-301010", "sd2.5-480p-301010", "sd2-福利", "sd2-fast福利"):
            self.assertIn(alive, VIDEO_MODELS)
        self.assertEqual(set(VIDEO_PRICES), set(VIDEO_MODELS))   # 价格表别漏模型

    def test_image_body_uses_count_and_ratio(self):
        p = XiaobalongProvider(api_key="k")
        seen = _stub(p, {"data": [{"url": "https://x/i.png"}, {"url": "https://x/i2.png"}]})
        p.generate_image(ImageTask(prompt="猫", size="16:9", n=2, model="image2"), "out.png")
        body = seen["body"]
        self.assertEqual(body["count"], 2)
        self.assertEqual(body["ratio"], "16:9")
        self.assertNotIn("n", body)                       # count 是推荐字段
        self.assertNotIn("size", body)

    def test_image_pixel_size_is_not_sent_as_ratio(self):
        """size 只有含 ':' 才是比例别名；'1024x1536' 发过去会 400。"""
        p = XiaobalongProvider(api_key="k")
        seen = _stub(p, {"data": [{"url": "https://x/i.png"}]})
        p.generate_image(ImageTask(prompt="猫", size="1024x1536", model="image2"), "out.png")
        self.assertNotIn("ratio", seen["body"])

    def test_resolution_whitelist_enforced(self):
        p = XiaobalongProvider(api_key="k")
        seen = _stub(p, {"data": [{"url": "https://x/i.png"}]})
        p.generate_image(ImageTask(prompt="猫", model="image2-4k"), "out.png")
        self.assertEqual(seen["body"]["resolution"], "4K")     # 该模型只认 4K
        seen = _stub(p, {"data": [{"url": "https://x/i.png"}]})
        p.generate_image(ImageTask(prompt="猫", model="image2"), "out.png")
        self.assertNotIn("resolution", seen["body"])          # 没白名单就不发

    def test_empty_data_is_failure_even_on_200(self):
        """HTTP 200 + data:[] 是失败（不结算），但也不许自动重提。"""
        p = XiaobalongProvider(api_key="k")
        _stub(p, {"created": 1, "data": []})
        with self.assertRaises(ApiError) as raised:
            p.generate_image(ImageTask(prompt="猫", model="image2"), "out.png")
        self.assertIn("不要自动重提", str(raised.exception))

    def test_rejects_local_refs_before_paying(self):
        p = XiaobalongProvider(api_key="k")
        with self.assertRaises(ApiError) as raised:
            p.generate_video(VideoTask(prompt="x", refs=["data:image/png;base64,abc"],
                                       model="bh2.0-720p"), "out.mp4")
        self.assertEqual(raised.exception.kind, TASK_FATAL)

    def test_upload_limits_match_doc(self):
        self.assertEqual(UPLOAD_LIMITS[".png"], 10)
        self.assertEqual(UPLOAD_LIMITS[".mp3"], 50)
        self.assertEqual(UPLOAD_LIMITS[".mp4"], 60)
        with self.assertRaises(ApiError):
            XiaobalongProvider(api_key="k").upload_asset("不存在的文件.mp4")


if __name__ == "__main__":
    unittest.main()
