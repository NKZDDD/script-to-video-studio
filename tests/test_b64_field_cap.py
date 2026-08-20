# -*- coding: utf-8 -*-
"""内嵌 base64 被截在整数长度上 = 字段上限，不是随机抖动。

用户实遇（超模 gpt-image-1k-th，0819 14:47）：

    来源：响应内嵌数据（4096 字符，开头 'data:image/png;base64,iVBORw0K
    S002: 这次没成功：这张图没有结尾标记，是个残图（只解出/下载了一半）。
          大小看着正常（3055 字节），但打不开
    S002: 第 2 次重试（最多 2 次）

**4096 是 2 的 12 次方。** 减去 `data:image/png;base64,` 那 22 个前缀 = 4074
字符，4074×3/4 = 3055 字节 —— 和量出来的残图一模一样。也就是说这条线路把
内嵌图片字段截在 4096，每次都截在同一处。

于是那两次重试是**确定性地白花钱**：一张资产出了三次图，三次都是同一张残图。
日志里 S002 / G001 / S008 三个资产都是这样。

同一批还暴露了第二个问题：递归兜底解析把 b64 排在链接前面 —— 同一份响应里
有好的 url 时也会取那段残 b64。
"""
import unittest

from core.apiutil import TASK_FATAL, ApiError, _field_cap, extract_image_items


class FieldCapTests(unittest.TestCase):
    """认出「这是个上限」而不是「这次运气差」。"""

    def test_the_real_case(self):
        """★ 用户日志里那一行。"""
        self.assertEqual(
            _field_cap("响应内嵌数据（4096 字符，开头 'data:image/png;base64,iVBORw0K"),
            4096)

    def test_other_powers_of_two(self):
        for n in (8192, 16384, 65536):
            self.assertEqual(_field_cap(f"响应内嵌数据（{n} 字符）"), n, n)

    def test_multiples_of_1024(self):
        self.assertEqual(_field_cap("响应内嵌数据（10240 字符）"), 10240)

    def test_a_random_length_is_not_a_cap(self):
        """★ 别拦过头：随机截断重试一次经常就好，判成上限就白丢一次机会。"""
        for n in (5137, 3055, 100001):
            self.assertEqual(_field_cap(f"响应内嵌数据（{n} 字符）"), 0, n)

    def test_something_too_small_is_not_a_cap(self):
        """几百字符不可能是图片字段上限，那是别的毛病。"""
        self.assertEqual(_field_cap("响应内嵌数据（800 字符）"), 0)

    def test_a_url_source_is_not_a_cap(self):
        """链接下载走的是另一条路（.part + 原子改名），不适用这一条。"""
        self.assertEqual(_field_cap("https://cdn.example/x/y.png"), 0)

    def test_no_number_no_cap(self):
        self.assertEqual(_field_cap(""), 0)
        self.assertEqual(_field_cap("响应内嵌数据"), 0)


class PreferUrlTests(unittest.TestCase):
    """递归兜底也要链接优先。"""

    def test_a_url_beats_a_truncated_b64_in_the_same_payload(self):
        """★ 这就是那个 bug。

        严格的 `data[]` 路径本来是链接优先的，但它只在 `{"data":[...]}`
        这种规范结构上生效。轮询 `/v1/images/{id}` 回来的结构一旦不是那个
        形状就落到递归兜底 —— 而那边 dict 分支先看 b64_json、再往下走，
        于是取到了被截断的那一段。
        """
        got = extract_image_items({
            "task_id": "t1",
            "images": [{"b64_json": "iVBORw0KGgo" + "A" * 4000,
                        "url": "https://cdn/x/good.png"}]})
        self.assertTrue(got[0].startswith("https://"), got[0][:60])

    def test_a_url_nested_deeper_still_wins(self):
        got = extract_image_items({
            "b64_json": "iVBORw0KGgo" + "A" * 100,
            "result": {"output": {"file": "https://cdn/x/deep.png"}}})
        self.assertTrue(got[0].startswith("https://"))

    def test_the_b64_is_still_kept_as_a_fallback(self):
        """★ 别把 b64 丢掉：很多家只给这个。它只是排在后面。"""
        got = extract_image_items({
            "images": [{"b64_json": "iVBORw0KGgo", "url": "https://cdn/x.png"}]})
        self.assertEqual(len(got), 2)
        self.assertTrue(got[1].startswith("data:image"))

    def test_b64_only_still_works(self):
        got = extract_image_items({"images": [{"b64_json": "iVBORw0KGgo"}]})
        self.assertEqual(len(got), 1)
        self.assertTrue(got[0].startswith("data:image"))

    def test_the_strict_path_still_wins_when_it_applies(self):
        """规范结构照旧走严格路径（一个元素一张、链接优先），这次没碰它。"""
        got = extract_image_items({"data": [
            {"url": "https://cdn/a.png", "b64_json": "AAAA"},
            {"url": "https://cdn/b.png"}]})
        self.assertEqual(got, ["https://cdn/a.png", "https://cdn/b.png"])


class NotRetryableTests(unittest.TestCase):
    """撞上限的那次不许再重试。"""

    def setUp(self):
        import os
        import tempfile
        self.dir = tempfile.mkdtemp()
        self.dest = os.path.join(self.dir, "x.png")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.dir, ignore_errors=True)

    def _save(self, n_chars):
        """造一张「够大但没有 IEND」的假 PNG，来源写成 n_chars 个字符。"""
        import os

        from core import apiutil
        with open(self.dest, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 4000)
        with self.assertRaises(ApiError) as cm:
            apiutil._check_saved(self.dest, f"响应内嵌数据（{n_chars} 字符）")
        self.assertFalse(os.path.exists(self.dest), "残图必须删掉")
        return cm.exception

    def test_a_capped_length_is_task_fatal(self):
        """★ 这一条直接省钱：以前每张残图都会再出两次。"""
        exc = self._save(4096)
        self.assertEqual(getattr(exc, "kind", None), TASK_FATAL)
        self.assertIn("整数上限", str(exc))
        self.assertIn("不再重试", str(exc))

    def test_it_says_what_to_do(self):
        s = str(self._save(4096))
        self.assertIn("链接", s)
        self.assertIn("排给别家", s)

    def test_a_random_truncation_is_still_retryable(self):
        """★ 别拦过头：随机截断重试一次经常就好。"""
        from core.apiutil import RETRYABLE
        self.assertEqual(getattr(self._save(5137), "kind", None), RETRYABLE)


if __name__ == "__main__":
    unittest.main()
