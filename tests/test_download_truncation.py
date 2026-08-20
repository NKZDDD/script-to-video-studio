# -*- coding: utf-8 -*-
"""长下载「干净地少给一半」—— 视频这一类以前没人查。

用户问的是 0KB。**0KB 不会有**：http 那条路先写 `.part` 再原子改名，
断在中途会抛异常，`finally` 把 `.part` 删掉，最终路径上什么都不会出现。
那一段本来就是为视频加的（注释原话：「视频几十 MB，断过」）。

但还有一种它防不住的：**连接正常关闭、只是少给了一半**。
有些 CDN / 代理在长传输上会这么干 —— `iter_content` 不抛异常就结束了，
于是我们改名收下一个半截文件。

图片有末尾标记兜底（PNG 的 IEND、JPEG 的 FFD9），**视频没有** ——
`_END_MARK` 表里只有 .png/.jpg，.mp4 走到那里直接放行。
而半截的 mp4 往往还能播，只是短了几秒：拼接出来的成片少一段，
`MIN_BYTES` 那道线（512 字节）也轻松通过，**全程没有任何报错**。

所以拿服务商自报的 `Content-Length` 核对一遍。
"""
import io
import os
import shutil
import tempfile
import unittest

from core.apiutil import MEDIA_VIDEO, RETRYABLE, ApiError, _END_MARK


class _Resp:
    """假响应：按 body 分块吐，headers 里的 Content-Length 可以撒谎。"""

    status_code = 200

    def __init__(self, body: bytes, declared=None):
        self._body = body
        self.headers = {}
        if declared is not None:
            self.headers["Content-Length"] = str(declared)

    def raise_for_status(self):
        pass

    def iter_content(self, chunk_size=1):
        for i in range(0, len(self._body), chunk_size):
            yield self._body[i:i + chunk_size]


class _Session:
    """够 save_item 用的假会话。"""

    base_url = "https://cdn.example"
    timeout = 30

    def __init__(self, resp):
        self.resp = resp

    def _headers(self):
        return {}

    def _proxies(self):
        return None


def _save(body: bytes, declared, name="x.mp4"):
    """跑一遍真实的落盘路径，返回 (最终文件在不在, 异常)。"""
    import requests

    from core import apiutil as A

    tmp = tempfile.mkdtemp()
    dest = os.path.join(tmp, name)
    orig = requests.get
    requests.get = lambda *a, **kw: _Resp(body, declared)
    try:
        sess = A.HttpSession.__new__(A.HttpSession)          # 不走 __init__
        sess.base_url = "https://cdn.example"
        sess.timeout = 30
        sess._headers = lambda: {}
        sess._proxies = lambda: None
        try:
            A.HttpSession._save_once(sess, "https://cdn.example/a.mp4", dest)
            return os.path.exists(dest), None
        except Exception as exc:                     # noqa: BLE001
            return os.path.exists(dest), exc
    finally:
        requests.get = orig
        shutil.rmtree(tmp, ignore_errors=True)


class TruncationTests(unittest.TestCase):

    def test_a_short_body_against_a_declared_length_fails(self):
        """★ 这就是那个没人查的情况：连接正常关闭、只给了一半。"""
        exists, exc = _save(b"\x00" * 5000, declared=20000)
        self.assertIsInstance(exc, ApiError)
        self.assertIn("下载没下完", str(exc))
        self.assertFalse(exists, "半截文件不许留在最终路径上")

    def test_it_says_why_there_was_no_network_error(self):
        """★ 不说清的话，人会去查网络 —— 而连接是正常关闭的。"""
        _, exc = _save(b"\x00" * 5000, declared=20000)
        self.assertIn("连接是正常关闭的", str(exc))

    def test_it_says_the_silent_consequence(self):
        _, exc = _save(b"\x00" * 5000, declared=20000)
        self.assertIn("成片会少一段", str(exc))

    def test_it_is_retryable(self):
        """★ 传输短一截多半是一次性的，重发经常就好 —— 别判成不可重试。"""
        _, exc = _save(b"\x00" * 5000, declared=20000)
        self.assertEqual(getattr(exc, "kind", None), RETRYABLE)

    def test_a_complete_body_is_kept(self):
        exists, exc = _save(b"\x00" * 20000, declared=20000)
        self.assertIsNone(exc)
        self.assertTrue(exists)

    def test_more_than_declared_is_not_an_error(self):
        """有的家会把 Content-Length 报小（压缩、分块）。多给不算错。"""
        exists, exc = _save(b"\x00" * 20000, declared=19000)
        self.assertIsNone(exc)
        self.assertTrue(exists)

    def test_no_declared_length_still_works(self):
        """★ 分块传输没有 Content-Length —— 那时候核不了，但不能因此失败。"""
        exists, exc = _save(b"\x00" * 20000, declared=None)
        self.assertIsNone(exc)
        self.assertTrue(exists)

    def test_a_garbage_header_is_ignored(self):
        exists, exc = _save(b"\x00" * 20000, declared="不是数字")
        self.assertIsNone(exc)
        self.assertTrue(exists)


class WhyVideoNeededItTests(unittest.TestCase):
    """把「视频没有末尾标记兜底」这个前提钉住。"""

    def test_video_has_no_end_marker(self):
        """★ 这是为什么必须核对字节数：图片那道兜底对视频不生效。

        哪天有人给 .mp4 加了末尾标记检查，这条会红 ——
        那时候可以回来看看字节数核对还要不要留（两道都留也没坏处）。
        """
        for ext in MEDIA_VIDEO:
            self.assertNotIn(ext, _END_MARK, ext)

    def test_images_do_have_one(self):
        self.assertIn(".png", _END_MARK)
        self.assertIn(".jpg", _END_MARK)

    def test_zero_byte_was_already_handled(self):
        """★ 用户问的是 0KB —— 那个早就修了，靠 .part + 原子改名。

        钉住它别被改回去：直接写 dest 的话，断在中途会留下一个
        「够大但不完整」的文件，而 isfile 为真 → 下次永远跳过。
        """
        import inspect

        from core import apiutil as A
        src = inspect.getsource(A.HttpSession._save_once)
        self.assertIn('part = dest + ".part"', src)
        self.assertIn("os.replace(part, dest)", src)
        i = src.index("os.replace(part, dest)")
        self.assertIn("finally", src[i:i + 200])


if __name__ == "__main__":
    unittest.main()
