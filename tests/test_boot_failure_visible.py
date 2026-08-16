# -*- coding: utf-8 -*-
"""启动失败必须显示出来，不能永远停在「加载中…」。

用户报的：两个 exe 同时开着时，页面显示「项目加载中」、设置点不动，
**而那个黑色的 exe 窗口什么都没报**。

原因结构上很清楚：boot() 是裸调的，中间任何一步抛异常，
后面的渲染（项目列表、设置页、服务商、优先级链）全都不跑，
页面就冻在初始占位符上。而 JS 的异常只进浏览器控制台 ——
exe 那个窗口是 Python 的，永远不会打出来。

**症状和原因隔着一层**：人看到的是「卡住了」，真正的错在另一个窗口里。
这一条修的不是那个未知的异常本身，是让它别再隐形。
"""
import io
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class BootFailureTests(unittest.TestCase):

    def setUp(self):
        self.html = io.open(os.path.join(ROOT, "web", "index.html"),
                            encoding="utf-8").read()

    def test_boot_is_not_called_bare(self):
        """★ 裸调 boot() = 出错时静默冻住。"""
        self.assertNotIn("\nboot();", self.html)
        self.assertIn("boot().catch(", self.html)

    def test_the_error_is_shown_on_the_page(self):
        """★ 只 console.error 不够 —— 用户看的是页面，不是 F12。"""
        i = self.html.index("boot().catch(")
        blk = self.html[i:i + 1400]
        self.assertIn("alert err", blk)
        self.assertIn("没能启动完", blk)

    def test_it_warns_against_using_a_half_loaded_page(self):
        """★ 半个页面最危险：设置看着能改，实际存的是残缺状态。"""
        i = self.html.index("boot().catch(")
        self.assertIn("别在这个状态下改设置", self.html[i:i + 1400])

    def test_the_stuck_placeholder_is_replaced(self):
        """「加载中…」挂着不动，人会一直等。要如实改成「没加载出来」。"""
        i = self.html.index("boot().catch(")
        blk = self.html[i:i + 1400]
        self.assertIn("projList", blk)
        self.assertIn("没加载出来", blk)

    def test_the_error_text_is_escaped(self):
        """异常消息里可能带 < > —— 直接塞进 innerHTML 会把页面搞坏。"""
        i = self.html.index("boot().catch(")
        self.assertIn("esc(String(err", self.html[i:i + 1400])


class TimeoutTests(unittest.TestCase):
    """请求挂住不返回时，也要能说出话来。

    这是最难查的一种：fetch 既不 resolve 也不 reject，
    **浏览器控制台一片干净**，页面停在「加载中…」——
    用户在另一台机器上报的就是这个，两边控制台都没有报错。
    """

    def setUp(self):
        self.html = io.open(os.path.join(ROOT, "web", "index.html"),
                            encoding="utf-8").read()

    def test_requests_have_a_timeout(self):
        self.assertIn("AbortController", self.html)
        self.assertIn("API_TIMEOUT", self.html)

    def test_the_timeout_message_names_the_endpoint(self):
        """★ 只说「超时了」没用 —— 要说清是哪个接口、等了多久。"""
        i = self.html.index("AbortError")
        blk = self.html[i:i + 300]
        self.assertIn("没有返回", blk)
        self.assertIn("黑窗口", blk)      # 指向真正有 traceback 的地方

    def test_non_json_replies_show_what_came_back(self):
        """★ 「Unexpected token」看不出任何东西，要把响应开头带出来。"""
        i = self.html.index("不是 JSON")
        self.assertIn("text.slice(0, 200)", self.html[i:i + 300])


if __name__ == "__main__":
    unittest.main()
