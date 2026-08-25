# -*- coding: utf-8 -*-
"""两个入口要一眼看得见。

用户原话（2026-08-26）：「把导入的 UI 放最上面而且要醒目…创建的时候选择通用
还是电影级也需要醒目」。

两条都不是审美问题，各自对应一个真代价：
  · 材料导入是现在的主路，埋在「一键跑到底」和环节表下面的话，人会先点
    那个 —— 而材料模式下 LLM 环节的中间产物压根不存在，那条路会一步步失败。
  · 体系**建完不能改**，选错整个项目作废。而它原来是「单个项目 · 粘贴正文」
    卡里的一个下拉，**批量建剧那张卡一个字都没有** —— 它偷偷读那个下拉。
    综合包里批量建 20 部剧，用的是另一张卡上一个你没看过的下拉。
"""
import re
import unittest

from core.store import read_text


class Page(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.html = read_text("web/index.html")

    def _flow(self):
        i = self.html.index('<section id="t-flow"')
        return self.html[i:self.html.index("</section>", i)]

    def _proj(self):
        i = self.html.index('<section id="t-proj"')
        return self.html[i:self.html.index("</section>", i)]

    # ---- 导入放最上面 ----

    def test_the_import_card_is_the_first_card_of_the_flow_tab(self):
        blk = self._flow()
        first = re.search(r'<h2>([^<]+)', blk).group(1)
        self.assertIn("导入生产材料", first, f"流程页第一张卡是「{first}」")

    def test_the_import_card_is_marked_prominent(self):
        blk = self._flow()
        i = blk.index("导入生产材料")
        self.assertIn('class="card hero"', blk[max(0, i - 400):i])

    def test_it_tells_you_where_to_go_next(self):
        """★ 导完要去勾「只跑跑生产」。不说的话人会去「生产」页点三次 ——
        那样拿不到跨类接力（前一步补出来的文件后一步立刻用）。"""
        blk = self._flow()
        i = blk.index("导入生产材料")
        self.assertIn("只跑生产", blk[i:i + 1600])

    def test_the_file_picker_accepts_the_recommended_format(self):
        """★ 契约要的是 jsonl —— 选文件框不认它的话，选都选不上。"""
        i = self.html.index('id="matFile"')
        self.assertIn(".jsonl", self.html[i:i + 200])

    # ---- 体系选择醒目、且两条路共用 ----

    def test_the_system_picker_is_the_first_card_of_the_project_tab(self):
        blk = self._proj()
        first = re.search(r'<h2>([^<]+)', blk).group(1)
        self.assertIn("生产体系", first, f"项目页第一张卡是「{first}」")

    def test_it_is_big_buttons_not_a_dropdown(self):
        self.assertIn('id="sysPick"', self._proj())
        self.assertIn(".pick label", self.html)

    def test_there_is_exactly_one_source_of_truth(self):
        """★ 两处各自解析 DOM 的话迟早不一致。隐藏的 select 仍是唯一真相源，
        建项目的三处（单个 / 批量 / 契约导出）都读它。"""
        self.assertEqual(self.html.count('id="spSystem"'), 1)

    def test_both_creation_cards_echo_the_choice(self):
        """★ 选择在页面顶上、填表的人眼睛在卡里 —— 不回显就等于没选择。
        批量建剧那张卡原来一个字都没有。"""
        blk = self._proj()
        self.assertIn('id="spSystemEcho"', blk)
        self.assertIn('id="npSystemEcho"', blk)
        self.assertIn("function echoSystem()", self.html)

    def test_the_echo_is_refreshed_when_the_choice_changes(self):
        i = self.html.index("input[name=sysPick]")
        self.assertIn("echoSystem()", self.html[i:i + 600])

    def test_it_still_says_the_choice_is_final(self):
        """★ 建完不能改 —— 这句话必须在选之前看得到，不是建完才说。"""
        blk = self._proj()
        self.assertIn("建完不能改", blk[:blk.index("项目列表")])
