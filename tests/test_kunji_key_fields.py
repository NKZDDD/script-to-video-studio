# -*- coding: utf-8 -*-
"""坤鸡的 4K 要单独一把 Key —— 页面上得有它自己的框。

底层一直支持 `1k=sk-a;4k=sk-b` 这个写法（`kunji.parse_keys`），
可页面上只有一个密码框，用户得**自己知道这个写法**。不知道就只填一把，
然后要 4K 时拿 1K 分组的 Key 去要 —— 服务商**静默降级**：
你以为出了 4K，实际拿到 1K，不报错，图也在。

所以按超模那种写法来：一把一个框。存下来仍然是那一个字符串
（服务商那边本来就认它），所以服务商一行代码都不用改，
老项目里存的单把 Key 也照旧能用。
"""
import io
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server"))

import app as A                                          # noqa: E402
from core import providers as P                          # noqa: E402
from core.providers.kunji import KunjiProvider, parse_keys  # noqa: E402


class DeclarationTests(unittest.TestCase):

    def test_kunji_declares_one_field_per_tier(self):
        got = [f[0] for f in KunjiProvider.key_fields]
        self.assertEqual(got, ["image_1k_api_key", "image_4k_api_key",
                               "image_high_api_key", "video_api_key"])

    def test_the_4k_field_says_it_is_required_for_4k(self):
        """★ 不说的话，人会以为 1K 那把也能出 4K —— 而降级是静默的。"""
        why = dict((f[0], f[3]) for f in KunjiProvider.key_fields)
        self.assertIn("要 4K 就必须有这一把", why["image_4k_api_key"])

    def test_every_field_maps_to_a_group_parse_keys_understands(self):
        """★ 前缀写错了不会报错 —— parse_keys 直接忽略，那一把等于没填。"""
        for (_name, gid, _label, _why) in KunjiProvider.key_fields:
            self.assertEqual(parse_keys(f"{gid}=sk-x").get(gid), "sk-x",
                             f"{gid} 不是 parse_keys 认的分组前缀")

    def test_the_capability_payload_carries_it(self):
        """★ 不下发的话页面照旧渲染一个框，改了后端等于没改。"""
        cap = next(c for c in P.list_capabilities() if c["id"] == "kunji")
        self.assertEqual(len(cap["key_fields"]), 4)

    def test_other_providers_are_untouched(self):
        for c in P.list_capabilities():
            if c["id"] != "kunji":
                self.assertEqual(c.get("key_fields") or [], [], c["id"])


