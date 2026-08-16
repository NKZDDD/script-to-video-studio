# -*- coding: utf-8 -*-
"""小裴 3.3.53 分支形状。

每个断言都对着「视频插件接口文档-3.3.53」的示例写 —— 这些字段发错**多半不报错**，
只是参考图被静默忽略，所以只能靠测试锁住。
"""
import unittest

from core.apiutil import ApiError
from core.providers.base import VideoTask
from core.providers.aicopy import AicopyProvider, branch_of

P = AicopyProvider()
U = ["https://cdn/1.jpg", "https://cdn/2.jpg"]
D = ["data:image/jpeg;base64,AAA", "data:image/jpeg;base64,BBB"]


def body(**kw):
    task = VideoTask(**kw)
    return P.build_video_body(task)


class BranchTests(unittest.TestCase):
    def test_branch_lookup(self):
        self.assertEqual(branch_of("grok-imagine-1.0-video"), "grok10")
        self.assertEqual(branch_of("grok-1.5-多参接口"), "grok15")
        self.assertEqual(branch_of("开源h3-720p"), "h3")
        self.assertEqual(branch_of("火山官方2.5-480p"), "volc")
        self.assertEqual(branch_of("sd-720满血-900（不售后）"), "sd900")
        # 认不出来的退到最通用的 metadata 形状，而不是崩掉
        self.assertEqual(branch_of("某个没见过的新模型"), "sd2full")

    def test_grok10_sends_duration_three_times(self):
        _, b, _ = body(prompt="x", model="grok-imagine-1.0-video", duration=6, refs=D)
        self.assertEqual(b["duration"], 6)
        self.assertEqual(b["video_length"], 6)
        self.assertEqual(b["video_config"]["video_length"], 6)
        self.assertEqual(b["reference_images"], D)          # 字符串数组

    def test_grok15_object_array_and_string_seconds(self):
        _, b, _ = body(prompt="x", model="grok-1.5-多参接口", duration=10, ratio="16:9", refs=D)
        self.assertEqual(b["seconds"], "10")
        self.assertIsInstance(b["seconds"], str)
        self.assertEqual(b["size"], "1280x720")
        self.assertEqual(b["reference_images"], [{"url": D[0]}, {"url": D[1]}])

    def test_horse_first_frame_omits_ratio(self):
        """首帧模式传 parameters.ratio 会让画幅不跟首帧（文档 #3）。"""
        _, b, _ = body(prompt="x", model="happyhorse-1.1-i2v-720p", refs=[D[0]])
        self.assertNotIn("ratio", b["parameters"])
        self.assertEqual(b["image_url"], D[0])
        _, b2, _ = body(prompt="x", model="happyhorse-1.1-r2v-720p", refs=D)
        self.assertIn("ratio", b2["parameters"])            # 多参考才有

    def test_h3_uses_generations_path_and_roles(self):
        path, b, qpath = body(prompt="x", model="开源h3-720p", refs=U,
                              extra={"first_last": True})
        self.assertEqual(path, "/v1/video/generations")     # 不是 /v1/videos/generations
        self.assertEqual(qpath, "/v1/video/generations/{id}")
        self.assertEqual(b["fps"], 24)
        self.assertEqual([r["role"] for r in b["reference_images"]],
                         ["first_frame", "last_frame"])

    def test_volcano_never_sends_ratio_on_frames(self):
        """首帧/首尾帧带 ratio 火山会回 InvalidParameter.TaskTypeConstraint。"""
        _, b, _ = body(prompt="x", model="火山官方2.5-480p", refs=U, extra={"first_last": True})
        self.assertNotIn("ratio", b)
        self.assertEqual([c.get("role") for c in b["content"][1:]],
                         ["first_frame", "last_frame"])
        _, b2, _ = body(prompt="x", model="火山官方2.5-720p",
                        refs=U + ["https://cdn/3.jpg"])
        self.assertEqual(b2["ratio"], "9:16")               # 多参考才发

    def test_volcano_refuses_text_to_video(self):
        with self.assertRaises(ApiError):
            body(prompt="x", model="火山官方2.5-480p", refs=[])

    def test_sd25_allows_29s_and_no_resolution(self):
        _, b, _ = body(prompt="x", model="sd-2.5-720p不卡脸(按秒)", duration=29, refs=U)
        self.assertEqual(b["duration"], 29)
        self.assertNotIn("resolution", b)
        self.assertEqual(b["images"], U)

    def test_sd2full_metadata_shape(self):
        _, b, _ = body(prompt="x", model="sd2.0-720满血-不卡脸（按秒）", refs=U,
                       extra={"first_last": True})
        self.assertEqual(b["metadata"]["enableSound"], "on")   # 字符串不是布尔
        self.assertIsInstance(b["metadata"]["enableSound"], str)
        self.assertEqual(b["metadata"]["modeType"], "frames2video")

    def test_ad_nested_input_media(self):
        _, b, _ = body(prompt="x", model="sd2.0-720满血-ad渠道16x9", refs=U)
        self.assertEqual(b["seconds"], "15")
        self.assertEqual(b["size"], "1280x720")
        self.assertEqual([m["type"] for m in b["input"]["media"]],
                         ["reference_image", "reference_image"])

    def test_dual_endpoint_switches_path(self):
        p1, b1, _ = body(prompt="x", model="sd-720满血-933（按秒）", refs=[U[0]])
        self.assertEqual(p1, "/v1/videos")
        self.assertEqual(b1["input_reference"], {"image_url": U[0]})   # 对象不是字符串
        p2, b2, _ = body(prompt="x", model="sd-720满血-933（按秒）", refs=U)
        self.assertEqual(p2, "/v1/video/generations")
        self.assertEqual(b2["image_references"], U)

    def test_kling_omni_uses_images_key_and_n(self):
        """文档 #14 末尾特意点名：这支多图字段是 images，不是 image_references。"""
        _, b, _ = body(prompt="x", model="可灵-3.0-omni（不卡脸）惊喜渠道", refs=U)
        self.assertEqual(b["images"], U)
        self.assertNotIn("image_references", b)
        self.assertEqual(b["n"], 1)

    def test_happyhorse_surprise_rejects_multi(self):
        with self.assertRaises(ApiError):
            body(prompt="x", model="快乐马1.1（不卡脸）惊喜渠道", refs=U)

    def test_sd900_string_duration_and_object_refs(self):
        _, b, _ = body(prompt="x", model="sd-720满血-900（不售后）", refs=U)
        self.assertEqual(b["duration"], "15")
        self.assertIsInstance(b["duration"], str)
        self.assertEqual(b["reference_images"], [{"url": U[0]}, {"url": U[1]}])

    def test_rotate_25_allows_29s(self):
        _, b, _ = body(prompt="x", model="sd-2.5-轮换渠道（按秒）", duration=25, refs=U,
                       extra={"first_last": True})
        self.assertEqual(b["seconds"], "25")
        self.assertEqual(b["first_frame_url"], U[0])
        self.assertEqual(b["last_frame_url"], U[1])
        _, b2, _ = body(prompt="x", model="sd-720满血-不卡脸（按次）", duration=8)
        self.assertEqual(b2["seconds"], "15")              # 按次固定 15

    def test_ref_form_differs_by_branch(self):
        """GROK/Horse 吃 Data URL，其余要公网 URL —— 声明反了参考图会被悄悄丢掉。"""
        self.assertFalse(P.needs_url("grok-imagine-1.0-video", "video"))
        self.assertTrue(P.accepts_url("grok-imagine-1.0-video", "video") is False)
        self.assertTrue(P.needs_url("sd-2.5-720p不卡脸(按秒)", "video"))
        self.assertTrue(P.accepts_url("sd-2.5-720p不卡脸(按秒)", "video"))

    def test_veo_snaps_duration(self):
        _, b, _ = body(prompt="x", model="veo视频生成", duration=7)
        self.assertIn(b["duration"], (6, 8))
        self.assertTrue(b["generate_audio"])


if __name__ == "__main__":
    unittest.main()
