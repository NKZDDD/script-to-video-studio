# -*- coding: utf-8 -*-
"""Canonical 注册表：完整 ID、不可变版本、文件指纹。

三件事一起管，因为它们是同一个问题的三面：
下游引用一张图时，得知道**引用的到底是哪一张**。

  完整 ID   区分「甲剧的 C001」和「乙剧的 C001」
  版本      区分「改之前那张脸」和「改之后那张脸」
  指纹      查出「文件被人原地换过」

少任何一样，都会出现「故事板看着正常，但用的其实是另一张图」。
"""
import os
import shutil
import unittest

from core import registry_v34 as REG, run_v34 as R
from test_v34_run import EP1, PARAMS, FakeLLM, new_project


CHAR = {"asset_id": "C001", "family": "CHAR", "name": "甲"}
STATE = {"asset_id": "ST007", "family": "CT", "name": "湿衣状态"}


class IdTests(unittest.TestCase):

    def setUp(self):
        self.pj = new_project()

    def tearDown(self):
        shutil.rmtree(self.pj.root, ignore_errors=True)

    def test_canonical_id_has_namespace_family_and_revision(self):
        cid = REG.canonical_id(self.pj, CHAR, 1)
        self.assertTrue(cid.startswith("PRJ_"), cid)
        self.assertIn("__CHAR_C001", cid)
        self.assertTrue(cid.endswith("_R01"), cid)

    def test_project_namespace_is_sanitised(self):
        self.pj.save_meta(dict(self.pj.meta(), project_code="小裴 剧/A-1"))
        self.assertNotIn(" ", REG.project_id(self.pj))
        self.assertNotIn("/", REG.project_id(self.pj))

    def test_family_falls_back_to_the_prefix(self):
        """family 字段缺了不该让整条链断掉。"""
        self.assertEqual(REG.family_of({"asset_id": "C002"}), "CHAR")
        self.assertEqual(REG.family_of({"asset_id": "ST009"}), "CT")
        self.assertEqual(REG.family_of({"asset_id": "S001"}), "LOC")
        self.assertEqual(REG.family_of({"asset_id": "怪ID"}), "ASSET")

    def test_declared_family_wins_over_the_prefix(self):
        self.assertEqual(REG.family_of({"asset_id": "C001", "family": "LOOK"}), "LOOK")


