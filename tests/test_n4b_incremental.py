# -*- coding: utf-8 -*-
"""n4b 增量编译 —— 不改环节顺序，只让每一轮少写一点。

为什么必须增量：n4b 是**全剧级**的，一次要写全剧所有资产的完整提示词。
拿 V6.1 的真实产物量过：逐集是 17 条 / 14,144 字符 ≈ 8,320 token（装得下），
而全剧级 4 集就 33,280 token —— 超过本机实测的输出天花板 19,612。

不增量的话，截断之后重跑写的是**同一批东西**，永远走不完，钱一直花。
增量之后每一轮补没写的，截断也在推进。

**它不动流程图**：n4b 还是一个环节、还在原位、产物文件名不变、下游不受影响。
"""
import shutil
import unittest

from core import run_v34 as R
from test_v34_run import new_project


def _assets(*rows):
    return {"assets": [dict(asset_id=a, family=f, name=n, decision=d)
                       for a, f, n, d in rows]}


def _prompts(*ids):
    return {"asset_prompts": [{"asset_id": i, "prompt": f"{i} 的提示词正文"}
                              for i in ids]}


class SplitTests(unittest.TestCase):

    def setUp(self):
        self.pj = new_project()
        self.pj.save_stage("n4_assets", _assets(
            ("C001", "CHAR", "女主", "must"),
            ("PH001", "PH", "女主素体", "must"),
            ("L001", "LOOK", "女主造型", "conditional"),
            ("X001", "PROP_SPEC", "路人手里的伞", "skip"),
        ), "")

    def tearDown(self):
        shutil.rmtree(self.pj.root, ignore_errors=True)

    def test_the_first_run_writes_everything_except_skips(self):
        done, todo, dropped = R.n4b_split(self.pj)
        self.assertEqual(done, [])
        self.assertEqual(todo, ["C001", "PH001", "L001"])
        self.assertEqual(dropped, 1)

    def test_skips_are_never_written(self):
        """★ decision=skip 的出图那层本来就会丢掉，写了是白写。

        而这一步恰恰是最容易被截断的 —— 把 token 花在注定要扔的东西上，
        等于自己把自己顶到天花板。
        """
        _, todo, _ = R.n4b_split(self.pj)
        self.assertNotIn("X001", todo)

    def test_the_second_run_only_writes_what_is_missing(self):
        """★ 这就是止血点：上一轮截断在第二条，这一轮从第三条接着写。"""
        self.pj.save_stage("n4b_asset_prompts", _prompts("C001", "PH001"), "")
        done, todo, _ = R.n4b_split(self.pj)
        self.assertEqual(done, ["C001", "PH001"])
        self.assertEqual(todo, ["L001"])

    def test_an_empty_prompt_does_not_count_as_written(self):
        """★ 有这一条但正文是空的 —— 那是没写成，不能算写过了。"""
        self.pj.save_stage("n4b_asset_prompts",
                           {"asset_prompts": [{"asset_id": "C001", "prompt": "  "}]}, "")
        _, todo, _ = R.n4b_split(self.pj)
        self.assertIn("C001", todo)


class MergeTests(unittest.TestCase):
    """增量的另一半：存盘必须合并。"""

    def test_a_partial_batch_does_not_wipe_the_earlier_ones(self):
        """★ 漏了这一步，资产提示词会越跑越少，而且不报错。"""
        merged = R.merge_asset_prompts(_prompts("C001", "PH001"), _prompts("L001"))
        self.assertEqual([r["asset_id"] for r in merged["asset_prompts"]],
                         ["C001", "PH001", "L001"])

    def test_rewriting_one_replaces_it_in_place(self):
        merged = R.merge_asset_prompts(
            _prompts("C001", "PH001"),
            {"asset_prompts": [{"asset_id": "C001", "prompt": "新版"}]})
        rows = merged["asset_prompts"]
        self.assertEqual([r["asset_id"] for r in rows], ["C001", "PH001"])
        self.assertEqual(rows[0]["prompt"], "新版", "重写应该覆盖，不是追加")

    def test_merging_into_nothing_works(self):
        self.assertEqual(
            [r["asset_id"] for r in
             R.merge_asset_prompts({}, _prompts("C001"))["asset_prompts"]],
            ["C001"])


class WorklistTests(unittest.TestCase):
    """发过去的内容：要写的留全量，其余压成一行目录。"""

    def setUp(self):
        self.pj = new_project()
        self.pj.save_stage("n4_assets", _assets(
            ("C001", "CHAR", "女主", "must"),
            ("PH001", "PH", "女主素体", "must"),
        ), "")
        self.pj.save_stage("n4b_asset_prompts", _prompts("C001"), "")

    def tearDown(self):
        shutil.rmtree(self.pj.root, ignore_errors=True)

    def _wl(self):
        return R._n4b_worklist(self.pj, self.pj.stage_data("n4_assets", ""))

    def test_only_the_pending_ones_are_sent_in_full(self):
        wl = self._wl()
        self.assertEqual([a["asset_id"] for a in wl["assets"]], ["PH001"])

    def test_the_written_ones_stay_visible_as_ids(self):
        """★ 不能直接删掉：写 LOOK 要引 PH 的 ID，看不到就会重新发明一个，

        出图那层再报「查不到这个资产」—— 那时候已经隔了好几个环节。
        """
        wl = self._wl()
        cat = wl["assets_already_done"]
        self.assertEqual([a["asset_id"] for a in cat], ["C001"])
        self.assertIn("family", cat[0])

    def test_the_catalog_rows_are_actually_small(self):
        """压缩没起作用就白改了 —— 目录条目不该带提示词正文那些重字段。"""
        cat = self._wl()["assets_already_done"]
        self.assertEqual(set(cat[0]), {"asset_id", "family", "name"})


class TemplateTests(unittest.TestCase):
    """程序分好了两块，模板不说清楚，模型会把目录那块也重写一遍。"""

    def test_the_template_explains_both_lists(self):
        import io
        import os
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        t = io.open(os.path.join(root, "prompts", "n4b_asset_prompts.md"),
                    encoding="utf-8").read()
        self.assertIn("assets_already_done", t)
        self.assertIn("不要重写", t)
        # ★ 光说别重写不够，还得说清可以引用 —— 否则模型会重新发明 ID
        self.assertIn("重新发明一个 ID", t)


if __name__ == "__main__":
    unittest.main()
