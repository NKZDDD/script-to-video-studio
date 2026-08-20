# -*- coding: utf-8 -*-
"""单段秒数 / 画幅 / 图片尺寸：一个旋钮，一处存。

用户问：「一键跑到底有时间选择，然后下面也有一个时间选择，是不是重复了」。
是。而且比「重复」更糟 —— 两处显示的值不一样（面板 30、项目参数 15），
**而面板那个赢**：

    params_of()  = 全局默认 ← 项目 meta.params
    然后 params_override（面板送的）盖在最上面

可面板那三个下拉是用**全局默认**初始化的，不是这个项目的参数。
于是项目里存的 15 被面板显示的 30 无声盖掉。而单段秒数是环节 2/7/8
编译进产物的东西：段落表按 15 秒装的箱，视频按 30 秒生成，
中间一处都不报错 —— 又是那一类。

用户定的规则：**全部按一键跑到底；面板为空就用「设置」里的。**
所以「项目参数」那张卡去掉，面板成为唯一输入口。
**存不能去掉**：单跑某个环节走的是项目 meta.params，不经过面板 ——
所以面板点「开始」时要写回去。
"""
import io
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server"))

import app as A                                          # noqa: E402

HTML = io.open(os.path.join(ROOT, "web", "index.html"),
               encoding="utf-8").read()
KEYS = ("image_size", "ratio", "duration")


class OverrideTests(unittest.TestCase):
    """面板为准；空的不覆盖。"""

    def test_values_from_the_panel_win(self):
        self.assertEqual(A._override({"duration": 30, "ratio": "9:16"}, KEYS),
                         {"duration": 30, "ratio": "9:16"})

    def test_an_empty_item_falls_back_instead_of_overriding(self):
        """★ 用户原话：「如果一键跑到底为空就用设置内的」。"""
        self.assertEqual(A._override({"duration": None, "ratio": "",
                                      "image_size": "16:9"}, KEYS),
                         {"image_size": "16:9"})

    def test_a_null_duration_does_not_crash_the_whole_run(self):
        """★ 原来是 `int(v)`。面板下拉为空时 JS 送 null，`int(None)` 抛

        TypeError —— 而它从「开始」那个请求里冒出来，报的是一句 Python
        类型错误，看不出是「视频链没配好、秒数候选是空的」。
        """
        self.assertEqual(A._override({"duration": None}, KEYS), {})
        self.assertEqual(A._override({"duration": "NaN"}, KEYS), {})
        self.assertEqual(A._override({"duration": "abc"}, KEYS), {})

    def test_a_float_string_is_accepted(self):
        """`int("30.0")` 也会抛 —— 而 30.0 是个完全正常的值。"""
        self.assertEqual(A._override({"duration": "30.0"}, KEYS),
                         {"duration": 30})

    def test_keys_outside_the_allow_list_are_dropped(self):
        """补生产那条路径只许改这一类活自己的那几项。"""
        self.assertEqual(A._override({"duration": 30, "image_size": "x"},
                                     ("ratio", "duration")),
                         {"duration": 30})

    def test_nothing_at_all_is_fine(self):
        self.assertEqual(A._override(None, KEYS), {})

    def test_both_entry_points_share_one_implementation(self):
        """★ 两处入口以前各写一遍，条件还不一样（一处认 `"None"` 字符串、

        一处不认）—— 同一个空值在一条路上被忽略、在另一条路上让整轮跑崩。
        """
        src = io.open(os.path.join(ROOT, "server", "app.py"),
                      encoding="utf-8").read()
        self.assertEqual(src.count("_override(body.get(\"params_override\")"), 2)
        self.assertNotIn("int(v) if k == \"duration\" else v", src)
        self.assertNotIn("int(v) if k not in (\"image_size\", \"ratio\")", src)

    def test_the_fallback_order_is_defaults_then_project(self):
        """★ 面板 → 项目参数 → 设置默认。中间那层是单跑环节读的。"""
        src = io.open(os.path.join(ROOT, "server", "app.py"),
                      encoding="utf-8").read()
        i = src.index("def params_of(")
        blk = src[i:i + 700]
        self.assertLess(blk.index('cfg.get("defaults")'),
                        blk.index('meta.get("params")'),
                        "项目参数必须盖在全局默认上面")


class PageTests(unittest.TestCase):
    """页面上只剩一个输入口，而且它写回项目。"""

    def test_the_duplicate_card_is_gone(self):
        """★ 这就是用户问的那件事。"""
        self.assertNotIn('id="paramBox"', HTML)
        self.assertNotIn('id="saveParams"', HTML)
        self.assertNotIn("项目参数 <span", HTML)

    def test_the_panel_reads_the_projects_own_params(self):
        """★ 原来读 `d`（全局默认）—— 项目里存的值被无声盖掉。"""
        i = HTML.index("$('#autoDur').innerHTML")
        blk = HTML[i - 600:i + 200]
        self.assertIn("currentParams()", blk)
        self.assertIn("pp.duration", blk)

    def test_the_panel_writes_back_to_the_project(self):
        """★ 不写回的话：一键跑到底按 30 秒跑，单跑环节2 按设置默认的

        15 秒编译 —— 两个数，都不报错。
        """
        self.assertIn("async function saveRunParams()", HTML)
        i = HTML.index("$('#autoRun').onclick")
        self.assertIn("saveRunParams()", HTML[i:i + 800])
        self.assertIn("/api/project/params", HTML)

    def test_changing_the_seconds_says_what_it_invalidates(self):
        """★ 单段秒数被环节 2/7/8 编译进产物 —— 改了它，那几份是按旧值排的。"""
        i = HTML.index("async function saveRunParams()")
        blk = HTML[i:i + 1600]
        self.assertIn("单段秒数从", blk)
        self.assertIn("重跑", blk)

    def test_an_unchanged_value_is_not_written_back(self):
        """没改就不写 —— 每次点开始都写一遍会刷掉一条无意义的日志。"""
        i = HTML.index("async function saveRunParams()")
        self.assertIn("没改，别白写一遍", HTML[i:i + 1200])

    def test_empty_panel_items_are_not_sent(self):
        """★ 送 null 过去服务端要么炸、要么把空值当成一个值。"""
        self.assertIn("function runParams()", HTML)
        i = HTML.index("function runParams()")
        self.assertIn("String(v).trim() !== ''", HTML[i:i + 600])
        self.assertIn("take('#autoDur', 'duration', true)", HTML)

    def test_the_global_defaults_page_still_has_the_three(self):
        """★ 「设置 → 默认参数」是回落的最后一档，不能一起删掉。"""
        self.assertIn("PARAM_FIELDS.concat(RUN_FIELDS)", HTML)

    def test_the_label_says_it_is_the_same_knob(self):
        i = HTML.index("单段秒数")
        self.assertIn("项目参数", HTML[i - 300:i + 100])


if __name__ == "__main__":
    unittest.main()
