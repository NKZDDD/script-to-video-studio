# -*- coding: utf-8 -*-
"""好漫剧：七个分支四种协议，选模型即选协议。

这些断言锁的都是**发错不报错、只是结果不对**的地方。
最要命的一条：GROK 和 minimax_h3 同一个端点 `/v1/videos`，
一个 multipart、一个 JSON —— 发错格式参考图会整个丢掉。
"""
import unittest

from core.apiutil import ApiError, TASK_FATAL
from core.providers import REGISTRY, resolve_id
from core.providers.base import ImageTask, VideoTask
from core.providers.haomanju import (H3_MAX_IMAGES, HaomanjuProvider,
                                     branch_of)

URLS = ["https://cdn/a.jpg", "https://cdn/b.jpg"]
DATA = "data:image/png;base64,iVBORw0KGgo="


def _stub(p, reply):
    seen = {}

    def fake(method, path, json_body=None, files=None, retries=3, timeout=None):
        seen.update(method=method, path=path, body=json_body, files=files)
        return reply

    p.session.request = fake
    p.session.save_item = lambda i, d: d
    p.session.poll = lambda *a, **k: "https://cdn/v.mp4"
    return seen


class BranchTests(unittest.TestCase):
    def test_registered_and_aliased(self):
        self.assertIn("haomanju", REGISTRY)
        for a in ("75api", "好漫剧", "hmj", "村长"):
            self.assertEqual(resolve_id(a), "haomanju")

    def test_branch_lookup(self):
        self.assertEqual(branch_of("grok-video-10s"), "grok")
        self.assertEqual(branch_of("minimax_h3"), "h3")
        self.assertEqual(branch_of("sd-2.5-c1"), "h3")
        self.assertEqual(branch_of("omni-flash-4s"), "omni")
        self.assertEqual(branch_of("sora2-12s-16x9"), "chat_video")
        self.assertEqual(branch_of("firefly-veo31-4s-16x9-1080p"), "chat_video")
        self.assertEqual(branch_of("firefly-nano-banana-pro-2k-16x9"), "banana")
        self.assertEqual(branch_of("gpt-image-2"), "image")
        # 认不出的走最通用的一套，而不是崩掉
        self.assertEqual(branch_of("以后新加的模型"), "h3")

    def test_ref_form_differs_by_branch(self):
        """★ 声明错了不报错，只会让参考图被悄悄丢掉。"""
        p = HaomanjuProvider()
        self.assertTrue(p.needs_bytes("grok-video-10s"))      # multipart 文件
        self.assertFalse(p.accepts_url("grok-video-10s"))
        self.assertTrue(p.needs_url("minimax_h3", "video"))   # 只收 http 链接
        self.assertTrue(p.needs_url("omni-flash-4s", "video"))
        self.assertFalse(p.needs_bytes("minimax_h3"))


class GrokTests(unittest.TestCase):
    """文档一：multipart + input_reference[] 文件上传。"""

    def test_uses_multipart_not_json(self):
        p = HaomanjuProvider(api_key="k")
        seen = _stub(p, {"task_id": "t1", "status": "queued"})
        p.generate_video(VideoTask(prompt="@图1 抱着 @图2", refs=[DATA, DATA],
                                   duration=10, model="grok-video-10s"), "o.mp4")
        self.assertEqual(seen["path"], "/v1/videos")
        self.assertIsNone(seen["body"], "GROK 必须走 multipart，不能发 JSON")
        names = [n for n, _ in seen["files"]]
        self.assertEqual(names.count("input_reference[]"), 2)
        self.assertIn("resolution_name", names)

    def test_seconds_follows_the_model_name(self):
        """模型名里带秒数，和 seconds 对不上文档说会失败。"""
        p = HaomanjuProvider(api_key="k")
        seen = _stub(p, {"task_id": "t1"})
        p.generate_video(VideoTask(prompt="x", refs=[DATA], duration=6,
                                   model="grok-video-10s"), "o.mp4")
        fields = {n: v[1] for n, v in seen["files"] if v[0] is None}
        self.assertEqual(fields["seconds"], "10")

    def test_link_refs_rejected_because_numbering_would_shift(self):
        """★ prompt 里 @图1/@图2 是按顺序对应的，丢一张后面全错位。"""
        p = HaomanjuProvider(api_key="k")
        _stub(p, {})
        with self.assertRaises(ApiError) as e:
            p.generate_video(VideoTask(prompt="@图1", refs=URLS,
                                       model="grok-video-10s"), "o.mp4")
        self.assertEqual(e.exception.kind, TASK_FATAL)
        self.assertIn("错位", str(e.exception))


