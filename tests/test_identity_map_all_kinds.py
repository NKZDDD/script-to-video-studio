# -*- coding: utf-8 -*-
"""身份映射回填要覆盖**四类**提示词，不是两类。

用户报（2026-08-21）：「图片的映射关系在提示词中没有出现，导致不能做下去」。

`with_identity_map()` 原来只认 `prompt` + `reference_role_map` —— 那是**资产**
提示词的字段名。故事板叫 `storyboard_prompt` + `reference_order`，
视频叫 `video_prompt` + `reference_order`。于是：

    n4b 资产提示词    ✅ 回填
    n11 场景状态图    ✅ 回填
    n12 故事板        ❌ 一行都补不上
    n13 视频          ❌ 一行都补不上

模型经常写了结构化字段、漏了正文里那几行（`Image N = <ID> <名称>` + 六项）。
资产和场景状态图能自己补上，故事板和视频直接硬停在出图/出片之前：
「要传 N 张参考图，却没说哪张是谁」。

顺手查到另一件，更严重：**电影级视频的补充参考图从来没被上传过。**
出片那一层只读 `aux_reference`，而那个字段只有 V6.1 会设；电影级把补图放在
`reference_images`，没有一处读它。后果不是报错 —— 提示词里写着
`Image 3 = LK002 当前造型`，实际只传了骨架那两张，**编号整体错位**。
"""
import inspect
import unittest

from core import produce as P
from core.run_v34 import with_identity_map as W


def _rows():
    return [{"image_n": 1, "asset_id": "SCST_EP01_SC01_01",
             "asset_name": "本段场景状态图",
             "who_what_visible": "两人在病床边",
             "story_time_state": "起始稳定状态",
             "must_preserve": "身份与造型", "must_not_copy": "中性机位"},
            {"image_n": 2, "asset_id": "PS001", "asset_name": "木盒"}]


class FieldNameTests(unittest.TestCase):
    """四类的字段名都不一样 —— 认全，别只认资产那一套。"""

    def test_asset_prompts_still_work(self):
        out = W({"prompt": "正文", "reference_role_map": _rows()})
        self.assertIn("Image 1 = SCST_EP01_SC01_01", out)

    def test_a_storyboard_sheet_gets_backfilled(self):
        """★ 这一类以前一行都补不上。"""
        out = W({"storyboard_prompt": "任务与本包身份：生成 SHEET_A。",
                 "reference_order": _rows()})
        self.assertIn("Image 1 = SCST_EP01_SC01_01 本段场景状态图", out)
        self.assertIn("Image 2 = PS001 木盒", out)
        self.assertIn("生成 SHEET_A", out, "正文丢了")

    def test_a_video_plan_gets_backfilled(self):
        out = W({"video_prompt": "视频执行计划正文。", "reference_order": _rows()})
        self.assertIn("Image 1 = SCST_EP01_SC01_01", out)
        self.assertIn("视频执行计划正文", out)

    def test_the_six_fields_come_along(self):
        """★ 只写 `Image N = ID` 不够 —— 六项决定这张图**管什么**。"""
        out = W({"storyboard_prompt": "x", "reference_order": _rows()})
        for label in ("是谁/是什么", "故事时间 / 当前状态", "有权控制", "无权控制"):
            self.assertIn(label, out)

    def test_a_row_without_an_id_is_skipped(self):
        """说不出是谁的那一项，补了也没用 —— 而且会把编号顶掉。"""
        out = W({"storyboard_prompt": "x",
                 "reference_order": [{"image_n": 1, "asset_id": ""},
                                     {"image_n": 2, "asset_id": "PS001"}]})
        self.assertIn("Image 2 = PS001", out)
        self.assertNotIn("Image 1 =", out)

    def test_it_keeps_the_models_own_numbering(self):
        """★ 重排编号 = 提示词说的和实际上传的对不上（LOC001/LOC006 那一类）。"""
        out = W({"video_prompt": "x",
                 "reference_order": [{"image_n": 3, "asset_id": "LK002"}]})
        self.assertIn("Image 3 = LK002", out)

    def test_a_prompt_that_already_maps_is_untouched(self):
        """★ 写了一部分说明模型有自己的排版，插进去只会打乱它。"""
        txt = "Image 1 = C001 甲\n后面还有很多"
        self.assertEqual(
            W({"storyboard_prompt": txt, "reference_order": _rows()}), txt)

    def test_no_structured_rows_means_no_change(self):
        self.assertEqual(W({"storyboard_prompt": "x"}), "x")


class WiringTests(unittest.TestCase):

    def test_all_four_kinds_go_through_it(self):
        """★ 函数改了但落盘那一步没接上，等于没改。"""
        from core import run_v34 as R
        src = inspect.getsource(R.write_prompt_files)
        self.assertEqual(src.count("with_identity_map"), 4,
                         "四类提示词都要过这一层")

    def test_the_storyboard_sheet_passes_its_own_reference_order(self):
        from core import run_v34 as R
        src = inspect.getsource(R.write_prompt_files)
        self.assertIn('"reference_order": sh.get("reference_order")', src)


class VideoRefUploadTests(unittest.TestCase):
    """补图要真的传上去 —— 不然提示词里的编号是空头承诺。"""

    def test_reference_images_are_uploaded_after_the_spine(self):
        """★ 以前只传 spine + aux_reference，而电影级不设 aux_reference。"""
        src = inspect.getsource(P.make_video_worker)
        self.assertIn('task.get("reference_images")', src)
        i = src.index("refs = [to_ref(s, log) for s in spine]")
        j = src.index('task.get("reference_images")')
        self.assertLess(i, j, "补图必须排在骨架之后 —— 顺序决定编号")

    def test_they_are_sorted_by_image_n(self):
        """★ 按字典顺序传 = 编号错位，而错位不报错。"""
        src = inspect.getsource(P.make_video_worker)
        self.assertIn('key=lambda r: r.get("image_n") or 0', src)

    def test_the_old_v61_field_still_works(self):
        """V6.1 那条路径一直是对的，别把它改坏。"""
        src = inspect.getsource(P.make_video_worker)
        self.assertIn('task.get("aux_reference")', src)

    def test_the_log_separates_spine_from_supplements(self):
        """★ 日志里分开数，才看得出补图到底传了几张。"""
        src = inspect.getsource(P.make_video_worker)
        self.assertIn("补图×", src)


if __name__ == "__main__":
    unittest.main()
