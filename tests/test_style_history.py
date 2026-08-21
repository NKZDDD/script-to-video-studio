# -*- coding: utf-8 -*-
"""自由文本字段的历史下拉（「视觉风格」填空 + 下拉框）。

用户实遇：视觉风格是自由文本，每部剧都要重新想一遍措辞，
而且手滑写出「3D漫剧风格」这类和「拍成什么形式」矛盾的词 ——
出图就在两种媒介之间随机。

改成「填空 + datalist 下拉」，下拉列出**所有项目以前填过的值**
（不另存历史文件，历史就是各项目真实用过的值）+ seeds 兜底。
"""
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "server")))

from server.app import _free_text_history          # noqa: E402

SEEDS = ["电影写实", "都市情感", "末日废土", "古装写实"]


def _mk_proj(base, name, style, age_sec=0):
    """造一个带 settings.visual_style 的假项目目录。"""
    import json
    root = os.path.join(base, name)
    os.makedirs(os.path.join(root, "00_项目说明"), exist_ok=True)
    with open(os.path.join(root, "00_项目说明", "project.json"),
              "w", encoding="utf-8") as fh:
        json.dump({"settings": {"visual_style": style} if style else {}}, fh,
                  ensure_ascii=False)
    t = time.time() - age_sec
    os.utime(root, (t, t))
    return root


class TestFreeTextHistory(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_empty_dir_seeds_only(self):
        """一个项目都没有：只剩 seeds，下拉也不是空的。"""
        out = _free_text_history({"projects_dir": self.base},
                                 "visual_style", SEEDS)
        self.assertEqual(out, SEEDS)

    def test_collects_and_orders_by_recency(self):
        """多个项目：值去重，最近动过的项目排前面。"""
        _mk_proj(self.base, "a_old", "末日废土", age_sec=99999)
        _mk_proj(self.base, "b_new", "古装写实", age_sec=1)
        _mk_proj(self.base, "c_dup", "末日废土", age_sec=2)
        out = _free_text_history({"projects_dir": self.base},
                                 "visual_style", SEEDS)
        # 去重：末日废土只出现一次（两个项目用过）
        self.assertEqual(out.count("末日废土"), 1)
        # 最近动过的项目（b_new=古装写实）排最前
        self.assertEqual(out[0], "古装写实")
        # 历史 + seeds 全都在
        self.assertIn("末日废土", out)
        self.assertIn("电影写实", out)      # seed 没被历史挤掉

    def test_current_value_included(self):
        """当前项目填过的值自然在历史里（它也是一个项目目录）。"""
        _mk_proj(self.base, "only", "赛博朋克", age_sec=1)
        out = _free_text_history({"projects_dir": self.base},
                                 "visual_style", SEEDS)
        self.assertEqual(out[0], "赛博朋克")

    def test_bad_dir_does_not_crash(self):
        """一个读不出 meta 的目录：跳过，不让设置页打不开。"""
        os.makedirs(os.path.join(self.base, "junk"))       # 没有 project.json
        _mk_proj(self.base, "ok", "都市情感", age_sec=1)
        out = _free_text_history({"projects_dir": self.base},
                                 "visual_style", SEEDS)
        self.assertIn("都市情感", out)

    def test_cap_12(self):
        """上限 12 条：历史攒多了不至于下拉一屏放不下。"""
        for i in range(15):
            _mk_proj(self.base, f"p{i:02d}", f"风格{i:02d}", age_sec=i)
        out = _free_text_history({"projects_dir": self.base},
                                 "visual_style", SEEDS)
        self.assertLessEqual(len(out), 12)


if __name__ == "__main__":
    unittest.main()
