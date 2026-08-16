# -*- coding: utf-8 -*-
"""本机占用统计 + 并发建议。

**先说一件容易搞反的事**：本机 CPU 和内存通常不是这套流水线的瓶颈。
每个在途任务干的是「发一个 HTTP 请求然后等」—— 等的时候既不吃 CPU
也不占多少内存。真正卡住的几乎总是服务商那边：限流、排队、账号并发上限。

所以建议值必须**两类依据都看**：

    本机余量   → 还能不能再开
    服务商反应 → 再开有没有用

只看第一类，会把并发调到一个本机撑得住、但服务商全在限流的数字上 ——
那时候任务不失败，只是变慢并反复重试，**看起来在跑，实际在原地烧钱**。
"""
import unittest

from core import resources as R


class SnapshotTests(unittest.TestCase):

    def test_it_always_reports_whether_it_can_see_anything(self):
        """★ 读不到就要说读不到，不能显示 0 —— 0 会被当成「很空闲」。"""
        s = R.snapshot()
        self.assertIn("has_psutil", s)
        self.assertIn("cpu_count", s)

    def test_real_numbers_when_psutil_is_there(self):
        if not R.available():
            self.skipTest("没装 psutil")
        s = R.snapshot()
        self.assertGreater(s["mem_total"], 0)
        self.assertGreater(s["proc_rss"], 0)
        self.assertGreaterEqual(s["mem_available"], 0)


class AdviceTests(unittest.TestCase):

    def test_it_says_it_cannot_tell_instead_of_guessing(self):
        """★ 样本不够时给 None，不给一个看起来很确定的假数字。

        这个项目在「编一个阈值」上栽过一次（我编过一条「300 秒时间墙」，
        被本机 74 次调用的实测数据推翻）。宁可说不知道。
        """
        a = R.advise()
        self.assertIsNone(a["limit"])
        self.assertTrue(any("样本不够" in b or "psutil" in b for b in a["basis"])
                        or "psutil" in a["note"])

    def test_heavy_rate_limiting_caps_the_advice(self):
        """★ 服务商已经在限流时，本机再有余量也不该往上加。"""
        a = R.advise(inflight_peak=20, recent_429=30, recent_calls=100)
        self.assertTrue(any("接不住" in b for b in a["basis"]))
        if a["limit"] is not None:
            self.assertLessEqual(a["limit"], 20)

    def test_low_rate_limiting_says_there_is_room(self):
        a = R.advise(inflight_peak=8, recent_429=1, recent_calls=100)
        self.assertTrue(any("还有空间" in b for b in a["basis"]))

    def test_too_few_calls_is_not_treated_as_healthy(self):
        """★ 只跑了三次没被限流 ≠ 服务商接得住。"""
        a = R.advise(inflight_peak=4, recent_429=0, recent_calls=3)
        self.assertTrue(any("看不出" in b for b in a["basis"]))

    def test_the_note_warns_that_local_is_rarely_the_limit(self):
        self.assertIn("不是", R.advise()["note"])


class PerTaskTests(unittest.TestCase):

    def setUp(self):
        R._SAMPLES.clear()

    def tearDown(self):
        R._SAMPLES.clear()

    def test_no_samples_means_no_answer(self):
        self.assertIsNone(R.per_task_bytes())

    def test_it_needs_the_concurrency_to_have_actually_varied(self):
        """★ 并发一直是同一个数，算不出「每个任务占多少」——

        硬算的话会拿噪音当结论。
        """
        for _ in range(20):
            R._SAMPLES.append((0.0, 100_000_000, 5))
        self.assertIsNone(R.per_task_bytes())

    def test_it_measures_the_delta_when_concurrency_varied(self):
        for i in range(10):
            R._SAMPLES.append((0.0, 100_000_000, 1))
        for i in range(10):
            R._SAMPLES.append((0.0, 200_000_000, 11))
        per = R.per_task_bytes()
        self.assertIsNotNone(per)
        self.assertAlmostEqual(per / 1048576, 9.5, delta=1)


class EndpointTests(unittest.TestCase):

    def test_it_works_without_a_project(self):
        """★ 没开项目也要能看本机占用 —— 那正是「开工前先看看」的时候。"""
        from server.app import api_post
        r = api_post("/api/resources", {})
        self.assertTrue(r["ok"])
        self.assertIn("usage", r)
        self.assertIn("advice", r)

    def test_it_reports_both_gates(self):
        from server.app import api_post
        g = api_post("/api/resources", {})["gates"]
        for k in ("global_limit", "global_inflight", "llm_limit", "llm_inflight"):
            self.assertIn(k, g, k)


class PageTests(unittest.TestCase):

    def test_the_gauge_is_wired(self):
        import io
        import os
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        html = io.open(os.path.join(root, "web", "index.html"),
                       encoding="utf-8").read()
        self.assertIn('id="resGauge"', html)
        self.assertIn("/api/resources", html)

    def test_it_says_unknown_rather_than_zero(self):
        import io
        import os
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        html = io.open(os.path.join(root, "web", "index.html"),
                       encoding="utf-8").read()
        self.assertIn("占用未知", html)


if __name__ == "__main__":
    unittest.main()
