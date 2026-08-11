# -*- coding: utf-8 -*-
"""V3.4 模板和环节表必须对得上。

三类错都不会立刻炸，只会在跑到那一步、花掉几十次调用之后才显形：

  · 模板里写了 `{{ASSETS}}` 但环节没声明依赖 n4_assets
    → 占位符永远填不上、原样发给模型。模型看到大括号通常会假装那里有内容
      继续往下编，产出看着正常但整段是瞎写的。
  · 必需输出字段在模板的 schema 里根本没提
    → 模型不可能产出它，每次都触发校验重试，三次之后整个环节失败。
  · 模板文件不存在
    → 跑到那一步才报「找不到文件」。
"""
import os
import re
import unittest

from core import system_v34 as V

PROMPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "prompts")
PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")


def written() -> list:
    """已经写好模板的环节。没写的先跳过 —— 模板是分批落地的。"""
    out = []
    for sid, (tpl, _, _) in V.LLM_SPEC.items():
        p = os.path.join(PROMPTS, tpl + ".md")
        if os.path.isfile(p):
            out.append((sid, tpl, open(p, encoding="utf-8").read()))
    return out


class TemplateTests(unittest.TestCase):

    def test_at_least_the_first_batch_is_written(self):
        """防止这个测试因为一个模板都没写而全部空跑，看着像通过。"""
        names = {sid for sid, _, _ in written()}
        self.assertTrue({"n1", "n2", "n3"} <= names,
                        f"前三个环节的模板还没齐：{sorted(names)}")

    def test_placeholders_are_all_fillable(self):
        """★ 模板里的每个占位符都得有人填。"""
        for sid, tpl, text in written():
            allowed = V.placeholders_for(sid)
            used = set(PLACEHOLDER.findall(text))
            extra = used - allowed
            self.assertFalse(
                extra,
                f"{tpl}.md 用了填不上的占位符 {sorted(extra)}；"
                f"这个环节能用的是 {sorted(allowed)}。"
                f"要用别的产物，先在 LLM_SPEC[{sid!r}] 的依赖里加上它")

    def test_declared_dependencies_are_actually_used(self):
        """反过来：声明了依赖却不在模板里引用，等于白读一份产物、白花 token。"""
        for sid, tpl, text in written():
            _, deps, _ = V.LLM_SPEC[sid]
            used = set(PLACEHOLDER.findall(text))
            for d in deps:
                self.assertIn(
                    V.placeholder_of(d), used,
                    f"{tpl}.md 声明依赖 {d} 却没用 {{{{{V.placeholder_of(d)}}}}}；"
                    f"要么在模板里用上，要么从依赖里去掉")

    def test_required_fields_appear_in_the_template(self):
        """★ 必需字段模板里得提过，否则模型不可能产出它。"""
        for sid, tpl, text in written():
            _, _, req = V.LLM_SPEC[sid]
            for f in req:
                key = f.split("[")[0].split(".")[-1] or f.split("[")[0]
                self.assertIn(
                    key, text,
                    f"{tpl}.md 里没提必需字段 {f}（找的是 {key!r}）—— "
                    f"模型产不出来，每次都会触发校验重试然后失败")

    def test_schema_block_parses(self):
        """schema 示例写歪了，模型会跟着输出错格式。"""
        import json
        for _, tpl, text in written():
            blocks = re.findall(r"```json\s*(.*?)```", text, re.S)
            self.assertTrue(blocks, f"{tpl}.md 没有 json schema 块")
            probe = re.sub(r"\{\{\w+\}\}", "0", blocks[0])
            try:
                json.loads(probe)
            except ValueError as exc:
                self.fail(f"{tpl}.md 的 schema 解析不了：{exc}")

    def test_per_episode_templates_say_so(self):
        """逐集/逐段的模板必须写明范围，否则模型会把整部剧都做一遍。"""
        for sid, tpl, text in written():
            if V.scope_of(sid) == "series":
                continue
            self.assertIn("{{EPISODE}}", text,
                          f"{tpl}.md 是{V.scope_of(sid)}级却没提 {{{{EPISODE}}}}")
            self.assertTrue(
                "只处理" in text or "只排这一段" in text or "只编译这一段" in text,
                f"{tpl}.md 没写清本次只做哪一集/哪一段")

    def test_series_templates_do_not_pretend_to_be_per_episode(self):
        for sid, tpl, text in written():
            if V.scope_of(sid) != "series":
                continue
            self.assertNotIn("{{EPISODE}}", text,
                             f"{tpl}.md 是全剧级，不该有 {{{{EPISODE}}}}")


class SeparationTests(unittest.TestCase):
    """这套体系最容易被写回去的两条边界，在模板里也钉一遍。"""

    def _text(self, tpl):
        p = os.path.join(PROMPTS, tpl + ".md")
        return open(p, encoding="utf-8").read() if os.path.isfile(p) else ""

    def test_rules_stage_separates_permanent_from_current_state(self):
        """★ 人物规则里混进当前状态 → 某一集的临时伤变成人物固有特征。"""
        t = self._text("n2_rules")
        if not t:
            self.skipTest("n2_rules 还没写")
        self.assertIn("当前状态", t)
        self.assertTrue("第六环节" in t or "连续性" in t,
                        "没指明当前状态该去哪个环节")

    def test_narrative_stage_forbids_seg_driven_rewriting(self):
        """★ 按 SEG 反推剧情是这一层最严重的错。"""
        t = self._text("n3_narrative")
        if not t:
            self.skipTest("n3_narrative 还没写")
        self.assertIn("SEG", t)
        self.assertIn("不许按 SEG 反推剧情", t)

    def test_narrative_stage_does_not_design_shots(self):
        t = self._text("n3_narrative")
        if not t:
            self.skipTest("n3_narrative 还没写")
        self.assertIn("不设计镜头", t)


if __name__ == "__main__":
    unittest.main()
