"""Writer guidance stays compatible with the unchanged production reader."""
from pathlib import Path
import zipfile

import pytest

from core import agent_handoff as H, agent_projects as A, matspec, produce
from core.reference_contract import MARKER, RULES
from core.independent_c import RULES as HANDOFF_RULES
from core.single_handoff import RULES as SINGLE_HANDOFF_RULES
from core.store import Project
from test_agent_workspace import pj, plan_for, publish  # noqa: F401


def test_contract_examples_pass_existing_reference_reader():
    prompt = RULES.split("```text\n", 1)[1].split("```", 1)[0]
    refs = [{"image_n": n, "asset_id": aid}
            for n, aid in enumerate(("CHAR_A", "LOC_A", "PROP_A"), 1)]
    assert produce.check_image_map(prompt, refs)[0] == ""
    assert produce.check_identity_map(prompt, refs)[0] == ""


@pytest.mark.parametrize("separator", ["；", ";", "。", "\n"])
def test_identity_after_boundary_remains_blocked(separator):
    prompt = "Image 1 = CHAR_A" + separator + "傅凛成，29岁成年男性"
    assert "REFERENCE_MAPPING_BLOCKED" in produce.check_identity_map(
        prompt, [{"image_n": 1, "asset_id": "CHAR_A"}])[0]


def test_new_project_and_export_include_rules(pj):
    contract = Path(pj.p("00_项目说明", "production-contract.md")).read_text(encoding="utf-8")
    assert RULES in contract
    assert pj.root in contract and pj.meta()["project_code"] in contract
    with zipfile.ZipFile(H.export_package(pj)["file"]) as archive:
        for name in ("00_项目说明/production-contract.md", "生产材料契约.md"):
            assert RULES in archive.read(name).decode("utf-8")


def test_old_contract_append_preserves_project_and_published_materials(pj):
    publish(pj, plan_for(pj))
    A.scan(pj)
    contract = Path(pj.p("00_项目说明", "production-contract.md"))
    original = ("\ufeff# 原契约\r\n项目和路径保持：" + pj.root + "\r\n").encode("utf-8")
    contract.write_bytes(original)
    protected = [p for directory in ("00_项目说明", "03_提示词")
                 for p in Path(pj.p(directory)).rglob("*") if p.is_file() and p != contract]
    before = {p: p.read_bytes() for p in protected}
    A.view(pj)
    A.view(pj)
    request = A.revision_request(pj, ["asset0"], "调整映射行标点")
    assert contract.read_bytes() == original + ("\n\n" + RULES + "\n\n" + HANDOFF_RULES + "\n\n" + SINGLE_HANDOFF_RULES).encode("utf-8")
    assert {p: p.read_bytes() for p in protected} == before
    assert RULES in request["text"] and pj.root in request["text"]
    assert "asset0" in request["text"]


def test_legacy_revision_gets_rules_without_migrating_project(tmp_path):
    project = Project(str(tmp_path))
    project.init_dirs()
    project.save_tasks({"asset_tasks": [{"key": "CHAR_A", "output": "02_固定资产/a.png",
                                       "prompt_ref": "03_提示词/a.txt", "reference_images": []}]})
    before = project.tasks()
    result = A.revision_request(project, ["CHAR_A"], "调整映射行标点")
    assert RULES in result["text"]
    assert project.tasks() == before
    assert not Path(project.p("00_项目说明", "production-contract.md")).exists()
    assert not Path(project.p("00_项目说明", "project-lock.json")).exists()


@pytest.mark.parametrize("system", ["v34", "v61"])
def test_material_contract_has_same_guidance(system):
    assert matspec.render(system=system).count(MARKER) == 1
    assert RULES in matspec.render(system=system)
