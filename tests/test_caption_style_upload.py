# -*- coding: utf-8 -*-
"""自己上传字幕样式。

字幕样式**只在「烧录进画面」时生效**，而选错/没装上的表现是「字幕出来了，
就是不是你要的样子」—— 一处都不报错。所以这条路上每一步都得是硬的。
"""
import io
import os
import tempfile
import unittest
from unittest import mock

from core import captions as C


def _sample() -> str:
    src = C.bundled()
    if src:
        return io.open(src[0], encoding="utf-8").read()
    return ("[V4+ Styles]\n"
            "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,"
            "OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,"
            "ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,"
            "MarginL,MarginR,MarginV,Encoding\n"
            "Style: Default,华文楷体,40,&H00ffffff,&H000000FF,&H00060606,"
            "&H00000000,-1,0,0,0,100,100,0.0,0,1,2.6,0,2,10,10,138,1\n")


def _resize(text: str, size: str) -> str:
    """把每条 Style 行的字号换掉 —— 用来验「同名再传真的覆盖了」。"""
    return "\n".join(
        ",".join(p if i != 2 else size for i, p in enumerate(ln.split(",")))
        if ln.startswith("Style:") else ln
        for ln in text.split("\n"))


class CaptionStyleUploadTests(unittest.TestCase):
    def test_user_dir_never_collides_with_the_bundled_one(self):
        """★ 用户传的和自带的必须分两个目录。

        合一个目录有两种死法，都不报错：
          · 打成 exe 后自带样式在 onefile 的临时解压目录（_MEI*）里 ——
            每次启动重建、退出即删，往里写等于下次就没了
          · 源码方式跑时 data_dir 就是仓库根，和自带样式同一个目录 ——
            传一份同名的直接覆盖仓库里的源文件
        """
        from core import paths
        self.assertNotEqual(os.path.normcase(C.user_dir()),
                            os.path.normcase(paths.res(C.DIR_NAME)))

    def test_a_name_with_a_path_is_refused_not_quietly_renamed(self):
        """带路径的文件名当场拒。

        basename 一下确实挡住了穿越（只会落在用户目录里），但它把一个和
        用户写的不一样的名字**静默收下**了 —— 而文件名就是样式名，
        人会在下拉里找一个自己没起过的名字。
        """
        for bad in ("../../跑出去.txt", "a" + chr(92) + "b.txt", "d/e.txt"):
            with self.assertRaises(ValueError, msg=bad) as cm:
                C.add_style(bad, _sample())
            self.assertIn("路径", str(cm.exception))

    def test_garbage_is_refused_before_it_is_saved(self):
        """★ 先解析再落盘。

        存下来的话页面上会列出一份永远装不进去的样式 —— 而
        「列出来了、选中了、字幕还是默认样子」正是要消灭的那类失效。
        """
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(C, "user_dir", lambda: d):
                for name, text in (("空的.txt", "   "),
                                   ("不是样式.txt", "随便写点什么"),
                                   ("扩展名不对.ass", _sample())):
                    with self.assertRaises(ValueError, msg=name):
                        C.add_style(name, text)
                self.assertEqual(os.listdir(d), [], "被拒的居然还落了盘")

    def test_same_name_really_replaces(self):
        """★ 同名再传必须真覆盖。

        install() 默认「同名不覆盖」（用户在 VideoCaptioner 那边改过的样式
        是他的）。但刚上传的这一份是人明确要求换的 —— 不覆盖的话
        「传了新的、字幕还是老样子」，而且一处都不报错。
        """
        try:
            from videocaptioner.core.subtitle.style_manager import SubtitleStyle
        except Exception:                                    # noqa: BLE001
            self.skipTest("这台机器上没装 videocaptioner")
        from pathlib import Path
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(C, "user_dir", lambda: d):
                name = "回归测试用样式"
                try:
                    C.add_style(name + ".txt", _resize(_sample(), "40"))
                    r = C.add_style(name + ".txt", _resize(_sample(), "96"))
                    self.assertTrue(r["replaced"])
                    self.assertTrue(r["installed"], r["failed"])
                    self.assertEqual(r["font_size"], 96)
                    # 落进 videocaptioner 那一份也要是新的，不只是我们目录里的
                    from videocaptioner.config import SUBTITLE_STYLE_PATH as vc
                    p = Path(str(vc)) / (name + ".json")
                    self.assertEqual(SubtitleStyle.from_file(p).font_size, 96)
                finally:
                    try: C.remove_style(name)
                    except Exception: pass                   # noqa: BLE001

    def test_bundled_styles_cannot_be_deleted_here(self):
        """自带的删了找不回来 —— 这个入口只删用户自己传的。"""
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(C, "user_dir", lambda: d):
                with self.assertRaises(ValueError) as cm:
                    C.remove_style("中文竖屏标准版")
                self.assertIn("不是你传上来的", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
