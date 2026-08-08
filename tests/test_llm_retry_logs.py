# -*- coding: utf-8 -*-
import unittest
from unittest.mock import patch

import requests

from core.llm import LLM, LLMError, _Retryable


class LLMRetryLogTests(unittest.TestCase):
    def _client(self):
        return LLM("test-key", "https://example.invalid", "test-model")

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

        client = self._client()
        with patch("core.llm.requests.post", return_value=Response()):
            with self.assertRaisesRegex(
                    _Retryable, "本次已收到 3 字.*已接收内容将被丢弃"):
                client._stream_once(
                    "https://example.invalid", {}, {"messages": []}, None,
                    (30, 60), None,
                )

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


if __name__ == "__main__":
    unittest.main()
