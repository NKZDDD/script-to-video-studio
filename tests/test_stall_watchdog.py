# -*- coding: utf-8 -*-
"""流式吐了一半就卡住 —— 要认出来、要重试、要留原文。

以前有三种「沉默」，只有两种被处理：

    ① 一个字都没收到          心跳线程会说「还在思考期」；上界是读超时
    ② 连接真的断了            RequestException → 可重试 ✓
    ③ **收了一些字之后卡住**   没有任何检测

第三种有两个下场，都不好：

  · 服务商完全不发字节 → 读超时（默认配 900 秒）触发 → **Fatal 不重试**，
    而且那次收到的原文也不留 —— 等 15 分钟，然后什么都没有
  · 服务商发心跳（`: ping` / 非 JSON 的 data 行）→ **读超时永远不触发**，
    因为线上一直有字节。正文不增长，日志每 15 秒打「收到 M 字」，
    M 一直不变 —— **一直挂着，直到人点取消**

第二个更糟：它连死都不会死。

看门狗只在**已经收到过字之后**才算。一个字都没收到时字数也是「不变」的，
但那是思考期 —— 正常可以几分钟，掐掉比现在更糟。
"""
import time
import unittest

import requests

from core import llm as L


class Resp:
    """假响应：按脚本产出 SSE 行。"""

    status_code = 200
    headers = {"Content-Type": "text/event-stream"}
    encoding = "utf-8"
    text = ""

    def __init__(self, lines):
        self._lines = lines

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def iter_lines(self, decode_unicode=False):
        for x in self._lines:
            if callable(x):
                x = x()
            if isinstance(x, Exception):
                raise x
            yield x


def _sse(text):
    import json
    return ('data: ' + json.dumps({"choices": [{"delta": {"content": text}}]})).encode()


class _Sess:
    def __init__(self, resp):
        self.resp = resp
        self.trust_env = True

    def post(self, *a, **kw):
        return self.resp


def _llm():
    m = L.LLM("k", "https://x", "m", stream=True, timeout=60)
    m._session = lambda trust_env: _Sess(m._resp)      # type: ignore[attr-defined]
    return m


class StallTests(unittest.TestCase):

    def setUp(self):
        self.orig = L.STALL_SECONDS
        L.STALL_SECONDS = 0.3          # 别让测试真等两分钟

    def tearDown(self):
        L.STALL_SECONDS = self.orig

    def _run(self, lines):
        m = _llm()
        m._resp = Resp(lines)          # type: ignore[attr-defined]
        kept = []
        return m, kept, lambda: m.chat(
            "s", "u", retries=1, log=None,
            on_partial=lambda t, why: kept.append((t, why)))

    def test_a_heartbeat_only_stall_is_caught(self):
        """★ 这就是那个「不会死的死法」。

        吐了字，然后一直只有心跳 —— 读超时永远不触发。
        """
        beats = [b": ping"] * 200
        m, kept, run = self._run([_sse("前半段"), lambda: (time.sleep(0.4), b": ping")[1]]
                                 + beats)
        with self.assertRaises(L.LLMError) as cm:
            run()
        self.assertIn("卡住", str(cm.exception))
        self.assertIn("心跳", str(cm.exception))

    def test_the_partial_is_kept(self):
        """★ 收到的那一半必须落盘 —— 不然排查时什么线索都没有。"""
        m, kept, run = self._run(
            [_sse("前半段"), lambda: (time.sleep(0.4), b": ping")[1]] + [b": ping"] * 50)
        with self.assertRaises(L.LLMError):
            run()
        self.assertTrue(kept, "原文没留下来")
        self.assertIn("前半段", kept[0][0])
        self.assertIn("卡住", kept[0][1])

    def test_it_is_retryable_not_fatal(self):
        """★ 传输卡住重发一次经常就好 —— 判成 Fatal 等于白扔掉那次机会。"""
        m, kept, run = self._run(
            [_sse("前半段"), lambda: (time.sleep(0.4), b": ping")[1]] + [b": ping"] * 50)
        with self.assertRaises(L.LLMError) as cm:
            run()
        self.assertNotIsInstance(cm.exception, L.LLMFatal)

    def test_a_normal_stream_is_untouched(self):
        """★ 别拦过头：正常吐字不该被判卡住。"""
        m, kept, run = self._run([_sse("一"), _sse("二"), _sse("三"),
                                  b"data: [DONE]"])
        self.assertEqual(run(), "一二三")
        self.assertEqual(kept, [])

    def test_slow_but_moving_is_fine(self):
        """慢不等于卡 —— 只要还在长就不管。"""
        m, kept, run = self._run([
            _sse("一"),
            lambda: (time.sleep(0.2), _sse("二"))[1],
            lambda: (time.sleep(0.2), _sse("三"))[1],
            b"data: [DONE]"])
        self.assertEqual(run(), "一二三")

    def test_the_thinking_phase_is_not_a_stall(self):
        """★ **最要紧的一条。** 一个字都没收到时字数也「不变」，

        但那是模型在想 —— 正常可以几分钟。掐掉它比现在更糟。
        """
        m, kept, run = self._run(
            [lambda: (time.sleep(0.5), b": ping")[1]] + [b": ping"] * 20
            + [_sse("终于开口了"), b"data: [DONE]"])
        self.assertEqual(run(), "终于开口了")


class MidStreamTimeoutTests(unittest.TestCase):
    """吐过字之后彻底断供（连心跳都没有）→ 读超时。"""

    def _run(self, lines):
        m = _llm()
        m._resp = Resp(lines)          # type: ignore[attr-defined]
        kept = []
        return kept, lambda: m.chat(
            "s", "u", retries=1, log=None,
            on_partial=lambda t, why: kept.append((t, why)))

    def test_it_becomes_retryable_and_keeps_the_partial(self):
        """★ 以前和思考期超时走同一条路：Fatal、不重试、原文也不留。

        而这两件事的修法完全不同 —— 那个要减小输入，这个重发一次就好。
        """
        kept, run = self._run([_sse("前半段"), requests.Timeout("read timed out")])
        with self.assertRaises(L.LLMError) as cm:
            run()
        self.assertNotIsInstance(cm.exception, L.LLMFatal)
        self.assertIn("断供", str(cm.exception))
        self.assertTrue(kept)
        self.assertIn("前半段", kept[0][0])

    def test_a_timeout_before_any_content_stays_fatal(self):
        """★ 一个字都没收到就超时 = 这次请求太重，重试只会再白等一轮。"""
        kept, run = self._run([requests.Timeout("read timed out")])
        with self.assertRaises(L.LLMFatal):
            run()


if __name__ == "__main__":
    unittest.main()
