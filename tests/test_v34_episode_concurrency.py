# -*- coding: utf-8 -*-
"""多集并发时，那些「跨集共享」的东西会不会打架。

这套体系里 GLOBAL 和 EP 不是干净地分成前后两段：
资产库是**全剧共享**的（同一个角色只出一张图，跨集人脸才一致），
但写资产提示词的环节 n4b 是**逐集**的，而逐集环节默认 4 集并发。

于是有一类竞争：n4b 靠「读前面几集已经写过哪些资产」来决定这一集写什么。
并发时前面几集可能还没写完 —— 读到的是一个还在变的中间状态。
"""
import shutil
import threading
import unittest

from core import run_v34 as R, system_v34 as V
from test_v34_run import new_project


class ScopeLayoutTests(unittest.TestCase):
    """先钉住结构本身：全剧级环节必须全部排在逐集之前。"""

    def test_only_the_first_two_stages_are_series_scope(self):
        """★ 全剧级环节一旦出现在逐集之后，现在的执行结构就不成立了。

        pipeline 是「head 串行跑完 → 逐集并行」。head 只收 episode="" 的步骤，
        所以一个排在逐集之后的全剧级环节会被提到最前面跑 ——
        那时候它依赖的逐集产物还不存在，拿到空字典，模型照着空输入编。
        """
        series = [s["id"] for s in V.STAGES
                  if s["kind"] == "llm" and s["scope"] == "series"]
        self.assertEqual(series, ["n1", "n2"])

    def test_no_series_stage_depends_on_a_narrower_one(self):
        """全剧级不许依赖逐集/逐段产物 —— 时序上不可能满足。

        规则只对 series 成立，**不是对称的**：逐集环节依赖逐段产物是
        合法的聚合（n14 审计就要读本集全部段的故事板），
        写成对称规则会把正常的聚合判成错的。
        """
        by_out = {s["out"]: s for s in V.STAGES if s.get("out")}
        for sid, (_tpl, deps, _req) in V.LLM_SPEC.items():
            if V.scope_of(sid) != "series":
                continue
            for d in deps:
                src = by_out.get(d)
                if src:
                    self.assertEqual(
                        src["scope"], "series",
                        f"全剧级的 {sid} 依赖了 {src['scope']} 级的 {d} —— "
                        f"它在逐集环节之前就跑了，那时候这份产物还不存在")


