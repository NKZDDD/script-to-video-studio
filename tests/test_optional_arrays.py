# -*- coding: utf-8 -*-
"""「这部剧没有这个东西」不等于「模型没答」。

`required` 里带 `[]` 的会要求**非空**数组。而「非空」在很多地方其实是
一个**关于剧本的假设** —— 而剧本没法穷举：

    reality_threads   一部平铺直叙的剧没有回忆线 / 平行时空
    prop_specs        一部戏可能一件道具都没有
    vt / transitions  一个段落可能就是一个长镜头，没有任何转场
    findings          **审计一个问题都没发现** —— 而这本来是好事

最后那条最能说明问题：全都要求非空的话，**一集拍得完美反而会让审计这一步
失败**，然后重试三次、报「拆剧本的模型没按格式回答」，
而人照着那条提示去换更强的模型 —— 完全是白费。

这一类和「s4 的业务规则」是同一个毛病的两种长相：
我们把自己对剧本的假设写成了硬性要求。
"""
import unittest

from core import stages as S, system_v34 as V
from core.llm import check_keys


class SyntaxTests(unittest.TestCase):

    def test_a_non_empty_array_is_still_required_by_default(self):
        """★ 放开不等于全放开 —— 核心产出空了就是这一步没干活。"""
        self.assertEqual(check_keys({"assets": []}, ["assets[]"]), ["assets[]"])

    def test_an_optional_array_may_be_empty(self):
        self.assertEqual(check_keys({"findings": []}, ["findings[]?"]), [])

    def test_but_the_key_must_still_be_there(self):
        """★ 「可以为空」不是「可以没有」——

        键都没有说明模型压根没输出这一块，那是真缺。
        """
        self.assertEqual(check_keys({}, ["findings[]?"]), ["findings[]?"])

    def test_and_it_must_still_be_an_array(self):
        self.assertEqual(check_keys({"findings": "无"}, ["findings[]?"]),
                         ["findings[]?"])

    def test_sub_fields_are_checked_on_the_items_that_exist(self):
        self.assertEqual(check_keys({"a": [{"x": 1}]}, ["a[]?.x"]), [])
        self.assertEqual(check_keys({"a": [{"y": 1}]}, ["a[]?.x"]), ["a[]?.x"])

    def test_an_empty_optional_array_has_no_items_to_check(self):
        """空数组时子字段无从检查 —— 该通过，不该报缺。"""
        self.assertEqual(check_keys({"a": []}, ["a[]?.x"]), [])


class WhichOnesTests(unittest.TestCase):
    """哪几条放开了 —— 逐条都能说出「什么样的剧本会让它为空」。"""

    CASES = {
        "n1": ("reality_threads[]?", "平铺直叙、没有回忆线的剧"),
        "n4": ("prop_specs[]?", "一件道具都没有的戏"),
        "n5": ("loc_views[]?", "还没登记机位需求"),
        "n8": ("vt[]?", "一个长镜头、没有转场的段落"),
        "n9": ("transitions[]?", "单镜头的一集"),
        "n14": ("findings[]?", "审计一个问题都没发现 —— 这是好事"),
    }

    def test_they_are_relaxed(self):
        for sid, (spec, why) in self.CASES.items():
            self.assertIn(spec, V.LLM_SPEC[sid][2], f"{sid}：{why}")

    def test_the_core_outputs_are_still_strict(self):
        """★ 这些空了就是这一步什么都没产出，必须拦。"""
        for sid, spec in (("n1", "entities[]"), ("n3", "scenes[]"),
                          ("n4", "assets[]"), ("n8", "cvs[]"),
                          ("n9", "shots[]"), ("n12", "sbpkg[]")):
            self.assertIn(spec, V.LLM_SPEC[sid][2], sid)

    def test_the_audit_no_longer_fails_on_a_clean_episode(self):
        """★ 这条单独钉：拍得完美不该让审计这一步失败。"""
        self.assertEqual(check_keys({"findings": []}, V.LLM_SPEC["n14"][2]), [])


class GeneralSystemTests(unittest.TestCase):
    """通用十二环节这边全是核心产出，暂时没有需要放开的。"""

    def test_its_required_arrays_are_all_core_outputs(self):
        for sid, (_tpl, _dep, req) in S._LLM_SPEC.items():
            for spec in req:
                if spec.endswith("[]"):
                    # 每一条都该是「空了 = 这一步没干活」
                    self.assertIn(spec.rstrip("[]"),
                                  {"characters", "segments", "segment_states",
                                   "assets", "asset_prompts", "bindings",
                                   "shots", "compiled"}, f"{sid}:{spec}")


if __name__ == "__main__":
    unittest.main()
