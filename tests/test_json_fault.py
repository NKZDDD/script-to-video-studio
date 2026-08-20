# -*- coding: utf-8 -*-
"""JSON 解析不了的时候，报错要说真话。

用户实遇（`s8 EP01-SEG01`，0819_1430 那个包）：报错说

    上一次的输出没有形成完整的 JSON（收到 6745 字，**括号始终没有闭合**…）
    而结束原因是 stop …… 请把同样的内容重新输出一遍，**重点检查括号配对和结尾**

而真相是：花括号数出来 **7 对 7**，模型在一段长正文中间多打了一个双引号，
把字符串提前闭合了，`json.loads` 报的是 `Expecting ',' delimiter`。

代价不是「说错一句话」：那段话是**给模型的重试反馈**。它被告知去检查括号，
于是照原样再写一遍同一个引号错误 —— EP01 的环节8 跑了 8 次、EP02 跑了 9 次。

判据不能用「括号配没配平」：引号一错位，扫描器分不清哪个括号在字符串里，
结论是随机的（那次 `_brackets_balanced` 就返回了 False）。
真正管用的判据是**解析停在哪里**：

    停在末尾  → 后面没内容了，写到一半（真截断）
    停在中间  → 后面还有几千字，内容写完了、只是坏了一处
"""
import unittest

from core.llm import LLMError, _brackets_balanced, extract_json

# 真实那一份的形状：长字符串中间多一个引号，后面还有几千字
BROKEN = ('{"compiled": [{"id": "EP01-SEG01", "prompt": "前半段正文，'
          + "很长的中文描写。" * 60
          + '空间固定物移动。" \n七、进入状态\n林溪19岁，穿深色校服。'
          + "后半段正文。" * 60
          + '", "aux_reference_asset_id": ""}]}')

TRUNCATED = '{"episode": "EP01", "segment": "EP01-SEG05", "scstates": ['


def _msg(text, reason=""):
    try:
        extract_json(text, reason=reason)
    except LLMError as e:
        return str(e)
    raise AssertionError("居然解析成功了")


class MidFaultTests(unittest.TestCase):
    """写完了、中间坏了一处。"""

    def test_it_does_not_claim_the_brackets_are_open(self):
        """★ 这就是那个 bug。人跟着去数括号，模型跟着去补括号，两边都白忙。"""
        m = _msg(BROKEN, "stop")
        self.assertNotIn("括号始终没有闭合", m)

    def test_it_quotes_the_parser(self):
        m = _msg(BROKEN, "stop")
        self.assertIn("Expecting ',' delimiter", m)

    def test_it_points_at_the_position(self):
        """★ **最有用的一样东西**：模型看到自己写坏的那一处才知道改哪里。"""
        m = _msg(BROKEN, "stop")
        self.assertIn("出错的位置在第", m)
        self.assertIn("就是这里", m)

    def test_it_explains_the_likely_cause_in_plain_words(self):
        """只说「JSON 解析失败」等于什么都没说 —— 三种错的改法完全不同。"""
        m = _msg(BROKEN, "stop")
        self.assertIn("多了一个双引号", m)

    def test_it_says_the_content_is_complete(self):
        """★ 说清「后面还有 N 字」，人才不会往「是不是被截断了」的方向查。"""
        m = _msg(BROKEN, "stop")
        self.assertIn("后面还有", m)
        self.assertIn("内容是写完了的", m)

    def test_it_tells_the_model_not_to_hunt_for_brackets(self):
        """★ 这一句是省钱的关键：上一版让它检查括号，于是重试必然无效。"""
        m = _msg(BROKEN, "stop")
        self.assertIn("不要去找少掉的括号", m)

    def test_it_still_forbids_shrinking_the_content(self):
        """内容没问题，删条目是纯损失 —— 这条立场不许因为改措辞而丢掉。"""
        self.assertIn("不要删减任何字段或条目", _msg(BROKEN, "stop"))

    def test_it_reminds_how_to_escape(self):
        m = _msg(BROKEN, "stop")
        self.assertIn("\\\"", m)
        self.assertIn("\\n", m)

    def test_it_works_without_a_stop_reason_too(self):
        """★ 服务商没给结束原因、但内容明显写完了 —— 也不该说「被切断」。"""
        m = _msg(BROKEN, "")
        self.assertIn("Expecting", m)
        self.assertIn("内容已经收全了", m)


class TruncationTests(unittest.TestCase):
    """真的写到一半 —— 措辞一个字都不该变。"""

    def test_a_truncated_reply_still_says_the_brackets_never_closed(self):
        """★ 别修过头：这一支原来是对的。"""
        m = _msg(TRUNCATED)
        self.assertIn("括号始终没有闭合", m)

    def test_it_still_asks_for_a_verbatim_resend(self):
        m = _msg(TRUNCATED)
        self.assertIn("原样再输出一遍", m)
        self.assertIn("不要压缩", m)

    def test_a_truncated_reply_with_stop_points_at_the_brackets(self):
        m = _msg(TRUNCATED, "stop")
        self.assertIn("括号配对", m)


class BalanceHelperTests(unittest.TestCase):
    """括号配平的判断本身：字符串里的括号不算。"""

    def test_braces_inside_strings_do_not_count(self):
        self.assertTrue(_brackets_balanced('{"a": "正文里有 { 和 } 两个符号"}'))

    def test_an_escaped_quote_does_not_end_the_string(self):
        self.assertTrue(_brackets_balanced('{"a": "他说\\"好\\"，然后 { 走了"}'))

    def test_a_real_imbalance_is_caught(self):
        self.assertFalse(_brackets_balanced('{"a": [1, 2'))

    def test_a_closer_before_any_opener_is_caught(self):
        self.assertFalse(_brackets_balanced('} {'))

    def test_an_unterminated_string_is_not_balanced(self):
        self.assertFalse(_brackets_balanced('{"a": "没收尾'))


if __name__ == "__main__":
    unittest.main()
