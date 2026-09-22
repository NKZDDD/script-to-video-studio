# -*- coding: utf-8 -*-
"""超模换址 + 接 image 2.5。全部依据 2026-09-14 用真 Key 实拉的结果。

旧地址 `www.chaomoapi.com` **已经连不上**：DNS 还解析得到 43.227.71.72、
80 端口活着（回 404 的 HTML），**443 在 TCP 层就超时** —— 按域名、按 IP、
直连、走系统代理，四种都试过。新地址 `https://zntcode.net`。
"""
import io
import os
import re
import unittest

from core.providers.chaomo import (IMAGE_25, IMAGE_MODEL_OPTIONS, IMAGE_MODELS,
                                   RATIOS, _RATIO_VALUES, ChaomoProvider,
                                   _to_ratio)

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "core", "providers", "chaomo.py")

# 实拉 `GET /v1/models` 回的那九个（这把 Key 可见的）。
LIVE = ["gpt-image-2.5-flare", "gpt-image-2.5-flare-2K-Native",
        "gpt-image-2.5-flare-4K-Native", "gpt-image-2.5-sunburst",
        "gpt-image-2.5-sunburst-2K-Native", "gpt-image-2.5-sunburst-4K-Native",
        "gpt-image2-1K-Native", "gpt-image2-2K-Native", "gpt-image2-4K-Native"]

# 非法 ratio 时接口原样列出来的十个（`unsupported_ratio` 那条报错里抄的）。
LIVE_RATIOS = ["1:1", "5:4", "9:16", "21:9", "16:9", "3:2", "4:3", "4:5", "3:4", "2:3"]


class ChaomoNewHostTests(unittest.TestCase):
    def test_the_base_url_moved(self):
        """★ 换址。老地址 443 已经不通，不换就是每条都连接超时。"""
        self.assertEqual(ChaomoProvider.default_base_url, "https://zntcode.net")

    def test_image_2_5_is_listed(self):
        """★ 两个系列 × 三档，六个都要在清单里。"""
        self.assertEqual(len(IMAGE_25), 6)
        for m in IMAGE_25:
            self.assertIn(m, IMAGE_MODELS, m)
        for fam in ("flare", "sunburst"):
            got = [m for m in IMAGE_25 if fam in m]
            self.assertEqual(len(got), 3, f"{fam} 该有 1K/2K/4K 三档")

    def test_everything_the_pull_saw_is_listed(self):
        for m in LIVE:
            self.assertIn(m, IMAGE_MODELS, m)

    def test_models_that_one_key_cannot_see_are_kept(self):
        """★ 这把 Key 看不见的**不删**。

        超模按能力分四把 Key（llm / image_1k / image_4k / video），
        一把看见的只是它那一组。拿一把的结果去删清单，会删掉别的 Key
        其实能用的模型 —— 而「少列一个能用的」是页面上根本没有、
        人只会以为不支持；「多列一个下线的」选中了至少有一句响的报错。
        """
        for m in ("gpt-image-1k-th", "gemini-3-pro-image-preview"):
            self.assertIn(m, IMAGE_MODELS, m)

    def test_the_default_model_exists_upstream(self):
        """★ 默认模型必须是实拉里真有的，而且**两处要一致**。

        原来 default_model 和代码兜底都是 `gpt-image2-1K` —— 实拉里没有，
        也就是「没改过模型就点开始」必然报找不到模型（鹤的 sd2-720p 同款）。
        """
        cap = ChaomoProvider().capabilities()
        dm = cap["image"]["default_model"]
        self.assertIn(dm, LIVE, "默认模型实拉里不存在")
        fb = re.search(r'model = task\.model or "([^"]+)"',
                       io.open(SRC, encoding="utf-8").read()).group(1)
        self.assertEqual(fb, dm, "代码兜底和 default_model 对不上")

    def test_ratios_match_what_the_service_allows(self):
        """★ 十个比例，和接口报错里列的一字不差。

        以前少了 `5:4` 和 `4:5` —— 少列的后果是页面上选不到，
        人只会以为这家不支持。
        """
        self.assertEqual(sorted(RATIOS), sorted(LIVE_RATIOS))

    def test_every_ratio_has_a_numeric_value(self):
        """★ 加比例必须同时加 `_RATIO_VALUES`。

        `_to_ratio` 遍历 RATIOS、去那张表取值，缺一个就 KeyError ——
        而且**只在「传的是像素尺寸」那条路上崩**（比例写法会提前返回），
        所以加比例时很容易漏。2026-09-14 加 5:4/4:5 就漏了一次，
        `1024x1280` 整条炸掉。
        """
        self.assertEqual(set(RATIOS) - set(_RATIO_VALUES), set())
        # 像素尺寸那条路不许再崩
        for px, want in (("1024x1280", "4:5"), ("1024x1536", "2:3"),
                         ("2048x2048", "1:1"), ("1920x1080", "16:9")):
            self.assertEqual(_to_ratio(px), want, px)

    def test_per_model_options_only_record_what_was_declared(self):
        """★ 只写声明了的。

        2.5 系列声明了 max_reference_images=9 / n_max=4 / quality 四档；
        gpt-image2-*-Native 的 max_reference_images 是 **null**（没声明）——
        所以不给它填数。填一个「看起来合理」的和照文档抄没区别。
        """
        for m in IMAGE_25:
            o = IMAGE_MODEL_OPTIONS[m]
            self.assertEqual(o["max_refs"], 9, m)
            self.assertEqual(o["n_max"], 4, m)
            self.assertEqual(o["quality"], ["auto", "low", "medium", "high"], m)
        for m in ("gpt-image2-1K-Native", "gpt-image2-4K-Native"):
            self.assertNotIn("max_refs", IMAGE_MODEL_OPTIONS.get(m, {}),
                             f"{m} 没声明参考图张数，不该替它填")

    def test_the_notes_record_that_three_field_names_all_work(self):
        """实测：`ratio` / `size` / `aspect_ratio` 三个名字都被同一套校验读。

        原来 notes 写的是「比例字段是 ratio（不是 size/aspect_ratio）」——
        那句话会让人以为另两个不能用。
        """
        notes = ChaomoProvider().capabilities()["image"]["notes"]
        for f in ("ratio", "size", "aspect_ratio"):
            self.assertIn(f, notes)


if __name__ == "__main__":
    unittest.main()
