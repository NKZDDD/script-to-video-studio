"""v7 材料从导入到出片及明细，不能退回 v6 故事板硬依赖。"""
import json
from pathlib import Path

import pytest

from core import diagnose, explorer, matimport, produce
from core.apiutil import ApiError
from core.providers.base import Provider
from core.relay import Relay
from core.store import Project, write_text
from test_no_storyboard_no_video import PNG


class RecordingProvider(Provider):
    ref_mode = "bytes"

    def __init__(self):
        self.sent = []

    def generate_video(self, task, dest, **kwargs):
        self.sent.append(task)
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(b"\x00\x00\x00\x18ftypisom" + bytes(4096))
        return {"task_id": "mock-1"}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    pj = Project(str(tmp_path))
    pj.init_dirs()
    prov = RecordingProvider()
    monkeypatch.setattr(produce, "build_provider", lambda *a, **k: prov)
    monkeypatch.setattr(produce, "_ratio_warn", lambda *a, **k: None)
    return pj, prov


def put(pj, rel, data=PNG):
    path = Path(pj.p(*rel.split("/")))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def material(pj, system="v61", with_board=False):
    rows = [
        {"kind": "image", "key": "P__CHAR_001", "filename": "P__CHAR_001.png", "prompt": "人物"},
        {"kind": "image", "key": "P__SCSTATE_FOREST", "filename": "P__SCSTATE_FOREST.png", "prompt": "场景"},
    ]
    video = {"kind": "video", "key": "EP01-SEG03", "filename": "EP01-SEG03.mp4",
             "duration": 30, "ratio": "9:16", "handoff_refs": [],
             "reference_images": [{"image_n": 1, "key": "P__SCSTATE_FOREST"},
                                  {"image_n": 2, "key": "P__CHAR_001"}],
             "prompt": "Image 1 = P__SCSTATE_FOREST\nImage 2 = P__CHAR_001\n继续行走"}
    if with_board:
        rows += [{"kind": "image", "key": "P__ABC_EP01_SEG01_TO_SEG02_R01",
                  "filename": "board.png", "prompt": "整板", "boundary": "EP01_SEG01_TO_SEG02"}]
        for region in ("A", "B", "C"):
            rows.append({"kind": "image", "key": f"P__ABC_{region}",
                         "filename": f"region_{region}.png", "prompt": "区域图",
                         "region": region, "board": rows[2]["key"],
                         "reference_images": [{"image_n": 1, "key": rows[2]["key"]}]})
    rows.append(video)
    built = matimport.build(matimport.parse("\n".join(json.dumps(r) for r in rows)), system=system)
    pj.save_meta({"system": system})
    pj.save_tasks(built["tasks"])
    for rel, prompt in built["prompts"].items():
        write_text(pj.p(*rel.split("/")), prompt)
    for tasks in built["tasks"].values():
        if not isinstance(tasks, list):
            continue
        for t in tasks:
            if t.get("output", "").endswith(".png"):
                put(pj, t["output"])
    return built["tasks"]


def run(pj, task):
    logs = []
    worker = produce.make_video_worker(pj, {"provider": "mock", "api_key": "x", "model": "m"})
    result = worker(task, logs.append, lambda: False)
    return result, logs


@pytest.mark.parametrize("system", ["v61", "v34"])
def test_imported_segment_without_handoff_reaches_provider_and_records_success(setup, system):
    pj, prov = setup
    t = material(pj, system)["video_tasks"][0]
    assert t["handoff_refs"] == []
    result, logs = run(pj, t)
    assert result["output"] == t["output"]
    assert len(prov.sent) == 1
    assert [Path(f) for f in prov.sent[0].refs] == [Path(pj.p(*r["file_ref"].split("/")))
                                                   for r in t["reference_images"]]
    assert all("故事板" not in line and "环节9" not in line for line in logs)
    record = pj.registry("video")[0]
    assert record["handoff_refs"] == []
    assert record["status"] == "generated"
    assert "storyboard_spine" not in record


