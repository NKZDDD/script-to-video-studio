# -*- coding: utf-8 -*-
import unittest

from core.apiutil import ApiError, TASK_FATAL
from core.providers import REGISTRY, resolve_id
from core.providers.base import ImageTask, VideoTask
from core.providers.chaomo import ChaomoProvider, _to_ratio, _to_size


def _stub(provider, reply):
    """拦下 session.request，记下发出去的 body/files，返回假响应。"""
    seen = {}

    def fake(method, path, json_body=None, files=None, retries=3, timeout=None):
        seen.update(method=method, path=path, body=json_body, files=files, retries=retries)
        return reply

    provider.session.request = fake
    provider.session.save_item = lambda item, dest: dest
    return seen


class ChaomoTests(unittest.TestCase):
    def test_registered_and_aliased(self):
        self.assertIn("chaomo", REGISTRY)
        for alias in ("chaomoapi", "超模", "cm"):
            self.assertEqual(resolve_id(alias), "chaomo")

    def test_ref_mode_differs_by_media(self):
        """视频只收链接、图片走 multipart —— 声明反了参考图会被静默丢掉。"""
        p = ChaomoProvider()
        self.assertTrue(p.needs_url("", "video"))
        self.assertFalse(p.needs_url("", "image"))
        self.assertFalse(p.needs_bytes(""))

    def test_size_is_a_resolution_tier_not_pixels(self):
        # 这家视频的 size 是档位；给像素/2K 也要归档，别原样发出去
        self.assertEqual(_to_size("720p"), "720p")
        self.assertEqual(_to_size("1920x1080"), "1080p")
        self.assertEqual(_to_size("2K"), "1080p")
        self.assertEqual(_to_size("3840x2160"), "4k")
        self.assertEqual(_to_size(""), "720p")

    def test_ratio_converts_pixels(self):
        self.assertEqual(_to_ratio("9:16"), "9:16")
        self.assertEqual(_to_ratio("1024x1536"), "2:3")
        self.assertEqual(_to_ratio(""), "9:16")

    def test_video_body_uses_content_blocks(self):
        """参考素材必须是 content 块 —— 发 images[] 不报错但会被忽略。"""
        p = ChaomoProvider(api_key="k")
        seen = _stub(p, {"id": "t1", "status": "completed", "data": [{"url": "https://x/v.mp4"}]})
        p.generate_video(VideoTask(prompt="走路", refs=["https://a/1.jpg"], duration=8,
                                   resolution="720p", model="seedance2"), "out.mp4")
        body = seen["body"]
        self.assertEqual(body["seconds"], "8")          # 字符串，不是 int
        self.assertIsInstance(body["seconds"], str)
        self.assertEqual(body["size"], "720p")          # 档位，不是像素
        self.assertNotIn("images", body)                # 别家的写法，这家会忽略
        self.assertEqual(body["content"], [{
            "type": "image_url", "role": "reference_image",
            "image_url": {"url": "https://a/1.jpg"},
        }])

    def test_video_duration_clamped(self):
        p = ChaomoProvider(api_key="k")
        seen = _stub(p, {"id": "t1", "status": "completed", "data": [{"url": "https://x/v.mp4"}]})
        p.generate_video(VideoTask(prompt="x", duration=30, model="seedance2"), "out.mp4")
        self.assertEqual(seen["body"]["seconds"], "15")

    def test_video_rejects_local_refs_before_paying(self):
        p = ChaomoProvider(api_key="k")
        with self.assertRaises(ApiError) as raised:
            p.generate_video(VideoTask(prompt="x", refs=["data:image/png;base64,abc"],
                                       model="seedance2"), "out.mp4")
        self.assertEqual(raised.exception.kind, TASK_FATAL)

    def test_text_to_image_uses_ratio_not_size(self):
        p = ChaomoProvider(api_key="k")
        seen = _stub(p, {"data": [{"url": "https://x/i.png"}]})
        p.generate_image(ImageTask(prompt="猫", size="9:16", model="gpt-image2-1K"), "out.png")
        body = seen["body"]
        self.assertEqual(body["ratio"], "9:16")
        self.assertNotIn("size", body)
        self.assertNotIn("aspect_ratio", body)
        self.assertEqual(body["n"], 1)
        self.assertTrue(body["async"])
        self.assertEqual(seen["path"], "/v1/images/generations")

    def test_edits_must_go_async_to_avoid_base64_transport(self):
        """图生图不发 async 就走同步 → 几 MB 的图塞进 JSON base64 → 传丢就整张报废。

        文档原文：「异步任务固定返回 URL 结果」。实跑撞过：超模一批资产全是 0KB，
        报错只有一句 Incorrect padding。所以 async 必须在，这条不能回退。
        """
        p = ChaomoProvider(api_key="k")
        seen = _stub(p, {"data": [{"url": "https://x/i.png"}]})
        p.generate_image(ImageTask(prompt="改背景", refs=["data:image/png;base64,iVBORw0KGgo="],
                                   model="gpt-image2-1K"), "out.png")
        fields = {name: payload[1] for name, payload in seen["files"] if payload[0] is None}
        self.assertEqual(fields["async"], "true")
        self.assertEqual(fields["include_metadata"], "true")

    def test_metadata_byte_count_catches_truncation(self):
        """网关自报 2MB、实际只收到几十字节 → 必须报错，不能把残图当成功。"""
        meta = {"width": 941, "height": 1672, "format": "png", "bytes": 2_000_000}
        short = "data:image/png;base64," + "iVBORw0KGgoAAAANSUhEUg=="
        with self.assertRaises(ApiError) as raised:
            ChaomoProvider.check_meta(meta, [short], log=lambda *a: None)
        self.assertEqual(raised.exception.kind, TASK_FATAL)
        self.assertIn("传输途中丢了", str(raised.exception))
        # 尺寸对得上就放行
        ChaomoProvider.check_meta({"bytes": 10}, [short], log=lambda *a: None)
        # URL 结果不在这儿查（由 save_item 的大小检查兜底），别误伤
        ChaomoProvider.check_meta(meta, ["https://x/i.png"], log=lambda *a: None)

    def test_image_with_refs_goes_multipart_image_bracket(self):
        """有参考图必须走 /v1/images/edits，字段名是 image[]（不是 image / images）。"""
        p = ChaomoProvider(api_key="k")
        seen = _stub(p, {"data": [{"url": "https://x/i.png"}]})
        tiny = "data:image/png;base64,iVBORw0KGgo="
        p.generate_image(ImageTask(prompt="改背景", refs=[tiny], size="1:1",
                                   model="gpt-image2-1K"), "out.png")
        self.assertEqual(seen["path"], "/v1/images/edits")
        self.assertIsNone(seen["body"])
        names = [f[0] for f in seen["files"]]
        self.assertIn("image[]", names)
        self.assertNotIn("image", names)
        self.assertNotIn("images", names)


    # -- 缩略图核验 ----------------------------------------------------
    def _png(self, w, h, pad=0):
        """造一个头部合法、宽高可控的 PNG（只需要文件头能被读出来）。"""
        import struct
        sig = bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A])
        ihdr = struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", w, h)
        return sig + ihdr + b"\x08\x02\x00\x00\x00" + b"\x00" * pad

    def test_header_size_reads_png_dimensions(self):
        import os
        import tempfile

        from core.providers.chaomo import _header_size
        p = os.path.join(tempfile.mkdtemp(), "a.png")
        with open(p, "wb") as f:
            f.write(self._png(3840, 2160))
        self.assertEqual(_header_size(p), (3840, 2160))
        self.assertEqual(_header_size(p + ".missing"), (0, 0))

    def test_thumbnail_is_rejected(self):
        """**缩略图是一张完整合法的小图** —— 结尾标记、体积都正常，
        save_item 那两道检查全放行。只有拿 include_metadata 的宽高一比才露馅。
        """
        import os
        import tempfile

        p = os.path.join(tempfile.mkdtemp(), "thumb.png")
        with open(p, "wb") as f:
            f.write(self._png(320, 180, pad=2000))          # 网关说 3840x2160，实际只有 320x180
        meta = {"width": 3840, "height": 2160, "format": "png", "bytes": 5_000_000}
        with self.assertRaises(ApiError) as raised:
            ChaomoProvider.check_meta(meta, [], dest=p, log=lambda *a: None)
        self.assertEqual(raised.exception.kind, TASK_FATAL)
        self.assertIn("缩略图", str(raised.exception))
        self.assertFalse(os.path.exists(p))                 # 必须删掉，否则下次被当成"已做过"跳过

    def test_full_size_passes(self):
        import os
        import tempfile

        p = os.path.join(tempfile.mkdtemp(), "full.png")
        with open(p, "wb") as f:
            f.write(self._png(3840, 2160, pad=5_000_000))
        meta = {"width": 3840, "height": 2160, "bytes": 5_000_000}
        ChaomoProvider.check_meta(meta, [], dest=p, log=lambda *a: None)   # 不该抛
        self.assertTrue(os.path.exists(p))

    def test_size_far_below_reported_is_rejected(self):
        """宽高读不出来（比如 webp）时，还有字节数这道防线。"""
        import os
        import tempfile

        p = os.path.join(tempfile.mkdtemp(), "x.webp")
        with open(p, "wb") as f:
            f.write(b"RIFF" + b"\x00" * 3000)
        with self.assertRaises(ApiError):
            ChaomoProvider.check_meta({"bytes": 5_000_000}, [], dest=p, log=lambda *a: None)


if __name__ == "__main__":
    unittest.main()
