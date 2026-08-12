# -*- coding: utf-8 -*-
"""输出被截断，必须认出来，不许伪装成别的错。

真跑撞到的：环节1 连着三次都是「输出写到一半断了」，程序却报了两种错：

  第 1 次  回复中未找到可解析的 JSON
  第 2 次  输出缺少必需字段: ['entities[]', 'events[]', 'story_truth', …]
  第 3 次  同上

第二种是**伪装**。extract_json 在外层 `{` 没配平时会「退而求其次」
去找第一个 `[` —— 于是把 JSON 里某个恰好完整的内层数组（entities）
当成整个文档返回，校验自然说「缺 entities[]」。

后果不是少报一个错，是**指向完全错误的方向**：看着像模型没按 schema
输出，实际模型写得很好、只是没写完。照着「模型不听话」去排查，
会去换模型、改模板、翻提示词 —— 每一轮都是一整次调用的钱。
"""
import io
import os
import unittest

from core.llm import LLMError, LLMFatal, extract_json

REAL = r"D:\WeChat\WeChat Files\wxid_l3s3w0nc4mx121\FileStorage\File\2026-08"


class TruncationTests(unittest.TestCase):

    def test_an_unclosed_fence_is_truncation(self):
        with self.assertRaises(LLMFatal) as cm:
            extract_json('```json\n{"a": 1, "b": [')
        self.assertIn("没写完", str(cm.exception))

    def test_an_unbalanced_object_is_truncation(self):
        with self.assertRaises(LLMFatal) as cm:
            extract_json('{"a": 1, "entities": [{"id": "E001"}], "events": [{"x"')
        self.assertIn("没写完", str(cm.exception))

    def test_it_does_not_fall_back_to_an_inner_array(self):
        """★ 这就是伪装的来源。

        外层对象截断了，但 `entities` 那个内层数组恰好是完整的 ——
        以前会把它当成整个文档返回，然后报「缺少必需字段」。
        """
        cut = '{"project_name": "x", "entities": [{"entity_id": "E001"}], "events": [{'
        with self.assertRaises(LLMFatal):
            extract_json(cut)

    def test_the_message_says_where_it_stopped(self):
        """断在哪一句，一眼就能看出是「写到一半」而不是「答错了」。"""
        msg = ""
        try:
            extract_json('{"a": "这是最后没写完的一句话')
        except LLMFatal as e:
            msg = str(e)
        self.assertIn("最后停在", msg)
        self.assertIn("这是最后没写完的一句话", msg)

    def test_it_is_fatal_so_the_same_prompt_is_not_retried(self):
        """★ 截断重试同一个提示词多半还是截断 —— 白花两次调用。

        LLMFatal 会被 json_call 直接往上抛，不进「反馈重试」那条路。
        """
        self.assertTrue(issubclass(LLMFatal, LLMError))
        try:
            extract_json("```json\n{")
        except LLMFatal:
            pass
        else:
            self.fail("没抛 LLMFatal")

    def test_the_message_suggests_things_that_actually_help(self):
        msg = ""
        try:
            extract_json("```json\n{")
        except LLMFatal as e:
            msg = str(e)
        self.assertIn("流式", msg)
        self.assertIn("只测第一集", msg)
        self.assertNotIn("缺少必需字段", msg)


class NormalParsingTests(unittest.TestCase):
    """修截断的时候别把正常的解析弄坏了。"""

    def test_a_fenced_object(self):
        self.assertEqual(extract_json('```json\n{"a": 1}\n```'), {"a": 1})

    def test_a_bare_object(self):
        self.assertEqual(extract_json('前言\n{"a": 1}\n后记'), {"a": 1})

    def test_a_bare_array_when_there_is_no_object(self):
        self.assertEqual(extract_json('[1, 2, 3]'), [1, 2, 3])

    def test_braces_inside_strings_do_not_confuse_it(self):
        self.assertEqual(extract_json('{"a": "有个 { 在字符串里"}'),
                         {"a": "有个 { 在字符串里"})

    def test_prose_with_no_json_at_all_is_not_called_truncation(self):
        """没有 JSON 和写了一半是两回事，报错也该不一样。"""
        with self.assertRaises(LLMError) as cm:
            extract_json("好的，我来帮你分析这个剧本。")
        self.assertNotIsInstance(cm.exception, LLMFatal)
        self.assertIn("未找到", str(cm.exception))


@unittest.skipUnless(os.path.isdir(REAL), "没有那三份真实失败原文")
class RealWorldTests(unittest.TestCase):
    """拿真跑落盘的三份原文回归。"""

    def test_all_three_are_recognised_as_truncation(self):
        for f in ("n1_01.txt", "n1_02.txt", "n1_03.txt"):
            body = io.open(os.path.join(REAL, f), encoding="utf-8").read()
            body = body.split("-" * 60 + "\n", 1)[1]
            with self.assertRaises(LLMFatal, msg=f):
                extract_json(body)


if __name__ == "__main__":
    unittest.main()
