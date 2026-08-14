# -*- coding: utf-8 -*-
"""「排序拼接与交付」失败时不许报「没见过的错误」。

闸门把视频生产拦住 → 一段视频都没有 → 拼接没东西可拼。
这是连带失败，可它是**最后一行**报出来的，人会以为拼接坏了去查
ffmpeg —— 那是白查，真正要看的是上面那四道闸门。
"""
import io
import os
import unittest

from core import diagnose

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class NothingToAssembleTests(unittest.TestCase):

    def test_the_message_the_code_actually_raises_is_recognised(self):
        """★ 用 stages.py 真的抛的那句原文，不是我编一句来对。"""
        src = io.open(os.path.join(ROOT, "core", "stages.py"),
                      encoding="utf-8").read()
        self.assertIn("没有可拼接的分段视频", src)
        self.assertIn("没有任何一集能拼接", src)
        for msg in ("EP01 没有可拼接的分段视频",
                    "没有任何一集能拼接。EP01：EP01 没有可拼接的分段视频"):
            self.assertEqual(diagnose.code_of(msg), "NOTHING_TO_ASSEMBLE", msg)

    def test_it_points_at_the_gates_not_at_ffmpeg(self):
        """★ 这一步不是源头。指向 ffmpeg 会让人查一个没坏的东西。"""
        d = diagnose.CATALOG["NOTHING_TO_ASSEMBLE"]
        self.assertIn("闸门", d["where"])
        self.assertIn("不是失败的源头", d["why"])
        # 「怎么改」这几步一个都不许把人支去查 ffmpeg —— 那个没坏。
        # （why 里提 ffmpeg 是为了说「别去查它」，那是对的。）
        self.assertNotIn("ffmpeg", "".join(d["fix"]))
        self.assertTrue(any("闸门" in f for f in d["fix"]))

    def test_it_does_not_steal_the_missing_ffmpeg_case(self):
        """ffmpeg 真的没装是另一回事，别被这条兜走。"""
        self.assertNotEqual(diagnose.code_of("未找到 ffmpeg"),
                            "NOTHING_TO_ASSEMBLE")


if __name__ == "__main__":
    unittest.main()
