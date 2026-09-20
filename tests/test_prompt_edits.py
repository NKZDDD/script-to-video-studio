"""Manual edits must reach production without changing existing media or running jobs."""
import copy
from pathlib import Path
import threading

import pytest

from core import agent_projects as A, agent_runtime as R, prompt_edits as P
from core.executor import JobManager
from core.store import Project, read_json, write_json, write_text
from server import app, agent_api
from test_agent_workspace import CAPS, pj, plan_for, board_plan, publish, image, resolve, wait_job


def save(pj, key="asset0", text="手动修改后的提示词"):
    return P.save(pj, key, text, P.read(pj, key)["version"])


def test_edit_preserves_old_media_and_unrelated_prompt_bytes(pj):
    plan = plan_for(pj, count=3)
    plan["asset_tasks"][2]["reference_images"] = []
    folder, _ = publish(pj, plan, "\ufeff原始提示词\r\n第二行\r\n")
    A.scan(pj)
    original = copy.deepcopy(pj.tasks())
    for row in original["asset_tasks"]:
        image(str(A.inside(pj.root, row["output"])))
    A.select(pj, {"asset1": False})
    before = A.file_manifest(folder)
    result = save(pj)
    current = pj.tasks()
    assert result["revision"] == "r0002" and result["changed"]
    assert [t["key"] for t in result["affected"]] == ["asset0", "asset1"]
    assert A.file_manifest(folder) == before
    assert P.read(pj, "asset0")["text"] == "手动修改后的提示词"
    assert current["asset_tasks"][0]["output"] != original["asset_tasks"][0]["output"]
    assert current["asset_tasks"][1]["reference_images"][0]["file_ref"] == current["asset_tasks"][0]["output"]
    assert current["asset_tasks"][2]["output"] == original["asset_tasks"][2]["output"]
    assert A.inside(pj.root, current["asset_tasks"][2]["prompt_ref"]).read_bytes() == A.inside(pj.root, original["asset_tasks"][2]["prompt_ref"]).read_bytes()
    assert all(R.valid_output(str(A.inside(pj.root, row["output"]))) for row in original["asset_tasks"])
    assert A.selections(pj, A.task_rows(current))["asset1"] is False
    A.select(pj, {"asset1": True})
    preview = R.preview(pj, {}, CAPS, resolve)
    assert not preview["blocked"] and preview["todo"] == 2 and preview["reuse"] == 1


def test_transitive_board_regions_and_video_follow_revised_output(pj):
    publish(pj, board_plan(pj)); A.scan(pj)
    result = save(pj)
    tasks = {t["key"]: t for t in A.task_rows(pj.tasks())}
    assert len(result["affected"]) == 5
    for task in tasks.values():
        for ref in A.references(task):
            assert ref["file_ref"] == tasks[ref["asset_id"]]["output"]
    assert tasks["regionB"]["board"] == "board"
    assert tasks["video"]["handoff_refs"][0]["role"] == "IN_B"
    assert not A.scan(pj)["errors"]


def test_editing_video_keeps_its_upstream_outputs(pj):
    publish(pj, board_plan(pj)); A.scan(pj)
    before = {t["key"]: t["output"] for t in A.task_rows(pj.tasks())}
    assert [t["key"] for t in save(pj, "video")["affected"]] == ["video"]
    after = {t["key"]: t["output"] for t in A.task_rows(pj.tasks())}
    assert {k: v for k, v in after.items() if k != "video"} == {k: v for k, v in before.items() if k != "video"}


def test_unchanged_text_does_not_invalidate_completed_outputs(pj):
    publish(pj, plan_for(pj), "\ufeff原文\r\n第二行\r\n"); A.scan(pj)
    data = P.read(pj, "asset0")
    assert not P.save(pj, "asset0", data["text"], data["version"])["changed"]
    assert A.scan(pj)["revision"] == "r0001"
    assert not Path(pj.p("03_提示词", "releases", "r0002")).exists()


@pytest.mark.parametrize("text", ["", "  \n\t", None])
def test_blank_edits_are_rejected_without_mutation(pj, text):
    publish(pj, plan_for(pj)); A.scan(pj)
    with pytest.raises(ValueError, match="不能为空"):
        P.save(pj, "asset0", text, P.read(pj, "asset0")["version"])
    assert A.scan(pj)["revision"] == "r0001"


def test_stale_editor_cannot_overwrite_another_saved_edit(pj):
    publish(pj, plan_for(pj)); A.scan(pj)
    stale = P.read(pj, "asset0")
    save(pj, text="先保存的内容")
    with pytest.raises(ValueError, match="本次未保存"):
        P.save(pj, "asset0", "过期内容", stale["version"])
    assert P.read(pj, "asset0")["text"] == "先保存的内容"


def test_new_agent_publication_invalidates_open_editor(pj):
    publish(pj, plan_for(pj)); A.scan(pj)
    stale = P.read(pj, "asset0")
    publish(pj, plan_for(pj, "r0002"))
    with pytest.raises(ValueError, match="已更新"):
        P.save(pj, "asset0", "来自旧页面", stale["version"])
    assert A.scan(pj)["revision"] == "r0002"


def test_incomplete_agent_directory_is_not_overwritten(pj):
    publish(pj, plan_for(pj)); A.scan(pj)
    reserved = Path(pj.p("03_提示词", "releases", "r0005"))
    reserved.mkdir(); (reserved / "draft.txt").write_text("未完成", encoding="utf-8")
    assert save(pj)["revision"] == "r0006"
    assert (reserved / "draft.txt").read_text(encoding="utf-8") == "未完成"