def test_empty_handoff_overrides_stale_legacy_fields_in_execution_display_and_relay(setup):
    pj, prov = setup
    tasks = material(pj)
    t = tasks["video_tasks"][0]
    t["storyboard_ref"] = "04_故事板/old-missing.png"
    t["storyboard_refs"] = [{"order": 1, "sheet_id": "OLD", "file_ref": t["storyboard_ref"]}]
    pj.save_tasks(tasks)
    assert Relay(pj)._missing(t) == []
    row = next(g for g in explorer.tasks(pj)["groups"] if g["kind"] == "video")["rows"][0]
    assert [r["asset_id"] for r in row["refs"]] == ["P__SCSTATE_FOREST", "P__CHAR_001"]
    run(pj, t)
    assert len(prov.sent[0].refs) == 2


def test_handoff_only_task_uses_order_roles_and_dedupes_in_panel_and_request(setup):
    pj, prov = setup
    tasks = material(pj)
    t = tasks["video_tasks"][0]
    # 无旧故事板兼容字段，文件名与 ID 不相同，也必须按 sheet_id 认映射。
    t.pop("storyboard_refs")
    t.pop("storyboard_ref")
    t["handoff_refs"] = [
        {"order": 2, "sheet_id": "P__ABC_C", "role": "IN_C", "file_ref": "04_交接板/区域图/space.png"},
        {"order": 1, "sheet_id": "P__ABC_B", "role": "IN_B", "file_ref": "04_交接板/区域图/open.png"},
    ]
    for r in t["handoff_refs"]:
        put(pj, r["file_ref"])
    t["reference_images"] = [
        {"image_n": 3, "asset_id": "P__ABC_B", "file_ref": t["handoff_refs"][1]["file_ref"]},
        {**t["reference_images"][1], "image_n": 4},
    ]
    write_text(pj.p(*t["prompt_ref"].split("/")),
               "Image 1 = P__ABC_B\nImage 2 = P__ABC_C\nImage 3 = P__CHAR_001\n继续走")
    pj.save_tasks(tasks)
    row = next(g for g in explorer.tasks(pj)["groups"] if g["kind"] == "video")["rows"][0]
    assert [r["asset_id"] for r in row["refs"]] == ["P__ABC_B（IN_B）", "P__ABC_C（IN_C）", "P__CHAR_001"]
    assert [r["image_n"] for r in row["refs"]] == [1, 2, 3]
    assert Relay(pj)._missing(t) == []
    _, logs = run(pj, t)
    assert [Path(f) for f in prov.sent[0].refs] == [Path(pj.p(*r["file"]["rel"].split("/")))
                                                   for r in row["refs"]]
    assert any("已去重" in line for line in logs)
    assert all("故事板" not in line for line in logs)


@pytest.mark.parametrize("missing_kind", ["absent", "empty", "no_path"])
def test_declared_missing_handoff_is_still_blocked_with_current_instructions(setup, missing_kind):
    pj, prov = setup
    t = material(pj)["video_tasks"][0]
    rel = "04_交接板/区域图/missing.png" if missing_kind != "no_path" else ""
    if missing_kind == "empty":
        put(pj, rel, b"")
    t["handoff_refs"] = [{"order": 1, "sheet_id": "P__ABC_B", "role": "IN_B", "file_ref": rel}]
    with pytest.raises(ApiError) as caught:
        run(pj, t)
    msg = str(caught.value)
    assert "交接板区域" in msg and "P__ABC_B" in msg
    assert "故事板" not in msg and "环节9" not in msg
    assert diagnose.build(caught.value)["code"] == "REF_MISSING"
    assert prov.sent == []


@pytest.mark.parametrize("missing_kind", ["absent", "empty", "no_path"])
def test_declared_missing_asset_cannot_slip_through_without_handoff(setup, missing_kind):
    pj, prov = setup
    t = material(pj)["video_tasks"][0]
    ref = t["reference_images"][0]
    if missing_kind == "no_path":
        ref["file_ref"] = ""
    else:
        Path(pj.p(*ref["file_ref"].split("/"))).unlink()
        if missing_kind == "empty":
            put(pj, ref["file_ref"], b"")
    with pytest.raises(ApiError):
        run(pj, t)
    assert prov.sent == []


