# -*- coding: utf-8 -*-
"""轮询：**服务商那边已经完成了，我们不许还在等**。

用户实遇（2026-09-17）：「我在服务商的平台上已经看到任务完成了，
但是我们 studio 还在等待结果」。

原来的判断只有两张表（完成词 / 失败词），认不出的词一律当成「还没好」——
于是**结果地址已经拿在手里了，却接着睡到超时**。等满之后报的是一句
「任务超时」，人会去查线路、查服务商、查网络，查不到任何东西，
因为那条任务早就跑完了，钱也早就花了。

这个文件盯三件事：
  1. 认不出的状态词 + 手上已经有结果 → 当场收下（并且点名那个词）
  2. 认识的「还在跑」+ 响应里有地址 → **不许**提前收（那多半是回显的输入图）
  3. 查询一直报同一个错 → 早停，别拖满超时
"""
import unittest

from core import apiutil
from core.apiutil import (DONE_STATES, FAIL_STATES, RUNNING_STATES, ApiError,
                          HttpSession, extract_video_url)


def _poll(replies, **kw):
    """replies 用完之后一直回最后一份。"""
    s = HttpSession("k", "https://x")
    seq, logs = list(replies), []

    def fake(method, path, **k):
        r = seq.pop(0) if len(seq) > 1 else seq[0]
        if isinstance(r, Exception):
            raise r
        return r

    s.request = fake
    kw.setdefault("timeout", 5)
    try:
        got = s.poll("/v1/videos/{id}", "t1", picker=extract_video_url,
                     interval=0, log=logs.append, **kw)
    except ApiError as exc:
        got = exc
    return got, logs


class PollTests(unittest.TestCase):
    def test_an_unknown_done_word_does_not_make_us_keep_waiting(self):
        """★ 状态词不在表里，但结果已经拿到了 —— 收下，别等到超时。"""
        got, logs = _poll([{"status": "ALL_DONE_OK", "url": "https://x/a.mp4"}])
        self.assertEqual(got, "https://x/a.mp4")
        # 要点名那个词：不点名的话这支兜底会一直兜着，没人知道该补表
        said = [l for l in logs if "不认识" in l]
        self.assertTrue(said, "收下了却没说状态词认不出 —— 那张表就永远补不上")
        self.assertIn("all_done_ok", said[0])

    def test_a_known_running_word_still_blocks(self):
        """★ 「还在跑」的时候**不许**提前收。

        响应里那个地址很可能是回显的输入图或预览图 —— 收下就是拿参考图
        当成片，而任务标成功、片子是错的，一处都不会说话。
        """
        got, _ = _poll([{"status": "processing", "url": "https://x/echo-input.png"},
                        {"status": "completed", "url": "https://x/real.mp4"}])
        self.assertEqual(got, "https://x/real.mp4")

    def test_every_running_word_is_neither_done_nor_failed(self):
        """三张表不许重叠 —— 重叠一个词就是把还在跑的付费任务判死（或判活）。"""
        for s in RUNNING_STATES:
            self.assertNotIn(s, DONE_STATES, s)
            self.assertNotIn(s, FAIL_STATES, s)

    def test_a_query_that_keeps_failing_the_same_way_stops_early(self):
        """★ 端点或参数不对时每次都是同一个错，等 30 分钟不会等出不同结果。

        而这段时间里那条任务在服务商那边可能早就跑完了。
        """
        got, _ = _poll([ApiError("HTTP 404 no such endpoint")], timeout=999)
        self.assertIsInstance(got, ApiError)
        self.assertIn("连续", str(got))
        self.assertEqual(got.kind, apiutil.TASK_FATAL)

    def test_a_flaky_query_is_not_treated_as_a_dead_end(self):
        """抖一下不算 —— 错的内容不一样就重新计数，接着等。"""
        got, _ = _poll([ApiError("HTTP 502 a"), ApiError("HTTP 500 b"),
                        {"status": "completed", "url": "https://x/a.mp4"}])
        self.assertEqual(got, "https://x/a.mp4")

    def test_the_timeout_message_names_the_last_status(self):
        """★ 只说「超时」的话，「平台上明明显示完成」这件事没有任何线索可查。"""
        got, _ = _poll([{"status": "奇怪的词"}], timeout=0.05)
        self.assertIsInstance(got, ApiError)
        self.assertIn("奇怪的词", str(got))
        self.assertIn("显示已完成", str(got))

    def test_a_done_word_with_no_url_still_tries_the_content_endpoint(self):
        """完成了但没给地址 → 去打下载端点。

        这一步**只有认出了完成词才有**，兜底那支拿不到 —— 所以常见的
        同义词该收进 DONE_STATES，而不是都交给兜底。
        """
        s = HttpSession("k", "https://x")
        seen = []

        def fake(method, path, **k):
            seen.append(path)
            if path.endswith("/content"):
                return {"url": "https://x/a.mp4"}
            return {"status": "succeed"}

        s.request = fake
        got = s.poll("/v1/videos/{id}", "t1", picker=extract_video_url,
                     interval=0, timeout=5, log=lambda *a: None,
                     content_path_tpl="/v1/videos/{id}/content")
        self.assertEqual(got, "https://x/a.mp4")
        self.assertIn("/v1/videos/t1/content", seen)


if __name__ == "__main__":
    unittest.main()
