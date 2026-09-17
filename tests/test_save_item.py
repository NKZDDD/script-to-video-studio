# -*- coding: utf-8 -*-
"""落盘这一步不许留下半成品。

用户实跑撞到（超模出图，8 张资产全军覆没）：

    [20:35:37] S001: 状态: completed
    [20:35:37] S001: 这次没成功: Incorrect padding
    [20:35:37] S001: 第 1 次重试（最多 2 次）

三个毛病叠在一起：

  1. **文件先建、再解码。** `open(dest,"wb")` 已经把文件截成 0 字节，
     `b64decode` 才抛异常 —— 磁盘上留下一个 0KB 的「图」。
  2. **解码太脆。** 少个 `=`、换行、URL-safe 字母表、嵌套的 data: 前缀，
     任何一样都是 `Incorrect padding`。
  3. **这种错还自动重试两次。** 每次要重出一张图（两分半），
     八张图跑掉半小时，最后既没有图，也没有一句能拿去问服务商的话 ——
     报错全文就是 `Incorrect padding` 四个字，不说它在解什么。
"""
import base64
import os
import shutil
import tempfile
import unittest

from core.apiutil import MIN_BYTES, ApiError, HttpSession

# 结尾要带 IEND —— 落盘那一步现在会查图片的收尾标记（防残图）。
# 这几条用例测的是 base64 解码，不是图片完整性，补上标记让它们只测自己那件事。
PNG = (b"\x89PNG\r\n\x1a\n" + b"x" * (MIN_BYTES * 2)
       + b"IEND" + bytes([0xAE, 0x42, 0x60, 0x82]))
B64 = base64.b64encode(PNG).decode()


class SaveItemTests(unittest.TestCase):

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.dest = os.path.join(self.d, "C001.png")
        self.s = HttpSession("k", "https://x")

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def _save(self, item):
        return self.s.save_item(item, self.dest)

    # ------------------------------------------------ 能解的都要解出来
    def test_a_plain_data_uri(self):
        self._save("data:image/png;base64," + B64)
        self.assertEqual(open(self.dest, "rb").read(), PNG)

    def test_bare_base64(self):
        self._save(B64)
        self.assertEqual(open(self.dest, "rb").read(), PNG)

    def test_line_wrapped_base64(self):
        """有的家把 b64 折行发过来。"""
        wrapped = "\n".join(B64[i:i + 76] for i in range(0, len(B64), 76))
        self._save("data:image/png;base64," + wrapped)
        self.assertEqual(open(self.dest, "rb").read(), PNG)

    def test_missing_padding(self):
        """★ `Incorrect padding` 最常见的来源：结尾的 = 被剥掉了。"""
        self._save("data:image/png;base64," + B64.rstrip("="))
        self.assertEqual(open(self.dest, "rb").read(), PNG)

    def test_url_safe_alphabet(self):
        u = base64.urlsafe_b64encode(PNG).decode().rstrip("=")
        self._save(u)
        self.assertEqual(open(self.dest, "rb").read(), PNG)

    def test_a_nested_data_prefix(self):
        """b64_json 里塞的本来就是整个 data URI，我们又拼了一次前缀。"""
        self._save("data:image/png;base64,data:image/png;base64," + B64)
        self.assertEqual(open(self.dest, "rb").read(), PNG)

    # ------------------------------------------------ 解不动的时候
    def test_no_zero_byte_file_is_left_behind(self):
        """★ 这就是那 8 个 0KB 文件。"""
        with self.assertRaises(ApiError):
            self._save("data:image/png;base64,这根本不是 base64 %%%")
        self.assertFalse(os.path.exists(self.dest), "留下了一个空壳文件")

    def test_the_error_says_what_came_back(self):
        """★ 「Incorrect padding」没法拿去问服务商 —— 得说清收到的是什么。"""
        with self.assertRaises(ApiError) as cm:
            self._save("data:image/png;base64,【生成失败，请重新提交】")
        m = str(cm.exception)
        self.assertIn("生成失败", m, "要把原文摘出来")
        self.assertIn("字符", m)
        self.assertIn("重试", m, "要写明重试没用")

    def test_garbage_that_happens_to_decode_is_still_traceable(self):
        """一段 HTML 错误页里的字母也能"解"出几十字节垃圾 —— 走的是大小检查。

        那条以前只说「拿链接去问服务商」，而内嵌数据**根本没有链接**，
        人会去翻一个不存在的东西。现在把原文带上。
        """
        with self.assertRaises(ApiError) as cm:
            self._save("<html>cdn.example.com 502 Bad Gateway</html>")
        m = str(cm.exception)
        self.assertIn("cdn.example.com", m, "要把原文摘出来")
        self.assertNotIn("下载地址", m, "内嵌数据没有下载地址可查")
        self.assertFalse(os.path.exists(self.dest))

    def test_it_is_not_retried(self):
        """★ 每重一次要重新出一张图。格式对不上，重一百次也是同一句话。"""
        from core.apiutil import TASK_FATAL
        with self.assertRaises(ApiError) as cm:
            self._save("data:image/png;base64,%%%%")
        self.assertEqual(cm.exception.kind, TASK_FATAL)

    def test_a_short_but_valid_decode_is_still_refused(self):
        """原有的大小检查不能被上面几条改掉：解得开但太小，一样不算图。"""
        with self.assertRaises(ApiError) as cm:
            self._save(base64.b64encode(b"tiny").decode())
        self.assertIn("字节", str(cm.exception))
        self.assertFalse(os.path.exists(self.dest))