def test_relay_waits_for_declared_handoff_without_legacy_aliases(setup):
    pj, _ = setup
    t = material(pj)["video_tasks"][0]
    rel = "04_交接板/区域图/pending.png"
    t["handoff_refs"] = [{"order": 1, "file_ref": rel, "role": "IN_B"}]
    relay = Relay(pj)
    relay.declare("p4", [{"output": rel}])
    assert relay.ready_of("p5")(t)[0] is False
    put(pj, rel)
    assert relay.ready_of("p5")(t)[0] is True


@pytest.mark.parametrize("system", ["v61", "v34"])
def test_task_detail_five_categories_follow_real_tasks_even_with_old_system_label(setup, system):
    pj, _ = setup
    material(pj, system, with_board=True)
    result = explorer.tasks(pj)
    assert [g["label"] for g in result["groups"]] == [
        "资产图", "场景状态图", "交接板整板", "交接板区域图", "分段视频"]
    assert [g["total"] for g in result["groups"]] == [1, 1, 1, 3, 1]
    assert all("环节9" not in g["stage"] for g in result["groups"])
    assert result["has_tasks"]


def test_real_legacy_storyboard_tasks_remain_visible(setup):
    pj, _ = setup
    tasks = material(pj, with_board=True)
    tasks["storyboard_tasks"] = [{"key": "LEGACY", "episode": "EP01", "prompt_ref": "",
                                   "output": "04_故事板/legacy.png", "reference_images": []}]
    pj.save_tasks(tasks)
    group = next(g for g in explorer.tasks(pj)["groups"] if g["key"] == "storyboard_tasks")
    assert group["total"] == 1
    assert group["label"] == "故事板（旧版）"


def test_missing_reference_instructions_do_not_require_storyboards():
    d = diagnose.build(ApiError("missing", err_code="reference_missing"))
    assert d["code"] == "REF_MISSING"
    assert "故事板" not in json.dumps({k: d[k] for k in ("why", "fix", "where")}, ensure_ascii=False)


def test_current_prompt_mapping_failure_mentions_actual_reference_types(setup):
    pj, prov = setup
    t = material(pj)["video_tasks"][0]
    write_text(pj.p(*t["prompt_ref"].split("/")), "Image 1 = GHOST_ASSET\n继续走")
    with pytest.raises(ApiError) as caught:
        run(pj, t)
    assert "GHOST_ASSET" in str(caught.value)
    assert "故事板" not in str(caught.value)
    assert prov.sent == []


def test_explicitly_no_references_does_not_create_a_phantom_storyboard(setup):
    pj, prov = setup
    t = material(pj)["video_tasks"][0]
    t["reference_images"] = []
    write_text(pj.p(*t["prompt_ref"].split("/")), "天空的云缓缓移动")
    run(pj, t)
    assert prov.sent[0].refs == []
    assert pj.registry("video")[0]["handoff_refs"] == []


def test_legacy_ordered_storyboards_still_reach_provider_in_order(setup):
    pj, prov = setup
    t = material(pj)["video_tasks"][0]
    t.pop("handoff_refs")
    t["storyboard_refs"] = [
        {"order": 2, "sheet_id": "OLD_SHEET_B", "file_ref": "04_故事板/b.png"},
        {"order": 1, "sheet_id": "OLD_SHEET_A", "file_ref": "04_故事板/a.png"}]
    for r in t["storyboard_refs"]:
        put(pj, r["file_ref"])
    t["reference_images"] = []
    write_text(pj.p(*t["prompt_ref"].split("/")), "Image 1 = OLD_SHEET_A\nImage 2 = OLD_SHEET_B\n走路")
    run(pj, t)
    assert [Path(f).name for f in prov.sent[0].refs] == ["a.png", "b.png"]
    assert pj.registry("video")[0]["storyboard_spine"] == ["04_故事板/a.png", "04_故事板/b.png"]