class H3Tests(unittest.TestCase):
    """文档七：同端点但是 JSON。"""

    def test_uses_json_on_the_same_endpoint(self):
        p = HaomanjuProvider(api_key="k")
        seen = _stub(p, {"task_id": "t1", "status": "queued"})
        p.generate_video(VideoTask(prompt="走路", refs=URLS, duration=10,
                                   ratio="9:16", resolution="768p",
                                   model="minimax_h3",
                                   extra={"audios": ["https://cdn/m.mp3"]}), "o.mp4")
        self.assertEqual(seen["path"], "/v1/videos")          # 和 GROK 同一个端点
        self.assertIsNone(seen["files"], "h3 必须走 JSON，不能发 multipart")
        b = seen["body"]
        self.assertEqual(b["seconds"], "10")                  # 字符串，不是 int
        self.assertIsInstance(b["seconds"], str)
        self.assertEqual(b["images"], URLS)
        self.assertEqual(b["audios"], ["https://cdn/m.mp3"])
        self.assertEqual(b["resolution"], "768p")
        self.assertEqual(b["aspect_ratio"], "9:16")

    def test_seconds_clamped_to_five_fifteen(self):
        p = HaomanjuProvider(api_key="k")
        seen = _stub(p, {"task_id": "t1"})
        p.generate_video(VideoTask(prompt="x", refs=URLS, duration=30,
                                   model="minimax_h3"), "o.mp4")
        self.assertEqual(seen["body"]["seconds"], "15")

    def test_images_capped_at_eight(self):
        p = HaomanjuProvider(api_key="k")
        seen = _stub(p, {"task_id": "t1"})
        p.generate_video(VideoTask(prompt="x", model="minimax_h3",
                                   refs=[f"https://cdn/{i}.jpg" for i in range(12)]),
                         "o.mp4")
        self.assertEqual(len(seen["body"]["images"]), H3_MAX_IMAGES)

    def test_local_refs_rejected_before_paying(self):
        p = HaomanjuProvider(api_key="k")
        _stub(p, {})
        with self.assertRaises(ApiError) as e:
            p.generate_video(VideoTask(prompt="x", refs=[DATA],
                                       model="minimax_h3"), "o.mp4")
        self.assertEqual(e.exception.kind, TASK_FATAL)

    def test_sd_models_go_through_the_same_shape(self):
        """sd-* 是 pricing 有、文档没写的，按 h3 的形状发。"""
        p = HaomanjuProvider(api_key="k")
        seen = _stub(p, {"task_id": "t1"})
        p.generate_video(VideoTask(prompt="x", refs=URLS, model="sd-2.5-c1"), "o.mp4")
        self.assertEqual(seen["path"], "/v1/videos")
        self.assertIsNone(seen["files"])
        self.assertEqual(seen["body"]["model"], "sd-2.5-c1")


class ChatVideoTests(unittest.TestCase):
    """文档四、五：chat/completions 同步返回。"""

    REPLY = {"choices": [{"message": {"content":
             "```html\n<video src='https://media/x.mp4' controls></video>\n```"}}]}

    def test_sora_text_to_video_uses_plain_string_content(self):
        p = HaomanjuProvider(api_key="k")
        seen = _stub(p, self.REPLY)
        p.generate_video(VideoTask(prompt="无人机飞过雪山", model="sora2-12s-16x9"), "o.mp4")
        self.assertEqual(seen["path"], "/v1/chat/completions")
        self.assertIsInstance(seen["body"]["messages"][0]["content"], str)

    def test_with_refs_uses_parts_array(self):
        p = HaomanjuProvider(api_key="k")
        seen = _stub(p, self.REPLY)
        p.generate_video(VideoTask(prompt="回头", refs=[URLS[0]],
                                   model="sora2-12s-9x16"), "o.mp4")
        parts = seen["body"]["messages"][0]["content"]
        self.assertEqual(parts[0]["type"], "text")
        self.assertEqual(parts[1]["image_url"]["url"], URLS[0])

    def test_veo_frame_mode_caps_at_two_images(self):
        """★ firefly-veo31-* 是帧模式（1=首帧、2=首尾帧）；多图要换 -ref- 模型。"""
        p = HaomanjuProvider(api_key="k")
        _stub(p, self.REPLY)
        with self.assertRaises(ApiError) as e:
            p.generate_video(VideoTask(prompt="x", refs=URLS + ["https://cdn/c.jpg"],
                                       model="firefly-veo31-4s-16x9-1080p"), "o.mp4")
        self.assertEqual(e.exception.kind, TASK_FATAL)
        self.assertIn("firefly-veo31-ref", str(e.exception))

    def test_veo_ref_mode_allows_three(self):
        p = HaomanjuProvider(api_key="k")
        seen = _stub(p, self.REPLY)
        p.generate_video(VideoTask(prompt="x", refs=URLS + ["https://cdn/c.jpg"],
                                   model="firefly-veo31-ref-8s-16x9-1080p"), "o.mp4")
        self.assertEqual(len(seen["body"]["messages"][0]["content"]), 4)   # text + 3 图

    def test_video_url_is_pulled_out_of_the_html_tag(self):
        p = HaomanjuProvider(api_key="k")
        _stub(p, self.REPLY)
        r = p.generate_video(VideoTask(prompt="x", model="sora2-12s-16x9"), "o.mp4")
        self.assertEqual(r["source"], "https://media/x.mp4")


