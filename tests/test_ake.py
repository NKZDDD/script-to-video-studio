# -*- coding: utf-8 -*-
import unittest

from core.providers import REGISTRY, resolve_id
from core.providers.ake import MAX_REFS, SIZE_TABLE, AkeProvider, _size_of


class AkeTests(unittest.TestCase):
    def test_registered_and_aliased(self):
        self.assertIn("ake", REGISTRY)
        for alias in ("snumom", "阿珂", "ako"):
            self.assertEqual(resolve_id(alias), "ake")

    def test_video_only(self):
        cap = AkeProvider().capabilities()
        self.assertEqual(tuple(cap["supports"]), ("video",))
        self.assertEqual(cap["video"]["ratios"], ["16:9", "9:16"])
        self.assertEqual(cap["video"]["max_refs"], MAX_REFS)

    def test_size_merges_resolution_and_ratio(self):
        """这家没有 aspect_ratio 字段，画面全靠 size —— 四种组合必须对上文档。"""
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


if __name__ == "__main__":
    unittest.main()
