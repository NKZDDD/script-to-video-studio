# -*- coding: utf-8 -*-
"""删掉 `_common.md` 里那三个占位符 = 三组设置被整组架空，而以前没人拦。

用户实遇（2026-08-21）：全局 `_common.md` 改写里第 10 条是**写死的**
「画面内禁止出现字幕」，第 11/12/13 是手写的「3D漫剧风格」
「无需剧内对话全部改为画外音」「男女主颜值」—— 三个占位符一个都不在。

于是设置页里的**字幕、旁白、媒介**三组设定，对他的每一个项目都一个字
都进不了系统提示词：那三句生成出来的话没有位置可去，静静地被丢掉。
页面上那几个下拉照旧显示、存得下、就是没人读。

为什么能长期存在：
  · `REQUIRED_VARS["_common"]` 是**空的** —— 删掉不会被任何一处拦住
  · 页面那条红字只说「内置模板的新改动不会生效」，读的人会想
    「少几条新规则，无所谓」，而真相是「你填的设置白填了」
  · `test_no_unrendered_placeholder` 只查「渲染后还有没有没替换的 {{}}」，
    整块删掉反而是干净的
"""
import io
import os
import re
import unittest

from core import prompts as P

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 用户那份的样子（简化到关键几行）
THEIRS = """# 全局固定原则

1. 保持原剧本的故事、人物关系、因果、结局不变。
9. 已通过检查的资产、故事板、视频立即固定。
10. 画面内禁止出现字幕
11.3D漫剧风格
12.无需剧内对话全部改为画外音
13.男主和女主形象要像影视明星一样高的颜值

# 不可逆原则
"""


class RequiredTests(unittest.TestCase):

    def test_the_three_rule_placeholders_are_required_now(self):
        """★ 这一条就是那个洞：以前 `_common` 的必需表是空的。"""
        req = P.required_vars("_common")
        for v in ("SUBTITLE_RULE", "NARRATION_RULE", "MEDIUM_RULE"):
            self.assertIn(v, req)

    def test_the_builtin_itself_has_all_three(self):
        """★ 必需表和内置模板对不上的话，内置自己就存不下去。"""
        t = io.open(os.path.join(ROOT, "prompts", "_common.md"),
                    encoding="utf-8").read()
        have = set(re.findall(r"\{\{(\w+)\}\}", t))
        for v in P.required_vars("_common"):
            self.assertIn(v, have, f"内置 _common.md 里没有 {{{{{v}}}}}")

    def test_saving_without_them_is_an_error_not_a_warning(self):
        """★ 只警告的话，下一个人照样删。"""
        r = P.check("_common", THEIRS)
        self.assertTrue(r["errors"], "居然让它过了")
        msg = " ".join(r["errors"])
        self.assertIn("字幕", msg)
        self.assertIn("旁白", msg)

    def test_the_error_says_settings_are_silently_dead(self):
        """★ 「少了占位符」对读的人没有意义 —— 得说「你填的设置白填了」。"""
        msg = " ".join(P.check("_common", THEIRS)["errors"])
        self.assertIn("一个字都进不了提示词", msg)

    def test_every_required_var_has_a_human_name(self):
        """★ 报出「缺 SUBTITLE_RULE」等于没报 —— 读的人不知道那是什么。"""
        for name in P.REQUIRED_VARS:
            for v in P.required_vars(name):
                self.assertIn(v, P.VAR_GROUPS, f"{v} 没有对应的中文说法")


class VoidedTests(unittest.TestCase):

    def test_it_names_the_groups_not_the_placeholders(self):
        got = P.groups_of(P.voided("_common", THEIRS))
        self.assertEqual(len(got), 3)
        self.assertTrue(any("字幕" in x for x in got))
        self.assertTrue(any("旁白" in x for x in got))
        self.assertTrue(any("真人" in x or "3D" in x for x in got))

    def test_a_healthy_template_reports_nothing(self):
        t = io.open(os.path.join(ROOT, "prompts", "_common.md"),
                    encoding="utf-8").read()
        self.assertEqual(P.voided("_common", t), [])

    def test_it_is_not_the_same_thing_as_stale(self):
        """★ stale 说「新规则不生效」，这一条说「你填的设置白填了」。

        混成一条的后果就是这次：用户看到红字、觉得无所谓，
        而三组设置已经死了几个月。
        """
        import inspect
        src = inspect.getsource(P.voided)
        self.assertIn("比 `_stale` 更硬", src)


