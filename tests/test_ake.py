# -*- coding: utf-8 -*-
"""阿珂（snumom.com）两条模型线的请求形状。

同一个网关、同一套 /v1/videos，但 Grok 线和 minimax_h3 线的参考字段
规矩**几乎相反**（Grok 靠 input_reference 传本地图，H3 线明令禁用该字段、
只收 reference_* 三个字段）—— 所以按模型分支各测各的，别糊成一套。
"""
import unittest

from core.apiutil import ApiError
from core.providers import REGISTRY, resolve_id
from core.providers.base import VideoTask
from core.providers.ake import MAX_REFS, SIZE_TABLE, AkeProvider, _size_of

P = AkeProvider()
U = [f"https://cdn/{i}.jpg" for i in range(15)]
V = ["https://cdn/m1.mp4", "https://cdn/m2.mp4", "https://cdn/m3.mp4",
     "https://cdn/m4.mp4"]
A = ["https://cdn/a1.mp3", "https://cdn/a2.mp3"]
LOCAL = ["data:image/png;base64,xxx"]


def body(**kw):
    return P.build_video_body(VideoTask(**kw), log=lambda *a: None)


class AkeTests(unittest.TestCase):
    def test_registered_and_aliased(self):
        self.assertIn("ake", REGISTRY)
        for alias in ("snumom", "阿珂", "ako"):
            self.assertEqual(resolve_id(alias), "ake")

    def test_video_only(self):
        cap = AkeProvider().capabilities()
        self.assertEqual(tuple(cap["supports"]), ("video",))
        self.assertIn("minimax_h3-768p", cap["video"]["models"])
        self.assertIn("minimax_h3-1080p", cap["video"]["models"])
        # 上限是两条线的最大值（H3 线 9；Grok 线 7 由 body 层裁）
        self.assertEqual(cap["video"]["max_refs"], 9)


class GrokLineTests(unittest.TestCase):
    """Grok Imagine 线 —— 原有规矩原样锁住。"""

    def test_size_merges_resolution_and_ratio(self):
        """这条线没有 aspect_ratio 字段，画面全靠 size —— 四种组合必须对上文档。"""
        self.assertEqual(_size_of("720p", "16:9"), "1280x720")
        self.assertEqual(_size_of("720p", "9:16"), "720x1280")
        self.assertEqual(_size_of("480p", "16:9"), "854x480")
        self.assertEqual(_size_of("480p", "9:16"), "480x854")
        self.assertEqual(len(SIZE_TABLE), 4)

    def test_size_falls_back_for_unsupported_inputs(self):
        # 只有横竖两种：其它比例按长宽归边，不认识的分辨率退回 720p
        self.assertEqual(_size_of("720p", "21:9"), "1280x720")
        self.assertEqual(_size_of("720p", "3:4"), "720x1280")
        self.assertEqual(_size_of("1080p", "9:16"), "720x1280")
        self.assertEqual(_size_of("", ""), "720x1280")

    def test_seconds_is_a_string(self):
        b = body(prompt="x", model="grok-imagine-video-1.5-preview", duration=8)
        self.assertEqual(b["seconds"], "8")           # 字符串，不是整数

    def test_all_url_refs_go_to_reference_images(self):
        b = body(prompt="x", model="grok-imagine-video-1.5-preview", refs=U[:3])
        self.assertEqual(b["reference_images"], [{"url": u} for u in U[:3]])

    def test_local_refs_go_to_input_reference(self):
        b = body(prompt="x", model="grok-imagine-video-1.5-preview",
                 refs=U[:1] + LOCAL)
        self.assertEqual(b["input_reference"], U[:1] + LOCAL)   # 整批字符串数组

    def test_refs_capped_at_seven(self):
        b = body(prompt="x", model="grok-imagine-video-1.5-preview", refs=U)
        self.assertEqual(len(b["reference_images"]), MAX_REFS)


