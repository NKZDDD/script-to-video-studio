# -*- coding: utf-8 -*-
"""0 字节的产物是最坏的一种失败 —— 它不报错，而且会永远跳过自己。

V6.1 实跑撞到：一个资产图出来了，文件 0KB。链路是这样的：

  服务商说任务成功、给了下载链接 → 链接返回 200 但 body 是空的
  → 我们建出一个 0KB 文件 → 注册表记成 generated
  → 比例检查量不出尺寸所以也不吭声
  → 下一次跑 os.path.isfile() 是真，**这一条永远被跳过**

结果：进度显示 100%，成片里那一段永远缺着。

两侧都要堵：
  · 落盘那一刻验大小，不合格就**删掉**并报错（留着会被当成做过了）
  · 「做过没有」的判据从「文件在不在」换成「文件够不够大」，
    这样盘上已经存在的空壳会被自动重做
"""
import os
import shutil
import tempfile
import unittest

from core import apiutil, probe


class SaveGuardTests(unittest.TestCase):
    """落盘那一侧。"""

    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _save(self, raw: bytes):
        import base64
        c = apiutil.ApiClient("https://example.com", "k") \
            if hasattr(apiutil, "ApiClient") else None
        dest = os.path.join(self.dir, "out.png")
        # 直接测校验本身，不去构造一个真的 HTTP 客户端
        with open(dest, "wb") as f:
            f.write(raw)
        apiutil._check_saved(dest, "https://example.com/x.png")
        return dest

    def test_an_empty_file_is_refused(self):
        with self.assertRaises(apiutil.ApiError) as cm:
            self._save(b"")
        self.assertIn("0 字节", str(cm.exception))

    def test_the_empty_file_is_deleted_not_left_behind(self):
        """★ 留着比不留更糟 —— 下次会被当成「已经做过了」跳过。"""
        dest = os.path.join(self.dir, "out.png")
        with open(dest, "wb") as f:
            f.write(b"")
        with self.assertRaises(apiutil.ApiError):
            apiutil._check_saved(dest, "x")
        self.assertFalse(os.path.exists(dest), "空文件必须删掉")

    def test_an_error_page_saved_as_an_image_is_refused(self):
        """★ 几十字节的 JSON/HTML 错误页被当成图存下来，也是这一类。"""
        with self.assertRaises(apiutil.ApiError) as cm:
            self._save(b'{"error":"quota exceeded"}')
        self.assertIn("quota exceeded", str(cm.exception),
                      "得把文件内容回显出来，不然没法拿去问服务商")

    def test_the_message_carries_the_source_url(self):
        """★ 服务商会问「你用什么调的」—— 那个下载链接就是要给他们的东西。"""
        with self.assertRaises(apiutil.ApiError) as cm:
            self._save(b"")
        self.assertIn("https://example.com/x.png", str(cm.exception))

    def test_a_real_file_passes(self):
        self._save(b"\x89PNG\r\n\x1a\n" + b"\x00" * 2000)


class DoneCheckTests(unittest.TestCase):
    """「做过没有」那一侧。"""

    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _mk(self, name, size):
        p = os.path.join(self.dir, name)
        with open(p, "wb") as f:
            f.write(b"\x00" * size)
        return p

    def test_a_zero_byte_file_does_not_count_as_done(self):
        """★ 盘上已经存在的空壳要能被自动重做。"""
        self.assertFalse(probe.have_output(self._mk("a.png", 0)))

    def test_a_tiny_file_does_not_count_as_done(self):
        self.assertFalse(probe.have_output(self._mk("b.png", 100)))

    def test_a_real_file_counts(self):
        self.assertTrue(probe.have_output(self._mk("c.png", 50_000)))

    def test_a_missing_file_does_not_count(self):
        self.assertFalse(probe.have_output(os.path.join(self.dir, "nope.png")))


class WiringTests(unittest.TestCase):
    """改了一处不算改 —— 判据散在好几个文件里，漏一处那一处照旧跳过。"""

    FILES = ("core/pipeline.py", "core/pipeline_v34.py", "core/stages.py",
             "core/produce.py", "server/app.py")

    def test_no_one_still_decides_done_by_existence_alone(self):
        import io
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for rel in self.FILES:
            src = io.open(os.path.join(root, rel), encoding="utf-8").read()
            for bad in ('os.path.isfile(pj.p(*t["output"]',
                        'os.path.isfile(pj.p(*v["file_ref"]',
                        "os.path.isfile(out)"):
                self.assertNotIn(bad, src,
                                 f"{rel} 还在只看文件在不在，0 字节的会被当成做好了")


if __name__ == "__main__":
    unittest.main()