class UpgradeTests(unittest.TestCase):
    """「补回占位符」—— 只提议，不保存。"""

    def test_it_replaces_the_hardcoded_lines_in_place(self):
        """★ 在旧句子旁边**再加**一条的话，散文和生成的句子就并存了 ——

        而那正是这一整套占位符要解决的矛盾（字幕那个坑）。
        """
        r = P.upgrade("_common", THEIRS)
        self.assertIn("{{SUBTITLE_RULE}}", r["text"])
        self.assertNotIn("画面内禁止出现字幕", r["text"])
        self.assertNotIn("3D漫剧风格", r["text"])
        self.assertNotIn("无需剧内对话全部改为画外音", r["text"])

    def test_it_leaves_the_users_own_rules_alone(self):
        """★ 用户答的是「第三条是特殊项，不需要你去固定」—— 别动它。"""
        r = P.upgrade("_common", THEIRS)
        self.assertIn("男主和女主形象要像影视明星一样高的颜值", r["text"])
        self.assertIn("保持原剧本的故事", r["text"])
        self.assertIn("# 不可逆原则", r["text"])

    def test_the_result_passes_the_check_it_used_to_fail(self):
        """★ 补完还过不了校验的话，这个按钮就是白给。"""
        r = P.upgrade("_common", THEIRS)
        self.assertEqual(P.voided("_common", r["text"]), [])
        self.assertEqual(P.check("_common", r["text"])["errors"], [])

    def test_it_says_which_lines_it_touched(self):
        """★ 不说改了哪几行，人就只能整份重读一遍才敢存。"""
        r = P.upgrade("_common", THEIRS)
        self.assertEqual(len(r["changes"]), 3)
        self.assertTrue(all("{{" in c for c in r["changes"]))

    def test_a_missing_placeholder_with_no_old_line_is_appended(self):
        """认不出旧句子时补在编号列表末尾，**不猜位置**。

        猜位置就得猜他的编号和内置的怎么对应，猜错了会把他自己写的规则
        挪走或覆盖掉。补在末尾不好看，但不动他的东西。
        """
        bare = "# 原则\n\n1. 第一条\n2. 第二条\n"
        r = P.upgrade("_common", bare)
        self.assertIn("1. 第一条", r["text"])
        self.assertIn("2. 第二条", r["text"])
        for v in ("SUBTITLE_RULE", "NARRATION_RULE", "MEDIUM_RULE"):
            self.assertIn(f"{{{{{v}}}}}", r["text"])
        self.assertEqual(P.voided("_common", r["text"]), [])

    def test_the_numbering_continues_and_nothing_is_renumbered(self):
        bare = "1. 第一条\n2. 第二条\n"
        r = P.upgrade("_common", bare)
        nums = re.findall(r"^(\d+)\.", r["text"], re.M)
        self.assertEqual(nums, ["1", "2", "3", "4", "5"])

    def test_a_healthy_template_is_left_untouched(self):
        t = io.open(os.path.join(ROOT, "prompts", "_common.md"),
                    encoding="utf-8").read()
        r = P.upgrade("_common", t)
        self.assertEqual(r["text"], t)
        self.assertEqual(r["changes"], [])

    def test_it_does_not_save(self):
        """★ 这份东西是他手写的，程序不该替他按保存。"""
        import inspect
        src = inspect.getsource(P.upgrade)
        self.assertNotIn("write_text", src)
        self.assertIn("只提议，不保存", src)


class WiringTests(unittest.TestCase):

    HTML = io.open(os.path.join(ROOT, "web", "index.html"),
                   encoding="utf-8").read()
    APP = io.open(os.path.join(ROOT, "server", "app.py"),
                  encoding="utf-8").read()

    def test_read_hands_the_groups_to_the_page(self):
        import inspect
        self.assertIn('"voided": groups_of(', inspect.getsource(P))

    def test_the_page_shows_the_groups(self):
        self.assertIn("c.voided", self.HTML)
        self.assertIn("这几组设置现在一个字都进不了提示词", self.HTML)

    def test_the_button_is_wired_on_every_open(self):
        """★ 按钮随 state 一起重画 —— 在工厂体里挂一次的话它是 null，

        点了没反应，又是一个「不报错、只是不生效」。
        """
        self.assertIn("const wireUpgrade", self.HTML)
        self.assertIn("wireUpgrade();", self.HTML)
        self.assertLess(self.HTML.index("const wireUpgrade"),
                        self.HTML.index("E.open = async"),
                        "const 有 TDZ —— 定义必须在 E.open 之前")

    def test_the_endpoint_exists(self):
        self.assertIn('"/api/prompts/upgrade"', self.APP)

    def test_the_endpoint_does_not_save(self):
        i = self.APP.index('"/api/prompts/upgrade"')
        blk = self.APP[i:i + 700]
        self.assertNotIn("_pt.save", blk)


if __name__ == "__main__":
    unittest.main()
