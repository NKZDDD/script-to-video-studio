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
        self.assertIn("括号始终没有闭合", str(self._err('```json\n{"a": 1, "b": [')))

    def test_an_unbalanced_object_is_truncation(self):
        e = self._err('{"a": 1, "entities": [{"id": "E001"}], "events": [{"x"')
        self.assertIn("括号始终没有闭合", str(e))

    def test_it_does_not_fall_back_to_an_inner_array(self):
        """★ 这就是伪装的来源。

        外层对象截断了，但 `entities` 那个内层数组恰好是完整的 ——
        以前会把它当成整个文档返回，然后报「缺少必需字段」。
        """
        cut = '{"project_name": "x", "entities": [{"entity_id": "E001"}], "events": [{'
        self.assertIn("括号始终没有闭合", str(self._err(cut)))

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

    def test_it_never_asks_the_model_to_compress(self):
        """★ **一次都不许让它压缩。** 这是想清楚之后的结论，不是保守起见。

        输出长度是由内容决定的 —— 这一集有几场戏、这部剧有几个资产。
        要它把十几万 token 压成几万，压掉的不是形容词，是场次和条目：
        压完的东西和原来那份**不是同一个产物**，只是恰好装得进一次调用。

        装不下的正确解法是**分段处理**：n3 改成按集跑、n4b 改成按资产分批
        之后，原来怎么都过不去的两步一次就过了。

        而且「真撞上限」那一支实跑中**根本走不到** —— `_finish()` 遇到
        finish_reason=length 会先抛 LLMFatal。也就是说每一次走到这里的
        截断都不是长度问题，那句「更紧凑」从来没有一次是对的。

        真实代价：n4b 被截断重试之后，模型照着「压到最短」把六字段的
        六行标签压成一段连续文字 —— 64 份提示词一个换行都没有，
        出图那层的逐项校验把 41 条全拦下了。
        """
        from core.llm import _truncated
        for reason in ("length", "stop", "", "content_filter"):
            msg = str(_truncated('{"a": 1', 7, reason))
            for banned in ("更紧凑", "压描述", "压缩描述", "压到最短"):
                self.assertNotIn(banned, msg, f"{reason}：又在让模型压缩")
            self.assertIn("不要", msg, f"{reason}：得明确说别删内容")

    def test_hitting_the_real_cap_says_to_split_not_to_shrink(self):
        """★ 真撞上限时该说的是「拆开跑」，不是「调大上限」也不是「写少点」。

        调大上限基本没用：网关自己有硬上限（实测 128000），再往上整个请求
        会被 400 挡回来 —— 几十万 token 的输入白发一遍。
        """
        import inspect

        from core.llm import LLM
        src = inspect.getsource(LLM._finish)
        self.assertIn("拆开分批跑", src)
        self.assertIn("128000", src)

    def test_the_stop_case_tells_it_the_json_is_malformed(self):
        """★ finish_reason=stop 说明它以为写完了 —— 该查括号，不是该删内容。"""
        from core.llm import _truncated
        msg = str(_truncated('{"a": 1', 7, "stop"))
        self.assertIn("括号", msg)
        self.assertIn("不要删减", msg)

    def test_the_reason_reaches_here_from_the_client(self):
        """★ 记了不用等于没记 —— 结束原因得真的传进来。"""
        import inspect

        from core.llm import LLM
        self.assertIn("extract_json(text, log, stop)", inspect.getsource(LLM.json_call))

    def test_user_facing_advice_lives_in_the_catalog_not_the_exception(self):
        """★ 用户该做什么不能塞进异常消息 —— 那会被模型当成任务要求。

        「把流式关掉」「只测第一集」是给人看的，写进反馈里模型会照着执行。
        """
        msg = str(self._err('```json\n{'))
        for user_advice in ("流式", "只测第一集", "换一个"):
            self.assertNotIn(user_advice, msg, f"「{user_advice}」不该出现在给模型的反馈里")
        from core import diagnose as D
        fix = "　".join(D.CATALOG["LLM_TRUNCATED"]["fix"])
        self.assertIn("流式", fix)
        self.assertIn("只测第一集", fix)

    def test_it_never_tells_you_to_turn_streaming_off(self):
        """★ 这条建议曾经写反过，代价很实在。

        非流式期间没有字节流动，中间那层会以为连接死了，
        在 100 秒左右直接掐断（HTTP 524）—— 关流式**加重**长输出的截断，
        而卡里一度写着「先把流式输出关掉再试一次」，把人往反方向指了一整轮。
        """
        from core import diagnose as D
        fix = "　".join(D.CATALOG["LLM_TRUNCATED"]["fix"])
        for backwards in ("关掉流式", "流式输出关掉", "关闭流式"):
            self.assertNotIn(backwards, fix)
        self.assertIn("不要关流式", fix)

    def test_it_says_which_field_to_look_at(self):
        """★ length 和 stop 的修法完全相反，不说看哪个字段就只能猜。"""
        from core import diagnose as D
        c = D.CATALOG["LLM_TRUNCATED"]
        blob = c["why"] + "　".join(c["fix"])
        self.assertIn("结束原因", blob)
        self.assertIn("length", blob)

    def test_it_does_not_invent_a_time_threshold(self):
        """★ 「用时接近 300 秒就是被切了」这条我写进去过，是错的。

        本机 74 次调用的实况：362 秒的成了、302 秒的断了；
        输出 17113 token 的成了、16942 的断了。时间和 token 两个维度上
        成功和失败都重叠，没有干净的阈值。
        给一个假阈值比不给更糟 —— 人会照着它去调一个根本没接线的旋钮。
        """
        from core import diagnose as D
        c = D.CATALOG["LLM_TRUNCATED"]
        blob = c["why"] + "　".join(c["fix"])
        self.assertIn("别在耗时上找规律", blob)
        self.assertNotIn("300 秒", blob)
        self.assertNotIn("时间墙", blob)

    def test_it_says_max_tokens_may_not_be_enforced(self):
        """★ 实测：配的 16000，实际输出到过 19612。

        不写这一句，人会一直加大那个数 —— 而这家网关压根不看它。
        """
        from core import diagnose as D
        blob = D.CATALOG["LLM_TRUNCATED"]["why"]
        self.assertIn("不执行 max_tokens", blob)
        self.assertIn("19612", blob)


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
        self.assertNotIn("括号始终没有闭合", str(cm.exception))


@unittest.skipUnless(os.path.isdir(REAL), "没有那三份真实失败原文")
class RealWorldTests(unittest.TestCase):
    """拿真跑落盘的三份原文回归。"""

    def test_all_three_are_recognised_as_truncation(self):
        for f in ("n1_01.txt", "n1_02.txt", "n1_03.txt"):
            body = io.open(os.path.join(REAL, f), encoding="utf-8").read()
            body = body.split("-" * 60 + "\n", 1)[1]
            with self.assertRaises(LLMError, msg=f) as cm:
                extract_json(body)
            self.assertIn("括号始终没有闭合", str(cm.exception), f)


if __name__ == "__main__":
    unittest.main()
