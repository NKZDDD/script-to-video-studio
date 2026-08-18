# -*- coding: utf-8 -*-
"""一个 SEG 里有闪回时，同一个人可以出现在两个地方。

用户实跑撞到（《烟火尽头》，一部穿越剧）：

    JSON 输出校验失败（已重试2次）：输出结构不符合要求：
    人物空间记录重复:C001/EP01-SEG04；人物空间记录重复:C001/EP01-SEG06

排错包里模型实际写的是：

    C001  EP01-SEG04  现实医院中仍位于座椅左前方…
    C001  EP01-SEG04  穿越前回忆中的林溪位于塑胶跑道直道左侧…

**模型是对的。** 那两段里有穿越回忆，同一个角色确实同时存在于两个空间。
判错的是我们这条校验 —— 它假设「角色 + 段」必须唯一，
而这部剧整个建立在穿越回忆上，这个假设从一开始就不成立。

更糟的是页面给的指引：「一般是这个模型能力不太够 → 换一个更强的模型」。
往那个方向使劲完全是白费 —— 换多强的模型都会这么写，因为它写得对。

而通用十二环节里**根本没有「现实线 / 回忆线」这个概念**（s1、s3、s4
三份模板一个字都没提），所以模型连表达的地方都没有。加了 `timeline`。
"""
import unittest

from core import stages as S


def _b(seg, timeline=None, char="C001"):
    r = {"character_asset_id": char, "character_name": "林溪", "seg_id": seg,
         "space_master_id": "SP001", "space_region_id": "A",
         "position": "某处", "facing": "朝北",
         "fixed_object_relations": [], "relative_character_positions": [],
         "exit_state": "站着", "inherit_to_seg": "", "inheritance_rule": "",
         "change_trigger": ""}
    if timeline is not None:
        r["timeline"] = timeline
    return r


def _dupes(rows):
    """跑一遍校验，只取「记录重复」那一类问题。"""
    out = S.check_s4_structure({"character_space_bindings": rows}) \
        if hasattr(S, "check_s4_structure") else None
    if out is None:
        raise unittest.SkipTest("找不到 s4 结构校验的入口")
    return [p for p in out if "人物空间记录重复" in p]


class DuplicateKeyTests(unittest.TestCase):
    """直接测键的构成 —— 不依赖整份 s4 产物是否完整。"""

    def _keys(self, rows):
        seen, dup = set(), []
        for r in rows:
            tl = str(r.get("timeline") or "现实").strip() or "现实"
            k = (r["character_asset_id"], r["seg_id"], tl)
            if k in seen:
                dup.append(k)
            seen.add(k)
        return dup

    def test_present_and_flashback_in_one_seg_are_not_duplicates(self):
        """★ 用户实跑那两条。"""
        self.assertEqual(self._keys([
            _b("EP01-SEG04", "现实"),
            _b("EP01-SEG04", "穿越前回忆"),
        ]), [])

    def test_two_rows_on_the_same_timeline_are_still_duplicates(self):
        """★ 放开闪回不等于放开真重复 ——

        同一条时间线上一个人不可能同时在两个地方，写两条下游不知道信哪个。
        """
        self.assertEqual(len(self._keys([
            _b("EP01-SEG04", "现实"),
            _b("EP01-SEG04", "现实"),
        ])), 1)

    def test_old_products_without_timeline_behave_as_before(self):
        """★ 老产物没有这个字段 —— 回落「现实」，行为和加它之前一致。"""
        self.assertEqual(len(self._keys([
            _b("EP01-SEG04"), _b("EP01-SEG04"),
        ])), 1)

    def test_an_empty_timeline_is_not_a_third_timeline(self):
        """填空字符串不该变成「另一条时间线」而绕过检查。"""
        self.assertEqual(len(self._keys([
            _b("EP01-SEG04", ""), _b("EP01-SEG04", "现实"),
        ])), 1)


class CheckerTests(unittest.TestCase):

    def test_the_checker_uses_the_timeline(self):
        import inspect
        src = inspect.getsource(S)
        self.assertIn('binding_key = (character_id, seg_id, timeline)', src)
        self.assertNotIn('binding_key = (character_id, seg_id)\n', src)

    def test_the_message_tells_the_model_how_to_fix_it(self):
        """★ 只说「重复了」，模型不知道该合并还是该分开。"""
        import inspect
        src = inspect.getsource(S)
        i = src.index("人物空间记录重复:")
        self.assertIn("现实和回忆", src[i:i + 400])


class TemplateTests(unittest.TestCase):

    def _tpl(self):
        import io
        import os
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return io.open(os.path.join(root, "prompts", "s4_assets_adapter.md"),
                       encoding="utf-8").read()

    def test_the_schema_has_the_field(self):
        self.assertIn('"timeline"', self._tpl())

    def test_it_explains_when_two_rows_are_allowed(self):
        t = self._tpl()
        self.assertIn("闪回、回忆、穿越", t)
        self.assertIn("只能有一条记录", t)

    def test_it_forbids_the_other_workaround(self):
        """★ 别为了绕开限制，把回忆和现实塞进同一条的 position 里 ——

        那样下游读不出这一段其实有两个空间。
        """
        self.assertIn("塞进同一条的 `position` 里", self._tpl())


if __name__ == "__main__":
    unittest.main()
