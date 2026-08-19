# -*- coding: utf-8 -*-
"""任务卡的折叠状态，重绘之后要还原 —— **两层都要**。

用户实遇：点「已结束 5 条（其中 15 个任务失败）」，展开一下自己又收起来了，
看着像点不开。

原因不是没记状态，是**记了没用**：

    // 存的时候两层都存了（选择器是 details[data-key]，内外层都带这个属性）
    $$('#jobCards details[data-key]').forEach(el => GRP_OPEN[el.dataset.key] = el.open);

    // 还的时候只还了外层
    <details class="jobgrp" data-key="..."${open ? ' open' : ''}>   ← 有
      <details class="jobpast" data-key="${key}::past">             ← 没有

任务卡每 1.5 秒重绘一次（整段 innerHTML 换掉），内层于是永远是关着的。

这一类特别难查：状态明明在 GRP_OPEN 里躺着，读代码时「存」那一行看着没问题，
要把存和还两处对着看才发现少了一半。所以用一条机器检查钉住。
"""
import io
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _html() -> str:
    return io.open(os.path.join(ROOT, "web", "index.html"),
                   encoding="utf-8").read()


class OpenStateTests(unittest.TestCase):

    def setUp(self):
        self.html = _html()

    def test_the_inner_group_restores_its_open_state(self):
        """★ 这就是那个 bug。"""
        i = self.html.index('class="jobpast"')
        blk = self.html[i:i + 200]
        self.assertIn("GRP_OPEN", blk,
                      "内层「已结束 N 条」没有还原展开状态 —— 点开就会自己收回去")
        self.assertIn("' open'", blk)

    def test_the_outer_group_still_does(self):
        i = self.html.index('class="jobgrp"')
        self.assertIn("open", self.html[i:i + 120])

    def test_both_levels_are_saved(self):
        """存的那一行要覆盖两层 —— 选择器不能只挑外层的 class。"""
        m = re.search(r"\$\$\('#jobCards ([^']+)'\)\.forEach\(el => \{ GRP_OPEN",
                      self.html)
        self.assertIsNotNone(m, "找不到保存展开状态那一行")
        sel = m.group(1)
        self.assertEqual(sel, "details[data-key]",
                         "按 data-key 选才能同时选到内外两层")

    def test_the_two_levels_use_different_keys(self):
        """★ 用同一个键的话，两层会互相覆盖 —— 那是另一种「点不开」。"""
        self.assertIn('::past', self.html)
        i = self.html.index('class="jobpast"')
        self.assertIn("::past", self.html[i:i + 200])

    def test_the_save_happens_before_the_redraw(self):
        """★ 顺序反了等于没存：innerHTML 一换，旧节点的 open 就读不到了。"""
        save = self.html.index("GRP_OPEN[el.dataset.key] = el.open")
        draw = self.html.index("$('#jobCards').innerHTML")
        self.assertLess(save, draw, "得先存状态再重绘")


if __name__ == "__main__":
    unittest.main()
