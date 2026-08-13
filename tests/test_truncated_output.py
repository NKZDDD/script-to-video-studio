# -*- coding: utf-8 -*-
"""输出被截断，必须认出来，不许伪装成别的错。

真跑撞到的：环节1 连着三次都是「输出写到一半断了」，程序却报了两种错：

  第 1 次  回复中未找到可解析的 JSON
  第 2 次  输出缺少必需字段: ['entities[]', 'events[]', 'story_truth', …]
  第 3 次  同上

第二种是**伪装**，而且是自己造的。extract_json 在外层 `{` 没配平时会
「退而求其次」去找第一个 `[` —— 于是把 JSON 里某个恰好完整的内层数组
（entities）当成整个文档返回，校验自然说「缺 entities[]」。

后果不是少报一个错，是**指向完全相反的方向**：看着像模型没按 schema 输出，
实际模型写得挺好、只是没写完。照着「模型不听话」排查，会去换模型、
改模板、翻提示词 —— 每一轮都是一整次调用的钱。

## 为什么是可重试而不是 Fatal

一开始做成 LLMFatal，理由是「同一个提示词重试多半还是同样的结果」。
拿真模型跑完之后这个判断被推翻了：同一个模型、同一步、同一份剧本，

  一次断在  8,570 output token（21,114 字）
  另一次    16,251 output token（40,015 字）**成功**

**是随机的。** 不重试等于白扔掉那个机会，所以改回可重试，
并且把报错写成模型能照着做的话（「压缩，不是重写」）。
"""
import io
import os
import unittest

from core.llm import LLMError, LLMFatal, extract_json

REAL = r"D:\WeChat\WeChat Files\wxid_l3s3w0nc4mx121\FileStorage\File\2026-08"


class TruncationTests(unittest.TestCase):

    def _err(self, text):
        with self.assertRaises(LLMError) as cm:
            extract_json(text)
        return cm.exception

    def test_an_unclosed_fence_is_truncation(self):
        self.assertIn("被截断", str(self._err('```json\n{"a": 1, "b": [')))

    def test_an_unbalanced_object_is_truncation(self):
        e = self._err('{"a": 1, "entities": [{"id": "E001"}], "events": [{"x"')
        self.assertIn("被截断", str(e))

    def test_it_does_not_fall_back_to_an_inner_array(self):
        """★ 这就是伪装的来源。

        外层对象截断了，但 `entities` 那个内层数组恰好是完整的 ——
        以前会把它当成整个文档返回，然后报「缺少必需字段」。
        """
        cut = '{"project_name": "x", "entities": [{"entity_id": "E001"}], "events": [{'
        self.assertIn("被截断", str(self._err(cut)))

    def test_the_message_says_where_it_stopped(self):
        """断在哪一句，一眼看出是「写到一半」而不是「答错了」。"""
        msg = str(self._err('{"a": "这是最后没写完的一句话'))
        self.assertIn("停在", msg)
        self.assertIn("这是最后没写完的一句话", msg)
        self.assertIn("字", msg)

    def test_it_is_retryable_not_fatal(self):
        """★ 实测同一步一次 8570 token 断、一次 16251 token 成 —— 是随机的。

        做成 Fatal 等于白扔掉重试那次可能成功的机会。
        """
        self.assertNotIsInstance(self._err('```json\n{'), LLMFatal)

    def test_the_message_tells_the_model_what_may_and_may_not_be_compressed(self):
        """★ 压缩指令必须划清界限：压描述，不压结构。

        真实后果：n4b 被截断重试之后，模型照着「压到最短」把六字段的
        六行标签压成了一段连续文字 —— 64 份提示词一个换行都没有，
        然后出图那层的逐项校验把 41 条全拦下了。
        压缩指令和结构要求直接冲突，不划界限就是这个下场。
        """
        msg = str(self._err('```json\n{'))
        self.assertIn("更紧凑", msg)
        self.assertIn("压描述，不压结构", msg)
        self.assertIn("逐项分行", msg)
        self.assertIn("不许合并成一段话", msg)

    def test_user_facing_advice_lives_in_the_catalog_not_the_exception(self):
        """★ 用户该做什么不能塞进异常消息 —— 那会被模型当成任务要求。

        「把流式关掉」「只测第一集」是给人看的，写进反馈里模型会照着执行。
        """
        msg = str(self._err('```json\n{'))
        for user_advice in ("流式", "只测第一集", "换一个"):
            self.assertNotIn(user_advice, msg, f"「{user_advice}」不该出现在给模型的反馈里")
        from core import diagnose as D
        self.assertIn("流式输出", "　".join(D.CATALOG["LLM_TRUNCATED"]["fix"]))


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
        self.assertIn("未找到", str(cm.exception))
        self.assertNotIn("被截断", str(cm.exception))


@unittest.skipUnless(os.path.isdir(REAL), "没有那三份真实失败原文")
class RealWorldTests(unittest.TestCase):
    """拿真跑落盘的三份原文回归。"""

    def test_all_three_are_recognised_as_truncation(self):
        for f in ("n1_01.txt", "n1_02.txt", "n1_03.txt"):
            body = io.open(os.path.join(REAL, f), encoding="utf-8").read()
            body = body.split("-" * 60 + "\n", 1)[1]
            with self.assertRaises(LLMError, msg=f) as cm:
                extract_json(body)
            self.assertIn("被截断", str(cm.exception), f)


if __name__ == "__main__":
    unittest.main()
