# -*- coding: utf-8 -*-
"""网关同时给 url 和 b64_json 时，**取链接**。

实跑（超模，烟火尽头06）：

    asset P006 | 残图，大小 3055 字节
    来源：响应内嵌数据（4096 字符，开头 'data:image/png;base64,iVBORw0KGgo…'）

4096 是个太整的数 —— 一张 1254×1254 的 PNG 有几百 KB。也就是说
**我们拿到的 base64 本身就只有 4KB**，不是解码解坏了。

ComfyUI 那边早就把这个坑写清楚了（utils.extract_data_array_images）：

    「同一张图会被数成多张 —— 最典型的是网关同时给了 url 和 b64_json
      （4K 模型常见）」

而 studio 这边的 extract_image_items 是**无条件优先 b64_json** 的，
正好每次都挑中会被截断的那个。超模自己的文档也写着
「异步任务固定返回 URL 结果」—— 几 MB 的图塞进 JSON 的 base64 字段，
传输链上任何一处丢字节，整张图就报废。
"""
import unittest

from core.apiutil import data_array_images, extract_image_items

URL = "https://cdn.chaomo.example/img/abc.png"
B64 = "iVBORw0KGgoAAAANSUhEUgAABOY" * 20        # 假装是被截断的那 4KB


class DataArrayTests(unittest.TestCase):

    def test_url_wins_when_both_are_given(self):
        """★ 这就是那批残图的源头。"""
        got = extract_image_items(
            {"data": [{"url": URL, "b64_json": B64}]})
        self.assertEqual(got, [URL])

    def test_one_element_is_one_image(self):
        """★ 同时给两种时递归扫描会数成两张 —— 批次里出现重复画面。"""
        self.assertEqual(len(extract_image_items(
            {"data": [{"url": URL, "b64_json": B64}]})), 1)

    def test_b64_is_used_when_there_is_no_url(self):
        got = extract_image_items({"data": [{"b64_json": B64}]})
        self.assertEqual(got, ["data:image/png;base64," + B64])

    def test_an_already_prefixed_b64_is_not_double_wrapped(self):
        full = "data:image/png;base64," + B64
        self.assertEqual(extract_image_items({"data": [{"b64_json": full}]}), [full])

    def test_a_nested_image_url_object(self):
        self.assertEqual(
            extract_image_items({"data": [{"image_url": {"url": URL}}]}), [URL])

    def test_several_elements_keep_their_order(self):
        got = extract_image_items({"data": [{"url": URL + "1"},
                                            {"url": URL + "2"}]})
        self.assertEqual(got, [URL + "1", URL + "2"])

    def test_plain_strings_in_data_work_too(self):
        self.assertEqual(extract_image_items({"data": [URL]}), [URL])


class FallbackTests(unittest.TestCase):
    """`data[]` 不规范时才退回递归扫描 —— 别把别家弄坏了。"""

    def test_no_data_array_falls_back(self):
        self.assertEqual(
            extract_image_items({"result": {"image_url": URL}}), [URL])

    def test_an_empty_data_array_falls_back(self):
        self.assertEqual(
            extract_image_items({"data": [], "output": {"url": URL}}), [URL])

    def test_data_that_is_not_a_list_falls_back(self):
        self.assertEqual(
            extract_image_items({"data": {"url": URL}}), [URL])

    def test_a_bare_b64_response_still_works(self):
        got = extract_image_items({"images": [{"b64_json": B64}]})
        self.assertEqual(got, ["data:image/png;base64," + B64])

    def test_the_strict_pass_says_nothing_when_it_cannot_help(self):
        self.assertEqual(data_array_images({"foo": 1}), [])
        self.assertEqual(data_array_images("不是字典"), [])


class ChaomoWiringTests(unittest.TestCase):
    """超模那一侧的三件事。"""

    def test_both_paths_ask_for_async_and_url(self):
        """★ 异步固定返回 URL —— 整条 base64 传输链就不存在了。"""
        import inspect

        from core.providers.chaomo import ChaomoProvider
        src = inspect.getsource(ChaomoProvider.generate_image)
        self.assertIn('"async"', src)
        self.assertIn('"response_format"', src)
        self.assertIn("include_metadata", src)

    def test_the_ref_guard_counts_real_attachments(self):
        """★ 拿魔法数字当哨兵，加一个字段就会把它悄悄废掉。

        原来是 `if len(files) == 5`（当时基数正好 5）。后来加了
        async / include_metadata / quality，基数变成 7 ——
        于是「一张参考图都没转成文件」时照样把请求发出去，
        出来的图不是同一个人，而任务标 ok。
        """
        import inspect

        from core.providers.chaomo import ChaomoProvider
        src = inspect.getsource(ChaomoProvider.generate_image)
        self.assertIn("if not attached:", src)
        # 只看代码，不看注释 —— 注释里写着这个 bug 当初长什么样，
        # 那段说明本身不该让测试失败
        code = "\n".join(ln for ln in src.splitlines()
                         if not ln.strip().startswith("#"))
        self.assertNotIn("len(files) == 5", code)
        self.assertNotIn("len(files) - 5", code)

    def test_the_metadata_check_uses_the_polled_payload(self):
        """★ 开了 async 之后，metadata 在轮询结果里，提交响应里没有。

        拿提交时那份去核对，永远是空的 —— 那道核对等于没接。
        """
        import inspect

        from core.providers.chaomo import ChaomoProvider
        src = inspect.getsource(ChaomoProvider.generate_image)
        self.assertIn("self.meta_of(final)", src)
        self.assertNotIn("self.meta_of(data)", src)

    def test_a_short_payload_is_caught_against_the_reported_size(self):
        """★ 服务商自己说了这张图多少字节 —— 那就真拿来核对。"""
        from core.apiutil import ApiError
        from core.providers.chaomo import ChaomoProvider
        import base64
        raw = b"\x89PNG\r\n\x1a\n" + b"x" * 3000
        item = "data:image/png;base64," + base64.b64encode(raw).decode()
        with self.assertRaises(ApiError) as cm:
            ChaomoProvider.check_meta({"bytes": 500000}, [item],
                                      log=lambda *a: None)
        self.assertIn("传输途中丢了", str(cm.exception))

    def test_a_matching_size_passes(self):
        from core.providers.chaomo import ChaomoProvider
        import base64
        raw = b"\x89PNG\r\n\x1a\n" + b"x" * 3000
        item = "data:image/png;base64," + base64.b64encode(raw).decode()
        ChaomoProvider.check_meta({"bytes": len(raw)}, [item], log=lambda *a: None)

    def test_urls_are_left_to_the_size_check_downstream(self):
        """链接结果没法在这儿核对字节 —— 由落盘那一步的大小/残图检查兜底。"""
        from core.providers.chaomo import ChaomoProvider
        ChaomoProvider.check_meta({"bytes": 500000}, [URL], log=lambda *a: None)


if __name__ == "__main__":
    unittest.main()
