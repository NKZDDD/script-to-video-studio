# -*- coding: utf-8 -*-
"""两个包要能同时开着 —— 所以默认端口按体系分开。

共用一个端口有两种坏法，第二种更坏：
  · 后开的起不来（至少看得见）
  · 后开的以为打开了，其实浏览器里是先开那套的页面 —— **不报错**，
    人会在电影级的页面上找通用的环节，怎么找都没有
"""
import importlib
import os
import socket
import unittest


class DefaultPortTests(unittest.TestCase):

    def tearDown(self):
        os.environ.pop("STV_SYSTEM", None)
        from core import build_info
        importlib.reload(build_info)

    def _port(self, flavor):
        os.environ["STV_SYSTEM"] = flavor
        from core import build_info
        importlib.reload(build_info)
        return build_info.default_port()

    def test_the_two_flavors_do_not_collide(self):
        self.assertNotEqual(self._port("v34"), self._port("v61"))

    def test_every_flavor_has_a_port(self):
        from core import build_info
        for f in ("", "v34", "v61"):
            self.assertIsInstance(self._port(f), int, f)


class FreePortTests(unittest.TestCase):
    """端口被占就顺延，不是崩。"""

    def test_it_steps_over_a_busy_port(self):
        """★ 崩掉的话人只看到一串 traceback，还得自己想到「换个端口」。"""
        from server.app import serve
        busy = socket.socket()
        busy.bind(("127.0.0.1", 0))
        port = busy.getsockname()[1]
        busy.listen(1)
        try:
            srv, got = serve("127.0.0.1", port)
            try:
                self.assertNotEqual(got, port)
                self.assertGreater(got, port)
            finally:
                srv.server_close()
        finally:
            busy.close()

    def test_it_returns_the_real_port(self):
        """★ 调用方必须拿真实端口拼 URL ——

        拿参数拼的话，顺延之后浏览器会打开一个没人在听的地址。
        """
        from server.app import serve
        srv, got = serve("127.0.0.1", 0)
        try:
            self.assertEqual(srv.server_address[1], got)
        finally:
            srv.server_close()

    def test_giving_up_says_what_to_do(self):
        from server.app import serve
        busy = socket.socket()
        busy.bind(("127.0.0.1", 0))
        port = busy.getsockname()[1]
        busy.listen(1)
        try:
            with self.assertRaises(OSError) as cm:
                serve("127.0.0.1", port, tries=1)
            self.assertIn("--port", str(cm.exception))
        finally:
            busy.close()


class RunWiringTests(unittest.TestCase):

    def test_run_py_uses_the_real_port(self):
        import io
        import os as _os
        root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
        src = io.open(_os.path.join(root, "run.py"), encoding="utf-8").read()
        self.assertIn("srv, port = serve(", src)
        self.assertIn("{port}/", src)
        # 默认值必须是 0（= 用这一版自己的端口），写死 8770 就又撞上了
        self.assertIn('"--port", type=int, default=0', src)


if __name__ == "__main__":
    unittest.main()
