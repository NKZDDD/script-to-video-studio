# -*- coding: utf-8 -*-
"""`reference_assets` 只能填会出图的编号 —— 模板必须说出来。

用户实遇（烟火尽头 01，0819_1430 那个包）：EP01 有 2 条、EP02 有 3 条

    ST001「林溪19岁穿越初始状态」参考资产不存在：SP001
    ST004「医院走廊现实空间状态」参考资产不存在：SP001
    ST008「重症监护室开门后空间状态」参考资产不存在：SP001

**闸门是对的，模型也没编造东西** —— `SP001` 是它自己在同一份输出的
`space_masters[]` 里声明的，编号真实存在。错的只是「放在了哪一栏」。

根因在模板的措辞。那一栏原文只有：

    "reference_assets": ["状态资产填写父资产及全部依赖资产；其他资产填空数组"]

而整个约束段通篇在讲**依赖顺序**（谁排谁前面、不许成环），
**从头到尾没有一句说「这一栏只能填有图的资产」**。于是模型的推理完全讲得通：
「这个状态逻辑上依赖那个空间 → 空间是依赖资产 → 写进 reference_assets」。

而正确的那一栏就在同一个对象里、隔着两行（`space_master_id`）。

三处旁证说明这是稳定的理解、不是抖动：
  · `space_master_id` 是空的（同一个信息不会写两遍）
  · 检查器的报错里有「图片资产」这个词，模板里一次都没有 ——
    写检查的人心里清楚，被检查的一方不知道
  · 每集都错在同一栏、同一类（ST 状态资产）、同一个编号（SP001）

而且错误会**被照抄下去**：s5 原来写着「先复制…不得擅自删减」，
所以同一个问题在 s4（ASSET_SCOPE）和 s5（PROMPT_REF_MISSING）各报一次。
"""
import io
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _tpl(name: str) -> str:
    return io.open(os.path.join(ROOT, "prompts", f"{name}.md"),
                   encoding="utf-8").read()


class AdapterTests(unittest.TestCase):
    """s4 适配层：根源在这里。"""

    def setUp(self):
        self.t = _tpl("s4_assets_adapter")

    def test_the_field_says_only_image_assets(self):
        """★ 这一行就是那个坑。"""
        i = self.t.index('"reference_assets"')
        self.assertIn("会出图", self.t[i:i + 200])

    def test_it_says_this_is_not_a_dependency_list(self):
        """★ 「全部依赖资产」这个说法本身在邀请错误 ——

        逻辑依赖和「要喂哪几张图」是两件事，必须点破。
        """
        self.assertIn("不是逻辑依赖清单", self.t)
        self.assertIn("出图时实际要喂的参考图列表", self.t)

    def test_it_names_the_space_master_case(self):
        """★ 实遇的就是这一种，直接点名比讲原则有用。"""
        self.assertIn("空间母资产", self.t)
        self.assertIn("space_master_id", self.t)

    def test_it_says_where_a_space_image_actually_lives(self):
        """光说「不许填」不够 —— 得说那张图在哪，否则模型只能再猜一次。"""
        self.assertIn("environment_asset_id", self.t)

    def test_it_spells_out_the_consequence(self):
        """★ 这个项目的规矩：说清「写错会怎样」，而且要说清它**不报错**。"""
        i = self.t.index("不是逻辑依赖清单")
        blk = self.t[i:i + 900]
        self.assertIn("永远出不来", blk)
        self.assertIn("故事板", blk)

    def test_the_space_field_is_still_there(self):
        """别把正确的那一栏改没了。"""
        self.assertIn('"space_master_id"', self.t)
        self.assertIn('"space_region_id"', self.t)


class CopyForwardTests(unittest.TestCase):
    """s5 原来被要求照抄 —— 错误于是传染。"""

    def setUp(self):
        self.t = _tpl("s5_asset_prompts")

    def test_it_no_longer_copies_blindly(self):
        """★ 「不得擅自删减」原话保留（那条是对的），但要加上例外。"""
        self.assertIn("不得擅自删减", self.t)
        self.assertIn("只复制会出图的那些", self.t)

    def test_it_names_what_to_strip(self):
        i = self.t.index("只复制会出图的那些")
        blk = self.t[i:i + 400]
        self.assertIn("空间母资产", blk)
        self.assertIn("SP001", blk)

    def test_it_asks_for_a_note(self):
        """★ 剔掉了要说一句 —— 悄悄剔掉的话，s4 那边的错永远没人去改。"""
        i = self.t.index("只复制会出图的那些")
        self.assertIn("note", self.t[i:i + 500])


class V34NotAffectedTests(unittest.TestCase):
    """电影级那套的措辞本来就不含糊，别顺手改坏。"""

    def test_n4_frames_it_as_what_to_upload(self):
        """「出图时实际要喂的参考图列表，顺序就是上传顺序」——

        这种说法天然排除空间结构编号，所以 v34 没踩这个坑。
        """
        t = _tpl("n4_assets")
        self.assertIn("出图时实际要喂的参考图列表", t)
        self.assertIn("画面里出现谁，谁就得在里面", t)


if __name__ == "__main__":
    unittest.main()
