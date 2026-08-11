# -*- coding: utf-8 -*-
"""V3.4 环节图自检。

这张表是手写的，写错了不会立刻炸 —— 会在跑到第 12 个环节、花掉几十次调用
之后，才发现某个依赖指向一个根本没有环节产出的产物。所以在这里整个走一遍。
"""
import unittest

from core import system_v34 as V


class GraphTests(unittest.TestCase):

    def test_graph_is_consistent(self):
        """★ 依赖指向真实产物、不倒挂、范围不越级、id 不重复。"""
        problems = V.check_graph()
        self.assertEqual(problems, [], "环节图有问题：\n  " + "\n  ".join(problems))

    def test_every_llm_stage_has_required_fields(self):
        for s in V.llm_stages():
            _, _, req = V.LLM_SPEC[s["id"]]
            self.assertTrue(req, f"{s['id']} 没有必需字段，模型答歪了没人拦")

    def test_produce_steps_declare_a_task_key(self):
        for sid in V.PRODUCE_ORDER:
            s = V.by_id()[sid]
            self.assertTrue(s.get("task_key"), f"{sid} 没写 task_key")

    def test_scopes_match_the_mapping_doc(self):
        """范围写错会让成本翻好几倍：逐集的东西写成逐段，一集多跑 5 倍。"""
        self.assertEqual(V.SERIES_STAGES, {"n1", "n2", "n3"})
        self.assertEqual(V.SEGMENT_STAGES, {"n11", "n12", "n13"})
        for sid in V.SERIES_STAGES:
            self.assertEqual(V.scope_of(sid), "series", sid)
        for sid in V.SEGMENT_STAGES:
            self.assertEqual(V.scope_of(sid), "segment", sid)

    def test_cvs_must_not_carry_camera_fields(self):
        """★ V3.4 第 9 章的硬边界：CVS 是物理真相，不含镜头。

        混进 camera 字段的话，切镜就会静默改变「人物实际站在哪」——
        这正是人物位置漂移的根源。
        """
        _, _, req = V.LLM_SPEC["n8"]
        banned = ("camera", "shot_size", "composition", "screen_direction", "angle")
        for f in req:
            if not f.startswith("cvs"):
                continue
            for b in banned:
                self.assertNotIn(b, f.lower(),
                                 f"CVS 的必需字段 {f} 里出现了镜头概念 {b}")

    def test_shot_stage_owns_screen_direction(self):
        """反过来：画面左右属于镜头，必须在 n9 里。"""
        _, _, req = V.LLM_SPEC["n9"]
        self.assertIn("shots[].screen_direction", req)
        self.assertIn("shots[].camera_position_xyz", req)

    def test_transitions_declare_mechanism_and_execution_mode(self):
        """★ 招牌功能：转场必须写清机制和「模型一次生成」的执行模式。

        只写「电影感转场」或把机制留给模型自选，就会退化成只会硬切。
        """
        _, _, req = V.LLM_SPEC["n9"]
        for f in ("transitions[].mechanism", "transitions[].cinematic_grammar",
                  "transitions[].execution_mode"):
            self.assertIn(f, req)

    def test_seg_stage_owns_transitions(self):
        """一次原生转场必须完整归属一个 SEG，不许拆到两次生成里。"""
        _, _, req = V.LLM_SPEC["n10"]
        self.assertIn("segs[].model_native_transition_ids", req)
        self.assertIn("segs[].boundary_rationale", req)

    def test_scstate_layer_exists_and_feeds_storyboard(self):
        """★ SCSTATE 是这套体系相对 V6.1 新增的一层，故事板必须依赖它。"""
        self.assertIn("n11", V.LLM_SPEC)
        _, deps, _ = V.LLM_SPEC["n12"]
        self.assertIn("n11_scstate", deps,
                      "故事板没依赖 SCSTATE，等于还是拿一堆原子资产直接拼")
        self.assertIn("p2", V.PRODUCE_ORDER)
        self.assertLess(V.PRODUCE_ORDER.index("p2"), V.PRODUCE_ORDER.index("p3"),
                        "SCSTATE 图要排在故事板前面，否则故事板没得参考")

    def test_asset_images_come_before_everything_else(self):
        """资产图是所有参考图的根，必须第一批出。"""
        self.assertEqual(V.PRODUCE_ORDER[0], "p1")

    def test_series_stage_may_not_depend_on_narrower_products(self):
        """全剧级只跑一次、排在最前面，依赖逐集产物时那份还不存在。"""
        for sid in V.SERIES_STAGES:
            _, deps, _ = V.LLM_SPEC[sid]
            outs = {s["out"]: s["id"] for s in V.STAGES if s["out"]}
            for d in deps:
                self.assertEqual(V.scope_of(outs[d]), "series",
                                 f"{sid} 依赖了 {d}（{V.scope_of(outs[d])}）")

    def test_episode_stage_may_aggregate_segment_products(self):
        """反向是允许的，别把范围规则写成对称的（第一版就写错了）。

        n14 审计是逐集的，但它要读本集全部段的故事板和视频计划才能审。
        只要排在那些环节之后就成立 —— 那由顺序检查负责，不归范围管。
        """
        _, deps, _ = V.LLM_SPEC["n14"]
        self.assertIn("n12_storyboard", deps)
        self.assertEqual(V.scope_of("n14"), "episode")
        self.assertEqual(V.scope_of("n12"), "segment")
        self.assertEqual(V.check_graph(), [])

    def test_stage_ids_do_not_collide_with_v61(self):
        """n/p/d 前缀和 main 上的 s1..s12 分开，两套产物能共存一个项目目录。"""
        for s in V.STAGES:
            self.assertRegex(s["id"], r"^[npd]\d+$", s["id"])


if __name__ == "__main__":
    unittest.main()
