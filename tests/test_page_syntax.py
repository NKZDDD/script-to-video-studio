# -*- coding: utf-8 -*-
"""页面里的 JS 必须能解析。

**这是整个项目里最贵的一类错误，而且以前没有任何东西在查。**

语法错的后果不是「某个功能坏了」，是**一行 JS 都不执行**：
页面停在初始占位符（项目列表「加载中…」），按钮全没反应，
而我为此加的那一堆错误捕获（boot().catch、unhandledrejection、
请求超时）**也一个都装不上**，因为它们自己就在这段脚本里。

所以现象是：什么都不动，什么都不报。用户在两台机器上各报了一次，
我在本机复现了五轮都没重现 —— 因为我一直在测后端接口，没看页面。

起因很低级：用 shell 改 index.html 时，字符串里的换行转义被吃掉了：

    alert(lines.join('
    '));

同一个坑在 run.py 上也栽过一次（那次是打包时 PyInstaller 报出来的）。

## 第二个坑：闸门本身是瞎的

第一版把提取脚本的正则在**打包脚本和测试里各写了一份**。
打包那份转义写坏了，`findall` 返回空列表，于是它「检查了 0 段脚本」
然后报成功 —— **闸门装上了，但永远不会拦**，而测试那份是好的，
所以测试一直绿着。现在两边都从 core/pagecheck 取。
"""
import io
import os
import shutil
import unittest

from core import pagecheck

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGE = os.path.join(ROOT, "web", "index.html")


def _html():
    return io.open(PAGE, encoding="utf-8").read()


class ExtractTests(unittest.TestCase):

    def test_it_actually_finds_the_scripts(self):
        """★ 找不到脚本却报成功，就是上次那个瞎闸门。"""
        self.assertTrue(pagecheck.scripts(_html()))

    def test_finding_nothing_is_a_failure_not_a_pass(self):
        ok, why = pagecheck.check("<html><body>没有脚本</body></html>")
        self.assertFalse(ok)
        self.assertIn("提取逻辑坏了", why)


class SyntaxTests(unittest.TestCase):

    def test_the_page_parses(self):
        ok, why = pagecheck.check(_html())
        self.assertTrue(ok, why)

    def test_a_raw_newline_in_a_string_is_caught(self):
        """★ 就是这次真踩的那个形状。"""
        if not shutil.which("node"):
            self.skipTest("没装 node，验不了语法")
        bad = "<script>\nconst x = 'a\nb';\n</script>"
        ok, why = pagecheck.check(bad)
        self.assertFalse(ok)
        self.assertIn("语法错", why)

    def test_missing_node_is_reported_not_faked(self):
        """★ 没装 node 时要说「未检查」，不能冒充通过。"""
        ok, why = pagecheck.check("<script>const a = 1;</script>")
        self.assertTrue(ok)
        if not shutil.which("node"):
            self.assertIn("未检查", why)


class PackerTests(unittest.TestCase):

    def test_the_packer_checks_before_building(self):
        """★ 光有单元测试不够 —— 坏页面绝不能被打进 exe。

        这次就是打进去了，用户在两台机器上各撞了一次。
        """
        src = io.open(os.path.join(ROOT, "打包exe.py"), encoding="utf-8").read()
        self.assertIn("check_page", src)
        # 必须在真正开打之前
        self.assertLess(src.index("if not check_page():"),
                        src.index('"-m", "PyInstaller"'))

    def test_the_packer_does_not_reimplement_the_check(self):
        """★ 各写一份 = 其中一份坏了也没人知道（上次就是这样）。"""
        src = io.open(os.path.join(ROOT, "打包exe.py"), encoding="utf-8").read()
        self.assertIn("from core import pagecheck", src)
        self.assertNotIn("<script", src, "打包脚本里又自己写了一份提取正则")


if __name__ == "__main__":
    unittest.main()
