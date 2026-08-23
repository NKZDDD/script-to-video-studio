# -*- coding: utf-8 -*-
"""V6.1 的参考图身份映射回填。

排错包 …_0823_0233（通用十二环节全剧）里五条 UNKNOWN，四条是这一类：

    ST076  要传 2 张（C001、S005），提示词里一条 `Image N =` 都没有
    ST078  要传 3 张（C008、P006、S005），同上
    EP01-SEG03（故事板）要传 11 张，同上
    ST070  Image 2 该是 S011，提示词里**没提这个编号**（部分缺）

模板确实要求写 `Image N = <asset_id> <名称>`（s5_asset_prompts.md:63-94），
而模型写成了自己的说法：「父资产C001的identity_anchors原样复述：…」
「参考资产S005的身份绑定：…」——**信息全在，只是不是那个写法**。

V6.1 的 schema 没有 v34 那样的 `reference_role_map`，只有 `reference_assets`
（有序 id 列表）。而上传顺序就是那个列表的顺序 —— 所以编号是确定的。

判据是**真检查器**（produce.check_image_map / check_identity_map），
不是「我觉得补上了」。
"""
import unittest

from core import produce as P
from core.stages import identity_notes, with_image_map

# 你截图里 ST076 的真实正文（截到关键几行）
ST076 = """资产名称：EP13林溪19岁公寓情绪与空间状态
输出结构：state_asset，以父资产C001为首张参考图，继承S005公寓环境资产。
父资产C001的identity_anchors原样复述：19岁东方少女，影视明星级高颜值、鹅蛋脸，清澈明亮杏眼，秀挺鼻梁。
参考资产S005的身份绑定：城市高层整洁闲置公寓，浅色墙面、木质地板、开放式厨房。
状态差异：保持19岁林溪面容、低马尾。"""

NAMES = {"C001": "林溪", "S005": "林溪公寓", "S011": "走廊", "C008": "饼饼"}


def _refs(ids):
    return [{"image_n": i, "asset_id": a} for i, a in enumerate(ids, 1)]


def _blocked(prompt, ids):
    """两道真检查器：返回 (第一道拦不拦, 第二道拦不拦)。"""
    e1, _ = P.check_image_map(prompt, _refs(ids))
    e2, _ = P.check_identity_map(prompt, _refs(ids))
    return bool(e1), bool(e2)


class TheTwoReportedProblemsTests(unittest.TestCase):
    """用户点名的那两个症状，逐个验。"""

    def test_no_mapping_at_all_was_blocked_and_now_passes(self):
        """★ 症状一：「提示词没有映射」（ST076 / ST078 / EP01-SEG03）。"""
        self.assertEqual(_blocked(ST076, ["C001", "S005"]), (True, False),
                         "夹具不对：修之前第一道就该拦")
        fixed, added = with_image_map(ST076, ["C001", "S005"], NAMES)
        self.assertEqual(added, [1, 2])
        self.assertEqual(_blocked(fixed, ["C001", "S005"]), (False, False))

    def test_a_missing_number_was_blocked_and_now_passes(self):
        """★ 症状二：「编号和实际上传顺序对不上」——**缺号**那一种（ST070）。

        produce 那边报的是「Image 2 应该是 S011，提示词里没提这个编号」。
        """
        p = ("Image 1 = C001 林溪，19岁东方少女正面半身\n"
             "参考资产S011的身份绑定：走廊尽头的窗。")
        self.assertEqual(_blocked(p, ["C001", "S011"]), (True, False))
        fixed, added = with_image_map(p, ["C001", "S011"], NAMES)
        self.assertEqual(added, [2], "只该补缺的那一号")
        self.assertEqual(_blocked(fixed, ["C001", "S011"]), (False, False))

    def test_a_real_conflict_is_still_left_to_the_existing_fixer(self):
        """★ 「号在但 ID 对不上」**不是这次要修的** —— 补一行进去会变成

        同一个编号两行指两个 ID，比不补更糟。那一半已经有 `_auto_fix_image_map`
        以上传列表为准直接改，两边正好互补。
        """
        p = "Image 1 = C001 林溪\nImage 2 = S999 错的那个\n正文。"
        fixed, added = with_image_map(p, ["C001", "S011"], NAMES)
        self.assertEqual(added, [], "不该动已经写了的号")
        self.assertEqual(fixed, p, "一个字都不该改")
        # 那一条由既有的纠正器处理
        got, fixes = P._auto_fix_image_map(p, _refs(["C001", "S011"]))
        self.assertIn("Image 2 由 S999 纠正为 S011", " ".join(fixes))


