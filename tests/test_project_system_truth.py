# -*- coding: utf-8 -*-
"""页面显示的体系，必须就是实际生产用的那一套。

用户实跑撞到：**通用版的包，项目页画着 12 个环节，实际跑的是电影级 17 章**。

原因是页面在猜：

    function projSystem() {
      const p = (BOOT.projects || []).find(x => x.root === PROJ);
      return (p && p.system) || 'v61';        // ← 找不到就默认通用版
    }

`BOOT.projects` 是**页面加载那一刻**取的列表，新建的项目根本不在里面 ——
find 返回 undefined，回落成 'v61'，于是页面画 12 个环节，
而后端按 project.json 里真实的 system 跑 17 章。

**两套东西并行，而且不报错。** 人照着 12 环节的界面操作，
产出的是另一套体系的产物，等发现时已经跑了一堆。

第二个坑藏得更深：`/api/project` 算「哪些环节做完了」时写死用 V6.1 的
环节表，跟项目实际体系无关 —— v34 项目拿到的是 s1..s8 的状态，
而页面画的是 n1..n14，每一格都显示「没做」，已经跑完的看起来一个都没跑。
"""
import io
import os
import shutil
import unittest

from server.app import api_get
from test_v34_run import new_project

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class ApiTests(unittest.TestCase):

    def setUp(self):
        self.pj = new_project()

    def tearDown(self):
        shutil.rmtree(self.pj.root, ignore_errors=True)

    def _get(self):
        return api_get("/api/project", {"root": [self.pj.root]})

    def test_it_reports_the_system_authoritatively(self):
        """★ 页面必须有一个权威来源可用，不然只能猜。"""
        self.pj.save_meta(dict(self.pj.meta(), system="v34"))
        self.assertEqual(self._get()["system"], "v34")

    def test_a_v61_project_reports_v61(self):
        self.pj.save_meta(dict(self.pj.meta(), system="v61"))
        self.assertEqual(self._get()["system"], "v61")

    def test_stage_state_follows_the_project_system(self):
        """★ 第二个坑：完成状态写死按 V6.1 的环节表算。

        v34 项目拿到 s1..s8 的状态，而页面画的是 n1..n14 ——
        每一格都显示「没做」，已经跑完的环节看起来一个都没跑。
        """
        self.pj.save_meta(dict(self.pj.meta(), system="v34"))
        self.pj.save_stage("n1_truth", {"x": 1}, "")
        done = self._get()["stages_done"]
        self.assertIn("n1", done, "v34 项目应该按 n* 环节表算")
        self.assertTrue(done["n1"])
        self.assertNotIn("s1", done, "不该混进 V6.1 的环节 id")

    def test_a_v61_project_gets_the_v61_table(self):
        self.pj.save_meta(dict(self.pj.meta(), system="v61"))
        done = self._get()["stages_done"]
        self.assertIn("s1", done)
        self.assertNotIn("n1", done)


class PageTests(unittest.TestCase):

    def setUp(self):
        self.html = io.open(os.path.join(ROOT, "web", "index.html"),
                            encoding="utf-8").read()

    def test_the_page_does_not_guess_from_the_stale_list(self):
        """★ 这就是那个 bug 本身。"""
        self.assertNotIn("(BOOT.projects || []).find(x => x.root === PROJ)",
                         self.html)

    def test_it_uses_the_value_from_the_api(self):
        self.assertIn("PROJ_SYSTEM = d.system", self.html)

    def test_unknown_system_is_not_silently_v61(self):
        """★ 不知道就说不知道 —— 默认成某一套就是页面替后端撒谎。"""
        i = self.html.index("function sysName()")
        blk = self.html[i:i + 400]
        self.assertIn("体系未知", blk)
        self.assertNotIn("|| '通用十二环节（V6.1）'", blk)

    def test_the_stage_table_is_empty_when_unknown(self):
        i = self.html.index("function sysStages()")
        blk = self.html[i:i + 400]
        self.assertNotIn("BOOT.stages", blk, "又回落到某一套了")

    def test_switching_projects_clears_the_old_system(self):
        """★ 上一个项目的体系不能带到下一个身上 —— 那会画错一整帧。"""
        i = self.html.index("async function openProject")
        self.assertIn("PROJ_SYSTEM = ''", self.html[i:i + 300])

    def test_an_unknown_system_says_so_instead_of_showing_nothing(self):
        """空白会让人以为「这个项目没有环节」。"""
        i = self.html.index("function renderStages")
        self.assertIn("还没读到这个项目用哪套体系", self.html[i:i + 600])


if __name__ == "__main__":
    unittest.main()
