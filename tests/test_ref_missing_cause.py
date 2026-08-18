# -*- coding: utf-8 -*-
"""参考图不见了，要说清是**它自己没出成**。

实跑同一批里连着两条：

    asset ST001 | QUOTA_EXHAUSTED | No available image quota
    asset ST002 | REF_MISSING     | 参考图文件不存在: …/02_固定资产/连续状态资产/ST001.png

第二条照着字面读是「文件不见了」，人会去翻硬盘、怀疑路径配置 ——
而真正要处理的是额度，那条记录就躺在同一份失败清单里。

单独重试 ST002 也没有意义：ST001 不出来，它永远缺参考图。
所以这一条要指向那一条，并且不再当成「可以重试」。
"""
import os
import shutil
import unittest

from core import diagnose, produce
from core.apiutil import TASK_FATAL, ApiError
from test_v34_run import new_project


class WhyTests(unittest.TestCase):

    def setUp(self):
        self.pj = new_project()
        self.src = "02_固定资产/连续状态资产/ST001.png"

    def tearDown(self):
        shutil.rmtree(self.pj.root, ignore_errors=True)

    def _record(self, target, code, raw):
        diagnose.record(self.pj.root, diagnose.warn(code, raw, stage="asset",
                                                    target=target))

    def _why(self):
        return produce._why_ref_missing(
            self.pj, self.src, ApiError(f"参考图文件不存在: {self.src}"))

    def test_it_points_at_the_asset_that_failed(self):
        """★ 这就是那两条记录的关系。"""
        self._record("ST001", "QUOTA_EXHAUSTED",
                     "HTTP 429: No available image quota")
        m = str(self._why())
        self.assertIn("ST001 自己没出成", m)
        self.assertIn("quota", m.lower(), "把它的原文带上，不然还得再去翻一遍")

    def test_it_stops_being_retryable(self):
        """★ ST001 不出来，这一条重试一百次也一样缺参考图。"""
        self._record("ST001", "QUOTA_EXHAUSTED", "x")
        self.assertEqual(self._why().kind, TASK_FATAL)

    def test_the_fix_says_to_deal_with_the_other_one(self):
        self._record("ST001", "QUOTA_EXHAUSTED", "x")
        self.assertIn("先把 ST001 出出来", " ".join(self._why().extra_fix))

    def test_an_unrelated_failure_is_not_blamed(self):
        """★ 别乱指：别的资产失败跟这张图没关系。"""
        self._record("C007", "CONTENT_REJECTED", "别人的问题")
        self.assertNotIn("没出成", str(self._why()))

    def test_with_no_failures_on_record_the_message_is_unchanged(self):
        """真的是文件被删了 —— 那就还是原来那句话，别编一个原因出来。"""
        exc = ApiError(f"参考图文件不存在: {self.src}")
        self.assertIs(produce._why_ref_missing(self.pj, self.src, exc), exc)

    def test_a_source_without_a_filename_does_not_crash(self):
        exc = ApiError("参考图文件不存在: ")
        self.assertIs(produce._why_ref_missing(self.pj, "", exc), exc)

    def test_the_resolver_actually_runs_it(self):
        """★ 写了函数没接上等于没写。"""
        import inspect
        self.assertIn("_why_ref_missing(pj, src, exc)",
                      inspect.getsource(produce.make_ref_resolver))


class EndToEndTests(unittest.TestCase):
    """真的走一遍 make_ref_resolver 返回的那个 resolve()。"""

    def setUp(self):
        self.pj = new_project()

    def tearDown(self):
        shutil.rmtree(self.pj.root, ignore_errors=True)

    def test_a_missing_ref_reaches_the_better_message(self):
        class Prov:
            def needs_url(self, model="", media="image"):
                return False

            def needs_bytes(self, model=""):
                return False

            def accepts_url(self, model="", media="image"):
                return False

        diagnose.record(self.pj.root, diagnose.warn(
            "QUOTA_EXHAUSTED", "HTTP 429: No available image quota",
            stage="asset", target="ST001"))
        resolve = produce.make_ref_resolver(self.pj, Prov(), {}, "m", "image")
        src = "02_固定资产/连续状态资产/ST001.png"
        with self.assertRaises(ApiError) as cm:
            resolve(src, log=lambda *a: None)
        self.assertIn("ST001 自己没出成", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
