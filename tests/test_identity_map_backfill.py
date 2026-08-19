# -*- coding: utf-8 -*-
"""模型写了结构化的身份映射、漏了正文里那几行 —— 我们自己补出来。

实跑一次里 7 个资产都这样（PI008、PSET001、PH006/007/010/011、PI009）：

    出问题的是 **PI008** 这一条的提示词，提示词里没有 `Image N = 资产ID`
    的参考图映射，但这一条要传 2 张参考图（PS009、LOC010）。

n4b 的模板**两样都要求**：结构化的 `reference_role_map`，和正文里逐项分行的
`Image N = <asset_id> <名称>` + 六个字段。模型经常写了前者、漏了后者。

**而那两样是同一份信息。** role_map 里每一项都带着 image_n、asset_id、
名称和六个字段 —— 正文里那几行就是它的文字形式。既然数据在手上，
补出来是确定性的，不用再花一次调用去问模型「把你已经写过的再写一遍」。
"""
import os
import shutil
import unittest

from core.produce import check_identity_map, check_image_map
from core.run_v34 import with_identity_map

FULL = {"image_n": 1, "asset_id": "PS009", "asset_name": "菜摊",
        "who_what_visible": "菜摊正面全貌", "story_time_state": "基准状态",
        "must_preserve": "摊位结构与货品摆放", "must_not_copy": "光线与机位",
        "applicable_scope": "本次生成这一张"}


def ap(prompt="一把青菜的四视图，白底。", rows=(FULL,)):
    return {"asset_id": "PI008", "prompt": prompt,
            "reference_role_map": [dict(r) for r in rows]}


class BackfillTests(unittest.TestCase):

    def test_the_missing_lines_are_generated(self):
        """★ 这就是那 7 条硬失败。"""
        out = with_identity_map(ap())
        self.assertIn("Image 1 = PS009 菜摊", out)
        self.assertIn("菜摊正面全貌", out)
        self.assertIn("一把青菜的四视图", out, "原文要留着")

    def test_the_result_passes_the_production_check(self):
        """★ 补出来的东西要真能过关，不是看着像。"""
        out = with_identity_map(ap())
        refs = [{"image_n": 1, "asset_id": "PS009"}]
        self.assertEqual(check_image_map(out, refs, "PI008", "x")[0], "")
        self.assertEqual(check_identity_map(out, refs, "PI008", "x")[0], "")

    def test_two_references_both_get_a_slot(self):
        second = dict(FULL, image_n=2, asset_id="LOC010", asset_name="菜市场",
                      who_what_visible="菜市场全景", must_preserve="场地布局",
                      must_not_copy="人物")
        out = with_identity_map(ap(rows=(FULL, second)))
        refs = [{"image_n": 1, "asset_id": "PS009"},
                {"image_n": 2, "asset_id": "LOC010"}]
        self.assertEqual(check_image_map(out, refs, "PI008", "x")[0], "")
        self.assertEqual(check_identity_map(out, refs, "PI008", "x")[0], "")

    def test_a_prompt_that_already_has_the_lines_is_left_alone(self):
        """★ 写了一部分说明模型有自己的排版，插进去只会打乱它。"""
        had = "Image 1 = PS009 菜摊\n  是谁/是什么 + 画面可见内容：正面\n正文"
        self.assertEqual(with_identity_map(ap(prompt=had)), had)

    def test_no_role_map_means_no_change(self):
        """没有数据就补不出来 —— 不许凭空编。"""
        self.assertEqual(with_identity_map(ap(rows=())), "一把青菜的四视图，白底。")

    def test_empty_fields_are_omitted_not_faked(self):
        """★ 写个「（未填）」等于骗过校验：那一项看起来填了，实际什么都没说。

        缺项让下游报个提醒是对的 —— 假装填了会把真问题藏起来。
        """
        thin = {"image_n": 1, "asset_id": "PS009", "asset_name": "菜摊",
                "who_what_visible": "菜摊正面"}
        out = with_identity_map(ap(rows=(thin,)))
        self.assertIn("Image 1 = PS009 菜摊", out)
        self.assertNotIn("未填", out)
        self.assertNotIn("有权控制", out)

    def test_a_row_without_an_asset_id_is_skipped(self):
        """说不出是谁的那一项，补出来也没用。"""
        out = with_identity_map(ap(rows=({"image_n": 1, "asset_name": "?"},)))
        self.assertNotIn("Image 1", out)

    def test_the_writer_actually_uses_it(self):
        """★ 写了函数没接上等于没写。"""
        import inspect

        from core import run_v34 as R
        self.assertIn("with_identity_map(ap)",
                      inspect.getsource(R.write_prompt_files))


class RefSuffixTests(unittest.TestCase):
    """参考图文件名带后缀时，也要认得出是哪个资产没出成。"""

    def setUp(self):
        from test_v34_run import new_project
        self.pj = new_project()

    def tearDown(self):
        shutil.rmtree(self.pj.root, ignore_errors=True)

    def test_a_suffixed_filename_still_finds_the_cause(self):
        """★ 实跑：LK006 报「参考图 PH006_R01.png 不存在」，而 PH006 自己
        就在同一份清单里失败着 —— 程序却没把两条连起来。"""
        from core import diagnose, produce
        from core.apiutil import ApiError
        diagnose.record(self.pj.root, diagnose.warn(
            "CONTENT_REJECTED", "被安全政策拦截", stage="asset", target="PH006"))
        src = "02_固定资产/人物身份资产/PH006_R01.png"
        got = produce._why_ref_missing(self.pj, src, ApiError(f"不存在：{src}"))
        self.assertIn("PH006 自己没出成", str(got))

    def test_a_plain_filename_still_works(self):
        from core import diagnose, produce
        from core.apiutil import ApiError
        diagnose.record(self.pj.root, diagnose.warn(
            "CONTENT_REJECTED", "x", stage="asset", target="ST001"))
        got = produce._why_ref_missing(
            self.pj, "a/ST001.png", ApiError("不存在：a/ST001.png"))
        self.assertIn("ST001 自己没出成", str(got))

    def test_an_unrelated_asset_is_not_blamed(self):
        """★ 别乱指：后缀剥完之后也得真的对上。"""
        from core import diagnose, produce
        from core.apiutil import ApiError
        diagnose.record(self.pj.root, diagnose.warn(
            "CONTENT_REJECTED", "x", stage="asset", target="PH009"))
        got = produce._why_ref_missing(
            self.pj, "a/PH006_R01.png", ApiError("不存在：a/PH006_R01.png"))
        self.assertNotIn("没出成", str(got))


if __name__ == "__main__":
    unittest.main()
