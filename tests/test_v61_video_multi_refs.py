# -*- coding: utf-8 -*-
"""通用十二环节的视频：补图从「一个」改成「有序一组」。

用户原话（2026-08-25）：「C 也是需要改的不应该只有一张参考图」。

原来这套体系的视频任务字段是 `storyboard_ref`（单数）+ `aux_reference`
（单数）—— 结构上最多两张参考图。而一段里经常有好几项故事板给不了的权威
（主角身份跨镜头易漂、当前造型的背面/下装/鞋履首次显露、关键道具的文字在
故事板里不可辨），只能挑一个的话剩下的全靠模型编，**而它不会报错**。

注意范围：这套体系的故事板环节**一段只出一张图**（`_STORYBOARD_V01_FIXED`），
所以骨架仍然是一张 —— 多张有序骨架是电影级十七章那套的设计。
这里长出来的是补图。
"""
import inspect
import unittest

from core import produce, stages as S


class TemplateTests(unittest.TestCase):

    def _tpl(self):
        from core.store import read_text
        return read_text("prompts/s8_compile.md")

    def test_the_template_asks_for_a_list(self):
        tpl = self._tpl()
        self.assertIn("video_reference_order", tpl)
        self.assertIn('"image_n": 2', tpl)

    def test_the_template_still_forbids_padding(self):
        """★ 上限是容量不是目标 —— 不写这句的话，从「只能一张」直接翻到
        「装满九张」，而多传一张互相打架不报错，只会把画面搞坏。"""
        tpl = self._tpl()
        self.assertIn("为了填满上限", tpl)
        self.assertIn("额度先给故事板", tpl)

    def test_the_template_demands_the_identity_mapping(self):
        """★ 只写编号不写身份，多人场景必然张冠李戴，而且不报错。"""
        self.assertIn("Image N = <asset_id>", self._tpl())

    def test_the_limit_variable_is_filled(self):
        """★ 模板里加了 {{REF_LIMIT}}，v61 原来不填它 ——
        不填就把字面量 `{{REF_LIMIT}}` 喂给模型。"""
        src = inspect.getsource(S)
        i = src.index('"BINDINGS": jd(binding),')      # s8 那一处的变量表
        self.assertIn("REF_LIMIT", src[i:i + 900])

    def test_the_limit_comes_from_the_video_provider(self):
        """★ s8 编的是视频提示词，拿出图那家的上限会差一倍
        （实遇：出图 9 张、视频 30 张）。"""
        src = inspect.getsource(S)
        i = src.index('"REF_LIMIT"')
        self.assertIn("ref_limit_video", src[i:i + 200])


class ProduceTests(unittest.TestCase):

    def test_the_old_single_field_is_not_uploaded_twice(self):
        """★ 两个字段都有值时第一张补图会被传两次 —— 而 image_n 是按上传
        顺序算的，它后面每一张的编号整体错位，每条描述都套到别的图上。
        画面出得来，参考全是错的。"""
        src = inspect.getsource(produce)
        self.assertIn('if task.get("aux_reference") and not aux:', src)

    def test_the_old_single_field_still_works_alone(self):
        """老 tasks.json 只有 aux_reference —— 不认它的话要重跑环节8 才能出片。"""
        src = inspect.getsource(produce)
        self.assertIn('task.get("aux_reference")', src)


class AssemblyTests(unittest.TestCase):

    def test_unknown_ids_stay_in_the_list(self):
        """★ 和 v34 的 split_refs 同一条口径：认不出的留在列表里、
        file_ref 留空。删掉的话数量看着是对的，反而看不出少了一张。"""
        src = inspect.getsource(S)
        i = src.index("vd_rows = [r for r in")
        blk = src[i:i + 1400]
        self.assertIn('"file_ref": (asset_output_rel(amap[rid])', blk)
        self.assertIn("ghost.setdefault", blk)

    def test_the_old_singular_output_is_read_as_a_one_item_list(self):
        src = inspect.getsource(S)
        i = src.index("vd_rows = [r for r in")
        self.assertIn('aux_reference_asset_id', src[i:i + 700])