class H3LineTests(unittest.TestCase):
    """minimax_h3 线 —— 三条硬规矩，踩了任何一条都是当场 400 或整个任务被拒：

      · size 从「模型×画幅」对照表取，768P / 1080P 两列不通用
      · 参考素材只发 reference_* 三个字段（多发报 metadata is too long）
      · 图≤9 视频≤3 音频≤3 合计≤12
    """

    def test_size_comes_from_the_model_ratio_table(self):
        b = body(prompt="x", model="minimax_h3-768p", ratio="16:9")
        self.assertEqual(b["size"], "1376x768")            # 768P 列
        b2 = body(prompt="x", model="minimax_h3-1080p", ratio="16:9")
        self.assertEqual(b2["size"], "1920x1080")          # 1080P 列，不通用
        b3 = body(prompt="x", model="minimax_h3-768p", ratio="9:16")
        self.assertEqual(b3["size"], "768x1376")

    def test_1080p_rejects_21x9_upfront(self):
        """上游 2560x1088 会创建失败 —— 当场说清，别让人白排一轮队。"""
        with self.assertRaises(ApiError) as ctx:
            body(prompt="x", model="minimax_h3-1080p", ratio="21:9")
        self.assertIn("21:9", str(ctx.exception))

    def test_unknown_ratio_is_rejected_with_the_allowed_list(self):
        with self.assertRaises(ApiError):
            body(prompt="x", model="minimax_h3-768p", ratio="5:4")

    def test_only_the_three_reference_fields_are_used(self):
        """★ 一个字段都不许多发 —— input_reference / images / extra 都不许在。"""
        b = body(prompt="x", model="minimax_h3-768p", refs=U[:3])
        self.assertEqual(b["reference_images"], [{"url": u} for u in U[:3]])  # 无 role
        for banned in ("images", "image", "input_reference", "input_references",
                       "image_references", "extra", "aspect_ratio"):
            self.assertNotIn(banned, b, banned)

    def test_first_frame_is_just_an_ordinary_reference(self):
        """这条线没有首尾帧模式 —— 打了标记也不许拼出 first_frame 角色。"""
        b = body(prompt="x", model="minimax_h3-768p", refs=U[:2],
                 extra={"first_last": True})
        self.assertEqual(b["reference_images"], [{"url": U[0]}, {"url": U[1]}])

    def test_videos_and_audios_are_bare_string_arrays(self):
        """★ 文档原样：reference_videos / audios 是裸字符串数组，不是对象数组。"""
        b = body(prompt="x", model="minimax_h3-768p", refs=U[:2],
                 extra={"videos": V[:2], "audios": A})
        self.assertEqual(b["reference_videos"], V[:2])
        self.assertEqual(b["reference_audios"], A)

    def test_caps_are_9_3_3_and_total_12(self):
        b = body(prompt="x", model="minimax_h3-768p", refs=U,
                 extra={"videos": V, "audios": A})
        self.assertEqual(len(b["reference_images"]), 9)
        self.assertEqual(len(b["reference_videos"]), 3)
        self.assertEqual(b.get("reference_audios"), None)   # 合计≤12，音频先舍光

    def test_local_refs_are_dropped_not_sent(self):
        """★ H3 线只收公网链接 —— 本地图混进来只能舍掉，别让整个任务被拒。"""
        b = body(prompt="x", model="minimax_h3-768p", refs=U[:1] + LOCAL)
        self.assertEqual(b["reference_images"], [{"url": U[0]}])

    def test_seconds_clamped_to_4_15(self):
        self.assertEqual(body(prompt="x", model="minimax_h3-768p",
                              duration=99)["seconds"], 15)
        self.assertEqual(body(prompt="x", model="minimax_h3-768p",
                              duration=0)["seconds"], 5)   # 不传=5 秒但照计费

    def test_h3_models_are_url_only(self):
        """解析器靠这个声明把本地图先上传换成链接 —— 声明错参考图就被丢。"""
        for m in ("minimax_h3-768p", "minimax_h3-1080p"):
            self.assertTrue(P.needs_url(m, "video"), m)
        # Grok 线不声明 URL-only：input_reference 能吃本地图
        self.assertFalse(P.needs_url("grok-imagine-video-1.5-preview", "video"))


if __name__ == "__main__":
    unittest.main()
