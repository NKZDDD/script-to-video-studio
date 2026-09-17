# -*- coding: utf-8 -*-
"""判「做完了没有」看**产物**，不看服务商嘴里那个词。

用户原话（2026-09-17）：「实际上我为什么不能拿产物来判断是否完成呢，
而且轮询不是回调吧应该可以这样」—— 对。轮询每一轮想看什么都可以看，
产物本来就该是判据。

但要拿产物当判据，「是不是产物」这句话得问得准。原来问得太松：
**「响应里随便哪儿有个 URL」**就算有产物。而服务商在任务还没跑完时
把我们传进去的参数原样回显是常态：

    {"status": "processing",
     "inputs": [{"name": "image_url", "image_url": ".../ref_first_frame.png"}]}

于是「有产物」在还没出片时就成立了，后面一路没人拦得住 ——
下载成 `out.mp4`、大小正常、结尾标记那道对 .mp4 不适用，
一张参考图被当成片收下、任务标 ok，到拼接才炸。

所以两道一起收紧，这个文件盯的就是这两道：
  1. 挑产物时：图片样的一律不算成片，挑不出来就返回空（接着等）
  2. 落盘之后：文件头得和扩展名对得上 —— 这是**唯一不依赖任何一方说法**的一道
"""
import os
import shutil
import tempfile
import unittest

from core import apiutil
from core.apiutil import ApiError, extract_video_url

MP4 = bytes(4) + b"ftypisom"
PNG = bytes((0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A))
IEND = b"IEND" + bytes((0xAE, 0x42, 0x60, 0x82))


class PickingTests(unittest.TestCase):
    def test_an_echoed_input_is_not_the_result(self):
        """★ 还在跑的响应里回显了我们传进去的首帧图 —— 那不是成片。"""
        mid = {"id": "t1", "status": "processing", "progress": "30%",
               "inputs": [{"name": "image_url",
                           "image_url": "https://cdn/ref_first_frame.png"}]}
        self.assertEqual(extract_video_url(mid), "",
                         "把回显的参考图当成了成片 —— 它会被下载成 out.mp4")

    def test_the_real_result_still_wins_when_both_are_there(self):
        done = {"status": "completed", "video_url": "https://cdn/out.mp4",
                "inputs": [{"image_url": "https://cdn/ref.png"}]}
        self.assertEqual(extract_video_url(done), "https://cdn/out.mp4")

    def test_a_cover_or_preview_is_not_the_result(self):
        """封面、预览、缩略图都是「跟着成片一起给的图」，不是成片本身。"""
        for key in ("cover", "thumbnail", "preview", "poster", "snapshot"):
            self.assertEqual(extract_video_url({key: "https://cdn/x.jpg"}), "", key)

    def test_a_link_with_no_suffix_still_counts(self):
        """★ 别收得太紧：很多家的结果就是个没后缀的直链或 `/content` 端点。

        收紧到「必须有 .mp4 后缀」的话，这几家会变成永远等不到结果 ——
        那比原来的毛病更糟。
        """
        self.assertEqual(extract_video_url({"download_url": "https://cdn/dl/abc123"}),
                         "https://cdn/dl/abc123")
        self.assertEqual(
            extract_video_url({"url": "https://api/v1/videos/t1/content"}),
            "https://api/v1/videos/t1/content")


class FileKindTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def _write(self, name, body):
        p = os.path.join(self.d, name)
        with open(p, "wb") as f:
            f.write(body)
        return p

    def test_an_image_saved_as_a_video_is_refused(self):
        """★ 这是上面那条漏网之后的最后一道。

        大小正常、下载过程正常、结尾标记那道对 .mp4 不适用 ——
        前面每一道都放行，只有看文件头这一道拦得住。
        """
        p = self._write("SEG01.mp4", PNG + bytes(200000))
        with self.assertRaises(ApiError) as c:
            apiutil._check_saved(p, "https://cdn/ref.png")
        self.assertIn("不是 .mp4", str(c.exception))
        self.assertIn("一张图片", str(c.exception))
        # **必须删掉** —— 留着的话下次 isfile 为真，这一条永远被跳过
        self.assertFalse(os.path.exists(p), "残次品留在盘上，下次会被当成做好了")

    def test_an_error_page_saved_as_a_video_is_refused(self):
        for body, word in ((b"<html><body>502</body></html>", "HTML"),
                           (b'{"error":"quota exceeded"}', "JSON")):
            p = self._write("SEG02.mp4", body + bytes(200000))
            with self.assertRaises(ApiError) as c:
                apiutil._check_saved(p, "https://cdn/x")
            self.assertIn(word, str(c.exception))

    def test_a_real_video_passes(self):
        p = self._write("SEG03.mp4", MP4 + bytes(200000))
        apiutil._check_saved(p, "https://cdn/o.mp4")     # 不抛就是过了
        self.assertTrue(os.path.exists(p))

    def test_a_real_image_passes(self):
        p = self._write("C001.png", PNG + bytes(200000) + IEND)
        apiutil._check_saved(p, "https://cdn/a.png")
        self.assertTrue(os.path.exists(p))

    def test_an_extension_we_do_not_know_is_left_alone(self):
        """认不出的扩展名一律放行 —— 宁可漏，不可误杀。

        误杀的代价是「片子明明是好的，我们把它删了」，那比漏检贵得多。
        """
        p = self._write("x.bin", b"whatever" + bytes(200000))
        apiutil._check_saved(p, "https://cdn/x.bin")
        self.assertTrue(os.path.exists(p))


if __name__ == "__main__":
    unittest.main()
