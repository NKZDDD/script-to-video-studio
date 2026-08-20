# -*- coding: utf-8 -*-
"""手动放图：把自己的图放进某条出图任务的位置。

用户问：「我现在如果是需要手动加入资产图怎么办」。以前的答案是「没有入口」——
只能自己往 `02_固定资产/<家族目录>/<ASSET_ID>_R01.png` 拷。
两个坑都很静默：

  · **家族目录名要猜对**（人物身份资产 / 人物造型资产 / 连续状态资产 /
    场景资产 / 道具资产…）。猜错了图放在那儿也没人读 —— 出图那步照样
    重新生成一张，把人工挑的那张顶掉，而且不报错。
  · 已经出过图的资产**原地换文件**会被指纹校验拦住。那道校验是对的：
    引用过旧那张的故事板还指着旧文件，原地换的话它们用的图变了、
    引用没变，没有一处会报错。

所以路径由后端从 tasks.json 里取（页面一个字都不拼），
已经有图的资产建新版本而不是覆盖。
"""
import base64
import io
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server"))

import app as A                                          # noqa: E402
from core import registry_v34 as REG                     # noqa: E402
from core.store import Project                           # noqa: E402


def a_png(color=(30, 60, 90), size=(256, 256)) -> str:
    """一张**够大的**图。

    别用 64x64 纯色：那种只有三百来字节，比 `probe.MIN_OUTPUT_BYTES`
    还小，会被判成「还没出」—— 夹具太小就会让这些测试测的是另一件事。
    """
    import random
    from PIL import Image
    im = Image.new("RGB", size, color)
    rnd = random.Random(sum(color))          # 固定种子：同色同图，可复现
    px = im.load()
    for i in range(0, size[0], 3):           # 加点噪声，免得 PNG 把纯色压到极小
        for j in range(0, size[1], 3):
            px[i, j] = (rnd.randrange(256), rnd.randrange(256), rnd.randrange(256))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode()