class PartialDownloadTests(unittest.TestCase):
    """下到一半断了，不能留下一个「够大但不完整」的文件。

    大小检查只拦 0 字节那种。一个下了 8MB 就断掉的视频能过检查，
    下次 `isfile` 为真于是**永远跳过** —— 成片里那一段是坏的，而进度 100%。
    """

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.dest = os.path.join(self.d, "SEG01.mp4")

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_a_broken_download_leaves_nothing(self):
        import core.apiutil as A

        class Resp:
            status_code = 200
            headers: dict = {}

            def raise_for_status(self):
                pass

            def iter_content(self, chunk_size=0):
                yield bytes(4) + b"ftypisom" + b"v" * (MIN_BYTES * 4)
                raise OSError("connection reset")

        old = A.requests.get
        A.requests.get = lambda *a, **k: Resp()
        try:
            with self.assertRaises(OSError):
                A.HttpSession("k", "https://x").save_item(
                    "https://cdn.example.com/a.mp4", self.dest)
        finally:
            A.requests.get = old
        self.assertFalse(os.path.exists(self.dest), "留下了半个文件")
        self.assertFalse(os.path.exists(self.dest + ".part"), "临时文件没清掉")


class AuthFallbackTests(unittest.TestCase):
    """鉴权头要**两个方向都能回退**。

    实遇 2026-09-07（云会画 ai.yunhuiart.cn）：`/v1/videos/{id}/content`
    302 到一个**预签名地址** —— query 串里就是凭证。跳转目标同主机时
    requests 不会替我们摘掉 Authorization（只有跨主机才摘），于是存储那边
    拿这个头去验、然后拒掉：

        502 {"code":"artifact_request_rejected",
             "message":"Artifact redirect was rejected"}

    而那会儿任务已经 completed、**已经计费** —— 取不回来是最亏的一种失败。

    原来这里只有「没带 → 带上」一个方向，这个方向缺着。
    """

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.dest = os.path.join(self.d, "SEG01.mp4")

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def _run(self, url, judge):
        """judge(headers) -> status_code。记下每次带的头，返回它判的状态码。"""
        import core.apiutil as A
        seen = []

        class Resp:
            def __init__(self, code):
                self.status_code = code
                self.headers = {}

            def raise_for_status(self):
                if self.status_code >= 400:
                    raise AssertionError(f"HTTP {self.status_code}")

            def iter_content(self, chunk_size=0):
                yield bytes(4) + b"ftypisom" + b"v" * (MIN_BYTES * 4)

            def close(self):
                pass

        def fake_get(u, headers=None, **kw):
            seen.append(headers)
            return Resp(judge(headers))

        old = A.requests.get
        A.requests.get = fake_get
        try:
            A.HttpSession("k", "https://x").save_item(url, self.dest)
        finally:
            A.requests.get = old
        return seen

    def test_signed_url_on_our_own_host_retries_without_auth(self):
        """同主机的签名地址：第一次带 Bearer 被拒，第二次不带就过。"""
        seen = self._run(
            "https://x/v1/videos/t1/content",
            lambda h: 502 if (h or {}).get("Authorization") else 200)
        self.assertEqual(len(seen), 2, f"应该回退一次，实际发了 {len(seen)} 次")
        self.assertIn("Authorization", seen[0], "第一次本该带鉴权（是本站地址）")
        self.assertNotIn("Authorization", seen[1] or {}, "第二次不该再带鉴权")
        self.assertTrue(os.path.exists(self.dest), "回退之后没落盘")

    def test_a_foreign_url_that_needs_auth_still_gets_it(self):
        """反方向不能被弄坏：外站地址第一次不带，被拒了要带上再试。"""
        seen = self._run(
            "https://cdn.example.com/a.mp4",
            lambda h: 200 if (h or {}).get("Authorization") else 401)
        self.assertEqual(len(seen), 2)
        self.assertIsNone(seen[0], "外站地址第一次本该不带头")
        self.assertIn("Authorization", seen[1], "被拒之后本该带上鉴权重试")
        self.assertTrue(os.path.exists(self.dest))

    def test_it_does_not_send_a_second_request_when_the_first_works(self):
        """能过的时候别多发一次 —— 视频几十 MB，白下一遍不是小事。"""
        seen = self._run("https://x/v1/videos/t1/content", lambda h: 200)
        self.assertEqual(len(seen), 1)

    def test_download_does_not_send_the_json_headers(self):
        """**下载不是 JSON 调用，不该带 JSON 的头。**

        `_headers()` 里有 `Accept: application/json` 和
        `Content-Type: application/json`。拿它去 GET 一个视频是自相矛盾的
        （一边说只收 JSON，一边等二进制），而 GET 带 Content-Type 本身就不合
        常理，网关有拒的。

        实遇 2026-09-07（云绘 AI）：`/v1/videos/{id}/content` 用 JSON 那份头
        请求，回 502 artifact_request_rejected「Artifact redirect was rejected」；
        而它文档的官方示例只带一个 Authorization，那样能用。
        那会儿任务已经 completed、**已经计费** —— 取不回来是最亏的一种失败。
        """
        seen = self._run("https://x/v1/videos/t1/content", lambda h: 200)
        h = seen[0] or {}
        self.assertIn("Authorization", h, "本站地址还是要带鉴权（/content 常是要鉴权的代理）")
        self.assertNotIn("Content-Type", h, "下载带 Content-Type: 有网关会拒")
        self.assertNotEqual(h.get("Accept"), "application/json",
                            "下载声明只收 JSON，那对方回二进制算什么")


if __name__ == "__main__":
    unittest.main()
