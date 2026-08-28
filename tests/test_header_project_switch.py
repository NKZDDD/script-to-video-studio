# -*- coding: utf-8 -*-
"""页头的项目名要能直接切项目。

用户原话（2026-08-26，配截图指着页头那个项目名）：「这个位置需要可以切换项目」。

原来切项目只有一条路：回「项目」页，在表格里找到那一行点「打开」。
同时做好几部剧时这是最高频的动作。
"""
import inspect
import unittest

from core.store import read_text


class Page(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.html = read_text("web/index.html")

    def _header(self):
        i = self.html.index("<header>")
        return self.html[i:self.html.index("</header>", i)]

    def test_the_switcher_lives_in_the_header(self):
        self.assertIn('id="projPick"', self._header())
        self.assertIn('id="projTag"', self._header())

    def test_the_name_is_clickable(self):
        """★ 不给可点的样子，没人会去点它。"""
        h = self._header()
        i = h.index('id="projTag"')
        self.assertIn("cursor:pointer", h[max(0, i - 200):i + 200])

    def test_the_list_is_refetched_on_open(self):
        """★ BOOT.projects 是页面加载那一刻的快照，刚建的项目不在里面。
        拿快照当真相这件事，这个项目里已经因为「画错体系」踩过一次。"""
        i = self.html.index("$('#projTag').onclick")
        blk = self.html[i:i + 700]
        self.assertIn("/api/projects", blk)

    def test_picking_the_current_project_does_nothing(self):
        """★ 重开当前项目会重拉全部面板、还把你正在看的那一页切走。"""
        i = self.html.index("$('#projPick').onchange")
        self.assertIn("root !== PROJ", self.html[i:i + 400])

    def test_open_project_syncs_the_selection(self):
        """★ 从「项目」页打开的话，页头这个下拉也得跟着变 ——
        两处各显示一个项目名是最容易让人跑错项目的样子。"""
        # 窗口放宽：openProject 里现在还要清材料选择（切项目串项目那个修），
        # 400 字够不到 syncProjPick 了。
        i = self.html.index("async function openProject")
        self.assertIn("syncProjPick()", self.html[i:i + 900])

    def test_the_label_says_which_system(self):
        """★ 两套体系的项目可以放在同一个目录里，产物结构完全不一样 ——
        列表里看不出来就会拿电影级的期待去看一个通用版的项目。"""
        i = self.html.index("function projLabel")
        self.assertIn("BOOT.systems", self.html[i:i + 300])


class Endpoint(unittest.TestCase):

    def test_the_endpoint_it_calls_exists(self):
        """★ 页面调一个不存在的口不会报错，只会静默拿不到列表。"""
        from server import app as A
        self.assertIn('"/api/projects"', inspect.getsource(A.api_get))
