# -*- coding: utf-8 -*-
"""没有故事板就不出片；空文件也算没有。

闸门改成「只记不拦」之后要回答的第一个问题：那视频还会不会在没有故事板的
情况下硬出？答案是不会 —— 出片那一步自己有一道硬检查，跟闸门是两回事。

但那道检查原来用的是 `os.path.isfile`，**0 字节和下了一半的文件都算存在**。
后果是这一整套里最贵的一种：

  · 出片是最花钱的一步
  · 拿一张空图当参考，模型等于没有参考，出来的人不是本人
  · 任务标 ok，只能靠肉眼在几百段里发现
  · 配了对象存储时更彻底 —— 那个 0 字节文件会被原样传上去再给服务商，
    连解码失败都不会有

参考图那条路同样：三条分支（本机路径 / data URI / 传对象存储）以前各走各的，
0 字节在每一条上都过得去。
"""
import os
import shutil
import unittest

from core import produce as P
from core.apiutil import MIN_BYTES, ApiError
from test_v34_run import new_project

def _png() -> bytes:
    """一张**真的**能被解码的图。

    拼几个字节冒充 PNG 是不行的 —— 转 data URI 那一步会用 Pillow 打开它，
    假的会在那儿炸，测出来的就不是我们要测的东西了。
    """
    import io as _io
    import random

    from PIL import Image
    # 纯色图压得太小（64×96 只有 233 字节），过不了「至少 512 字节」那道检查。
    # 用噪点让它压不动 —— 真实的出图产物是几十 KB，这里要像一点。
    rnd = random.Random(7)
    img = Image.new("RGB", (96, 128))
    img.putdata([(rnd.randrange(256), rnd.randrange(256), rnd.randrange(256))
                 for _ in range(96 * 128)])
    buf = _io.BytesIO()
    img.save(buf, format="PNG")
    raw = buf.getvalue()
    assert len(raw) >= MIN_BYTES, len(raw)
    return raw


PNG = _png()


class Prov:
    """够 worker 用的最小服务商。"""

    def needs_url(self, model="", media="image"):
        return False

    def needs_bytes(self, model=""):
        return False

    def accepts_url(self, model="", media="image"):
        return False


class StoryboardTests(unittest.TestCase):

    def setUp(self):
        self.pj = new_project()
        self.rel = "05_故事板/EP01-SEG01.png"
        # build_provider 认不出 "fake"，会在建 worker 时就抛 —— 那时候
        # 还没走到我们要测的那道检查
        self._orig = P.build_provider
        P.build_provider = lambda *a, **k: Prov()

    def tearDown(self):
        P.build_provider = self._orig
        shutil.rmtree(self.pj.root, ignore_errors=True)

    def _write(self, data):
        p = self.pj.p(*self.rel.split("/"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(data)

    def _run(self):
        w = P.make_video_worker(self.pj, {"provider": "fake", "api_key": "k",
                                          "model": "m"})
        return w({"key": "EP01-SEG01", "output": "06_视频/EP01-SEG01.mp4",
                  "storyboard_ref": self.rel,
                  "prompt_ref": "03_提示词/视频提示词/EP01-SEG01.txt",
                  "params": {}}, lambda *a: None, lambda: False)

    def test_no_storyboard_at_all_stops_it(self):
        with self.assertRaises(RuntimeError) as cm:
            self._run()
        self.assertIn("故事板", str(cm.exception))

    def test_an_empty_storyboard_stops_it_too(self):
        """★ 这就是那个洞：0 字节也是「文件存在」。"""
        self._write(b"")
        with self.assertRaises(RuntimeError) as cm:
            self._run()
        self.assertIn("空文件", str(cm.exception))

    def test_a_half_downloaded_storyboard_stops_it(self):
        self._write(b"\x89PNG\r\n")
        with self.assertRaises(RuntimeError):
            self._run()

    def test_the_message_says_what_to_do(self):
        with self.assertRaises(RuntimeError) as cm:
            self._run()
        self.assertIn("环节9", str(cm.exception))

    def test_a_real_storyboard_gets_past_this_check(self):
        """★ 别拦过头：正常的故事板要走得过去。"""
        self._write(PNG)
        with self.assertRaises(Exception) as cm:
            self._run()          # 后面会因为没有提示词文件之类而挂，那是另一回事
        self.assertNotIn("故事板", str(cm.exception))


class RefTests(unittest.TestCase):
    """参考图：0 字节在三条分支上都得被拦住。"""

    def setUp(self):
        self.pj = new_project()
        self.rel = "02_固定资产/角色/C001.png"

    def tearDown(self):
        shutil.rmtree(self.pj.root, ignore_errors=True)

    def _write(self, data):
        p = self.pj.p(*self.rel.split("/"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(data)

    def _resolve(self):
        return P.make_ref_resolver(self.pj, Prov(), {}, "m", 1024)(
            self.rel, log=lambda *a: None)

    def test_an_empty_reference_is_refused(self):
        """★ 传上去是个空对象，服务商看到的是「有参考图」而实际什么都没有。"""
        self._write(b"")
        with self.assertRaises(ApiError) as cm:
            self._resolve()
        self.assertIn("空文件", str(cm.exception))

    def test_a_missing_reference_is_refused(self):
        with self.assertRaises(ApiError):
            self._resolve()

    def test_it_still_points_at_the_real_cause(self):
        """★ 别把上一版那条「指向真正原因」弄丢了。"""
        from core import diagnose
        diagnose.record(self.pj.root, diagnose.warn(
            "QUOTA_EXHAUSTED", "HTTP 429 No available image quota",
            stage="asset", target="C001"))
        self._write(b"")
        with self.assertRaises(ApiError) as cm:
            self._resolve()
        self.assertIn("C001 自己没出成", str(cm.exception))

    def test_a_real_reference_passes(self):
        self._write(PNG)
        self.assertTrue(self._resolve().startswith("data:"))

    def test_an_http_reference_is_left_alone(self):
        r = P.make_ref_resolver(self.pj, Prov(), {}, "m", 1024)(
            "https://cdn.example.com/a.png", log=lambda *a: None)
        self.assertEqual(r, "https://cdn.example.com/a.png")


if __name__ == "__main__":
    unittest.main()
