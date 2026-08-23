# -*- coding: utf-8 -*-
"""该有的模块要钉成必须有，而且要查它**真的进了包**。

实测（2026-08-24）：用户那份 81MB 的包里**没有 pypdf、也没有 fitz 兜底** ——
传 PDF 剧本直接失败，报「未安装 pypdf，可 pip install pypdf」，
而那句话对一个 exe 用户来说没法照做。

**当时打包自检是绿的**：它只查「服务商 12 家、提示词模板 26 份、页面字符数」，
从没查过 PDF 能不能解析、图片能不能压、对象存储能不能上传。

两个洞各堵一个：
  ① 依赖检查里这几个是「可选增强」，缺了问一句 y/N 就放行
  ② 自检从不问 exe「这几个模块在不在」——
     而「打包机器上装了」和「进了包」是两件事
"""
import io
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server"))

import app as A                                          # noqa: E402

PACK = io.open(os.path.join(ROOT, "打包exe.py"), encoding="utf-8").read()


class RequiredListTests(unittest.TestCase):

    def test_the_functional_packages_are_required_not_optional(self):
        """★ 它们不是「可选增强」—— 缺了是功能没了。"""
        for m in ("pypdf", "PIL", "imageio_ffmpeg", "boto3"):
            self.assertIn(f'"{m}"', PACK.split("OPTIONAL = {")[0],
                          f"{m} 不在 REQUIRED 里")

    def test_a_missing_required_dep_stops_the_build(self):
        """★ 那个 y/N 就是上次打出一个读不了 PDF 的包的原因。"""
        i = PACK.index("必须有的依赖")
        blk = PACK[i:i + 900]
        self.assertIn("return 1", blk)
        self.assertNotIn("现在就这样打包？", blk)

    def test_it_says_what_each_one_breaks(self):
        """★ 只说「缺 pypdf」的话，人不知道要不要管它。"""
        head = PACK.split("OPTIONAL = {")[0]
        self.assertIn("传 PDF 直接失败", head)
        self.assertIn("撞网关体积上限", head)

    def test_psutil_stays_optional(self):
        """缺了只是页面显示「占用未知」—— 不该挡打包。"""
        opt = PACK.split("OPTIONAL = {")[1].split("}")[0]
        self.assertIn("psutil", opt)

    def test_every_required_module_is_also_a_hidden_import(self):
        """★ 装了但 PyInstaller 扫不到 = 照样不进包。"""
        head = PACK.split("OPTIONAL = {")[0]
        hidden = PACK.split("HIDDEN = [")[1].split("]")[0]
        for m in ("pypdf", "PIL", "imageio_ffmpeg", "boto3"):
            if f'"{m}"' in head:
                self.assertIn(f'"{m}"', hidden, f"{m} 没写进 HIDDEN")


class ModuleEndpointTests(unittest.TestCase):
    """让 exe 自己回答「在不在」—— 只有它答得准。"""

    def test_it_reports_per_module(self):
        got = A.api_get("/api/modules",
                        {"names": ["pypdf,PIL.Image,boto3"]})["modules"]
        self.assertEqual(set(got), {"pypdf", "PIL.Image", "boto3"})
        self.assertTrue(all(isinstance(v, bool) for v in got.values()))

    def test_a_module_that_is_not_there_reports_false(self):
        got = A.api_get("/api/modules",
                        {"names": ["definitely_not_a_real_module_xyz"]})["modules"]
        self.assertFalse(got["definitely_not_a_real_module_xyz"])

    def test_it_has_a_default_list(self):
        """不带参数也能问 —— 人自查时不用记名字。"""
        got = A.api_get("/api/modules", {})["modules"]
        self.assertIn("pypdf", got)

    def test_it_does_not_leak_paths(self):
        """★ 只回「能不能 import」。回路径等于把机器目录结构发出去。"""
        got = A.api_get("/api/modules", {"names": ["pypdf"]})
        self.assertEqual(list(got), ["modules"])
        self.assertNotIn("site-packages", repr(got))


class SelfcheckWiringTests(unittest.TestCase):

    def test_the_selfcheck_asks_the_exe(self):
        """★ 不问的话，缺 pypdf 的包照样是绿灯（上次就是这样）。"""
        self.assertIn("/api/modules?names=", PACK)
        self.assertIn("MUST_IMPORT", PACK)

    def test_a_missing_module_fails_the_build(self):
        i = PACK.index("功能模块 ")
        blk = PACK[i - 400:i + 900]
        self.assertIn("ok = False", blk)

    def test_it_says_what_each_missing_one_costs(self):
        """★ 「缺 imageio_ffmpeg」对读的人没有意义；「拼不了成片」有。"""
        i = PACK.index("功能模块 ")
        blk = PACK[i:i + 1200]
        self.assertIn("传 PDF 读不了", blk)
        self.assertIn("拼不了成片", blk)


if __name__ == "__main__":
    unittest.main()
