# -*- coding: utf-8 -*-
"""程序自己出问题时，页面上必须看得见。

用户在另一台机器上报的：打开首页，项目列表一直「加载中」，
**exe 黑窗口和浏览器控制台都没有任何报错**。

那种「什么都不说」的状态最难查 —— 人不知道该看哪儿，只能猜。
这一组把四种失败方式各钉一条：

    请求挂住不返回   → fetch 既不 resolve 也不 reject，控制台一片干净
    后端 500        → 返回的是合法 JSON，页面当数据用，炸在别处
    返回不是 JSON    → 只报 \"Unexpected token\"，看不出后端说了什么
    异步调用没人接    → 静默失败，用户看到「点了没反应」
"""
import io
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class ErrorNetTests(unittest.TestCase):

    def setUp(self):
        self.html = io.open(os.path.join(ROOT, "web", "index.html"),
                            encoding="utf-8").read()

    def test_hung_requests_time_out(self):
        """★ 用户实际撞到的那一种。"""
        self.assertIn("AbortController", self.html)
        i = self.html.index("AbortError")
        self.assertIn("没有返回", self.html[i:i + 300])

    def test_backend_errors_are_thrown_not_used_as_data(self):
        """★ 后端出错返回的是合法 JSON —— 当数据用会炸在不相干的地方。"""
        i = self.html.index("const data = JSON.parse(text)")
        blk = self.html[i:i + 500]
        self.assertIn("!r.ok", blk)
        self.assertIn("data.error", blk)

    def test_non_json_shows_what_came_back(self):
        i = self.html.index("不是 JSON")
        self.assertIn("text.slice(0, 200)", self.html[i:i + 300])

    def test_unhandled_async_errors_are_caught(self):
        """★ boot() 之外还有一堆异步调用，抛了没人接就是静默失败。"""
        self.assertIn("unhandledrejection", self.html)
        self.assertIn("window.addEventListener('error'", self.html)

    def test_the_banner_says_the_page_may_be_stale(self):
        """★ 光报错不够 —— 要告诉人「你现在看到的可能不是最新的」。"""
        i = self.html.index("function showFault")
        self.assertIn("可能不是最新的", self.html[i:i + 700])

    def test_repeated_faults_do_not_spam(self):
        """一次失败常常连带好几个 —— 弹一屏横幅比不弹更糟。"""
        i = self.html.index("function showFault")
        self.assertIn("_faultShown", self.html[i:i + 400])

    def test_the_banner_can_be_dismissed(self):
        i = self.html.index("function showFault")
        self.assertIn("知道了", self.html[i:i + 900])


class BackendTests(unittest.TestCase):
    """后端那一侧：出错要有 traceback 落到黑窗口，并且回一个能读的 JSON。"""

    def test_handlers_print_the_traceback(self):
        src = io.open(os.path.join(ROOT, "server", "app.py"),
                      encoding="utf-8").read()
        # GET 和 POST 两条路径都要有 —— 只有一条的话另一条就是静默 500
        self.assertEqual(src.count("traceback.print_exc()"), 2)

    def test_errors_come_back_as_json_with_a_message(self):
        src = io.open(os.path.join(ROOT, "server", "app.py"),
                      encoding="utf-8").read()
        self.assertIn('self._json({"error": str(exc)}, 500)', src)


if __name__ == "__main__":
    unittest.main()
