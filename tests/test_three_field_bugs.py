# -*- coding: utf-8 -*-
"""一晚上三份排错包里的三个问题，来源完全不同。

1. **残图被当成好图收下**（通用级，59 条 `Truncated File Read`）
   超模返回的 base64 是完整解出来了，但**内容只有一半**。写成 PNG 之后
   大小几百 KB，512 字节那道线轻松通过，于是注册成 generated；
   到下一步拿它当参考图时 Pillow 才炸 —— 而报错出现在**用它的那一步**，
   看不出是上一轮存下来的那几张图坏了。

2. **`max_output_tokens` 超上限**（电影级，n3/n4/n4b 各挂一次）
       HTTP 400: Field 'max_output_tokens' must be at most 128000
   我原来把上限定在 20 万，理由是「远高于任何现役模型的输出能力，撞不到」——
   撞不到的是**模型**，而**网关**会先把整个请求挡回来。每次都是先把几十万
   token 的输入发出去、等 127 秒，再拿回这一句。

3. **资产提示词没落盘**（电影级，83 条「文件不存在」）
   n4b 八批都跑完了、产物里有 prompt，磁盘上一个 txt 都没有 ——
   因为写文件那句被 `if ep:` 挡住了，而 n4b 是全剧级，ep 是空的。
"""
import io
import os
import shutil
import tempfile
import unittest

from core import apiutil as A

PNG_HEAD = b"\x89PNG\r\n\x1a\n"
PNG_END = b"IEND" + bytes([0xAE, 0x42, 0x60, 0x82])


class TruncatedImageTests(unittest.TestCase):
    """残图必须当场拦下，不能等到别人用它的时候才炸。"""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.dest = os.path.join(self.d, "C001.png")

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def _write(self, data, name="C001.png"):
        p = os.path.join(self.d, name)
        with open(p, "wb") as f:
            f.write(data)
        return p

    def test_a_half_decoded_png_is_caught(self):
        """★ 这就是那 59 条 Truncated File Read 的源头。"""
        p = self._write(PNG_HEAD + b"x" * 50000)          # 没有 IEND
        self.assertIn("残图", A._incomplete_image(p))

    def test_size_alone_does_not_catch_it(self):
        """★ 说明为什么光有大小检查不够：残图一点都不小。"""
        p = self._write(PNG_HEAD + b"x" * 50000)
        self.assertGreater(os.path.getsize(p), A.MIN_BYTES * 90)

    def test_a_complete_png_passes(self):
        p = self._write(PNG_HEAD + b"x" * 50000 + PNG_END)
        self.assertEqual(A._incomplete_image(p), "")

    def test_a_complete_jpeg_passes(self):
        p = self._write(b"\xff\xd8" + b"x" * 5000 + b"\xff\xd9", "a.jpg")
        self.assertEqual(A._incomplete_image(p), "")

    def test_a_half_jpeg_is_caught(self):
        p = self._write(b"\xff\xd8" + b"x" * 5000, "a.jpg")
        self.assertIn("残图", A._incomplete_image(p))

    def test_formats_we_do_not_know_are_left_alone(self):
        """★ 宁可漏，不可误杀 —— 认不出的格式一律放过。"""
        for name in ("a.mp4", "a.webp", "a.bin"):
            p = self._write(b"x" * 5000, name)
            self.assertEqual(A._incomplete_image(p), "", name)

    def test_saving_a_truncated_image_deletes_it_and_raises(self):
        """★ **必须删掉。** 留着的话下次会被当成「已经做过了」跳过。"""
        import base64
        s = A.HttpSession("k", "https://x")
        raw = PNG_HEAD + b"x" * 50000
        with self.assertRaises(A.ApiError) as cm:
            s.save_item(base64.b64encode(raw).decode(), self.dest)
        self.assertIn("残图", str(cm.exception))
        self.assertFalse(os.path.exists(self.dest), "残图留在盘上了")

    def test_it_is_retryable(self):
        """截断多半是这一次传输的事，重来一次经常就好了。"""
        import base64
        s = A.HttpSession("k", "https://x")
        with self.assertRaises(A.ApiError) as cm:
            s.save_item(base64.b64encode(PNG_HEAD + b"x" * 50000).decode(), self.dest)
        self.assertEqual(cm.exception.kind, A.RETRYABLE)

    def test_a_good_image_still_saves(self):
        """★ 别拦过头。"""
        import base64
        s = A.HttpSession("k", "https://x")
        raw = PNG_HEAD + b"x" * 50000 + PNG_END
        s.save_item(base64.b64encode(raw).decode(), self.dest)
        self.assertEqual(open(self.dest, "rb").read(), raw)


