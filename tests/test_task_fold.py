# -*- coding: utf-8 -*-
"""任务明细的折叠：点一下就要展开。

实遇（2026-08-31）：加「跑这一组」按钮时中间多包了一层 `.row`，于是 `.grp`
从「父节点的子节点」变成了「父节点的兄弟节点」——
`el.parentElement.querySelector('.grp')` 返回 null，下一行 `.classList` 抛异常。

而 `TKFold[k] = !TKFold[k]` 排在抛之前，状态**已经翻了**。所以表现是
「点了没反应，再点一下刷新就展开了」—— 不是没执行，是执行了一半。
这类「一半」比整个不执行难查得多。
"""
import os
import re
import unittest

HTML = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "web", "index.html")


def _page() -> str:
    with open(HTML, encoding="utf-8") as f:
        return f.read()


class TaskFoldTests(unittest.TestCase):
    def test_the_fold_anchors_on_the_group_box_not_the_parent(self):
        """★ 用 `closest('.tkgroup')`，不用 `parentElement`。

        层级会变（这次就变了一回）。锚在容器类名上，中间再包几层都不影响。
        """
        page = _page()
        h = re.search(r"#tkBody \.tkfold'\)[\s\S]{0,1200}?\n  \}\);", page)
        self.assertIsNotNone(h, "找不到折叠的处理器")
        body = h.group(0)
        self.assertIn("closest('.tkgroup')", body)
        self.assertNotIn("el.parentElement.querySelector('.grp')", body,
                         "又回到靠 parentElement 认亲了")

    def test_the_group_box_has_the_class_the_handler_looks_for(self):
        """处理器找 `.tkgroup`，模板就得真的输出它 —— 两边对不上就是静默失效。"""
        page = _page()
        tpl = re.search(r'return `<div class="tkgroup"[\s\S]*?</div>`;', page)
        self.assertIsNotNone(tpl, "分组模板上没有 .tkgroup 这个类")
        block = tpl.group(0)
        # `.grp` 必须在 `.row` **之后**（是兄弟）—— 这正是坑的来源，
        # 写下来是为了下次有人把它挪回 `.row` 里面时，上面那条锚点仍然成立。
        self.assertLess(block.index('class="row"'), block.index('class="grp'))

    def test_the_row_fold_anchors_on_segrow(self):
        """条目那一层同理。现在层级还对，但别再靠 parentElement。"""
        page = _page()
        h = re.search(r"#tkBody \.segrow > \.hd'\)[\s\S]{0,900}?\n  \}\);", page)
        self.assertIsNotNone(h)
        body = h.group(0)
        self.assertIn("closest('.segrow')", body)
        self.assertNotIn("el.parentElement.querySelector('.bd')", body)

    def test_state_flips_only_after_the_dom_is_found(self):
        """★ 状态不许在拿到节点之前就翻。

        原来的顺序是「先翻 TKFold，再去找 .grp」—— 找不到就抛，而状态已经
        变了。于是一次点击留下「状态说展开、画面还是收起」，下一次重渲染
        才对上。**找不到就什么都不做**，别留下这种一半。
        """
        page = _page()
        body = re.search(r"#tkBody \.tkfold'\)[\s\S]{0,1200}?\n  \}\);", page).group(0)
        guard = body.index("if (!grp) return;")
        flip = body.index("TKFold[k] = !TKFold[k];")
        self.assertLess(guard, flip, "状态翻在了取节点的守卫之前")


if __name__ == "__main__":
    unittest.main()