def test_failed_publication_preserves_active_plan(pj, monkeypatch):
    publish(pj, plan_for(pj)); A.scan(pj)
    original = pj.tasks()
    def reject(*args):
        raise ValueError("模拟校验失败")
    monkeypatch.setattr(A, "validate_release", reject)
    with pytest.raises(ValueError, match="模拟校验失败"):
        save(pj)
    assert pj.tasks() == original and A.scan(pj)["revision"] == "r0001"
    assert not Path(pj.p("03_提示词", "releases", "r0002", "READY.json")).exists()


def test_running_batch_keeps_old_prompts_outputs_and_references(pj, monkeypatch):
    publish(pj, plan_for(pj)); A.scan(pj)
    original = copy.deepcopy(pj.tasks())
    entered, release = threading.Event(), threading.Event()
    calls = []
    def maker(project, *args):
        def worker(task, log, cancel):
            if task["key"] == "asset0":
                entered.set(); assert release.wait(10)
            calls.append((copy.deepcopy(task), A.inside(pj.root, task["prompt_ref"]).read_text(encoding="utf-8")))
            image(str(A.inside(pj.root, task["output"])))
            return {"output": task["output"]}
        return worker
    monkeypatch.setattr(R.produce, "make_image_worker", maker)
    jobs = JobManager()
    result = R.start(pj, {}, CAPS, resolve, jobs)
    try:
        assert entered.wait(4)
        save(pj)
    finally:
        release.set()
    wait_job(jobs.get(result["job_id"]))
    assert [text for _, text in calls] == ["生成测试图片"] * 2
    assert [t["output"] for t, _ in calls] == [t["output"] for t in original["asset_tasks"]]
    assert calls[1][0]["reference_images"] == original["asset_tasks"][1]["reference_images"]
    next_job = R.start(pj, {}, CAPS, resolve, jobs, expected_revision="r0002")
    wait_job(jobs.get(next_job["job_id"]))
    assert calls[2][1] == "手动修改后的提示词"
    assert calls[2][0]["output"] != calls[0][0]["output"]
    assert calls[3][0]["reference_images"][0]["file_ref"] == calls[2][0]["output"]


@pytest.mark.parametrize("system", ["v34", "v61"])
def test_legacy_prompts_and_reference_fields_are_versioned(tmp_path, system):
    project = Project(str(tmp_path)); project.init_dirs(); project.save_meta({"title": "旧项目", "system": system})
    tasks = {"asset_tasks": [{"key": "asset", "output": "02_固定资产/a.png", "prompt_ref": "03_提示词/asset.txt", "params": {"size": "16:9"}, "reference_images": []}],
             "storyboard_tasks": [{"key": "board", "output": "04_故事板/b.png", "prompt_ref": "03_提示词/board.txt", "params": {"size": "16:9"}, "reference_images": [{"asset_id": "asset", "file_ref": "02_固定资产/a.png", "image_n": 1}]}],
             "video_tasks": [{"key": "video", "output": "05_分段视频/v.mp4", "prompt_ref": "03_提示词/video.txt", "params": {"ratio": "16:9", "duration": 10}, "storyboard_ref": "04_故事板/b.png", "aux_reference": "02_固定资产/a.png"}]}
    for task in A.task_rows(tasks, legacy=True):
        write_text(str(A.inside(project.root, task["prompt_ref"])), "旧提示词")
    project.save_tasks(tasks)
    result = save(project, "asset")
    updated = project.tasks()
    assert A.scan(project)["revision"] == result["revision"] != "legacy"
    assert updated["video_tasks"][0]["storyboard_ref"] == updated["storyboard_tasks"][0]["output"]
    assert updated["video_tasks"][0]["aux_reference"] == updated["asset_tasks"][0]["output"]
    assert Path(project.p("03_提示词", "asset.txt")).read_text(encoding="utf-8") == "旧提示词"
    assert P.read(project, "asset")["text"] == "手动修改后的提示词"
    with pytest.raises(ValueError, match="计划已更新"):
        R.start(project, {}, CAPS, resolve, JobManager(), expected_revision="legacy")


def test_edit_api_only_accepts_registered_task_and_uses_conflict_token(pj, monkeypatch):
    publish(pj, plan_for(pj)); A.scan(pj)
    cfg = {"projects_dir": str(Path(pj.root).parent), "providers": {}, "limits": {"global": 8, "per_provider": {}}}
    monkeypatch.setattr(app, "load_config", lambda: cfg)
    monkeypatch.setattr(app, "caps_of", lambda cfg: CAPS)
    data = agent_api.get(app, "/api/agent/prompt-edit", {"root": [pj.root], "key": ["asset0"]})
    result = agent_api.post(app, "/api/agent/prompt-edit", {"root": pj.root, "key": "asset0", "version": data["version"], "text": "接口修改"})
    assert result["changed"] and P.read(pj, "asset0")["text"] == "接口修改"
    with pytest.raises(ValueError, match="任务不存在"):
        agent_api.get(app, "/api/agent/prompt-edit", {"root": [pj.root], "key": ["../任意文件"]})
    meta = pj.meta(); meta["archived"] = True; pj.save_meta(meta)
    with pytest.raises(ValueError, match="恢复归档"):
        save(pj)
