# -*- coding: utf-8 -*-
import tempfile
import unittest

from core import prompts, stages
from core.store import Project, read_text


class AssetReferenceTests(unittest.TestCase):
    def test_single_state_has_one_parent_first(self):
        out = {"assets": [{
            "asset_id": "ST001", "category": "state", "state_type": "single",
            "parent_asset_id": "S001",
            "reference_assets": ["S001", "S001"],
        }]}
        stages.normalize_s4_asset_refs(out)
        self.assertEqual(out["assets"][0]["parent_asset_id"], "S001")
        self.assertEqual(out["assets"][0]["reference_assets"], ["S001"])

    def test_composite_has_no_parent_and_keeps_multiple_sources(self):
        out = {"assets": [{
            "asset_id": "CA001", "category": "state", "state_type": "composite",
            "parent_asset_id": "S001",
            "reference_assets": ["S001", "C002", "C005", "C002"],
            "output_spec": "scene_wide",
        }]}
        stages.normalize_s4_asset_refs(out)
        asset = out["assets"][0]
        self.assertEqual(asset["parent_asset_id"], "")
        self.assertEqual(asset["reference_assets"], ["S001", "C002", "C005"])
        self.assertEqual(asset["output_spec"], "state_composite")

    def test_incremental_s5_save_merges_instead_of_overwriting(self):
        previous = {"asset_prompts": [
            {"asset_id": "C001", "prompt": "old-1"},
            {"asset_id": "C002", "prompt": "old-2"},
        ]}
        fresh = {"asset_prompts": [
            {"asset_id": "C002", "prompt": "new-2"},
            {"asset_id": "ST003", "prompt": "new-3"},
        ]}
        merged = stages.merge_s5_outputs(previous, fresh)
        self.assertEqual([x["asset_id"] for x in merged["asset_prompts"]],
                         ["C001", "C002", "ST003"])
        self.assertEqual(merged["asset_prompts"][1]["prompt"], "new-2")

    def test_task_builder_keeps_all_composite_sources(self):
        with tempfile.TemporaryDirectory() as root:
            pj = Project(root)
            pj.init_dirs()
            pj.save_stage("episodes", {"episodes": [{"episode": "EP01"}]})
            assets = [
                {"asset_id": "S001", "category": "environment", "name": "病房",
                 "decision": "must", "used_by_segs": ["EP01-SEG01"]},
                {"asset_id": "C002", "category": "identity", "name": "Rizky",
                 "decision": "must", "used_by_segs": ["EP01-SEG01"]},
                {"asset_id": "C005", "category": "identity", "name": "Dewi",
                 "decision": "must", "used_by_segs": ["EP01-SEG01"]},
                {"asset_id": "CA001", "category": "state", "state_type": "composite",
                 "name": "病房人物关系锚点", "parent_asset_id": "",
                 "reference_assets": ["S001", "C002", "C005"],
                 "output_spec": "state_composite", "decision": "must",
                 "used_by_segs": ["EP01-SEG01"]},
            ]
            pj.save_stage("s4_assets", {"assets": assets}, "EP01")
            pj.save_stage("s5_asset_prompts", {"asset_prompts": [
                {"asset_id": "S001", "prompt": "room", "reference_assets": []},
                {"asset_id": "C002", "prompt": "rizky", "reference_assets": []},
                {"asset_id": "C005", "prompt": "dewi", "reference_assets": []},
                # 模拟环节5退化成只写一个来源；装配层仍必须恢复环节4的完整依赖。
                {"asset_id": "CA001", "prompt": "state", "state_type": "composite",
                 "reference_assets": ["S001"]},
            ]}, "EP01")

            tasks = stages._build_tasks(pj, {"project_code": "T", "image_size": "1x1"})
            anchor = next(t for t in tasks["asset_tasks"] if t["key"] == "CA001")
            self.assertEqual([r["asset_id"] for r in anchor["reference_images"]],
                             ["S001", "C002", "C005"])
            self.assertEqual(len(anchor["reference_images"]), 3)

            used = stages.assets_used_by(pj, ["EP01"])
            self.assertTrue({"CA001", "S001", "C002", "C005"}.issubset(used))

    def test_s5_builtin_requires_full_asset_catalog(self):
        text = read_text(stages.prompt_files("s5_asset_prompts")[0])
        result = prompts.check("s5_asset_prompts", text)
        self.assertEqual(result["errors"], [])
        self.assertIn("{{ASSET_CATALOG}}", text)


if __name__ == "__main__":
    unittest.main()
