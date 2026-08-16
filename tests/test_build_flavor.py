# -*- coding: utf-8 -*-
"""分体系打包：两个包，各自主打一套，但都能打开对方建的老项目。

起因：包里两套模板都在（自检 25/25），页面上却只有一个选项 ——
「通用版」那个包建不出通用项目。选项写死在 HTML 里了。

**限制的只是「新建时能选哪套」，不是把另一套裁掉。** 真裁的话：
  · 拿错包打开老项目 = 产物全被判成「还没做」，重跑一遍花第二份钱
  · 引擎层（服务商、诊断、并发、打包）两套共用，裁不干净
  · 包大小也省不下多少 —— 大头是 Python 运行时
"""
import importlib
import io
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class FlavorTests(unittest.TestCase):

    def tearDown(self):
        os.environ.pop("STV_SYSTEM", None)
        from core import build_info
        importlib.reload(build_info)

    def _boot(self, flavor):
        os.environ["STV_SYSTEM"] = flavor
        from core import build_info
        importlib.reload(build_info)
        from server import app
        importlib.reload(app)
        return app.api_get("/api/bootstrap", {})

    def test_no_flavor_offers_both(self):
        """源码方式跑、以及不带 --system 打的包 —— 两套都能建。"""
        b = self._boot("")
        self.assertEqual({k for k, v in b["systems"].items() if v["new_ok"]},
                         {"v34", "v61"})

    def test_a_flavor_limits_what_you_can_create(self):
        for sid in ("v34", "v61"):
            b = self._boot(sid)
            self.assertEqual([k for k, v in b["systems"].items() if v["new_ok"]],
                             [sid], sid)

    def test_both_stage_tables_are_still_served(self):
        """★ 限定了体系也要能打开对方建的老项目。

        拿不到环节表的话，那个项目在页面上会显示成「一个环节都没有」。
        """
        b = self._boot("v61")
        self.assertTrue(b["systems"]["v34"]["stages"])
        self.assertTrue(b["systems"]["v61"]["stages"])

    def test_new_projects_default_to_the_flavor(self):
        """★ 不跟着走的话，「通用版」那个包默认建出来是电影级 ——

        而体系建完不能改，等于白建一个项目。
        """
        os.environ["STV_SYSTEM"] = "v61"
        from core import build_info
        importlib.reload(build_info)
        from server import app
        importlib.reload(app)
        self.assertEqual(app._new_system(None), "v61")

    def test_the_flavor_name_is_reported(self):
        self.assertEqual(self._boot("v34")["flavor"], "电影级十七章")
        self.assertEqual(self._boot("")["flavor"], "全体系")


class PageTests(unittest.TestCase):

    def setUp(self):
        self.html = io.open(os.path.join(ROOT, "web", "index.html"),
                            encoding="utf-8").read()

    def test_the_picker_is_not_hardcoded(self):
        """★ 写死在 HTML 里就是这次的 bug 本身。"""
        self.assertIn("function fillSystemPicker", self.html)
        self.assertNotIn('<option value="v34">电影级十七章（V6.1）</option>',
                         self.html)

    def test_it_filters_by_new_ok(self):
        i = self.html.index("function fillSystemPicker")
        self.assertIn("new_ok", self.html[i:i + 600])

    def test_it_is_called_after_bootstrap(self):
        self.assertIn("fillSystemPicker();", self.html)


class PackerTests(unittest.TestCase):

    def _src(self):
        return io.open(os.path.join(ROOT, "打包exe.py"), encoding="utf-8").read()

    def test_it_accepts_a_system_flag(self):
        s = self._src()
        self.assertIn("--system", s)
        self.assertIn("电影级十七章", s)
        self.assertIn("通用十二环节", s)

    def test_the_stamp_is_always_restored(self):
        """★ 留着标记的话，之后源码方式跑也被限死 ——

        而那是最容易忽略的一种「我明明没改配置，怎么少了一套体系」。
        """
        s = self._src()
        i = s.index('if __name__ == "__main__":')
        tail = s[i:]
        self.assertIn("finally:", tail)
        self.assertIn('_stamp("")', tail)

    def test_the_checked_in_stamp_is_empty(self):
        """仓库里那份必须是空的 —— 不然源码方式跑就少一套。"""
        src = io.open(os.path.join(ROOT, "core", "build_info.py"),
                      encoding="utf-8").read()
        self.assertIn('SYSTEM = ""', src)


if __name__ == "__main__":
    unittest.main()