class OmniTests(unittest.TestCase):
    def test_uses_its_own_endpoint(self):
        p = HaomanjuProvider(api_key="k")
        seen = _stub(p, {"task_id": "t1", "status": "pending"})
        p._poll_generations = lambda *a, **k: "https://cdn/v.mp4"
        p.generate_video(VideoTask(prompt="日落", refs=[URLS[0]], ratio="16:9",
                                   model="omni-flash-4s"), "o.mp4")
        self.assertEqual(seen["path"], "/v1/video/generations")
        self.assertEqual(seen["body"]["aspect_ratio"], "landscape")   # 不是 16:9
        self.assertEqual(seen["body"]["image"], URLS[0])              # 单数 image


class ImageTests(unittest.TestCase):
    def test_text_to_image_json(self):
        p = HaomanjuProvider(api_key="k")
        seen = _stub(p, {"data": [{"b64_json": "aGk="}]})
        p.generate_image(ImageTask(prompt="猫", size="1536x1152",
                                   model="gpt-image-2"), "o.png")
        self.assertEqual(seen["path"], "/v1/images/generations")
        self.assertEqual(seen["body"]["size"], "1536x1152")   # 这家独有的尺寸
        self.assertEqual(seen["body"]["response_format"], "b64_json")

    def test_edits_use_multipart_image_bracket(self):
        p = HaomanjuProvider(api_key="k")
        seen = _stub(p, {"data": [{"b64_json": "aGk="}]})
        p.generate_image(ImageTask(prompt="融合", refs=[DATA, DATA],
                                   model="gpt-image-2"), "o.png")
        self.assertEqual(seen["path"], "/v1/images/edits")
        names = [n for n, _ in seen["files"]]
        self.assertEqual(names.count("image[]"), 2)

    def test_banana_goes_through_chat(self):
        """香蕉的档位写在模型名里，走的是 chat/completions。"""
        p = HaomanjuProvider(api_key="k")
        seen = _stub(p, {"choices": [{"message": {
            "content": "![Generated Image](http://x/a.png)"}}]})
        p.generate_image(ImageTask(prompt="山", model="firefly-nano-banana-pro-2k-16x9"),
                         "o.png")
        self.assertEqual(seen["path"], "/v1/chat/completions")
        self.assertNotIn("size", seen["body"])       # Pro 不需要 size

    def test_only_banana2_takes_the_size_field(self):
        p = HaomanjuProvider(api_key="k")
        seen = _stub(p, {"choices": [{"message": {
            "content": "![img](http://x/a.png)"}}]})
        p.generate_image(ImageTask(prompt="山", model="nano-banana2",
                                   extra={"size": "16x9-2k"}), "o.png")
        self.assertEqual(seen["body"]["size"], "16x9-2k")
        # Pro 传了也不发 —— 它的档位在模型名里
        seen2 = _stub(p, {"choices": [{"message": {
            "content": "![img](http://x/a.png)"}}]})
        p.generate_image(ImageTask(prompt="山", model="firefly-nano-banana-pro-4k-1x1",
                                   extra={"size": "16x9-2k"}), "o.png")
        self.assertNotIn("size", seen2["body"])


if __name__ == "__main__":
    unittest.main()
