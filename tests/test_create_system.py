# -*- coding: utf-8 -*-
"""建项目时用哪套体系 —— 必须跟着这一版的包走，不许写死。

用户实跑撞到：**「我打开的是通用十二环节，创建出来为什么都是电影级十七章」**

原因在页面里，一行常量：

    const DEFAULT_SYS = 'v34';
    ...
    system: DEFAULT_SYS,        // ← 批量建剧直接发它

于是不管开的是哪个包、下拉框选了什么，**批量建剧建出来的永远是电影级**。
单个项目那条路是对的（发 $('#spSystem').value），所以问题只在批量那边 ——
更隐蔽，因为两条路看起来都「有传 system」。

**而体系建完不能改**：整批建错就是整批作废，剧本要重新拖一遍、
所有产物重跑一遍。
"""
import io
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class PageTests(unittest.TestCase):

    def setUp(self):
        self.html = io.open(os.path.join(ROOT, "web", "index.html"),
                            encoding="utf-8").read()

    def test_no_hardcoded_default_system(self):
        """★ 这就是那个 bug 本身。

        只查**真正的代码**：声明和使用。注释里提到这个名字是允许的 ——
        那段注释正是在解释这个坑，删了下次没人知道为什么不能写死。
        """
        self.assertNotIn("\nconst DEFAULT_SYS", self.html, "常量声明还在")
        self.assertNotIn("system: DEFAULT_SYS", self.html, "还在用那个常量")
        self.assertNotIn("|| DEFAULT_SYS", self.html, "还有地方回落到它")

    def test_the_default_comes_from_what_this_build_can_create(self):
        self.assertIn("function defaultSystem()", self.html)
        i = self.html.index("function defaultSystem()")
        self.assertIn("new_ok", self.html[i:i + 500])

    def test_batch_create_uses_it(self):
        i = self.html.index("/api/project/create_batch")
        # 往前找这次调用的准备段落
        blk = self.html[max(0, i - 900):i + 300]
        self.assertIn("defaultSystem()", blk)
        self.assertIn("system: sys", blk)

    def test_batch_create_refuses_rather_than_guessing(self):
        """★ 猜一个的代价是整批项目作废 —— 宁可不建。"""
        i = self.html.index("const sys = defaultSystem();")
        blk = self.html[i:i + 400]
        self.assertIn("if (!sys)", blk)
        self.assertIn("宁可不建", blk)

    def test_batch_create_says_which_system_before_doing_it(self):
        """★ 建完不能改，所以动手前要把「建成哪套」摆到人面前。"""
        i = self.html.index("const sys = defaultSystem();")
        blk = self.html[i:i + 700]
        self.assertIn("confirm(", blk)
        self.assertIn("建完不能改", blk)


class BackendTests(unittest.TestCase):

    def test_the_backend_also_follows_the_flavor(self):
        """页面传错时后端是最后一道 —— 它按构建标记兜底，不是写死 v34。"""
        src = io.open(os.path.join(ROOT, "server", "app.py"),
                      encoding="utf-8").read()
        i = src.index("def _new_system")
        self.assertIn("build_info.only()", src[i:i + 400])


if __name__ == "__main__":
    unittest.main()
