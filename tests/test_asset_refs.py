# -*- coding: utf-8 -*-
import tempfile
import unittest

from core import prompts, stages
from core.store import Project, read_text


class AssetReferenceTests(unittest.TestCase):
    def test_state_keeps_parent_first_and_all_dependencies(self):
        out = {"assets": [{
            "asset_id": "ST001", "category": "state", "state_type": "single",
            "parent_asset_id": "C002",
            "reference_assets": ["S001", "C002", "P001", "S001"],
            "output_spec": "closeup",
        }]}
        stages.normalize_s4_asset_refs(out)
        asset = out["assets"][0]
        self.assertEqual(asset["parent_asset_id"], "C002")
        self.assertNotIn("state_type", asset)
        self.assertEqual(asset["reference_assets"], ["C002", "S001", "P001"])
        self.assertEqual(asset["output_spec"], "state_asset")

    def test_missing_parent_is_inferred_from_first_dependency(self):
        out = {"assets": [{
            "asset_id": "ST002", "category": "state",
            "reference_assets": ["C002", "P001"],
        }]}
        stages.normalize_s4_asset_refs(out)
        asset = out["assets"][0]
        self.assertEqual(asset["parent_asset_id"], "C002")
        self.assertEqual(asset["reference_assets"], ["C002", "P001"])

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

    def test_explicit_s5_rerun_does_not_skip_existing_prompt_files(self):
        with tempfile.TemporaryDirectory() as root:
            pj = self._make_project(root)
            prompt_dir = pj.p("03_提示词", "资产生产提示词")
            import os
            os.makedirs(prompt_dir, exist_ok=True)
            for aid in ("S001", "C002", "C005", "ST001"):
                with open(os.path.join(prompt_dir, f"{aid}_PROMPT.txt"),
                          "w", encoding="utf-8") as f:
                    f.write("old")
            data = {"s4_assets": pj.stage_data("s4_assets", "EP01")}
            normal, _, skipped = stages._s5_filter(pj, data, claim=False)
            forced, todo, forced_skipped = stages._s5_filter(
                pj, data, claim=False, force=True)
            self.assertEqual(normal["assets"], [])
            self.assertEqual(len(skipped), 4)
            self.assertEqual(todo, ["S001", "C002", "C005", "ST001"])
            self.assertEqual(len(forced["assets"]), 4)
            self.assertEqual(forced_skipped, [])

    def _make_project(self, root):
        pj = Project(root)
        pj.init_dirs()
        pj.save_stage("episodes", {"episodes": [{"episode": "EP01"}]})
        assets = [
            {"asset_id": "S001", "category": "environment", "name": "病房",
             "parent_asset_id": "", "reference_assets": [], "decision": "must",
             "used_by_segs": ["EP01-SEG01"]},
            {"asset_id": "C002", "category": "identity", "name": "Rizky",
             "parent_asset_id": "", "reference_assets": [], "decision": "must",
             "used_by_segs": ["EP01-SEG01"]},
            {"asset_id": "C005", "category": "identity", "name": "Dewi",
             "parent_asset_id": "", "reference_assets": [], "decision": "must",
             "used_by_segs": ["EP01-SEG01"]},
            {"asset_id": "ST001", "category": "state", "name": "病房关系状态",
             "parent_asset_id": "C002",
             "reference_assets": ["C002", "S001", "C005"],
             "output_spec": "state_asset", "decision": "must",
             "used_by_segs": ["EP01-SEG01"]},
        ]
        pj.save_stage("s4_assets", {"assets": assets}, "EP01")
        return pj

    def test_task_builder_keeps_parent_and_all_state_dependencies(self):
        with tempfile.TemporaryDirectory() as root:
            pj = self._make_project(root)
            pj.save_stage("s5_asset_prompts", {"asset_prompts": [
                {"asset_id": "S001", "prompt": "room", "reference_assets": []},
                {"asset_id": "C002", "prompt": "rizky", "reference_assets": []},
                {"asset_id": "C005", "prompt": "dewi", "reference_assets": []},
                {"asset_id": "ST001", "prompt": "state",
                 "parent_asset_id": "C002", "reference_assets": ["C002"]},
            ]}, "EP01")
            tasks = stages._build_tasks(
                pj, {"project_code": "T", "image_size": "1x1"})
            state = next(t for t in tasks["asset_tasks"] if t["key"] == "ST001")
            self.assertEqual([r["asset_id"] for r in state["reference_images"]],
                             ["C002", "S001", "C005"])
            used = stages.assets_used_by(pj, ["EP01"])
            self.assertTrue({"ST001", "C002", "S001", "C005"}.issubset(used))

    def test_offscreen_names_do_not_create_false_dependency_warning(self):
        with tempfile.TemporaryDirectory() as root:
            pj = self._make_project(root)
            out = {"asset_prompts": [{
                "asset_id": "ST001", "parent_asset_id": "C002",
                "reference_assets": ["C002", "S001", "C005"],
                "prompt": "Rizky听到Dewi提及Aisyah；依赖仍由结构字段决定。",
            }]}
            self.assertEqual(stages.check_prompt_refs(pj, out, "EP01"), [])

    def test_txt_is_copied_verbatim_and_adapter_is_separate(self):
        txt = read_text(stages.prompt_files("s4_assets")[0])
        skill_ref = read_text(stages.HERE +
                              "/../skills/script-to-video-prompts-v2/references/02-assets.md")
        self.assertEqual(txt, skill_ref)
        self.assertNotIn("{{GLOBAL}}", txt)
        effective = stages.stage_prompt("s4", "s4_assets")
        self.assertTrue(effective.startswith(txt))
        self.assertIn("# 程序传输适配（不改变上文业务规则）", effective)
        self.assertIn("{{GLOBAL}}", effective)
        self.assertEqual(prompts.check("s4_assets", txt)["errors"], [])

    def test_s5_uses_parent_state_model(self):
        text = read_text(stages.prompt_files("s5_asset_prompts")[0])
        self.assertEqual(prompts.check("s5_asset_prompts", text)["errors"], [])
        self.assertIn("parent_asset_id", text)
        self.assertIn("state_asset", text)
        self.assertNotIn("state_anchor", text)

    def test_space_masters_reuse_latest_version(self):
        with tempfile.TemporaryDirectory() as root:
            pj = Project(root)
            pj.init_dirs()
            pj.save_stage("episodes", {"episodes": [
                {"episode": "EP01"}, {"episode": "EP02"}
            ]})
            pj.save_stage("s4_assets", {"assets": [], "space_masters": [{
                "space_id": "SP001", "regions": [{"region_id": "A"}]
            }]}, "EP01")
            pj.save_stage("s4_assets", {"assets": [], "space_masters": [{
                "space_id": "SP001", "regions": [
                    {"region_id": "A"}, {"region_id": "B"}
                ]
            }]}, "EP02")
            spaces = stages.known_space_masters(pj, "EP02")
            self.assertEqual([r["region_id"] for r in spaces[0]["regions"]],
                             ["A", "B"])


if __name__ == "__main__":
    unittest.main()