class ManualPutTests(unittest.TestCase):

    def setUp(self):
        self.pj = Project(tempfile.mkdtemp(prefix="manual-"))
        self.pj.init_dirs()
        self.pj.save_meta({"project_code": "T", "system": "v34"})
        self.pj.save_tasks({"system": "v34", "asset_tasks": [
            {"key": "C001", "output": "02_固定资产/人物身份资产/C001_R01.png",
             "prompt_ref": "03_提示词/x.txt", "reference_images": [],
             "params": {"size": "1024x1536"}}],
            "storyboard_tasks": [
                {"key": "EP01-SEG01", "output": "04_故事板/T_EP01-SEG01_STORYBOARD.png",
                 "prompt_ref": "03_提示词/y.txt", "reference_images": [],
                 "params": {}}],
            "scstate_tasks": [], "video_tasks": []})

    def tearDown(self):
        shutil.rmtree(self.pj.root, ignore_errors=True)

    _NONE = object()

    def _put(self, kind, key, b64=_NONE):
        # **不能写 `b64 or a_png()`** —— 空字符串是这些测试里的一个用例
        # （「没收到内容」），被 `or` 换成一张真图就测不到了。
        content = a_png() if b64 is self._NONE else b64
        return A.api_post("/api/task/manual",
                          {"project_root": self.pj.root, "kind": kind, "key": key,
                           "filename": "x.png", "content_b64": content})

    # ---- 路径 ----

    def test_it_lands_on_the_path_the_task_declares(self):
        """★ 这就是那个坑：路径必须来自 tasks.json，不许页面或这里另拼一份。

        另拼一份就得复制「家族 → 目录名」那张表（它在 run_v34 里），
        两份迟早对不上 —— 而对不上的表现是图放在没人读的位置。
        """
        r = self._put("asset_tasks", "C001")
        self.assertEqual(r["file"], "02_固定资产/人物身份资产/C001_R01.png")
        self.assertTrue(os.path.isfile(self.pj.p(*r["file"].split("/"))))

    def test_the_produce_step_will_now_skip_it(self):
        """★ 放进去的意义就在这儿：不再生成、不再花钱。"""
        from core import probe
        self._put("asset_tasks", "C001")
        self.assertTrue(probe.have_output(
            self.pj.p("02_固定资产", "人物身份资产", "C001_R01.png")))

    def test_an_unknown_key_says_what_is_available(self):
        """★ 「没有这个任务」要说清当前有哪些，否则人会以为是放图坏了。"""
        with self.assertRaises(ValueError) as e:
            self._put("asset_tasks", "C999")
        self.assertIn("C001", str(e.exception))
        self.assertIn("先把文字环节跑完", str(e.exception))

    # ---- 不是图片 ----

    def test_a_file_that_is_not_an_image_is_refused(self):
        """★ 改了扩展名的文件放进去之后，「出没出」只看大小，会判成已出。

        然后下游把它当参考图发给服务商，报的是一句服务商的解码错误 ——
        看不出是这张图的事。
        """
        with self.assertRaises(ValueError) as e:
            self._put("asset_tasks", "C001",
                      base64.b64encode("这不是图片".encode() * 100).decode())
        self.assertIn("不是能读的图片", str(e.exception))
        self.assertFalse(os.path.isfile(
            self.pj.p("02_固定资产", "人物身份资产", "C001_R01.png")))

    def test_an_image_too_small_to_count_is_refused(self):
        """★ 「做出来了没有」全程只看文件在不在加一个体积下限。

        比那个下限还小的图放进去会被判成「还没出」，
        然后出图那一步生成一张把它盖掉 —— 图在盘上、被顶掉了、一句话都没有。
        """
        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGB", (8, 8), (0, 0, 0)).save(buf, "PNG")
        with self.assertRaises(ValueError) as e:
            self._put("asset_tasks", "C001",
                      base64.b64encode(buf.getvalue()).decode())
        self.assertIn("还小", str(e.exception))
        self.assertIn("盖掉", str(e.exception))

    def test_an_empty_body_is_refused(self):
        with self.assertRaises(ValueError):
            self._put("asset_tasks", "C001", "")

    # ---- 指纹 ----

    def test_it_registers_the_fingerprint(self):
        """★ 不登记的话参考图解析找不到它，报的是「注册表里没有这个资产」。"""
        REG.sync(self.pj, [{"asset_id": "C001", "family": "CHAR", "name": "甲"}])
        r = self._put("asset_tasks", "C001")
        v = REG.verify(self.pj, "C001")
        self.assertTrue(v["ok"], v.get("why"))
        self.assertEqual(v["file"], r["file"])

    # ---- 已经有一张 ----

    def test_putting_over_an_existing_asset_makes_a_new_revision(self):
        """★ 原地覆盖 = 引用过旧那张的故事板用的图变了、引用没变，不报错。"""
        REG.sync(self.pj, [{"asset_id": "C001", "family": "CHAR", "name": "甲"}])
        first = self._put("asset_tasks", "C001")
        second = self._put("asset_tasks", "C001", a_png((200, 20, 20)))
        self.assertEqual(first["file"], "02_固定资产/人物身份资产/C001_R01.png")
        self.assertEqual(second["file"], "02_固定资产/人物身份资产/C001_R02.png")
        # 旧那张还在 —— 这才是不覆盖的意思
        self.assertTrue(os.path.isfile(self.pj.p(*first["file"].split("/"))))
        self.assertIn("建了第 2 版", second["note"])

    def test_the_new_revision_gets_into_tasks_json(self):
        """★ 不重装配的话 tasks.json 里还是 R01 —— 出图那步看 R01 不存在

        （其实存在，只是任务指的是旧路径），会去重新生成一张。
        """
        REG.sync(self.pj, [{"asset_id": "C001", "family": "CHAR", "name": "甲"}])
        self._put("asset_tasks", "C001")
        r = self._put("asset_tasks", "C001", a_png((200, 20, 20)))
        self.assertGreaterEqual(r["rebuilt"], 0)

    def test_a_storyboard_says_plainly_that_it_overwrites(self):
        """故事板没有版本机制 —— 那就直说是覆盖，别假装有版本。"""
        self._put("storyboard_tasks", "EP01-SEG01")
        r = self._put("storyboard_tasks", "EP01-SEG01", a_png((9, 9, 9)))
        self.assertIn("覆盖", r["note"])

    # ---- 留痕 ----

    def test_it_is_written_into_the_event_log(self):
        """★ 事后要查得出这一张是人放的，不是模型出的 —— 否则复盘时

        会拿它当模型的产出去评估提示词。
        """
        self._put("asset_tasks", "C001")
        log = io.open(self.pj.p("07_检查与记录", "execution_log.jsonl"),
                      encoding="utf-8").read()
        self.assertIn("manual_image", log)
        self.assertIn("C001", log)


class PageTests(unittest.TestCase):

    HTML = io.open(os.path.join(ROOT, "web", "index.html"),
                   encoding="utf-8").read()

    def test_the_entry_is_on_every_image_task(self):
        self.assertIn("function manualBlock(", self.HTML)
        self.assertIn("/api/task/manual", self.HTML)

    def test_video_tasks_do_not_get_it(self):
        """出片那一类不给这个口 —— 手动放 mp4 是另一件事（拼接会读它）。"""
        self.assertIn("kind === 'video_tasks' ? '' : manualBlock(r, kind)", self.HTML)

    def test_the_row_carries_its_kind(self):
        """★ 后端要按类别找任务。类别只在分组上，不在行上 —— 得带下来。"""
        self.assertIn('data-kind="${esc(kind', self.HTML)

    def test_the_handler_is_delegated_not_bound(self):
        """★ taskRow 是拼字符串重画的 —— 直接 onclick 在重画之后就没了。"""
        self.assertIn("document.addEventListener('click'", self.HTML)

    def test_it_warns_before_overwriting(self):
        i = self.HTML.index("function manualBlock(")
        blk = self.HTML[i:i + 1200]
        self.assertIn("建一个新版本", blk)
        self.assertIn("没有版本机制", blk)


if __name__ == "__main__":
    unittest.main()
