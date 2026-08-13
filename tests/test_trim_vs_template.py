# -*- coding: utf-8 -*-
"""裁掉的字段，不能是模板点名要用的。

真跑撞到过一次，而且现象非常迷惑：

  环节3 只吐了 373 字就交白卷，报「输出缺少必需字段 scenes[]」。

根因是我把 n3 从逐集改成全剧级时，一边**加了**「每一场必须写
`scenes[].episode`」这个必需字段，一边把 `episode_ranges`
（这部剧有哪几集、各自从哪开始）**从它的输入里裁掉了**。
模型被要求给每场标集号，却不知道有哪几集。

这类错的signature：**裁剪表和必需字段表是两个地方**，
在一处加要求、在另一处砍依据，而且**完全不报错** —— 模型只是交白卷。

能自动查的信号：n3 的模板正文里白纸黑字写着
「集的划分见【故事真相】里的 `episode_ranges`」。
模板点名要的东西被裁掉了，那一定是错的。
"""
import io
import json
import os
import re
import unittest

from core import system_v34 as V

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def body_of(tpl: str) -> str:
    """模板正文，去掉输出 schema 那一段。

    必须去掉：schema 里的键名是**这个环节自己要产出的**，
    和「它需要上游的哪些字段」是两回事。不去掉会一堆误报
    （比如 n4b 自己的 schema 里也有 scope）。
    """
    t = io.open(os.path.join(ROOT, "prompts", f"{tpl}.md"),
                encoding="utf-8").read()
    return re.sub(r"```json.*?```", "", t, flags=re.S)


def source_template(out_name: str) -> str:
    sid = next(s["id"] for s in V.STAGES if s.get("out") == out_name)
    return V.LLM_SPEC[sid][0]


def source_schema(out_name: str) -> dict:
    t = io.open(os.path.join(ROOT, "prompts", f"{source_template(out_name)}.md"),
                encoding="utf-8").read()
    m = re.search(r"```json(.*?)```", t, re.S)
    return json.loads(re.sub(r"\{\{\w+\}\}", "0", m.group(1))) if m else {}


def removed_fields(sid: str, out: str) -> list:
    """这条裁剪实际去掉了哪些顶层字段。"""
    spec = V.needs_of(sid, out)
    if spec.get("keep"):
        return [k for k in source_schema(out) if k not in spec["keep"]]
    return [p.replace("[]", "").split(".")[-1] for p in spec.get("drop", [])]


class TrimVsTemplateTests(unittest.TestCase):

    def test_no_template_asks_for_a_field_that_was_trimmed_away(self):
        """★ 模板正文点名要的上游字段，一个都不许裁。"""
        bad = []
        for (sid, out) in V.PRODUCT_NEEDS:
            text = body_of(V.LLM_SPEC[sid][0])
            for f in removed_fields(sid, out):
                if re.search(r"`" + re.escape(f) + r"`", text):
                    bad.append(f"{sid} 的模板里点名要 `{f}`，却被从 {out} 里裁掉了")
        self.assertEqual(bad, [], "\n".join(bad))

    def test_the_narrative_stage_keeps_what_it_needs_to_tag_episodes(self):
        """★ 这就是踩过的那一处，单独钉死。

        n3 要给每一场标集号 → 必须看得到集清单。
        """
        self.assertIn("scenes[].episode", V.LLM_SPEC["n3"][2])
        self.assertNotIn("episode_ranges",
                         V.needs_of("n3", "n1_truth").get("drop", []))
        self.assertIn("episode_ranges", body_of("n3_narrative"),
                      "模板没告诉模型去哪找集清单")

    def test_every_trim_actually_removes_something(self):
        """裁剪表里写了一条却什么都没去掉 —— 多半是字段名拼错了。

        拼错的话那条规则永远不生效，而且一声不吭。
        """
        dead = []
        for (sid, out) in V.PRODUCT_NEEDS:
            schema = source_schema(out)
            for f in removed_fields(sid, out):
                if f not in schema and not any(
                        f in (r or {}) for v in schema.values()
                        if isinstance(v, list) for r in v
                        if isinstance(r, dict)):
                    dead.append(f"{sid} ← {out}：`{f}` 在 {source_template(out)} "
                                f"的 schema 里根本不存在")
        self.assertEqual(dead, [], "\n".join(dead))


if __name__ == "__main__":
    unittest.main()
