# -*- coding: utf-8 -*-
"""契约只管数据形状，不管创作。

用户原话（2026-09-01）：「我们契约的本质只是告诉 codex 要给我们什么参数
才能让我们去出图和出视频，而不是去影响他的工作」。

上一版里混进了一整套分镜方法论 —— 一段该几张骨架、补图要证明什么、
额度先给谁。那些没有任何程序逻辑依赖，纯粹是在替 codex 做创作决定。
更糟的是它们看起来和真要求一样硬，codex 分不出哪条是「不这样程序读不了」、
哪条是「我们觉得这样好」。
"""
import unittest

from core import matspec


class SpecScopeTests(unittest.TestCase):
    def setUp(self):
        self.texts = {sid: matspec.render(None, "", sid) for sid in ("v34", "v61")}

    def test_no_shot_planning_directives(self):
        """★ 分镜方法论不许回来。

        这些句子的共同点：程序里没有任何一行代码依赖它们，
        而它们摆在必填字段的说明里，读起来和真要求一样硬。
        """
        banned = [
            "不许退化成一张起始图",
            "要证明有独有作用",
            "不许砍骨架",
            "额度先给骨架",
            "关键动作阶段与稳定结果",
            "镜头顺序与切换动机",
            "主角身份跨镜头易漂",
            "人物立绘竖",           # 画幅该横该竖是内容判断，不是参数规范
        ]
        for sid, text in self.texts.items():
            for s in banned:
                # 「已经撤掉」那段说明里会引用它们，只查正文别处
                body = text.replace(
                    text[text.index("## 这份契约管什么"):
                         text.index("## 交出来的就一个文件")], "")
                self.assertNotIn(s, body, f"{sid}: 「{s}」又回来了")

    def test_the_scope_is_stated_up_front(self):
        """开头就说清管什么不管什么 —— 不说的话下次还会滑回去。"""
        for sid, text in self.texts.items():
            self.assertIn("这份契约管什么、不管什么", text, sid)
            self.assertIn("不管你怎么创作", text, sid)
            self.assertIn("程序哪一行代码要读它", text, sid)

    def test_fields_the_program_ignores_are_marked(self):
        """★ 程序不读的字段要说明白。

        `who` / `controls` / `not_controls` / `scope` / `role` 一个都不进任务
        （`role` 甚至被写成空串丢掉）。留在必填表里等于要求 codex 按我们那套
        方法论做一遍记账，而产出的东西哪儿都不去。
        """
        self.assertEqual(matspec.REF_FIELDS_IGNORED,
                         ["who", "controls", "not_controls", "scope", "role"])
        for f, need, _why in matspec.REF_FIELDS:
            self.assertNotIn(f, matspec.REF_FIELDS_IGNORED)
        self.assertEqual([f for f, need, _ in matspec.REF_FIELDS if need],
                         ["image_n", "key"], "程序真读的只有这两个")
        for sid, text in self.texts.items():
            self.assertIn("别为它们花时间", text, sid)

    def test_those_fields_really_do_not_reach_the_task(self):
        """★ 上面那条断言的前提，用真数据验一次。

        光断言「文档说它不读」不够 —— 万一哪天有人接上了，文档就成了错的。
        """
        import json
        rows = [
            {"kind": "image", "key": "PRJ__SBSHEET_EP01_SEG01_A_R01",
             "family": "SBSHEET", "filename": "a.png", "size": "9:16",
             "reference_images": [], "prompt": "正文"},
            {"kind": "video", "key": "EP01-SEG01", "episode": "EP01", "seg": "SEG01",
             "filename": "v.mp4", "duration": 10, "ratio": "9:16", "prompt": "正文",
             "storyboard_refs": [{"image_n": 1,
                                  "key": "PRJ__SBSHEET_EP01_SEG01_A_R01",
                                  "role": "ENTRY", "who": "谁",
                                  "controls": "A", "not_controls": "B",
                                  "scope": "C"}],
             "reference_images": []},
        ]
        from core import matimport as M
        units = M.parse("\n".join(json.dumps(r, ensure_ascii=False) for r in rows))
        t = M.build(units, system="v34")["tasks"]["video_tasks"][0]
        blob = json.dumps(t, ensure_ascii=False)
        for v in ("谁", '"A"', '"B"', '"C"'):
            self.assertNotIn(v, blob, f"{v} 居然进任务了 —— 文档说它不读")
        # role 被写成空串丢掉，这就是「不读」的证据
        self.assertEqual(t["storyboard_refs"][0]["spine_role"], "")

    def test_the_hard_identity_rule_is_one_line(self):
        """身份映射只有一行是硬的，其余提醒级。

        `produce.check_identity_map` 的注释自己写过这一课：
        「≥2 张却没划分权威」原来是硬停，那是把偏好当规范去拦人，已经降级。
        契约不能还把六行模板摆得像全是必填。
        """
        for sid, text in self.texts.items():
            self.assertIn("硬要求只有这一行", text, sid)

    def test_the_sample_is_still_compliant(self):
        """★ 边界收紧了，示例仍然要 0 条问题导入。

        改文案时最容易顺手把示例改坏 —— 而示例是 codex 唯一的形状权威。
        """
        import json
        from core import matimport as M
        for sid, text in self.texts.items():
            lines = [ln.strip() for ln in text.split("\n")
                     if ln.strip().startswith('{"kind"')]
            self.assertGreaterEqual(len(lines), 4, sid)
            units = M.parse("\n".join(lines))
            bad = [i for i in M.audit(units) if i["level"] == "error"]
            self.assertEqual(bad, [], f"{sid}: 示例自己不合格 {bad}")


if __name__ == "__main__":
    unittest.main()