class RevisionTests(unittest.TestCase):

    def setUp(self):
        self.pj = new_project()
        REG.register(self.pj, CHAR)

    def tearDown(self):
        shutil.rmtree(self.pj.root, ignore_errors=True)

    def _make(self, rel):
        p = self.pj.p(*rel.split("/"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "wb").write(b"PNGDATA")
        return rel

    def test_starts_at_r01(self):
        self.assertEqual(REG.current_revision(self.pj, "C001"), 1)
        self.assertIn("_R01", R._asset_out(CHAR, 1))

    def test_bump_creates_a_new_revision_and_a_new_path(self):
        """★ 内容要改就建新版本，不原地覆盖 —— 已经引用过它的故事板
        还指着旧那张，覆盖了就查不出当时用的是哪一版。"""
        self._make(R._asset_out(CHAR, 1))
        REG.promote(self.pj, "C001", R._asset_out(CHAR, 1))
        REG.bump(self.pj, "C001", "脸偏了，重新设计")
        self.assertEqual(REG.current_revision(self.pj, "C001"), 2)
        self.assertIn("_R02", R.asset_out(self.pj, CHAR))
        # 旧文件还在
        self.assertTrue(os.path.isfile(self.pj.p(*R._asset_out(CHAR, 1).split("/"))))

    def test_bump_requires_a_reason(self):
        """几个月后看注册表，要知道 R02 和 R01 差在哪、为什么换。"""
        with self.assertRaises(ValueError):
            REG.bump(self.pj, "C001", "")
        REG.bump(self.pj, "C001", "换发型")
        self.assertEqual(REG.entry(self.pj, "C001")["bumps"][0]["why"], "换发型")

    def test_bumping_an_unknown_asset_is_refused(self):
        with self.assertRaises(ValueError):
            REG.bump(self.pj, "没这个", "x")

    def test_canonical_id_follows_the_revision(self):
        REG.bump(self.pj, "C001", "改了")
        self.assertTrue(REG.entry(self.pj, "C001")["canonical_id"].endswith("_R02"))


class ResolveTests(unittest.TestCase):
    """V3.4：任一 Reference 未解析时阻断，不得猜图继续。"""

    def setUp(self):
        self.pj = new_project()
        REG.register(self.pj, CHAR)
        self.rel = R._asset_out(CHAR, 1)
        p = self.pj.p(*self.rel.split("/"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "wb").write(b"PNGDATA")

    def tearDown(self):
        shutil.rmtree(self.pj.root, ignore_errors=True)

    def test_unregistered_asset_does_not_resolve(self):
        r = REG.resolve(self.pj, "根本没有")
        self.assertFalse(r["ok"])
        self.assertIn("注册表里没有", r["why"])

    def test_registered_but_not_produced_does_not_resolve(self):
        r = REG.resolve(self.pj, "C001")
        self.assertFalse(r["ok"])
        self.assertIn("还没出图", r["why"])

    def test_resolves_after_promote(self):
        REG.promote(self.pj, "C001", self.rel)
        r = REG.resolve(self.pj, "C001")
        self.assertTrue(r["ok"])
        self.assertEqual(r["file"], self.rel)
        self.assertEqual(len(r["sha256"]), 64)
        self.assertTrue(r["canonical_id"].endswith("_R01"))

    def test_file_deleted_after_promote_does_not_resolve(self):
        REG.promote(self.pj, "C001", self.rel)
        os.remove(self.pj.p(*self.rel.split("/")))
        r = REG.resolve(self.pj, "C001")
        self.assertFalse(r["ok"])
        self.assertIn("文件不在了", r["why"])

    def test_swapped_file_is_caught_by_the_fingerprint(self):
        """★ 原地换文件 —— 这正是版本不可变要防的事。

        换过之后下游还照着旧引用跑，出来的东西看着正常但用的是另一张图。
        """
        REG.promote(self.pj, "C001", self.rel)
        open(self.pj.p(*self.rel.split("/")), "wb").write("另一张图".encode())
        r = REG.verify(self.pj, "C001")
        self.assertFalse(r["ok"])
        self.assertIn("指纹对不上", r["why"])
        self.assertIn("别原地换文件", r["why"], "没告诉人正确做法是建新版本")

    def test_verify_passes_when_untouched(self):
        REG.promote(self.pj, "C001", self.rel)
        self.assertTrue(REG.verify(self.pj, "C001")["ok"])


class ManifestTests(unittest.TestCase):
    """一次调用的参考图清单。声明几张就必须解析出几张。"""

    def setUp(self):
        self.pj = new_project()
        for a in (CHAR, STATE):
            REG.register(self.pj, a)

    def tearDown(self):
        shutil.rmtree(self.pj.root, ignore_errors=True)

    def _produce(self, a):
        rel = R._asset_out(a, 1)
        p = self.pj.p(*rel.split("/"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "wb").write(b"PNG" + a["asset_id"].encode())
        REG.promote(self.pj, a["asset_id"], rel)

    def test_all_resolved_is_ok(self):
        self._produce(CHAR)
        self._produce(STATE)
        m = REG.manifest(self.pj, ["C001", "ST007"])
        self.assertTrue(m["ok"])
        self.assertEqual([i["image_n"] for i in m["images"]], [1, 2])
        self.assertTrue(all(i["canonical_id"] for i in m["images"]))

    def test_one_unresolved_blocks_the_whole_manifest(self):
        """★ 少一张就不许凑合出图 —— 少一张的后果是脸和场景跑掉。"""
        self._produce(CHAR)
        m = REG.manifest(self.pj, ["C001", "ST007"])
        self.assertFalse(m["ok"])
        self.assertEqual(len(m["blocked"]), 1)
        self.assertEqual(m["blocked"][0]["asset_id"], "ST007")
        self.assertEqual(m["blocked"][0]["availability"], "BLOCKED")

    def test_image_numbers_start_from_one_each_call(self):
        self._produce(STATE)
        m = REG.manifest(self.pj, ["ST007"])
        self.assertEqual(m["images"][0]["image_n"], 1)


class BuildTasksIntegrationTests(unittest.TestCase):

    def setUp(self):
        self.pj = new_project()
        self.llm = FakeLLM()
        q = lambda *a, **k: None
        for sid in ("n1", "n2"):
            R.run_stage(self.pj, sid, llm=self.llm, params=PARAMS, log=q)
        for sid in ("n3", "n4", "n4b"):
            R.run_stage(self.pj, sid, llm=self.llm, params=PARAMS,
                        episode=EP1, log=q)

    def tearDown(self):
        shutil.rmtree(self.pj.root, ignore_errors=True)

    def test_assets_are_registered_during_assembly(self):
        R.build_tasks(self.pj, PARAMS)
        self.assertIn("C001", REG.load(self.pj))

    def test_task_output_carries_the_revision(self):
        t = R.build_tasks(self.pj, PARAMS)
        out = t["asset_tasks"][0]["output"]
        self.assertIn("_R01.png", out, out)

    def test_after_a_bump_the_task_points_at_the_new_revision(self):
        """★ 建了新版本之后，下一次出图要出到新文件，不覆盖旧的。"""
        R.build_tasks(self.pj, PARAMS)
        REG.bump(self.pj, "C001", "脸要改")
        t = R.build_tasks(self.pj, PARAMS)
        out = next(x for x in t["asset_tasks"] if x["key"] == "C001")["output"]
        self.assertIn("_R02.png", out, out)


if __name__ == "__main__":
    unittest.main()
