# -*- coding: utf-8 -*-
"""模板里的占位符必须**真的渲染得出来**。

漏一个的后果不是报错：`{{EPISODE_SECONDS}}` 会原样出现在发给模型的提示词里，
模型会把它当成一个要填的空位、或者一句奇怪的指令。`settings.mapping` 的注释
自己就写着这个后果，而我照样漏了一个 —— 加了模板里的占位符，
却没接上渲染那一端。

**两份 mapping 是分开的**，这就是漏的地方：

    settings.mapping()   → 只渲染 _common.md（系统提示词）
    stages._mapping()    → 渲染业务模板（s1_global.md / n1_truth.md / …）

所以往业务模板里塞一个设置项占位符，不动 stages._mapping 是没用的。
这条测试把两边一起扫，任何一个渲染不出来的占位符都会红。
"""
import io
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROMPTS = os.path.join(ROOT, "prompts")

# 这几个是**模板正文里当例子写的**，不是要渲染的占位符。
# 例：n4b 的示例正文里写着 `{{XXX}}` 教模型怎么排版。
ALLOW = set()


def _placeholders(text: str) -> set:
    return set(re.findall(r"\{\{([A-Z_][A-Z0-9_]*)\}\}", text))


# 这四个只有 `_common.md` 用得到 —— 它们是 system_prompt() 在
# settings.mapping 之外额外塞的，业务模板不需要。
COMMON_ONLY = {"SUBTITLE_RULE", "NARRATION_RULE", "MEDIUM_RULE", "PROJECT_BRIEF"}


def _settings_keys() -> set:
    """`settings.mapping()` **实际**返回的键。跑一遍，不猜。"""
    import tempfile

    from core import settings as S
    from core.store import Project
    return set(S.mapping(Project(tempfile.mkdtemp()), {}, {}))


def _stage_keys() -> set:
    """两套体系的业务模板 mapping **实际**能提供的键。

    不用「从源码里抠 \"KEY\":」那种办法 —— 两边的写法不一样
    （一边是字典字面量，一边是 m.update(...)），抠出来的必然不全，
    然后报一堆假缺失。直接把函数跑一遍，看它返回了什么。
    """
    import tempfile

    from core import run_v34 as R
    from core import stages as S
    from core.store import Project

    root = tempfile.mkdtemp()
    pj = Project(root)
    params = {"project_code": "P", "duration": 15, "ratio": "9:16",
              "image_size": "1024x1536", "script": ""}
    from core import system_v34 as V
    out = set()
    # **每个环节都要跑一遍。** mapping 是按环节给依赖产物的
    # （n2 才有 TRUTH、n11 才有 CVS），只跑三个必然缺一堆，
    # 然后报一片假缺失 —— 上一版就是这么错的。
    for st in S.STAGES:
        if st["kind"] == "llm":
            out |= set(S._mapping(pj, st["id"], params, {}, "EP01", ""))
    for st in V.STAGES:
        if st["kind"] == "llm":
            out |= set(R.mapping(pj, st["id"], params, {},
                                 "EP01", "EP01-SEG01"))
    # 上游产物那一类占位符（TRUTH / CVS / SEGS…）只在 data 里真有那份产物时
    # 才出现在 mapping 里。**按规格表推导**，不手写清单 ——
    # 手写的迟早和依赖表对不上，而对不上就是一个假缺失或者一个真漏报。
    out |= {V.placeholder_of(st["out"]) for st in V.STAGES if st.get("out")}
    out |= {V.placeholder_of(dep)
            for _tpl, deps, _req in V.LLM_SPEC.values() for dep in deps}
    return out


class PlaceholderTests(unittest.TestCase):

    def setUp(self):
        self.settings_keys = _settings_keys()
        self.stage_keys = _stage_keys()

    def test_the_common_block_only_uses_settings_keys(self):
        """`_common.md` 走 settings.mapping。"""
        used = _placeholders(io.open(os.path.join(PROMPTS, "_common.md"),
                                     encoding="utf-8").read())
        missing = sorted(used - self.settings_keys - COMMON_ONLY - ALLOW)
        self.assertEqual(missing, [], f"_common.md 里这些渲染不出来：{missing}")

    def test_every_stage_template_renders(self):
        """★ 这就是逮到那个 bug 的检查。

        业务模板走 stages._mapping / run_v34.mapping —— **不走 settings.mapping**。
        把设置项占位符塞进业务模板而不动渲染那一端，它会原样发给模型。
        """
        bad = {}
        for name in sorted(os.listdir(PROMPTS)):
            if not name.endswith(".md") or name.startswith("_"):
                continue
            used = _placeholders(io.open(os.path.join(PROMPTS, name),
                                         encoding="utf-8").read())
            missing = sorted(used - self.stage_keys - ALLOW)
            if missing:
                bad[name] = missing
        self.assertEqual(bad, {},
                         "这些占位符会原样出现在提示词里：\n"
                         + "\n".join(f"  {k}: {v}" for k, v in bad.items()))

    def test_the_stage_mapping_includes_the_settings(self):
        """★ 这一条钉的就是那个 bug 的修法。

        两份 mapping 本来是分开的：settings.mapping 只渲染 `_common.md`，
        业务模板走另一份。于是往业务模板里写设置项占位符会**原样发给模型**。
        电影级那边一直是并的，通用级漏了 —— 现在两边都并。
        谁把这一步去掉，这条会红。
        """
        missing = sorted(self.settings_keys - self.stage_keys - COMMON_ONLY)
        self.assertEqual(missing, [],
                         f"业务模板 mapping 里少了这些设置项：{missing}")

    def test_both_systems_merge_it(self):
        import inspect

        from core import run_v34 as R
        from core import stages as S
        for fn in (S._mapping, R.mapping):
            self.assertIn("_st.mapping", inspect.getsource(fn), fn.__name__)


if __name__ == "__main__":
    unittest.main()