class MergeTests(unittest.TestCase):
    """几个框 → 一个 api_key。每一把各自「留空 = 不改」。"""

    def _m(self, incoming, saved_key=""):
        r = A._merge_group_keys("kunji", dict(incoming),
                                {"api_key": saved_key} if saved_key else {})
        return parse_keys(r.get("api_key", ""))

    def test_one_key_is_stored_in_the_grouped_form(self):
        self.assertEqual(self._m({"image_4k_api_key": "sk-four"})["4k"], "sk-four")

    def test_changing_4k_does_not_wipe_1k(self):
        """★ 不按把合并的话，改 4K 会把 1K 清掉 —— 而清掉之后 1K 回落到

        default，图照样出得来，只是用的是另一把 Key，谁都看不出来。
        """
        got = self._m({"image_4k_api_key": "sk-NEW"}, "1k=sk-one;4k=sk-old")
        self.assertEqual(got["1k"], "sk-one")
        self.assertEqual(got["4k"], "sk-NEW")

    def test_a_blank_box_changes_nothing(self):
        got = self._m({"image_4k_api_key": "", "image_1k_api_key": ""},
                      "1k=sk-one")
        self.assertEqual(got["1k"], "sk-one")

    def test_a_masked_echo_is_not_stored_as_a_key(self):
        """★ 掩码回显被存回去是真踩过的（access_key 存成 5 个字符）。"""
        got = self._m({"image_4k_api_key": "sk-f…"}, "4k=sk-four")
        self.assertEqual(got["4k"], "sk-four")

    def test_a_legacy_single_key_survives(self):
        """★ 老项目只填过一把。补 4K 的时候把它冲掉 = 其余档位没 Key 了。"""
        got = self._m({"image_4k_api_key": "sk-four"}, "sk-legacy")
        self.assertEqual(got["default"], "sk-legacy")
        self.assertEqual(got["4k"], "sk-four")

    def test_a_pasted_whole_string_is_the_base(self):
        """★ 用户会把客服给的整段粘进来再改一把。拿已存的当底就把它丢了。"""
        r = A._merge_group_keys("kunji",
                                {"api_key": "1k=sk-a;4k=sk-b",
                                 "image_4k_api_key": "sk-NEW"}, {})
        got = parse_keys(r["api_key"])
        self.assertEqual((got["1k"], got["4k"]), ("sk-a", "sk-NEW"))

    def test_providers_without_split_keys_are_untouched(self):
        r = A._merge_group_keys("paisio", {"api_key": "sk-x"}, {})
        self.assertEqual(r, {"api_key": "sk-x"})

    def test_nothing_submitted_leaves_api_key_alone(self):
        """★ 只改了 Base URL 那次不许动 api_key。"""
        r = A._merge_group_keys("kunji", {"base_url": "http://x"},
                                {"api_key": "1k=sk-one"})
        self.assertNotIn("api_key", r)

    def test_the_field_names_never_reach_the_stored_config(self):
        """★ 明文密钥不许以这几个名字落进 config.json。"""
        r = A._merge_group_keys("kunji", {"image_4k_api_key": "sk-four"}, {})
        self.assertNotIn("image_4k_api_key", r)


class StatusTests(unittest.TestCase):
    """哪几把已保存 —— 页面靠它显示「已保存，留空不改」。"""

    def test_it_reports_each_field(self):
        st = A._provider_key_status("kunji", {"api_key": "1k=a;4k=b"})
        self.assertTrue(st["image_1k_api_key"])
        self.assertTrue(st["image_4k_api_key"])
        self.assertFalse(st["image_high_api_key"])

    def test_a_single_legacy_key_shows_as_default_only(self):
        """★ 不这么报的话，老用户看到四个空框会重新粘一遍 ——

        而重新粘的时候很容易只粘一把，把另外几把冲掉。
        """
        st = A._provider_key_status("kunji", {"api_key": "sk-solo"})
        self.assertTrue(st["key_default_only"])
        self.assertFalse(st["image_4k_api_key"])

    def test_it_never_returns_the_key_itself(self):
        st = A._provider_key_status("kunji", {"api_key": "1k=sk-secret"})
        self.assertNotIn("sk-secret", repr(st))


class PageTests(unittest.TestCase):
    """页面上真的按它渲染了 —— 不然后端改了等于没改。"""

    @staticmethod
    def _html():
        return io.open(os.path.join(ROOT, "web", "index.html"),
                       encoding="utf-8").read()

    def test_the_page_renders_one_box_per_declared_field(self):
        t = self._html()
        self.assertIn("(c.key_fields || []).length", t)
        self.assertIn("data-f=\"${field}\"", t)

    def test_the_header_counts_how_many_are_filled(self):
        """★ 只显示「已配置」的话，缺 4K 那一把看不出来。"""
        self.assertIn("已配置 ${kfReady}/${kf.length}", self._html())

    def test_the_high_field_is_in_the_secret_list(self):
        """★ 漏了的话它会以明文原样存进 config.json。"""
        t = io.open(os.path.join(ROOT, "server", "app.py"),
                    encoding="utf-8").read()
        m = re.search(r"SECRET = \((.*?)\)", t, re.S)
        for name in ("image_1k_api_key", "image_4k_api_key",
                     "image_high_api_key", "video_api_key"):
            self.assertIn(name, m.group(1), name)


if __name__ == "__main__":
    unittest.main()
