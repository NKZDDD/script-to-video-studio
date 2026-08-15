# -*- coding: utf-8 -*-
"""两套体系对外只有两个名字，而且只有一个来源。

名字一度有三层在打架：内部 id 叫 `v34`、界面写「V3.4」、
而实际在跑的 skill 已经是 V5.6 —— 同一套东西三个叫法。
后果不是难看：讨论问题时说「V3.4 的第 9 环节」，对方不确定你说的是
skill 的第 9 章还是程序的第 9 步，排查一个 bug 要先花几轮对齐术语。

**内部 id 不许改**：它是 project.json 里 `system` 字段的值，
改了所有已建项目会被判成「另一套体系」，产物全部重跑、钱重花一遍。
"""
import io
import os
import unittest

from server import app

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(rel):
    return io.open(os.path.join(ROOT, rel), encoding="utf-8").read()


class LabelTests(unittest.TestCase):

    def test_every_system_has_a_label(self):
        self.assertEqual(set(app.SYSTEM_LABELS), set(app.SYSTEMS))

    def test_the_full_names(self):
        self.assertEqual(app.system_label("v34"), "电影级十七章（V5.6）")
        self.assertEqual(app.system_label("v61"), "通用十二环节（V6.1）")

    def test_an_unknown_id_does_not_crash(self):
        self.assertEqual(app.system_label("nope"), "nope")
        self.assertTrue(app.system_label(""))

    def test_the_internal_ids_are_unchanged(self):
        """★ 改 id = 已建项目全被判成另一套体系，产物重跑、钱重花。

        这条不是洁癖：project.json 里存的就是这两个字符串。
        """
        self.assertEqual(app.SYSTEMS, ("v61", "v34"))


class SingleSourceTests(unittest.TestCase):
    """名字散成几份就会飘 —— 这次就是这么飘的。"""

    def test_the_page_does_not_hardcode_the_old_name(self):
        html = _read(os.path.join("web", "index.html"))
        for stale in ("V3.4 电影级十七章", "V6.1 十二环节"):
            self.assertNotIn(stale, html, f"页面上还留着旧叫法：{stale}")

    def test_the_picker_shows_the_new_name(self):
        html = _read(os.path.join("web", "index.html"))
        self.assertIn('<option value="v34">电影级十七章（V5.6）</option>', html)

    def test_the_stage_heading_is_not_hardcoded_to_twelve(self):
        """★ 写死「十二环节」时，跑十七章的项目标题是错的，

        而下面列的又是十七章的环节 —— 自相矛盾，人会以为程序拿错了体系。
        """
        html = _read(os.path.join("web", "index.html"))
        self.assertNotIn("<h2>十二环节", html)
        self.assertIn('id="stagesTitle"', html)

    def test_the_shared_system_prompt_does_not_claim_one_system(self):
        """★ _common 是**两套共用**的 system prompt。

        原来第一句写着「V6.1 通用版」，跑十七章的项目时等于告诉模型
        它在跑另一套东西。
        """
        common = _read(os.path.join("prompts", "_common.md"))
        first = common.strip().splitlines()[0]
        self.assertNotIn("V6.1", first)
        self.assertNotIn("V3.4", first)


class BootstrapTests(unittest.TestCase):

    def test_bootstrap_serves_the_canonical_names(self):
        """页面拿到的名字必须来自 SYSTEM_LABELS，不是另写的一份。"""
        boot = app.api_get("/api/bootstrap", {})
        got = {k: v["name"] for k, v in boot["systems"].items()}
        self.assertEqual(got, {"v34": "电影级十七章（V5.6）",
                               "v61": "通用十二环节（V6.1）"})

    def test_each_system_still_carries_its_stage_table(self):
        """改名不能顺手把环节表弄丢 —— 页面靠它渲染整个流程。"""
        boot = app.api_get("/api/bootstrap", {})
        self.assertTrue(boot["systems"]["v34"]["stages"])
        self.assertTrue(boot["systems"]["v61"]["stages"])


if __name__ == "__main__":
    unittest.main()