class IdentityNoteTests(unittest.TestCase):
    """接身份描述：只写裸 ID 的话第二道会拦 —— 症状换个名字而已。"""

    def test_it_picks_up_the_three_wordings_seen_in_the_wild(self):
        got = identity_notes(ST076)
        self.assertIn("C001", got)
        self.assertIn("19岁东方少女", got["C001"])
        self.assertIn("S005", got)

    def test_an_unrecognizable_wording_yields_nothing(self):
        """★ 认不出就不接。**编一段身份描述比不接严重得多** ——

        模型会照着编的那段画。
        """
        self.assertEqual(identity_notes("这里啥也没有，只有一段散文。"), {})

    def test_without_a_note_it_still_writes_id_and_name(self):
        """认不出身份时只写 ID + 名称，让第二道照旧说话。"""
        p = "完全认不出身份的正文。"
        fixed, added = with_image_map(p, ["C001"], NAMES)
        self.assertEqual(added, [1])
        self.assertIn("Image 1 = C001 林溪", fixed)
        self.assertNotIn("是谁/是什么", fixed)
        # 单张参考图时第二道不要求划分权威，所以这里只确认没有编造
        self.assertNotIn("岁", fixed)


class ShapeTests(unittest.TestCase):

    def test_the_order_is_the_upload_order(self):
        """★ reference_assets 的第 i 个就是 Image i —— 这是编号的唯一依据。"""
        fixed, _ = with_image_map("正文", ["S005", "C001"], NAMES)
        self.assertLess(fixed.index("Image 1 = S005"),
                        fixed.index("Image 2 = C001"))

    def test_nothing_missing_means_no_change(self):
        p = "Image 1 = C001 林溪\nImage 2 = S005 林溪公寓\n正文。"
        self.assertEqual(with_image_map(p, ["C001", "S005"], NAMES), (p, []))

    def test_no_refs_means_no_change(self):
        """父资产自己没有参考图 —— 模板说这一段整段省略，别补出「Image 1 = 无」。"""
        self.assertEqual(with_image_map("正文", [], NAMES), ("正文", []))

    def test_the_original_text_is_kept_whole(self):
        """★ 补在最前面、单独一块：不猜模型的排版、不插进它的段落中间。"""
        fixed, _ = with_image_map(ST076, ["C001", "S005"], NAMES)
        self.assertIn(ST076, fixed)

    def test_it_says_it_was_the_program(self):
        """★ 不说的话，下次有人对着提示词排查会以为模型写了这几行。"""
        fixed, _ = with_image_map("正文", ["C001"], NAMES)
        self.assertIn("程序按 reference_assets 的顺序补的", fixed)

    def test_a_blank_id_is_skipped(self):
        fixed, added = with_image_map("正文", ["C001", "", None], NAMES)
        self.assertEqual(added, [1])


class WiringTests(unittest.TestCase):

    def test_the_write_path_goes_through_it(self):
        """★ 函数写好但落盘那一步没接上，等于没做。"""
        import inspect
        from core import stages as S
        src = inspect.getsource(S.run_llm_stage)
        self.assertIn("with_image_map(", src)
        self.assertIn("asset_names(pj, episode)", src)

    def test_it_logs_which_numbers_were_added(self):
        import inspect
        from core import stages as S
        self.assertIn("补了身份映射 Image", inspect.getsource(S.run_llm_stage))


if __name__ == "__main__":
    unittest.main()
