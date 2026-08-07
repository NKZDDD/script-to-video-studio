# -*- coding: utf-8 -*-
import tempfile
import unittest

from core import prompts, stages
from core.store import Project, read_text


class AssetReferenceTests(unittest.TestCase):
    def test_legacy_parent_migrates_to_anchor_source(self):
        out = {"assets": [{
            "asset_id": "ST001", "category": "state", "state_type": "single",
            "parent_asset_id": "S001",
            "reference_assets": ["C002", "S001", "C002"],
            "output_spec": "closeup",
        }]}
        stages.normalize_s4_asset_refs(out)
        asset = out["assets"][0]
        self.assertNotIn("parent_asset_id", asset)
        self.assertNotIn("state_type", asset)
        self.assertEqual(asset["reference_assets"], ["S001", "C002"])
        self.assertEqual(asset["output_spec"], "state_anchor")

    def test_anchor_accepts_one_or_many_sources(self):
        out = {"assets": [
            {"asset_id": "SA001", "category": "state",
             "reference_assets": ["C002"]},
            {"asset_id": "SA002", "category": "state",
             "reference_assets": ["S001", "C002", "P001", "C002"]},
        ]}
        stages.normalize_s4_asset_refs(out)
        self.assertEqual(out["assets"][0]["reference_assets"], ["C002"])
        self.assertEqual(out["assets"][1]["reference_assets"],
                         ["S001", "C002", "P001"])
        self.assertTrue(all(a["output_spec"] == "state_anchor"
                            for a in out["assets"]))

    def test_incremental_s5_save_merges_instead_of_overwriting(self):
        previous = {"asset_prompts": [
            {"asset_id": "C001", "prompt": "old-1"},
            {"asset_id": "C002", "prompt": "old-2"},
        ]}
        fresh = {"asset_prompts": [
            {"asset_id": "C002", "prompt": "new-2"},
            {"asset_id": "SA003", "prompt": "new-3"},
        ]}
        merged = stages.merge_s5_outputs(previous, fresh)
        self.assertEqual([x["asset_id"] for x in merged["asset_prompts"]],
                         ["C001", "C002", "SA003"])
        self.assertEqual(merged["asset_prompts"][1]["prompt"], "new-2")

    def _make_project(self, root):
        pj = Project(root)
        pj.init_dirs()
        pj.save_stage("episodes", {"episodes": [{"episode": "EP01"}]})
        assets = [
            {"asset_id": "S001", "category": "environment", "name": "病房",
             "reference_assets": [], "decision": "must",
             "used_by_segs": ["EP01-SEG01"]},
            {"asset_id": "C002", "category": "identity", "name": "Rizky Adhitama",
             "reference_assets": [], "decision": "must",
             "used_by_segs": ["EP01-SEG01"]},
            {"asset_id": "C005", "category": "identity", "name": "Nyonya Dewi",
             "reference_assets": [], "decision": "must",
             "used_by_segs": ["EP01-SEG01"]},
            {"asset_id": "C006", "category": "identity", "name": "Aisyah",
             "reference_assets": [], "decision": "must",
             "used_by_segs": ["EP01-SEG01"]},
            {"asset_id": "SA001", "category": "state", "name": "病房关系锚点",
             "reference_assets": ["S001", "C002", "C005"],
             "output_spec": "state_anchor", "decision": "must",
             "used_by_segs": ["EP01-SEG01"]},
        ]
        pj.save_stage("s4_assets", {"assets": assets}, "EP01")
        return pj

    def test_task_builder_keeps_all_anchor_sources(self):
        with tempfile.TemporaryDirectory() as root:
            pj = self._make_project(root)
            pj.save_stage("s5_asset_prompts", {"asset_prompts": [
                {"asset_id": "S001", "prompt": "room", "reference_assets": []},
                {"asset_id": "C002", "prompt": "rizky", "reference_assets": []},
                {"asset_id": "C005", "prompt": "dewi", "reference_assets": []},
                {"asset_id": "C006", "prompt": "aisyah", "reference_assets": []},
                # 模拟环节5退化成只写一个来源；装配层仍恢复环节4的完整依赖。
                {"asset_id": "SA001", "prompt": "anchor",
                 "reference_assets": ["S001"]},
            ]}, "EP01")

            tasks = stages._build_tasks(
                pj, {"project_code": "T", "image_size": "1x1"})
            anchor = next(t for t in tasks["asset_tasks"] if t["key"] == "SA001")
            self.assertEqual([r["asset_id"] for r in anchor["reference_images"]],
                             ["S001", "C002", "C005"])

            used = stages.assets_used_by(pj, ["EP01"])
            self.assertTrue({"SA001", "S001", "C002", "C005"}.issubset(used))

    def test_offscreen_names_do_not_trigger_false_missing_reference(self):
        with tempfile.TemporaryDirectory() as root:
            pj = Project(root)
            pj.init_dirs()
            pj.save_stage("episodes", {"episodes": [{"episode": "EP01"}]})
            pj.save_stage("s4_assets", {"assets": [
                {"asset_id": "C002", "category": "identity", "name": "Rizky Adhitama",
                 "reference_assets": []},
                {"asset_id": "C005", "category": "identity", "name": "Nyonya Dewi",
                 "reference_assets": []},
                {"asset_id": "C006", "category": "identity", "name": "Aisyah",
                 "reference_assets": []},
                {"asset_id": "ST003", "category": "state", "name": "Rizky苏醒锚点",
                 "reference_assets": ["C002"], "output_spec": "state_anchor"},
            ]}, "EP01")
            out = {"asset_prompts": [{
                "asset_id": "ST003",
                "reference_assets": ["C002"],
                "prompt": "Rizky听到Dewi的谎言后急切寻找Aisyah，但画面只固定Rizky。",
            }]}
            self.assertEqual(stages.check_prompt_refs(pj, out, "EP01"), [])

    def test_s4_and_s5_builtins_match_new_schema(self):
        for stage_id in ("s4_assets", "s5_asset_prompts"):
            text = read_text(stages.prompt_files(stage_id)[0])
            result = prompts.check(stage_id, text)
            self.assertEqual(result["errors"], [])
            self.assertNotIn("state_type", text)
            self.assertNotIn("state_composite", text)
        s5 = read_text(stages.prompt_files("s5_asset_prompts")[0])
        self.assertIn("{{ASSET_CATALOG}}", s5)


if __name__ == "__main__":
    unittest.main()
