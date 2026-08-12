# -*- coding: utf-8 -*-
"""模板的 schema 示例，必须过得了它自己的必需字段。

这是一类**只能靠真跑才发现**的错，而且报错完全指错方向：

  环节2 输出 15411 字、跑了 155 秒，完整答完了 ——
  然后报「输出缺少必需字段：['cultural_rules[]']」，重试两次全废。

看着像模型不听话。实际是模板里 `cultural_rules` 写成了**对象**，
而必需字段那张表写成了 `cultural_rules[]`（check_keys 要求非空数组）。
模型照着 schema 答出一个 dict，永远过不了。

同一批还有 n7：模板里是 `zone_id`，必需字段写成 `zone`。

这两处都不是模型的问题，是**两份声明对不上**。而它们分在两个文件里
（schema 在 prompts/*.md，必需字段在 system_v34.py），改一处忘另一处
是迟早的事 —— 所以这个检查必须常驻。
"""
import io
import json
import os
import re
import unittest

from core import stages as S, system_v34 as V
from core.llm import check_keys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def schema_of(tpl: str):
    """模板里那段 ```json 示例，占位符换成 0 之后解析出来。"""
    t = io.open(os.path.join(ROOT, "prompts", f"{tpl}.md"), encoding="utf-8").read()
    m = re.search(r"```json(.*?)```", t, re.S)
    if not m:
        return None
    return json.loads(re.sub(r"\{\{\w+\}\}", "0", m.group(1)))


class V34Tests(unittest.TestCase):

    def test_every_schema_passes_its_own_required_fields(self):
        """★ 模板自己的示例都过不了，模型照着写当然也过不了。"""
        bad = {}
        for sid, (tpl, _deps, req) in V.LLM_SPEC.items():
            d = schema_of(tpl)
            self.assertIsNotNone(d, f"{tpl} 里没有 json 示例")
            miss = check_keys(d, req)
            if miss:
                bad[sid] = miss
        self.assertEqual(bad, {}, f"这几个环节永远过不了校验：{bad}")

    def test_array_markers_match_the_actual_shape(self):
        """★ `xxx[]` 要求非空数组，`xxx` 只要求键存在。写反了必然卡死。

        cultural_rules 就是写反的：它是对象，却标了 []。
        """
        for sid, (tpl, _deps, req) in V.LLM_SPEC.items():
            d = schema_of(tpl)
            for spec in req:
                if "." in spec:
                    continue                       # 嵌套的交给上面那条整体验
                key, want_list = spec.rstrip("[]"), spec.endswith("[]")
                if key not in d:
                    continue                       # 缺键由上面那条报
                got_list = isinstance(d[key], list)
                self.assertEqual(
                    got_list, want_list,
                    f"{sid} 的 {key}：schema 里是 "
                    f"{'数组' if got_list else type(d[key]).__name__}，"
                    f"必需字段写成 {spec}")

    def test_nested_field_names_exist_in_the_schema(self):
        """★ `blocking[].zone` vs 模板里的 `zone_id` —— 名字差一点就永远缺。"""
        bad = []
        for sid, (tpl, _deps, req) in V.LLM_SPEC.items():
            d = schema_of(tpl)
            for spec in req:
                if "." not in spec:
                    continue
                parent, child = spec.split(".", 1)
                rows = d.get(parent.rstrip("[]"))
                if not isinstance(rows, list) or not rows:
                    continue
                if not isinstance(rows[0], dict):
                    continue
                if child.rstrip("[]") not in rows[0]:
                    bad.append(f"{sid}: {spec} —— 示例里的键是 "
                               f"{sorted(rows[0])[:6]}")
        self.assertEqual(bad, [], "必需字段名和 schema 对不上：\n" + "\n".join(bad))


class V61Tests(unittest.TestCase):
    """V6.1 那套也过一遍 —— 同一类错，同样只有真跑才撞得到。"""

    def test_every_schema_passes_its_own_required_fields(self):
        bad = {}
        for sid, (tpl, _deps, req) in S._LLM_SPEC.items():
            d = schema_of(tpl)
            if d is None:
                continue
            miss = check_keys(d, req)
            if miss:
                bad[sid] = miss
        self.assertEqual(bad, {}, f"这几个环节永远过不了校验：{bad}")


if __name__ == "__main__":
    unittest.main()
