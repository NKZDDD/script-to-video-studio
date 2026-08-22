# -*- coding: utf-8 -*-
"""小裴统一视频接口（2026-08-19）的请求形状。

所有模型同一套字段：单首帧走顶层 input_reference，首尾帧/多参考/参考视频
音频走 extra.reference_*，画幅走 extra.aspect_ratio。发错**多半不报错**，
只是参考图被静默忽略 —— 所以只能靠测试锁住。

按模型族保留的知识（时长上限、参考上限、模式限制）也一并锁住。
"""
import unittest

from core.apiutil import ApiError
from core.providers.base import VideoTask
from core.providers.aicopy import AicopyProvider, branch_of

P = AicopyProvider()
U = ["https://cdn/1.jpg", "https://cdn/2.jpg", "https://cdn/3.jpg"]
V = ["https://cdn/motion.mp4"]
A = ["https://cdn/voice.mp3"]


def body(**kw):
    task = VideoTask(**kw)
    return P.build_video_body(task)


class UnifiedShapeTests(unittest.TestCase):
    """★ 统一接口：一套字段走所有模型，选模型不再选协议。"""

    def test_every_model_uses_the_same_two_paths(self):
        for m in ("sd2.0-720满血-不卡脸（按秒）", "开源h3-720p", "grok-imagine-1.0-video",
                  "火山官方2.5-480p", "veo视频生成", "sd-720满血-933（按秒）",
                  "sd-720fast-ad渠道16x9", "omni-fast-视频生成（无水印）"):
            path, _, qpath = body(prompt="x", model=m, refs=[U[0]])
            self.assertEqual((path, qpath), ("/v1/videos", "/v1/videos/{id}"), m)

    def test_single_ref_goes_to_top_level_input_reference(self):
        _, b, _ = body(prompt="x", model="sd2.0-720满血-不卡脸（按秒）", refs=[U[0]])
        self.assertEqual(b["input_reference"], {"url": U[0]})     # 顶层、对象、url 键
        self.assertNotIn("reference_images", b.get("extra", {}))

    def test_first_and_last_frame_use_roles(self):
        _, b, _ = body(prompt="x", model="开源h3-720p", refs=U[:2],
                       extra={"first_last": True})
        self.assertEqual(b["extra"]["reference_images"],
                         [{"url": U[0], "role": "first_frame"},
                          {"url": U[1], "role": "last_frame"}])

    def test_multi_refs_use_reference_image_role(self):
        _, b, _ = body(prompt="x", model="sd-2.5-720p不卡脸(按秒)", refs=U)
        self.assertEqual(b["extra"]["reference_images"],
                         [{"url": u, "role": "reference_image"} for u in U])

    def test_two_refs_without_the_flag_are_just_multi_refs(self):
        """没打首尾帧标记的 2 张图走多参考，不许被当成首尾帧。"""
        _, b, _ = body(prompt="x", model="sd2.0-720满血-不卡脸（按秒）", refs=U[:2])
        self.assertEqual([r["role"] for r in b["extra"]["reference_images"]],
                         ["reference_image", "reference_image"])

    def test_videos_and_audios_go_to_extra(self):
        _, b, _ = body(prompt="x", model="sd2.0-720满血-不卡脸（按秒）", refs=U,
                       extra={"videos": V, "audios": A})
        self.assertEqual(b["extra"]["reference_videos"], [{"url": V[0]}])
        self.assertEqual(b["extra"]["reference_audios"], [{"url": A[0]}])

    def test_ratio_goes_to_extra_aspect_ratio(self):
        _, b, _ = body(prompt="x", model="sd2.0-720满血-不卡脸（按秒）",
                       ratio="16:9")
        self.assertEqual(b["extra"]["aspect_ratio"], "16:9")
        self.assertNotIn("aspect_ratio", b)                      # 不在顶层

    def test_ad_models_lock_ratio_in_their_name(self):
        """ad 渠道画幅锁在模型名（…16x9/…9x16），再传 aspect_ratio 是递矛盾。"""
        _, b, _ = body(prompt="x", model="sd2.0-480fast-ad渠道16x9", refs=U)
        self.assertNotIn("aspect_ratio", b.get("extra", {}))

    def test_text_to_video_has_no_reference_fields(self):
        _, b, _ = body(prompt="x", model="sd2.0-720满血-不卡脸（按秒）")
        self.assertNotIn("input_reference", b)
        self.assertNotIn("reference_images", b.get("extra", {}))


