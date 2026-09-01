# -*- coding: utf-8 -*-
"""场景状态图要落进自己那一类，不能混进资产图。

实遇（2026-09-01）：明细页「资产图（第5环节）」里列着
`PRJ_LATEFINE__SCSTATE_EP01_SEG01_W01_R01` —— 那是场景状态图。

`out_path` 一直是对的（SCSTATE → `03b_场景状态图/`），错的是 `build()`：
它只判了一个 `04_故事板`，**其余全进 asset_tasks**。一串静默后果：

  · 明细页「资产图」里混着 SCSTATE 条目
  · 「场景状态图（第11环节）」永远 0/0 —— 看得见、跑不了
  · **资产按集过滤失效**：资产那一类是全剧共享（同一个角色跨集只出一张，
    跨集人脸才一致），所以页面故意不按集筛它；而混进来的场景状态图是
    带集号的 —— 选了 EP01，别的集的场景状态图照样跟着跑
  · relay 的批次也错：p1（资产）里混着场景状态，而 p2 永远是空的
"""
import json
import unittest

from core import matimport as M


def _units():
    rows = [{"kind": "manifest", "total": 9,
             "params": {"image_size": "9:16", "ratio": "9:16"}},
            {"kind": "image", "key": "PRJ_X__CHAR_001_R02", "family": "CHAR",
             "name": "人", "filename": "PRJ_X__CHAR_001_R02.png", "size": "3:4",
             "reference_images": [], "prompt": "正文"}]
    for ep, n in (("EP01", 2), ("EP02", 3)):
        for i in range(1, n + 1):
            for fam, tag in (("SCSTATE", "W01"), ("SBSHEET", "A")):
                rows.append({
                    "kind": "image", "family": fam, "name": fam,
                    "key": f"PRJ_X__{fam}_{ep}_SEG{i:02d}_{tag}_R01",
                    "filename": f"PRJ_X__{fam}_{ep}_SEG{i:02d}_{tag}_R01.png",
                    "size": "9:16", "reference_images": [], "prompt": "正文"})
    return M.parse("\n".join(json.dumps(r, ensure_ascii=False) for r in rows))


class ScstateRoutingTests(unittest.TestCase):
    def setUp(self):
        self.tasks = M.build(_units(), system="v34")["tasks"]

    def test_scene_state_lands_in_its_own_bucket(self):
        """★ 三路分流，不是「不是故事板就算资产」。"""
        self.assertEqual(len(self.tasks["asset_tasks"]), 1)
        self.assertEqual(len(self.tasks["scstate_tasks"]), 5)
        self.assertEqual(len(self.tasks["storyboard_tasks"]), 5)
        for t in self.tasks["asset_tasks"]:
            self.assertNotIn("SCSTATE", t["key"].upper(),
                             "场景状态图又混进资产图了")

    def test_the_bucket_matches_where_the_file_goes(self):
        """★ 分到哪一类，要和 `out_path` 算出来的落点一致。

        两处不一致的表现最难查：产物落在 `03b_场景状态图/`，
        而任务挂在资产那一批 —— 进度、按集、relay 全都按错的那一批算。
        """
        for key, prefix in (("asset_tasks", "02_固定资产/"),
                            ("scstate_tasks", "03b_场景状态图/"),
                            ("storyboard_tasks", "04_故事板/"),
                            ("video_tasks", "05_分段视频/")):
            for t in self.tasks[key]:
                self.assertTrue(t["output"].startswith(prefix),
                                f"{key} 里的 {t['key']} 落在 {t['output']}")

    def test_scene_state_keeps_its_episode(self):
        """★ 它是**带集号**的 —— 这正是它不能混进资产的原因。

        资产不带集号（全剧共享），页面故意不按集筛资产那一类。
        混进去的带集号条目会跟着一起「不筛」。
        """
        eps = {t["episode"] for t in self.tasks["scstate_tasks"]}
        self.assertEqual(eps, {"EP01", "EP02"})
        self.assertEqual([t["episode"] for t in self.tasks["asset_tasks"]], [""])

    def test_filtering_by_episode_now_narrows_scene_state(self):
        from core import pipeline_v34 as PV
        del PV
        for tk, whole, ep01 in (("scstate_tasks", 5, 2), ("storyboard_tasks", 5, 2)):
            items = self.tasks[tk]
            self.assertEqual(len(items), whole, tk)
            self.assertEqual(
                len([t for t in items if t.get("episode") == "EP01"]), ep01, tk)

    def test_the_produce_table_has_a_row_for_it(self):
        """★ 生产任务那张表要有它自己那一行，否则按集也选不到。"""
        import io
        import os
        import re
        page = io.open(os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "web", "index.html"), encoding="utf-8").read()
        m = re.search(r"const GEN_KINDS = \[[\s\S]*?\];", page)
        self.assertIsNotNone(m)
        blk = m.group(0)
        for k, tkey in (("asset", "asset_tasks"), ("scstate", "scstate_tasks"),
                        ("storyboard", "storyboard_tasks"), ("video", "video_tasks")):
            self.assertIn(f"'{k}'", blk, k)
            self.assertIn(f"'{tkey}'", blk, tkey)

    def test_the_summary_reports_it_separately(self):
        """后端 summary 也要单列，不然那一行永远 0/0。"""
        import io
        import os
        src = io.open(os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "server", "app.py"), encoding="utf-8").read()
        i = src.index('if path == "/api/project":')
        seg = src[i:src.index('if path == "/api/episodes":', i)]
        self.assertIn('("scstate_tasks", "scstate")', seg)

    def test_run_settings_are_found_by_task_key(self):
        """★ 明细页「跑这一组」要按 task_key 找那一行，不能只按 kind。

        场景状态图和故事板的 kind 都是 storyboard（同一个 worker）——
        只按 kind 找会拿到故事板那一行的服务商和模型，而人在场景状态图
        那一行选的是另一家。两个入口用不同的服务商，**两边都不报错**。
        """
        import io
        import os
        import re
        page = io.open(os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "web", "index.html"), encoding="utf-8").read()
        m = re.search(r"function genSettings\(([^)]*)\)", page)
        self.assertIn("taskKey", m.group(1), "genSettings 还是只按 kind 找")
        self.assertIn('tr[data-tkey="${taskKey}"]', page)


if __name__ == "__main__":
    unittest.main()
