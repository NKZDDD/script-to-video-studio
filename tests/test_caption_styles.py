# -*- coding: utf-8 -*-
r"""自带四份字幕样式（中文/英文 × 竖屏/横屏）。

用户给的是 ASS `[V4+ Styles]` 块的 txt（2026-08-27）。

两个坑，都在「不报错」那一类：

1. videocaptioner 的 `SubtitleStyle.from_file` **认** legacy `.txt`，
   但 `list_styles` / `load_style` **只 glob `*.json`** —— 直接把 txt 丢进它的
   样式目录，`caption style` 一个都列不出来，`--style 中文竖屏标准版` 也找不到。
   所以要转成它的 json 形状。
2. 它的样式目录在 `%LOCALAPPDATA%\VideoCaptioner\...\resource\subtitle_style`，
   打进 exe 之后目标机器上第一次跑才建，里面一份都没有 ——
   而缺样式的表现不是报错，是**字幕照出，只是用的是它的默认样子**。
"""
import json
import os
import tempfile
import unittest

from core import captions
from core.store import read_text


class BundledTests(unittest.TestCase):

    WANT = ["中文横屏标准版", "中文竖屏标准版", "英文横屏标准版", "英文竖屏标准版"]

    def test_all_four_are_in_the_repo(self):
        got = sorted(os.path.splitext(os.path.basename(p))[0]
                     for p in captions.bundled())
        self.assertEqual(got, sorted(self.WANT))

    def test_they_are_ass_style_blocks(self):
        for p in captions.bundled():
            txt = read_text(p)
            self.assertIn("[V4+ Styles]", txt, p)
            self.assertIn("Style: Default,", txt, p)

    def test_portrait_and_landscape_really_differ(self):
        """★ 四份要真的不一样 —— 拷同一份改个名字的话，竖屏用横屏的边距，
        字会压在安全区外，而没有一处会说话。"""
        margins = {}
        for p in captions.bundled():
            name = os.path.splitext(os.path.basename(p))[0]
            line = next(l for l in read_text(p).splitlines()
                        if l.startswith("Style: Default,"))
            cols = line.split(",")
            margins[name] = (cols[2], cols[-2])      # 字号、MarginV
        self.assertNotEqual(margins["中文竖屏标准版"], margins["中文横屏标准版"])
        self.assertNotEqual(margins["英文竖屏标准版"], margins["英文横屏标准版"])


class InstallTests(unittest.TestCase):

    def test_it_converts_to_the_json_the_tool_actually_reads(self):
        """★ 转换要按它的字段名来。手写 json 迟早写错一个键，
        而写错的表现是「样式列出来了、字幕出来还是默认样子」。"""
        try:
            from videocaptioner.core.subtitle.style_manager import SubtitleStyle
        except Exception:                                   # noqa: BLE001
            self.skipTest("这台机器没装 videocaptioner")
        from pathlib import Path
        for p in captions.bundled():
            d = SubtitleStyle.from_file(Path(p)).to_json_dict()
            for key in ("name", "mode", "font_name", "font_size",
                        "primary_color", "margin_bottom"):
                self.assertIn(key, d, p)
            self.assertEqual(d["mode"], "ass")

    def test_installing_twice_does_not_overwrite(self):
        """★ 用户在那边改过的样式是他的 —— 每次启动盖回去等于
        「我改的怎么又没了」。"""
        try:
            import videocaptioner  # noqa: F401
        except Exception:                                   # noqa: BLE001
            self.skipTest("这台机器没装 videocaptioner")
        first = captions.install()
        again = captions.install()
        self.assertEqual(again["installed"], [])
        self.assertEqual(sorted(again["kept"]),
                         sorted(first["installed"] + first["kept"]))

    def test_it_does_not_crash_without_the_tool(self):
        """★ 源码方式跑、没装 videocaptioner 时不能连启动都进不去。"""
        import sys
        # **整条链都要挡住。** 只把顶层设成 None 不管用：
        # `from videocaptioner.core.subtitle.style_manager import X` 会先在
        # sys.modules 里找那个子模块，而前面的测试已经把它导进来了 ——
        # 于是「模拟没装」压根没生效，测试绿着但什么都没验（差点就这样过了）。
        names = ("videocaptioner", "videocaptioner.core",
                 "videocaptioner.core.subtitle",
                 "videocaptioner.core.subtitle.style_manager",
                 "videocaptioner.config")
        saved = {n: sys.modules.get(n, "缺") for n in names}
        for n in names:
            sys.modules[n] = None            # None 在 sys.modules 里 = ImportError
        try:
            got = captions.install()
            self.assertTrue(got["failed"], got)
            self.assertEqual(got["installed"], [])
        finally:
            for n, v in saved.items():
                if v == "缺":
                    sys.modules.pop(n, None)
                else:
                    sys.modules[n] = v


class PackagingTests(unittest.TestCase):

    def test_the_folder_is_packed(self):
        """★ 不 --add-data 进去的话，exe 里这个目录压根不存在。"""
        self.assertIn("字幕样式", read_text("打包exe.py"))

    def test_run_installs_them_on_caption(self):
        src = read_text("run.py")
        i = src.index("def _caption")
        self.assertIn("captions.install", src[i:i + 3000])
