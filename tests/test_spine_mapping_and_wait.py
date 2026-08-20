# -*- coding: utf-8 -*-
"""V6.2 之后两件实遇的事。

## 一、故事板出了 2 张，视频只锁了 1 张

用户实遇（女频竖屏外婆的旧食谱 电影级 03）。根因是我上一版留的缺陷：
n13 的 schema 示例里 `storyboard_spine.images[]` 和 `reference_order[]`
**都只写了一张 Sheet**。模型跟着示例写，于是正文只映射了第一张。

而任务本身传的是整条骨架（`storyboard_refs` 由装配层给全）。
所以模型收到 N 张**没有标签**的图、正文只说清其中一张是谁 ——
**它不会报错**，只会把后段的画面当前段用，出来的片子时间顺序是乱的。

出图那一层一直有这道校验（check_image_map），出片这一层以前没有。

## 二、「一直在等模型开口」刷的是假警报

日志实录：n12 的一段等了 615 秒、一个字没收到、**也没有 524**，
而心跳一直在刷「中转站看不到数据可能会在 125 秒左右切断（HTTP 524）」。

熬过 180 秒还没被切，就说明这条线路容得下长思考 —— 那句预言再刷下去，
人会一直等着看一个不会来的错误，而真正该知道的是「还能等多久、到点会怎样」。
"""
import io
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _tpl(name: str) -> str:
    return io.open(os.path.join(ROOT, "prompts", f"{name}.md"),
                   encoding="utf-8").read()


class TemplateExampleTests(unittest.TestCase):
    """示例教的东西，模型会照着写 —— 所以示例本身就是规范。"""

    def setUp(self):
        self.t = _tpl("n13_video")

    def test_the_spine_example_shows_more_than_one_sheet(self):
        """★ 这就是那个缺陷：示例只有一张，模型就只写一张。"""
        i = self.t.index('"storyboard_spine"')
        blk = self.t[i:self.t.index('"reference_order"', i)]
        self.assertIn("SHEET_A", blk)
        self.assertIn("SHEET_B", blk, "示例还是只有一张 Sheet")
        self.assertIn('"order": 2', blk)

    def test_the_spine_count_is_tied_to_stage_twelve(self):
        """★ 光给两条示例不够 —— 得说「条数等于环节12 实际产出的张数」。"""
        i = self.t.index('"storyboard_spine"')
        self.assertIn("等于第十二环节", self.t[i:i + 400])

    def test_the_reference_order_example_lists_the_whole_spine(self):
        i = self.t.index('"reference_order"')
        blk = self.t[i:i + 2200]
        self.assertIn("SHEET_A", blk)
        self.assertIn("SHEET_B", blk)
        self.assertIn('"image_n": 2', blk)

    def test_it_says_supplements_come_after_the_spine(self):
        """补图混在骨架中间会把编号顺序打乱。"""
        i = self.t.index('"reference_order"')
        self.assertIn("补图排在骨架之后", self.t[max(0, i - 300):i + 100])

    def test_the_supplement_example_has_to_justify_itself(self):
        """★ 补图必须写得出独有贡献 —— 示例里不留白，不然模型学会留空。"""
        i = self.t.index('"reference_order"')
        blk = self.t[i:i + 2200]
        j = blk.index('"reference_role": "IDENTITY"')
        self.assertIn("跨镜头容易漂", blk[j:j + 400])


class VideoMappingCheckTests(unittest.TestCase):
    """程序这一层也要认出「传了 N 张、只映射了 1 张」。"""

    def setUp(self):
        import inspect

        from core import produce as P
        self.src = inspect.getsource(P.make_video_worker)

    def test_the_video_worker_checks_the_mapping(self):
        """★ 出图那层一直有这道校验，出片这层以前完全没有。"""
        self.assertIn("_IMAGE_MAP.findall", self.src)
        self.assertIn("if len(spine) > 1:", self.src)

    def test_it_only_checks_when_there_is_more_than_one_sheet(self):
        """一张的时候顺序上没有歧义，别刷没用的提醒。"""
        i = self.src.index("if len(spine) > 1:")
        self.assertLess(i, self.src.index("_IMAGE_MAP.findall"))

    def test_it_names_which_sheets_are_missing(self):
        """只说「对不上」等于什么都没说。"""
        self.assertIn("没提到的", self.src)

    def test_it_spells_out_the_silent_consequence(self):
        """★ 这个项目的规矩：说清它**不报错**，只是做出来是错的。"""
        i = self.src.index("if len(spine) > 1:")
        blk = self.src[i:i + 1400]
        self.assertIn("不报错", blk)
        self.assertIn("把后段当前段用", blk)

    def test_it_warns_instead_of_stopping(self):
        """★ 出片是最贵的一步，为一句措辞把整段拦住不值得。"""
        i = self.src.index("if len(spine) > 1:")
        blk = self.src[i:i + 1400]
        self.assertIn("log(", blk)
        self.assertNotIn("raise", blk)


class ThinkingWaitTests(unittest.TestCase):
    """心跳别刷假警报。"""

    def setUp(self):
        import inspect

        from core import llm as L
        self.src = inspect.getsource(L.LLM._stream_once)

    def test_the_524_prediction_stops_after_a_while(self):
        """★ 熬过 180 秒没被切，那句预言就是假的了。"""
        self.assertIn("elif waited < 180:", self.src)
        i = self.src.index("elif waited < 180:")
        after = self.src[i:]
        self.assertIn("不是网络问题", after)

    def test_it_says_how_much_longer_it_will_wait(self):
        """★ 这是唯一真正有用的信息：等，还是取消。"""
        i = self.src.index("elif waited < 180:")
        after = self.src[i:]
        self.assertIn("再等", after)
        self.assertIn("self.timeout", after)

    def test_it_says_this_kind_is_not_retried(self):
        """同样的输入重试必然同样慢 —— 别让人以为再点一次就好。"""
        i = self.src.index("elif waited < 180:")
        self.assertIn("不重试", self.src[i:])

    def test_it_says_what_to_actually_do(self):
        i = self.src.index("elif waited < 180:")
        after = self.src[i:]
        self.assertIn("输入调小", after)

    def test_the_early_message_is_unchanged(self):
        """前 180 秒那条是对的（524 真的常在 125 秒左右），别改坏。"""
        self.assertIn("125 秒左右切断", self.src)
        self.assertIn("还没收到第一个字", self.src)


if __name__ == "__main__":
    unittest.main()