class TokenCapTests(unittest.TestCase):
    """网关说上限是多少，就按它说的来。"""

    REAL = ('HTTP 400: {"error":{"message":"Field \'max_output_tokens\' '
            'must be at most 128000 unknown_error。请调整相关参数后重试"}}')

    def test_the_ceiling_is_not_above_what_gateways_accept(self):
        """★ 20 万会被整个请求挡回来。"""
        from server.app import MAX_TOKENS_CEILING
        self.assertLessEqual(MAX_TOKENS_CEILING, 128000)

    def test_the_limit_is_read_out_of_the_error(self):
        """★ 正确的值就写在报错里，不用猜。"""
        from core.llm import token_cap
        self.assertEqual(token_cap(self.REAL), 128000)

    def test_other_wordings_too(self):
        from core.llm import token_cap
        for msg, want in (
                ("max_tokens must be less than or equal to 64000", 64000),
                ("Field 'max_completion_tokens' exceeds the limit of 32768", 32768)):
            self.assertEqual(token_cap(msg), want, msg)

    def test_unrelated_errors_give_nothing(self):
        """★ 认错了会把一个正常的上限改小，输出白白变短。"""
        from core.llm import token_cap
        for msg in ("HTTP 524 A timeout occurred",
                    "insufficient balance, 1000 credits needed",
                    "content policy violation"):
            self.assertEqual(token_cap(msg), 0, msg)

    def test_the_client_lowers_itself_and_retries(self):
        """★ 不自愈的代价：几十万 token 的输入白发一遍、白等两分钟。"""
        import inspect

        from core.llm import LLM
        src = inspect.getsource(LLM.chat)
        self.assertIn("_TokenCap", src)
        self.assertIn("self.max_tokens = exc.limit", src)
        self.assertIn("continue", src)

    def test_the_400_is_recognised_as_a_cap_problem(self):
        import inspect

        from core.llm import LLM
        self.assertIn("_TokenCap(cap", inspect.getsource(LLM._check_status))


class AssetPromptFilesTests(unittest.TestCase):
    """全剧级环节跑完也要落 txt。"""

    def test_the_writer_is_not_gated_on_having_an_episode(self):
        """★ 这就是那 83 条「文件不存在」。

        n4b 是全剧级，ep 是空字符串 —— 写成 `if ep:` 就等于
        资产提示词永远落不成文件，而出图那一层是按路径读文件的。
        """
        import inspect

        from core import pipeline_v34 as P
        src = inspect.getsource(P.run)
        self.assertIn("R.write_prompt_files(pj, ep)", src)
        i = src.index("R.write_prompt_files(pj, ep)")
        head = src[:i].rstrip().splitlines()[-1]
        self.assertNotIn("if ep:", head, "又被 if ep 挡回去了")

    def test_an_empty_episode_writes_the_series_level_prompts(self):
        """★ 传空 episode 要真的能写出来，不是「不报错」而已。"""
        from core import run_v34 as R
        from test_v34_run import new_project
        pj = new_project()
        try:
            pj.save_stage("n4b_asset_prompts", {"asset_prompts": [
                {"asset_id": "C001", "filename": "C001_PROMPT.txt",
                 "prompt": "一个人的定妆照"},
                {"asset_id": "LK002", "prompt": "一件外套"}]}, "")
            self.assertEqual(R.write_prompt_files(pj, ""), 2)
            for name in ("C001_PROMPT.txt", "LK002_PROMPT.txt"):
                p = pj.p("03_提示词", "资产生产提示词", name)
                self.assertTrue(os.path.isfile(p), name)
            self.assertIn("定妆照", io.open(
                pj.p("03_提示词", "资产生产提示词", "C001_PROMPT.txt"),
                encoding="utf-8").read())
        finally:
            shutil.rmtree(pj.root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
