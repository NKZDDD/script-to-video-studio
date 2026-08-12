# -*- coding: utf-8 -*-
import unittest
from unittest.mock import patch

import requests

from core.llm import LLM, LLMError, _Retryable
from server.app import build_llm


class LLMRetryLogTests(unittest.TestCase):
    def _client(self, stream=True):
        return LLM("test-key", "https://example.invalid", "test-model",
                   stream=stream)

    def test_default_mode_is_nonstreaming_without_stream_options(self):
        client = LLM("test-key", "https://example.invalid", "test-model")
        captured = {}

        # sess 是第一个参数：trust_env 只能在 Session 上设，
        # 裸 requests.post 会让「强制直连」失效。
        def plain_once(sess, url, headers, body, proxies, tmo, log,
                       on_usage=None, on_partial=None):
            captured.update(body)
            return "完整结果"

        client._plain_once = plain_once
        client._stream_once = lambda *args, **kwargs: self.fail("不应调用流式读取")
        logs = []
        self.assertEqual(client.chat("", "任务", log=logs.append), "完整结果")
        self.assertIs(captured["stream"], False)
        self.assertNotIn("stream_options", captured)
        self.assertIn("非流式（完成后一次返回", "\n".join(logs))

    def test_build_llm_applies_saved_switch_and_false_override(self):
        cfg = {
            "providers": {"paisio": {"api_key": "provider-key"}},
            "llm": {"provider": "paisio", "model": "test-model", "stream": True},
        }
        self.assertTrue(build_llm(cfg).stream)
        self.assertFalse(build_llm(cfg, {"stream": False}).stream)
        del cfg["llm"]["stream"]
        self.assertFalse(build_llm(cfg).stream)

    def test_stream_interruption_names_attempt_and_discarded_output(self):
        client = self._client()
        responses = iter([
            _Retryable("连接在流传输中途断开：只收到 26778 字就没了"),
            "完整结果",
        ])
        def stream_once(*args, **kwargs):
            value = next(responses)
            if isinstance(value, Exception):
                raise value
            return value

        client._stream_once = stream_once
        logs = []
        with patch("core.llm.time.sleep"):
            result = client.chat("", "任务", retries=3, log=logs.append)

        self.assertEqual(result, "完整结果")
        joined = "\n".join(logs)
        self.assertIn("[传输中断] 第 1/3 次请求未完整结束", joined)
        self.assertIn("只收到 26778 字", joined)
        self.assertIn("本次内容不会进入 JSON 校验", joined)
        self.assertIn("传输重试 1/2", joined)

    def test_midstream_socket_error_reports_received_character_count(self):
        class Response:
            status_code = 200
            headers = {"Content-Type": "text/event-stream"}

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def iter_lines(self, decode_unicode=False):
                yield (b'data: {"choices":[{"delta":{"content":"abc"}}]}')
                raise requests.ConnectionError("peer closed connection")

        class FakeSession:
            """请求必须从 Session 上发出去 —— patch requests.post 已经拦不到了。"""
            trust_env = True

            def post(self, *a, **k):
                return Response()

        client = self._client()
        kept = []
        with self.assertRaisesRegex(
                _Retryable, "本次已收到 3 字.*已接收内容将被丢弃"):
            client._stream_once(
                FakeSession(), "https://example.invalid", {}, {"messages": []},
                None, (30, 60), None, on_partial=lambda t, w: kept.append((t, w)),
            )
        # 丢弃之前必须先存下来：断在哪个字段是排这类问题唯一有用的证据
        self.assertEqual(kept[0][0], "abc")
        self.assertIn("流式连接中断", kept[0][1])

    def test_network_retry_exhaustion_is_explicit(self):
        client = self._client()
        client._stream_once = lambda *args, **kwargs: (_ for _ in ()).throw(
            requests.ConnectionError("connection reset"))
        logs = []
        with patch("core.llm.time.sleep"):
            with self.assertRaisesRegex(LLMError, "网络错误"):
                client.chat("", "任务", retries=3, log=logs.append)

        joined = "\n".join(logs)
        self.assertIn("[网络中断] 第 1/3 次请求失败", joined)
        self.assertIn("[网络中断] 第 2/3 次请求失败", joined)
        self.assertIn("[网络失败] 第 3/3 次请求仍失败", joined)
        self.assertIn("传输重试已耗尽", joined)

    def test_json_retry_is_labeled_as_content_problem(self):
        client = self._client()
        client.chat = lambda *args, **kwargs: "not json"
        logs = []
        with self.assertRaisesRegex(LLMError, "JSON 输出校验失败"):
            client.json_call("", "任务", json_retries=1, log=logs.append)

        self.assertIn("[JSON 校验重试] 第 1/1 次", "\n".join(logs))
        self.assertIn("不是传输中断", "\n".join(logs))



class UnparseableJsonTests(unittest.TestCase):
    """★ 「回复中未找到可解析的 JSON」必须带上模型实际回了什么。

    真跑撞到过：n1 输出 2700 字、38 秒答完，然后只报一句「没找到 JSON」。
    那句话什么都没说 —— 模型到底是写了散文、拒答了、还是用了别的围栏，
    三种情况在日志里长得一模一样，改法却完全不同：

      散文/解说   → 模板或 _common 里的「只输出 JSON」被改掉了
      拒答/安全语 → 内容触发审核，要改剧本措辞
      围栏不对    → 提示词里加一句示例

    不带原文就只能靠猜，而每猜一次是一整轮调用的钱。
    """

    def _err(self, text):
        from core.llm import extract_json, LLMError
        with self.assertRaises(LLMError) as cm:
            extract_json(text)
        return str(cm.exception)

    def test_it_quotes_the_beginning_of_the_reply(self):
        msg = self._err("好的，我来帮你分析这个剧本。首先这是一个都市情感故事…")
        self.assertIn("我来帮你分析", msg)
        self.assertIn("字", msg)

    def test_it_says_how_long_the_reply_was(self):
        self.assertIn("2000 字", self._err("啊" * 2000))

    def test_a_long_reply_also_shows_the_end(self):
        """结尾常常是关键：被截断的话尾部是半句话。"""
        msg = self._err("头" * 200 + "中" * 200 + "尾巴在这里")
        self.assertIn("尾巴在这里", msg)

    def test_an_empty_reply_says_so_instead_of_showing_nothing(self):
        self.assertIn("（空）", self._err("   \n\t "))

    def test_it_points_at_the_saved_raw_output(self):
        """截断的开头看不出全貌时，得知道去哪看完整的。"""
        self.assertIn("失败原文", self._err("随便回一句"))

    def test_newlines_do_not_break_the_one_line_log(self):
        msg = self._err("第一行\n\n第二行\n第三行")
        self.assertNotIn("\n", msg.split("开头是：")[1][:40])

if __name__ == "__main__":
    unittest.main()
