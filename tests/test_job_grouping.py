# -*- coding: utf-8 -*-
"""任务卡按项目分组、已结束的折起来。

平铺的时候一屏十几张卡：同一个项目跑过几轮，每轮还派生出图任务，
**正在跑的那一张被埋在中间**，等于看不见。

改成两层：
  项目（有在跑的默认展开）
    └ 运行中的卡片
    └ 「已结束 N 条」（默认收起）

最容易写坏的一处是**展开状态**：任务卡每 1.5 秒重绘一次，
不记住的话人刚点开一个分组，下一次刷新就弹回去 —— 那个折叠等于不能用。

用 node 把页面里那段分组逻辑抠出来真跑一遍。光「语法能解析」不代表分组是对的。
"""
import io
import json
import os
import re
import shutil
import subprocess
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGE = os.path.join(ROOT, "web", "index.html")
NODE = shutil.which("node")

JOBS = [
    {"id": "1", "project_name": "烟火尽头05", "status": "done",
     "counts": {"ok": 3, "failed": 2}},
    {"id": "2", "project_name": "烟火尽头05", "status": "running", "counts": {}},
    {"id": "3", "project_name": "烟火尽头05", "status": "error",
     "counts": {"failed": 1}},
    {"id": "4", "project_name": "外婆的旧食谱", "status": "done", "counts": {}},
    {"id": "5", "project_name": "外婆的旧食谱", "status": "cancelled", "counts": {}},
    {"id": "6", "project_name": None, "status": "done", "counts": {}},
]

# 页面里那段分组逻辑，一字不改地抠出来跑
LOGIC = """
const ACTIVE = j => !['done','error','cancelled','aborted'].includes(j.status);
const groups = [];
const byName = {};
for (const j of JOBS) {
  const key = j.project_name || '—';
  if (!byName[key]) { byName[key] = {key, live: [], past: []}; groups.push(byName[key]); }
  (ACTIVE(j) ? byName[key].live : byName[key].past).push(j);
}
groups.sort((a, b) => (b.live.length > 0) - (a.live.length > 0));
console.log(JSON.stringify(groups.map(g => ({
  key: g.key,
  live: g.live.map(j => j.id),
  past: g.past.map(j => j.id),
  failed: g.past.reduce((n, j) => n + ((j.counts||{}).failed || 0), 0),
  open: g.live.length > 0,
}))));
"""


@unittest.skipUnless(NODE, "没有 node，跳过分组逻辑的实跑")
class GroupingTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        src = f"const JOBS = {json.dumps(JOBS, ensure_ascii=False)};\n" + LOGIC
        out = subprocess.run([NODE, "-e", src], capture_output=True, text=True,
                             encoding="utf-8")
        assert out.returncode == 0, out.stderr
        cls.g = {x["key"]: x for x in json.loads(out.stdout)}
        cls.order = [x["key"] for x in json.loads(out.stdout)]

    def test_each_project_becomes_one_group(self):
        self.assertEqual(set(self.g), {"烟火尽头05", "外婆的旧食谱", "—"})

    def test_running_and_finished_are_separated(self):
        self.assertEqual(self.g["烟火尽头05"]["live"], ["2"])
        self.assertEqual(self.g["烟火尽头05"]["past"], ["1", "3"])

    def test_a_group_with_something_running_comes_first(self):
        """★ 那是人打开这一页要看的东西。"""
        self.assertEqual(self.order[0], "烟火尽头05")

    def test_a_group_with_something_running_defaults_to_open(self):
        self.assertTrue(self.g["烟火尽头05"]["open"])

    def test_a_finished_group_defaults_to_collapsed(self):
        """★ 这就是「太多太难看」要解决的那一半。"""
        self.assertFalse(self.g["外婆的旧食谱"]["open"])

    def test_failed_counts_roll_up_to_the_group(self):
        """收起来之后也要一眼看出这个项目有没有失败。"""
        self.assertEqual(self.g["烟火尽头05"]["failed"], 3)
        self.assertEqual(self.g["外婆的旧食谱"]["failed"], 0)

    def test_a_job_without_a_project_name_still_lands_somewhere(self):
        """★ 别让它凭空消失 —— 出图任务有时没带项目名。"""
        self.assertEqual(self.g["—"]["past"], ["6"])


class PageWiringTests(unittest.TestCase):
    """结构上的几条，不依赖 node。"""

    def _page(self):
        return io.open(PAGE, encoding="utf-8").read()

    def test_the_open_state_survives_a_refresh(self):
        """★ 每 1.5 秒重绘一次。不记状态，人刚点开就被弹回去。"""
        p = self._page()
        self.assertIn("const GRP_OPEN = {}", p)
        self.assertIn("GRP_OPEN[el.dataset.key] = el.open", p)
        self.assertIn("(g.key in GRP_OPEN) ? GRP_OPEN[g.key]", p)

    def test_the_state_is_read_before_the_redraw(self):
        """★ 顺序反了等于没记：先重绘就把旧节点连同状态一起冲掉了。"""
        p = self._page()
        self.assertLess(p.index("GRP_OPEN[el.dataset.key] = el.open"),
                        p.index("$('#jobCards').innerHTML = groups.length"))

    def test_the_pill_classes_exist(self):
        """★ 写了不存在的类名不会报错，只是那个标记完全没有样式。"""
        p = self._page()
        for cls in re.findall(r'class="pill (s-\w+)"', p):
            self.assertIn(f".{cls}{{", p.replace(" ", ""), cls)

    def test_the_group_styles_are_defined(self):
        p = self._page().replace(" ", "")
        for sel in (".jobgrp{", ".jobgrp>summary{", ".jobpast>summary{"):
            self.assertIn(sel, p, sel)

    def test_the_detail_and_cancel_buttons_still_get_wired(self):
        """★ 卡片挪进 details 之后，选择器还得选得到。"""
        p = self._page()
        self.assertIn("$$('#jobCards [data-detail]')", p)
        self.assertIn("$$('#jobCards [data-cancel]')", p)


if __name__ == "__main__":
    unittest.main()