class FamilyKnowledgeTests(unittest.TestCase):
    """网关换协议也带不走的上游限制 —— 时长夹取、参考上限、模式限制。"""

    def test_branch_lookup(self):
        self.assertEqual(branch_of("grok-imagine-1.0-video"), "grok10")
        self.assertEqual(branch_of("开源h3-720p"), "h3")
        self.assertEqual(branch_of("火山官方2.5-480p"), "volc")
        self.assertEqual(branch_of("sd-720满血-900（不售后）"), "sd900")
        # 认不出来的退到最通用的族（时长按 4-15 夹），而不是崩掉
        self.assertEqual(branch_of("某个没见过的新模型"), "sd2full")

    def test_durations_are_clamped_per_family(self):
        cases = [
            ("grok-imagine-1.0-video", 7, 6),          # 只有 6/10 档，就近吸附
            ("grok-1.5-多参接口", 12, 10),              # 6/10/15 档，就近吸附
            ("veo视频生成", 7, 6),                       # 4/6/8 档（7 与 6/8 平手取小）
            ("omni-fast-视频生成（无水印）", 15, 10),    # 固定 10
            ("sd2.0-480fast-ad渠道16x9", 8, 15),        # 固定 15
            ("sd-720满血-不卡脸（按次）", 8, 15),        # 按次轮换固定 15
            ("开源h3-720p", 4, 5),                       # h3 最低 5
            ("sd-2.5-720p不卡脸(按秒)", 29, 29),        # sd-2.5 到 29
            ("sd-2.5-轮换渠道（按秒）", 25, 25),        # 2.5 轮换按秒到 29
            ("sd-2.5-轮换渠道（按次）", 25, 15),        # 按次封顶 15
            ("火山官方2.5-480p", 20, 20),                # 火山 2.5 到 30
            ("火山官方2.0-480p-mini", 20, 15),           # 火山 2.0 封顶 15
            ("sd2.0-720满血-不卡脸（按秒）", 29, 15),   # sd2.0 封顶 15
        ]
        for model, ask, want in cases:
            # 带一张图：火山官方没有文生模式，不带参考图会先被模式限制拦下
            _, b, _ = body(prompt="x", model=model, duration=ask, refs=[U[0]])
            self.assertEqual(b["seconds"], want, model)

    def test_unknown_models_fall_back_to_4_to_15(self):
        _, b, _ = body(prompt="x", model="某个没见过的新模型", duration=29)
        self.assertEqual(b["seconds"], 15)

    def test_ref_caps_differ_by_family(self):
        many = [f"https://cdn/{i}.jpg" for i in range(35)]
        _, b25, _ = body(prompt="x", model="sd-2.5-720p不卡脸(按秒)", refs=many)
        self.assertEqual(len(b25["extra"]["reference_images"]), 30)   # sd-2.5 吃 30
        _, b2, _ = body(prompt="x", model="sd2.0-720满血-不卡脸（按秒）", refs=many)
        self.assertEqual(len(b2["extra"]["reference_images"]), 9)     # sd2.0 吃 9
        _, bg, _ = body(prompt="x", model="grok-imagine-1.0-video", refs=many)
        self.assertEqual(len(bg["extra"]["reference_images"]), 7)     # grok 吃 7

    def test_volcano_refuses_text_to_video(self):
        with self.assertRaises(ApiError):
            body(prompt="x", model="火山官方2.5-480p", refs=[])

    def test_sd900_needs_refs(self):
        with self.assertRaises(ApiError):
            body(prompt="x", model="sd-720满血-900（不售后）", refs=[])

    def test_happyhorse_surprise_rejects_multi(self):
        with self.assertRaises(ApiError):
            body(prompt="x", model="快乐马1.1（不卡脸）惊喜渠道", refs=U)


class RefFormTests(unittest.TestCase):
    """统一接口的参考素材全要公网链接 —— GROK/Horse 旧例外已取消。"""

    def test_all_video_models_need_public_urls(self):
        for m in ("grok-imagine-1.0-video", "happyhorse-1.1-r2v-720p",
                  "sd2.0-720满血-不卡脸（按秒）", "veo视频生成"):
            self.assertTrue(P.needs_url(m, "video"), m)
            self.assertTrue(P.accepts_url(m, "video"), m)

    def test_image_still_only_takes_bare_base64(self):
        self.assertFalse(P.needs_url("", "image"))
        self.assertFalse(P.accepts_url("", "image"))


if __name__ == "__main__":
    unittest.main()
