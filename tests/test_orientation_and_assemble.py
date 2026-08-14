# -*- coding: utf-8 -*-
"""实跑暴露的两个洞。

一、**图片尺寸和画幅方向可以相反，而且一路不报错。**
    页面上是两个各自独立的下拉框。选成「1536x1024（横）+ 9:16（竖）」
    完全合法，图出得出来、片也出得出来、服务商谁都不报错。
    出来的是横构图故事板配竖屏视频 —— 出片时模型只能裁两边或者加黑边，
    人脸常常正好被裁掉。**要到成片才看得见**，那时候两笔钱都花完了。
    实跑撞过：项目写 9:16，故事板任务是 1536x1024，是审计发现的。

二、**最后一步「排序拼接与交付」失败时报「没见过的错误」。**
    闸门把视频生产拦住 → 一段视频都没有 → 拼接没东西可拼。
    这是连带失败，可它是**最后一行**报出来的，人会以为拼接坏了去查
    ffmpeg —— 那是白查，真正要看的是上面那四道闸门。
"""
import io
import os
import shutil
import unittest

from core import diagnose, probe

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class OrientationTests(unittest.TestCase):

    def test_the_real_case_is_caught(self):
        """★ 实跑那一组：项目 9:16，故事板 1536x1024。"""
        msg = probe.orientation_conflict("1536x1024", "9:16")
        self.assertTrue(msg)
        self.assertIn("1536x1024", msg)
        self.assertIn("9:16", msg)

    def test_the_default_pair_is_not_flagged(self):
        """★ 1024x1536（0.667）和 9:16（0.5625）差 18%，但都是竖的。

        按数值比就会天天误报默认配置 —— 那种警告三天就没人看了。
        """
        self.assertIsNone(probe.orientation_conflict("1024x1536", "9:16"))

    def test_landscape_pairs_are_fine(self):
        self.assertIsNone(probe.orientation_conflict("1536x1024", "16:9"))

    def test_square_never_conflicts(self):
        """方的配竖配横都不算躺倒，别拦。"""
        self.assertIsNone(probe.orientation_conflict("1024x1024", "9:16"))
        self.assertIsNone(probe.orientation_conflict("1024x1024", "16:9"))

    def test_unparseable_values_do_not_block_the_run(self):
        """读不懂就别拦 —— 没把握的时候拦人比放行更糟。"""
        for a, b in (("", "9:16"), ("1536x1024", ""), ("auto", "9:16"),
                     ("1536x0", "9:16")):
            self.assertIsNone(probe.orientation_conflict(a, b), (a, b))

    def test_orientation_names(self):
        self.assertEqual(probe.orientation("9:16"), "竖")
        self.assertEqual(probe.orientation("1536x1024"), "横")
        self.assertEqual(probe.orientation("1024x1024"), "方")
        self.assertEqual(probe.orientation("nonsense"), "")

    def test_the_message_says_what_goes_wrong_not_just_that_it_mismatches(self):
        """★ 「方向不一致」这五个字没用 —— 人会以为是无所谓的提示直接改回去。

        得说清后果（裁掉人脸）和什么时候才看得见（成片）。
        """
        msg = probe.orientation_conflict("1536x1024", "9:16")
        self.assertIn("参考图", msg)
        self.assertIn("裁", msg)


class OrientationWiringTests(unittest.TestCase):
    """查不出来的检查等于没写。"""

    def setUp(self):
        from test_v34_run import new_project
        self.pj = new_project()

    def tearDown(self):
        shutil.rmtree(self.pj.root, ignore_errors=True)

    def test_starting_a_run_with_conflicting_values_is_refused(self):
        """★ 在花钱之前拦住。跑完再发现，图和片的钱都已经花了。"""
        from server.app import api_post
        with self.assertRaises(ValueError) as cm:
            api_post("/api/pipeline/run",
                     {"project_root": self.pj.root,
                      "params_override": {"image_size": "1536x1024",
                                          "ratio": "9:16"},
                      "skip_model_check": True})
        self.assertIn("方向相反", str(cm.exception))

    def test_the_page_warns_before_you_click_start(self):
        html = io.open(os.path.join(ROOT, "web", "index.html"),
                       encoding="utf-8").read()
        self.assertIn("orientHint", html)
        self.assertIn("方向相反", html)


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
