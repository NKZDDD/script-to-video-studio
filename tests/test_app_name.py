# -*- coding: utf-8 -*-
r"""改名：Respect短剧制作平台。

用户原话（2026-08-26）：「我需要把程序改名为Respect短剧制作平台」。

改名有一个不明显的代价：**数据目录名是从程序名来的**
（`%LOCALAPPDATA%\<APP_NAME>`）。只认新名字的话，老用户升级之后看到的是
一个全新的空程序 —— API Key 没了、优先级链没了、计价表没了、项目列表空了，
而且**不报错**，看起来就像「新版本把我的东西删了」。
东西都还在，只是程序不再往那儿看。
"""
import os
import tempfile
import unittest

from core import paths
from core.store import read_text


class DisplayNameTests(unittest.TestCase):

    NAME = "Respect短剧制作平台"

    def test_the_page_title_and_header(self):
        html = read_text("web/index.html")
        self.assertIn(f"<title>{self.NAME}</title>", html)
        self.assertIn(f"<h1>{self.NAME}</h1>", html)

    def test_the_running_title_too(self):
        """★ 跑起来之后标签页会被改写 —— 漏了这一处就是「跑起来又变回旧名」。"""
        html = read_text("web/index.html")
        i = html.index("document.title =")
        self.assertIn(self.NAME, html[i:i + 120])

    def test_the_exe_name(self):
        src = read_text("打包exe.py")
        self.assertIn(f'NAME = "{self.NAME}"', src)

    def test_the_cli_description(self):
        self.assertIn(self.NAME, read_text("run.py"))

    def test_no_old_display_name_is_left(self):
        for f in ("web/index.html", "run.py", "打包exe.py"):
            self.assertNotIn("自动化生产台", read_text(f), f)


class DataDirTests(unittest.TestCase):
    """数据目录：新名字是 ASCII，老名字要兜底。"""

    def test_the_dir_name_is_ascii(self):
        """★ 这个字符串会变成磁盘路径。中文路径在命令行、压缩包、某些第三方库
        里会出编码怪事，而它是给程序自己用的，没人需要看懂。"""
        self.assertTrue(paths.APP_NAME.isascii(), paths.APP_NAME)

    def test_the_old_name_is_still_remembered(self):
        self.assertIn("script-to-video-studio", paths.LEGACY_APP_NAMES)

    def _dir(self, base, make: dict):
        """在假的 base 下按 make={目录名: 有没有 config.json} 造现场。"""
        for name, has in make.items():
            d = os.path.join(base, name)
            os.makedirs(d, exist_ok=True)
            if has:
                with open(os.path.join(d, "config.json"), "w",
                          encoding="utf-8") as f:
                    f.write("{}")
        old = os.environ.get("LOCALAPPDATA")
        os.environ["LOCALAPPDATA"] = base
        try:
            return paths.default_data_dir()
        finally:
            if old is None:
                os.environ.pop("LOCALAPPDATA", None)
            else:
                os.environ["LOCALAPPDATA"] = old

    def test_the_legacy_dir_wins_when_only_it_has_a_config(self):
        """★ 这一条就是「改名之后配置还在不在」。"""
        base = tempfile.mkdtemp()
        got = self._dir(base, {"script-to-video-studio": True})
        self.assertEqual(got, os.path.join(base, "script-to-video-studio"))

    def test_the_new_dir_wins_once_it_has_a_config(self):
        """两边都有配置时用新的 —— 老的那份是升级前留下的，已经搬过一次了。"""
        base = tempfile.mkdtemp()
        got = self._dir(base, {"script-to-video-studio": True,
                               paths.APP_NAME: True})
        self.assertEqual(got, os.path.join(base, paths.APP_NAME))

    def test_a_fresh_machine_uses_the_new_name(self):
        base = tempfile.mkdtemp()
        self.assertEqual(self._dir(base, {}),
                         os.path.join(base, paths.APP_NAME))

    def test_an_empty_legacy_dir_does_not_hijack(self):
        """★ 老目录存在但没有 config.json（比如只剩个空壳）不算 ——
        照着空壳走会把新配置写进一个看着像旧版残留的地方。"""
        base = tempfile.mkdtemp()
        got = self._dir(base, {"script-to-video-studio": False})
        self.assertEqual(got, os.path.join(base, paths.APP_NAME))
