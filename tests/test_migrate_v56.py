# -*- coding: utf-8 -*-
"""老 v34 项目迁到 V5.6 的全剧级布局。

叙事结构、资产表、资产提示词、空间主表、连续性总账从逐集变成全剧一份，
产物路径也就从 `01_剧本与分段/EP01/n4_assets.json` 挪到
`01_剧本与分段/n4_assets.json`。

不迁的后果很实在：程序去项目根找，找不到，判成「这个环节还没跑」，
然后把**已经花过钱的七个环节重跑一遍**。

这是整个仓库里唯一会动用户已有产物的代码，所以这里钉得比别处细：
合并对不对、集号有没有丢、老文件有没有留、迁两遍会不会出事。
"""
import os
import shutil
import unittest

from core import migrate_v56 as M
from test_v34_run import new_project


class MigrationTests(unittest.TestCase):

    def setUp(self):
        self.pj = new_project()
        import core.episodes as _eps
        self._ids = _eps.ids
        _eps.ids = lambda pj: ["EP01", "EP02"]
        # 造一个老项目：五份产物都在集目录下
        self.pj.save_stage("n4_assets", {"assets": [
            {"asset_id": "C001", "name": "主角"},
            {"asset_id": "ST10", "name": "EP01 的状态"}]}, "EP01")
        self.pj.save_stage("n4_assets", {"assets": [
            {"asset_id": "C001", "name": "主角（第二集又写了一遍）"},
            {"asset_id": "ST20", "name": "EP02 的状态"}]}, "EP02")
        self.pj.save_stage("n6_ledger", {"ledger": [
            {"event_id": "EV001", "affected_entity": "C001"}]}, "EP01")
        self.pj.save_stage("n6_ledger", {"ledger": [
            {"event_id": "EV009", "affected_entity": "C001"}]}, "EP02")

    def tearDown(self):
        import core.episodes as _eps
        _eps.ids = self._ids
        shutil.rmtree(self.pj.root, ignore_errors=True)

    def test_pending_finds_the_stale_layout(self):
        self.assertEqual(set(M.pending(self.pj)), {"n4_assets", "n6_ledger"})

    def test_a_fresh_project_needs_nothing(self):
        pj = new_project()
        try:
            self.assertEqual(M.pending(pj), [])
        finally:
            shutil.rmtree(pj.root, ignore_errors=True)

    def test_arrays_are_merged_and_deduped_first_wins(self):
        """★ 同一个 asset_id 在两集里都有，合并后只留一条。

        留两条的后果是同一个角色排两条出图任务 —— 两张不同的脸。
        先出现的赢，和旧的 build_tasks 规则一致。
        """
        M.run(self.pj, log=lambda *_: None)
        got = self.pj.stage_data("n4_assets", "")
        ids = [a["asset_id"] for a in got["assets"]]
        self.assertEqual(ids, ["C001", "ST10", "ST20"])
        self.assertEqual(got["assets"][0]["name"], "主角",
                         "没按「先出现的赢」，第二集的定义覆盖了第一集")

    def test_every_row_keeps_the_episode_it_came_from(self):
        """★ 不打集号的话跨集信息就丢了 —— 而那正是这次重定级要换来的东西。

        「这条永久状态是第几集留下的」是连续性总账的核心，
        合并时不记，合完就再也查不出来了。
        """
        M.run(self.pj, log=lambda *_: None)
        led = self.pj.stage_data("n6_ledger", "")["ledger"]
        self.assertEqual({r["event_id"]: r["episode"] for r in led},
                         {"EV001": "EP01", "EV009": "EP02"})

    def test_an_existing_episode_field_is_not_overwritten(self):
        """模型自己写了集号的，以它为准 —— 别拿目录位置去覆盖内容。"""
        self.pj.save_stage("n6_ledger", {"ledger": [
            {"event_id": "EV777", "episode": "EP05"}]}, "EP02")
        M.run(self.pj, log=lambda *_: None)
        led = self.pj.stage_data("n6_ledger", "")["ledger"]
        self.assertEqual(next(r for r in led if r["event_id"] == "EV777")["episode"],
                         "EP05")

    def test_the_old_files_are_kept_as_bak(self):
        """★ 迁错了要能拿回来。直接删是不可接受的 —— 那是花过钱的产物。"""
        M.run(self.pj, log=lambda *_: None)
        for ep in ("EP01", "EP02"):
            self.assertFalse(os.path.isfile(self.pj.stage_path("n4_assets", ep)))
            self.assertTrue(os.path.isfile(
                self.pj.stage_path("n4_assets", ep) + ".bak"))

    def test_running_twice_is_harmless(self):
        """★ 人会重复点。第二次必须什么都不做，不能把合好的又合一遍。"""
        M.run(self.pj, log=lambda *_: None)
        first = self.pj.stage_data("n4_assets", "")
        r = M.run(self.pj, log=lambda *_: None)
        self.assertEqual(r["moved"], [])
        self.assertEqual(self.pj.stage_data("n4_assets", ""), first)

    def test_the_merged_product_is_marked(self):
        M.run(self.pj, log=lambda *_: None)
        got = self.pj.stage_data("n4_assets", "")
        self.assertEqual(got["scope"], "full_series")
        self.assertEqual(got["migrated_from_episodes"], ["EP01", "EP02"])

    def test_after_migration_the_stages_read_as_done(self):
        """★ 迁移的全部意义：别让程序把花过钱的环节判成没跑过。"""
        from core import run_v34 as R
        M.run(self.pj, log=lambda *_: None)
        data = R.deps_data(self.pj, "n6", "")
        self.assertTrue(data["n4_assets"].get("assets"),
                        "迁完之后依赖还是取到空的")


class EndpointTests(unittest.TestCase):

    def setUp(self):
        self.pj = new_project()
        self.pj.save_meta(dict(self.pj.meta(), system="v34"))
        import core.episodes as _eps
        self._ids = _eps.ids
        _eps.ids = lambda pj: ["EP01"]
        self.pj.save_stage("n5_spatial", {"spatial_masters": [
            {"spatial_id": "SP001"}]}, "EP01")

    def tearDown(self):
        import core.episodes as _eps
        _eps.ids = self._ids
        shutil.rmtree(self.pj.root, ignore_errors=True)

    def test_opening_the_project_reports_what_needs_migrating(self):
        """★ 只发现、不自动迁 —— 迁移会动产物，得让人点一下。"""
        from server.app import api_get
        d = api_get("/api/project", {"root": [self.pj.root]})
        self.assertEqual(d["need_migrate"], ["n5_spatial"])
        # 光看一眼不许改动任何东西
        self.assertTrue(os.path.isfile(self.pj.stage_path("n5_spatial", "EP01")))

    def test_the_endpoint_actually_migrates(self):
        from server.app import api_get, api_post
        r = api_post("/api/project/migrate_v56", {"project_root": self.pj.root})
        self.assertEqual(r["moved"], ["n5_spatial"])
        self.assertEqual(
            api_get("/api/project", {"root": [self.pj.root]})["need_migrate"], [])

    def test_v61_projects_are_never_asked_to_migrate(self):
        """V6.1 那套的产物本来就是逐集的，动它是错的。"""
        from server.app import api_get
        self.pj.save_meta(dict(self.pj.meta(), system="v61"))
        self.assertEqual(
            api_get("/api/project", {"root": [self.pj.root]})["need_migrate"], [])


if __name__ == "__main__":
    unittest.main()
