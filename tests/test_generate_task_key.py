# -*- coding: utf-8 -*-
"""「跑哪一批活」和「用哪个 worker」是两件事。

以前是同一个字段（kind），于是场景状态图没有任何入口能跑：它和故事板的
worker 都是 image、kind 都是 "storyboard"，而 key_map 里 "storyboard" 只指向
storyboard_tasks。明细页把它列出来、有多少条都写着，但页面上没有一个按钮
能跑它。直接给它一个按钮而不分开这两个概念的话，点下去跑的是故事板 ——
出图、花钱、标成功，而你要的那一批一条没动。
"""
import ast
import io
import os
import unittest

APP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "server", "app.py")


def _generate_handler() -> str:
    """把 /api/generate 那一段源码抠出来 —— 跑不动它（要真服务商），但形状能验。"""
    src = io.open(APP, encoding="utf-8").read()
    i = src.index('if path == "/api/generate":')
    j = src.index('if path == "/api/failures/clear":', i)
    return src[i:j]


class GenerateTaskKeyTests(unittest.TestCase):
    def test_the_batch_table_covers_all_four_task_lists(self):
        """四类活都要能单独跑到，尤其是场景状态图。"""
        body = _generate_handler()
        tree = ast.parse("if True:\n" + "\n".join(
            "    " + ln for ln in body.split("\n")))
        table = None
        for node in ast.walk(tree):
            if (isinstance(node, ast.Assign) and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Name)
                    and node.targets[0].id == "BATCH"):
                table = ast.literal_eval(node.value)
        self.assertIsNotNone(table, "找不到 BATCH 那张表")
        self.assertEqual(table, {
            "asset_tasks": ("asset", "p1"),
            "scstate_tasks": ("storyboard", "p2"),
            "storyboard_tasks": ("storyboard", "p3"),
            "video_tasks": ("video", "p4"),
        })
        # p2 和 p3 的 worker 一样、批次必须不一样 —— 共用批次会让一步的完成
        # 信号提前解除另一步下游的等待（2026-08-20 实跑踩过：故事板还在跑，
        # 视频已经被判成「没人会做它了」而派出去撞空）。
        self.assertNotEqual(table["scstate_tasks"][1], table["storyboard_tasks"][1])

    def test_kind_is_derived_from_task_key_not_taken_from_the_request(self):
        """kind 必须由 task_key 推出来，且要在**任何人用它之前**定死。

        原来 pcfg = resolve_provider_cfg(..., kind) 在重新赋值之前就跑了 ——
        请求里的 kind 和 task_key 对不上时，服务商按一个算、活按另一个跑。
        """
        body = _generate_handler()
        derive = body.index("kind, _mine = BATCH[task_key]")
        for user in ("resolve_provider_cfg(cfg, body.get", "_media_capability(",
                     "resolve_chain(cfg, kind"):
            at = body.find(user)
            if at >= 0:
                self.assertGreater(at, derive,
                                   f"{user} 用 kind 用在了它被定死之前")

    def test_the_relay_batch_is_not_recomputed_from_kind(self):
        """_mine 只能有一处来源。

        第二处（按 kind 反查）会把场景状态图算成故事板那一批，于是它自己
        那一批 p2 被标成「收摊」，等它产物的下游立刻开跑撞空 —— 而这条
        路径是合法的，所以不报错。
        """
        body = _generate_handler()
        self.assertEqual(body.count("_mine ="), 1,
                         "_mine 被算了不止一次，多出来的那处迟早和这处不一致")
        self.assertNotIn('{"asset": "p1", "storyboard": "p3", "video": "p4"}', body)


if __name__ == "__main__":
    unittest.main()
