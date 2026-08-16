# -*- coding: utf-8 -*-
"""项目基础信息的表单：schema 和值从后端来，页面不维护第二份字段表。

维护两份的后果是「填了没生效」而且不报错：字段表加了一项，
页面没跟着加，人在别处填了值，程序永远读不到。
"""
import io
import os
import shutil
import unittest

from core import settings as ST
from server.app import api_post
from test_v34_run import new_project

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class ApiTests(unittest.TestCase):

    def setUp(self):
        self.pj = new_project()

    def tearDown(self):
        shutil.rmtree(self.pj.root, ignore_errors=True)

    def _get(self, **kw):
        return api_post("/api/project/settings", dict(kw, project_root=self.pj.root))

    def test_it_serves_every_field_with_its_value(self):
        r = self._get()
        self.assertEqual(len(r["fields"]), len(ST.FIELDS))
        self.assertEqual(set(r["groups"]), {f["group"] for f in ST.FIELDS})

    def test_saving_a_settings_field_sticks(self):
        self._get(values={"visual_style": "末日废土"})
        got = {f["key"]: f["value"] for f in self._get()["fields"]}
        self.assertEqual(got["visual_style"], "末日废土")

    def test_a_mirrored_field_goes_back_to_params_not_settings(self):
        """★ 存两份就会出现「页面显示 9:16、实际按 16:9 跑」而且没人发现。"""
        self._get(values={"aspect_ratio": "16:9"})
        self.assertEqual((self.pj.meta().get("params") or {}).get("ratio"), "16:9")
        self.assertNotIn("aspect_ratio", self.pj.meta().get("settings") or {})

    def test_a_readonly_field_is_not_writable(self):
        self._get(values={"id_policy": "乱写的"})
        self.assertNotIn("id_policy", self.pj.meta().get("settings") or {})

    def test_each_field_says_which_templates_use_it(self):
        """★ 这是扫模板得出的，不是手写表。"""
        by = {f["key"]: f for f in self._get()["fields"]}
        self.assertIn("n12_storyboard", by["storyboard_max_kf_per_sheet"]["used_by"])

    def test_settings_fields_are_never_marked_unused(self):
        """★ 这条标反过一次，而标反比不标更糟。

        `special_notes`、`dialogue_language` 这些一度显示「暂未被任何模板
        使用」—— 用户看到就问「为什么这些都没用上」。**它们一直在起作用**：
        走 {{PROJECT_BRIEF}} 进 `_common`，而 `_common` 是每一次调用都发的
        系统提示词。只是没有哪份业务模板单独写 {{DIALOGUE_LANGUAGE}}。
        """
        by = {f["key"]: f for f in self._get()["fields"]}
        for k in ("special_notes", "dialogue_language", "video_audio_mode"):
            self.assertTrue(by[k]["used_by"], f"{k} 被标成了没人用")
            self.assertTrue(any("全环节" in u for u in by[k]["used_by"]), k)

    def test_program_side_fields_can_still_be_unused(self):
        """反过来也要成立：画幅是发给出图接口的参数，**通过代码生效**，

        没有模板读它是对的 —— 全都标成「有人用」就等于这个标记没意义了。
        """
        by = {f["key"]: f for f in self._get()["fields"]}
        self.assertEqual(by["aspect_ratio"]["used_by"], [])

    def test_an_empty_mirrored_value_does_not_wipe_the_param(self):
        """★ 只读字段渲染成空的时候会被一起回传；不能把 params 清掉。"""
        self._get(values={"aspect_ratio": "9:16"})
        self._get(values={"aspect_ratio": ""})
        self.assertEqual((self.pj.meta().get("params") or {}).get("ratio"), "9:16")


class PageTests(unittest.TestCase):

    def setUp(self):
        self.html = io.open(os.path.join(ROOT, "web", "index.html"),
                            encoding="utf-8").read()

    def test_the_panel_exists_and_is_wired(self):
        for hook in ('id="briefBox"', 'id="saveBrief"', "function renderBrief",
                     "/api/project/settings"):
            self.assertIn(hook, self.html, hook)

    def test_the_page_does_not_hardcode_the_field_list(self):
        """★ 页面写死一份字段表 = 两处要同步，漏一处就是填了没生效。"""
        self.assertIn("BRIEF = r.fields", self.html)
        for k in ("visual_medium", "adaptation_authority", "costume_asset_mode"):
            self.assertNotIn(f"'{k}'", self.html, f"页面里写死了字段 {k}")

    def test_it_refreshes_when_you_switch_projects(self):
        self.assertIn("renderBrief();", self.html)

    def test_bold_in_the_notes_is_escaped_before_converting(self):
        """★ 先 esc 再转粗体。顺序反了就是 XSS。"""
        i = self.html.index("const mdBold")
        self.assertIn("esc(s)", self.html[i:i + 120])


if __name__ == "__main__":
    unittest.main()