class SharedAssetRaceTests(unittest.TestCase):
    """★ 资产提示词跨集共享，但写它的环节是逐集并发的。"""

    def setUp(self):
        self.pj = new_project()
        # 造三集，每集的资产表都含同一个角色 C001（跨集复用的主角）
        self.pj.save_stage("n1_truth", {"episode_ranges": [
            {"episode": f"EP{i:02d}"} for i in (1, 2, 3)]}, "")
        import core.episodes as _eps
        self._ids = _eps.ids
        _eps.ids = lambda pj: ["EP01", "EP02", "EP03"]
        for ep in ("EP01", "EP02", "EP03"):
            self.pj.save_stage("n4_assets", {"assets": [
                {"asset_id": "C001", "family": "CHAR", "name": "主角"},
                {"asset_id": f"ST{ep[-1]}0", "family": "CT", "name": "本集状态"},
            ]}, ep)

    def tearDown(self):
        import core.episodes as _eps
        _eps.ids = self._ids
        shutil.rmtree(self.pj.root, ignore_errors=True)

    def _write(self, ep, ids):
        self.pj.save_stage("n4b_asset_prompts", {"asset_prompts": [
            {"asset_id": a, "filename": f"{a}_PROMPT.txt",
             "prompt": f"{ep} 写的 {a}"} for a in ids]}, ep)

    def test_serial_runs_write_each_shared_asset_once(self):
        """串行时是对的：EP01 写了 C001，后面两集就跳过。"""
        todo, _ = R.assets_to_write(self.pj, "EP01")
        self.assertIn("C001", [a["asset_id"] for a in todo])
        self._write("EP01", ["C001", "ST10"])

        todo, skipped = R.assets_to_write(self.pj, "EP02")
        self.assertNotIn("C001", [a["asset_id"] for a in todo])
        self.assertIn("C001", [a["asset_id"] for a in skipped])

    def test_concurrent_episodes_all_claim_the_same_shared_asset(self):
        """★ 并发时三集同时算清单，都认为 C001 还没人写。

        `assets_to_write` 读的是磁盘上前面几集的产物，而并发下那几集
        可能一个都还没写完 —— 读到的是一个还在变的中间状态。
        后果不是出错图（下游按 asset_id 去重了），是**同一份提示词
        被三次 LLM 调用各写一遍，钱花三份**，而且三份内容还不一样。
        """
        claims = {}
        barrier = threading.Barrier(3)

        def one(ep):
            barrier.wait()          # 逼出真实的并发时序：三集同时算清单
            todo, _ = R.assets_to_write(self.pj, ep, claim=True)
            claims[ep] = [a["asset_id"] for a in todo]
            self._write(ep, claims[ep])

        ts = [threading.Thread(target=one, args=(e,))
              for e in ("EP01", "EP02", "EP03")]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        got = [ep for ep, ids in claims.items() if "C001" in ids]
        self.assertEqual(
            len(got), 1,
            f"C001 被 {len(got)} 集同时认领了（{sorted(got)}）—— "
            f"每多一集就是白花一次 LLM 调用，写出来的还是三份不同的提示词")

    def test_the_prompt_file_on_disk_matches_the_task_that_points_at_it(self):
        """★ 更糟的一层：提示词文件和任务对不上。

        C001_PROMPT.txt 是全剧一个文件名。两集都写过 C001 的话，
        文件内容是**后写的那一集**的，而 build_tasks 按「先出现的赢」
        挑了前一集那份的参考图顺序 —— 于是提示词里的 Image 编号
        和实际上传顺序对不上，出图那一层会硬停。

        认领之后这个状态根本造不出来：只有一集拿得到 C001。
        """
        owners = []
        for ep in ("EP01", "EP02", "EP03"):
            todo, _ = R.assets_to_write(self.pj, ep, claim=True)
            ids = [a["asset_id"] for a in todo]
            if "C001" in ids:
                owners.append(ep)
            self._write(ep, ids)
            R.write_prompt_files(self.pj, ep)
        self.assertEqual(len(owners), 1, f"C001 被 {owners} 都写了")
        on_disk = open(self.pj.p("03_提示词", "资产生产提示词",
                                 "C001_PROMPT.txt"), encoding="utf-8").read()
        self.assertIn(owners[0], on_disk,
                      "盘上这份不是认领它那一集写的")

    def test_the_worklist_is_actually_wired_into_the_prompt(self):
        """★ 过滤函数写了但没接上，等于没写。

        原来 mapping() 对 n4b 没有任何特判，{{ASSETS}} 是本集**全部**资产 ——
        模板里那句「只剩还没写过提示词的」是假的，主角会在 40 集里被重写 40 遍。
        """
        self._write("EP01", ["C001", "ST10"])
        data = R.deps_data(self.pj, "n4b", "EP02")
        m = R.mapping(self.pj, "n4b", {"project_code": "P"}, data, "EP02")
        # 查结构，不查子串：jd 是带缩进的，字符串匹配一碰就碎
        import json
        got = json.loads(m["ASSETS"])
        self.assertEqual([a["asset_id"] for a in got["assets"]], ["ST20"],
                         "C001 前面写过了，还留在待写清单里")
        self.assertEqual(got["already_written_do_not_rewrite"], ["C001"])

    def test_preview_does_not_claim(self):
        """★ 预览必须无副作用 —— 看一眼不该改变下一次真跑写什么。"""
        R.preview_prompt(self.pj, "n4b", {"project_code": "P"}, "EP01")
        todo, _ = R.assets_to_write(self.pj, "EP02", claim=True)
        self.assertIn("C001", [a["asset_id"] for a in todo],
                      "预览把资产认领走了，真跑时这一集反而不写了")


if __name__ == "__main__":
    unittest.main()
