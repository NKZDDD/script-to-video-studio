# -*- coding: utf-8 -*-
"""切集锚点：不可能唯一的，在**搜索之前**就判掉。

实跑撞到（通用十二环节，21 集）：环节1 给的 start_anchor 是
`10`、`11`、`13` —— 它把章节标记当成了正文第一行。

两个字符拿去做包含匹配（`anchor in line`），剧本里上千行都能命中，
匹配到哪一行纯属偶然。而一旦命中，游标就被带到错的位置，
**从那一集起后面每一集都跟着崩** —— 日志里一口气报了 11 集。

更坏的是下游：切歪之后每个环节都在错的分集上工作，
而它们自己不知道 —— 那一轮直接带着坏切集跑到了第二环节。
"""
import unittest

from core.episodes import _why_bad_anchor, split


def _script(*lines):
    return "\n".join(lines)


class BadAnchorTests(unittest.TestCase):

    def test_a_two_char_anchor_is_refused(self):
        """★ 就是实跑那几条：`10`、`11`、`13`。"""
        for a in ("10", "11", "13"):
            self.assertIn("不可能在全剧里唯一", _why_bad_anchor(a), a)

    def test_a_pure_marker_line_is_refused(self):
        """章节标记是分隔符，不是内容。"""
        for a in ("———", "* * *", "12345"):
            self.assertIn("不是章节标记", _why_bad_anchor(a), a)

    def test_a_real_first_line_is_accepted(self):
        self.assertEqual(_why_bad_anchor("20岁的顾远在朝阳里怔怔地看了我很久。"), "")

    def test_a_short_but_real_line_is_accepted(self):
        """★ 别把话说死 —— 三个字的标题行是合法的。"""
        self.assertEqual(_why_bad_anchor("第三章"), "")


class SplitTests(unittest.TestCase):

    def test_an_anchor_that_is_not_there_says_how_to_fix_it(self):
        """★ 只说「切不出来」没用 —— 要说清去哪儿改。

        **「锚点合不合格」那一关已经去掉了**（skill 只要求给出每集正文的
        第一行原文，没有对它的长度或字符做任何规定）—— 现在只剩事实：
        这一行在剧本里找不到。「我觉得这个锚点不够好」是本地判断，不该拦人。
        """
        r = split(_script("第一句正文", "第二句"), [{"episode": "EP01",
                                                "start_anchor": "剧本里没有这一行"}])
        self.assertEqual(r["episodes"], [])
        why = r["issues"][0]["reason"]
        self.assertIn("找不到这一行", why)

    def test_the_quality_gate_is_gone(self):
        """★ 单独钉住：不再因为「锚点太短 / 全是数字」而拒绝切集。"""
        from core import episodes as E
        import inspect
        self.assertNotIn("_why_bad_anchor(anchor)", inspect.getsource(E.split))

    def test_a_short_anchor_no_longer_matches_by_containment(self):
        """★ 这就是那个 bug：`10` 以前会命中任何含「10」的行。

        比如「他等了10分钟」—— 那一行和这一集毫无关系。
        """
        s = _script("他等了10分钟。", "真正的开头在这里，很长的一句正文。")
        r = split(s, [{"episode": "EP01", "start_anchor": "10"}])
        self.assertEqual(r["episodes"], [], "短锚点不该靠包含匹配蒙到一行")

    def test_a_long_anchor_still_matches_by_containment(self):
        """够长的锚点照旧允许包含匹配 —— 模型常漏抄行首的空格或引号。"""
        s = _script("　　真正的开头在这里，很长的一句正文。", "后面")
        r = split(s, [{"episode": "EP01",
                       "start_anchor": "真正的开头在这里，很长的一句正文。"}])
        self.assertEqual(len(r["episodes"]), 1)

    def test_an_out_of_order_anchor_reports_both_line_numbers(self):
        """★ 光说「顺序乱了」看不出错在哪 —— 要给出两个行号来对照。"""
        s = _script("靠前的那一句很长很长，作为锚点足够唯一。",
                    "中间", "第二集从这里开始，这一句也足够长。")
        r = split(s, [
            {"episode": "EP01", "start_anchor": "第二集从这里开始，这一句也足够长。"},
            {"episode": "EP02", "start_anchor": "靠前的那一句很长很长，作为锚点足够唯一。"},
        ])
        why = [i["reason"] for i in r["issues"] if i["episode"] == "EP02"][0]
        self.assertIn("比上一集还靠前", why)
        self.assertIn("第 1 行", why)
        self.assertIn("下游全部环节都会在错的分集上工作", why)

    def test_a_good_split_still_works(self):
        s = _script("第一集的开头，这一句足够长。", "内容", "内容",
                    "第二集的开头，这一句也足够长。", "内容")
        r = split(s, [
            {"episode": "EP01", "start_anchor": "第一集的开头，这一句足够长。"},
            {"episode": "EP02", "start_anchor": "第二集的开头，这一句也足够长。"},
        ])
        self.assertEqual([e["episode"] for e in r["episodes"]], ["EP01", "EP02"])


class TemplateTests(unittest.TestCase):
    """程序拦得住但模板不教，等于每次都要人工改。"""

    def _tpl(self, name):
        import io
        import os
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return io.open(os.path.join(root, "prompts", f"{name}.md"),
                       encoding="utf-8").read()

    def test_both_systems_are_told(self):
        """★ 两套体系共用同一份切集代码，所以两份模板都要说。"""
        for name in ("s1_global", "n1_truth"):
            t = self._tpl(name)
            self.assertIn("章节标记不算正文第一行", t, name)
            self.assertIn("`10`", t, name)


if __name__ == "__main__":
    unittest.main()
