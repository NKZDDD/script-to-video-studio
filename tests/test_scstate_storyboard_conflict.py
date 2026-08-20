# -*- coding: utf-8 -*-
"""场景状态图判「不出图」，而故事板拿它当参考图 —— 两个环节打架。

用户实遇（女频竖屏外婆的旧食谱 电影级 03）：

    SCST_EP01_SC01_01   参5/5  未出
    提示词正文第一行：本条判定为LOGICAL_ONLY，不派发图片任务，
                      因此没有reference_assets，也没有Image编号映射
    报错：要传 5 张参考图（C001、LK001、C006、C005…），却没有 Image N 映射

    EP01-SEG01_SHEET_A  参0/1  未出
    提示词里：Image 1 = SCST_EP01_SC01_01 本段场景状态图
    报错：参考图不存在或者是个空文件：03b_场景状态图/PROJ-001_SCST_EP01_SC01_01.png

**这和 CST002 那个不是同一件事。** 那次被引的是简单服装（走文字契约，
本来就不该有图），挑掉就对了。这次被引的是场景状态图 —— 故事板结构上
需要它当 Image 1（模板原话：`Image 1 = 本段的 SCSTATE（主参考）`）。
挑掉之后故事板就没有主参考了，画面和这一段没有关系。

四处一起改：
  ① 判据放宽 —— 「判定为 LOGICAL_ONLY」这种写法以前认不出（我要求了冒号）
  ② n11 schema —— 判不出图时 reference_assets 必须留空（那份产物自己前后矛盾了）
  ③ n12 模板 —— 补上「本段 SCSTATE 没有图时 Image 1 引什么」（原来一句都没有）
  ④ 矛盾检查 —— 已经发生时要一眼看懂，而不是只说「参考图不存在」
"""
import io
import os
import unittest

from core.run_v34 import _SCST_NO_IMAGE_RE, scstate_no_image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 用户实际那一句，逐字
REAL = ("参考图角色映射：本条判定为LOGICAL_ONLY，不派发图片任务，"
        "因此没有reference_assets，也没有Image编号映射。所有身份、空间、支撑、"
        "道具和持有关系以CVS及空间主表文字合同为唯一依据。")


def _tpl(name: str) -> str:
    return io.open(os.path.join(ROOT, "prompts", f"{name}.md"),
                   encoding="utf-8").read()


class WordingTests(unittest.TestCase):
    """① 判据放宽。"""

    def test_the_real_wording_is_recognised(self):
        """★ 这就是漏掉的那一种 —— 「判定为」后面没有冒号。"""
        self.assertTrue(scstate_no_image({"prompt": REAL}))

    def test_the_other_three_wordings_still_work(self):
        for s in ("当前判定：LOGICAL_ONLY。本条不生成图片。",
                  "决定：LOGICAL_ONLY",
                  "判断为 DEFER_TO_VIDEO"):
            self.assertTrue(scstate_no_image({"prompt": s}), s)

    def test_the_action_phrase_alone_is_enough(self):
        """「不派发图片任务」本身就是判词，模板规定的说法。"""
        self.assertTrue(_SCST_NO_IMAGE_RE.search("本条不派发图片任务"))

    def test_a_negated_mention_is_not_a_verdict(self):
        """★ 别拦过头。误判的代价是**该出的图不出**，而且悄无声息。"""
        self.assertFalse(scstate_no_image({"prompt":
            "本条不是 LOGICAL_ONLY，需要出图。（LOGICAL_ONLY 的条目只出文字合同）"}))

    def test_story_words_are_not_verdicts(self):
        """剧情里的「判定」「生成」不算 —— 只咬模板规定的判词。"""
        self.assertFalse(scstate_no_image({"prompt":
            "她判定为凶手，画面里不生成任何多余人物"}))

    def test_the_structured_field_still_wins(self):
        self.assertFalse(scstate_no_image(
            {"decision": "VISUAL_ANCHOR_REQUIRED", "prompt": REAL}))


class TemplateTests(unittest.TestCase):
    """②③ 让它不再发生。"""

    def test_n11_requires_the_refs_to_be_empty(self):
        """★ 那份产物自己前后矛盾：正文说没有参考图，字段里填了 5 个。"""
        t = _tpl("n11_scstate")
        self.assertIn("必须是空数组", t)
        i = t.index("必须是空数组")
        self.assertIn("reference_role_map", t[max(0, i - 200):i + 200])

    def test_n11_explains_why_both_sides_looked_right(self):
        """★ 这个项目的规矩：说清「为什么两边都没错」，不然下次还这么写。"""
        t = _tpl("n11_scstate")
        self.assertIn("程序信字段", t)
        self.assertIn("提示词用正文", t)

    def test_n12_says_what_image_one_is_when_there_is_no_scstate_image(self):
        """★ **这是四处里最要紧的一处** —— 原来一句都没有。"""
        t = _tpl("n12_storyboard")
        self.assertIn("本段 SCSTATE 没有图的时候", t)
        i = t.index("本段 SCSTATE 没有图的时候")
        blk = t[i:i + 900]
        self.assertIn("`Image 1` **不是它**", blk)
        self.assertIn("LOOK", blk, "没说改引什么")
        self.assertIn("LOC_VIEW", blk)

    def test_n12_keeps_the_position_contract(self):
        """★ 删图不等于删位置合同 —— 这一条 skill 反复强调，不许漏。"""
        i = _tpl("n12_storyboard").index("本段 SCSTATE 没有图的时候")
        blk = _tpl("n12_storyboard")[i:i + 900]
        for k in ("Zone", "Anchor", "Support", "Orientation"):
            self.assertIn(k, blk, k)

    def test_n12_still_prefers_the_scstate_when_it_exists(self):
        """别改过头：有图的时候 Image 1 照旧是 SCSTATE。"""
        self.assertIn("Image 1 = 本段的 SCSTATE（主参考）", _tpl("n12_storyboard"))


class ConflictReportTests(unittest.TestCase):
    """④ 已经发生时能一眼看懂。"""

    def setUp(self):
        import inspect

        from core import run_v34 as R
        self.src = inspect.getsource(R.build_tasks)

    def test_it_cross_checks_the_two_stages(self):
        self.assertIn("sb_conflict", self.src)
        self.assertIn("SCSTATE_STORYBOARD_CONFLICT", self.src)

    def test_the_skipped_list_keeps_the_ids(self):
        """★ 只存一串「编号（理由）」的字符串就对不上账了 —— 要能按编号查。"""
        self.assertIn("scst_skipped: dict = {}", self.src)
        self.assertIn("scst_skipped[sid] = why", self.src)

    def test_it_says_dropping_the_ref_is_not_the_fix(self):
        """★ 这一句是整条报错的重点 —— 上一个 bug 的修法在这里是错的。"""
        self.assertIn("不能靠挑掉参考图解决", self.src)
        self.assertIn("没有主参考", self.src)

    def test_it_offers_both_ways_out(self):
        self.assertIn("让它出图", self.src)
        self.assertIn("改引原子资产", self.src)

    def test_it_does_not_silently_drop_the_reference(self):
        """★ 参考图要**留在列表里** —— 留着出图那层才会硬停。

        悄悄挑掉的话，故事板会拿剩下的图凑合着出一张，
        而那张图和这一段没有关系，任务还标 ok。
        """
        i = self.src.index("sb_conflict.append")
        blk = self.src[max(0, i - 700):i]
        self.assertIn("不能像 CST002 那样挑掉", blk)


if __name__ == "__main__":
    unittest.main()
