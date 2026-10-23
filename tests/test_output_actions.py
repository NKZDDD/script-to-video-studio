"""Image rerolls and exact multi-output recycling, without provider calls."""
import copy
from pathlib import Path

import pytest
from PIL import Image

from core import agent_projects as A, assemble, recycle
from core.store import Project
from server import agent_api, app


@pytest.fixture
def project(tmp_path, monkeypatch):
    pj = Project(str(tmp_path / "project"))
    pj.init_dirs()
    pj.save_meta({"system": "v61"})
    tasks = {}
    for kind in A.LEGACY_KINDS:
        if kind == "video":
            continue
        rel = f"02_固定资产/{kind}.png"
        tasks[kind + "_tasks"] = [{"key": kind, "output": rel, "prompt_ref": "03_提示词/p.txt"}]
        Image.effect_noise((128, 128), 100).convert("RGB").save(pj.p(*rel.split("/")))
    pj.save_tasks(tasks)
    cfg = {"projects_dir": str(tmp_path), "providers": {}, "limits": {"global": 8, "per_provider": {}}}
    monkeypatch.setattr(app, "load_config", lambda: copy.deepcopy(cfg))
    monkeypatch.setattr(app, "caps_of", lambda cfg=None: [])
    monkeypatch.setattr(agent_api.R, "busy", lambda *a: {})
    return pj


@pytest.mark.parametrize("kind", [k for k in A.LEGACY_KINDS if k != "video"])
def test_each_image_kind_rerolls_only_selected_output_with_backup(project, monkeypatch, kind):
    pj = project
    before = pj.tasks()
    current = Path(pj.p("02_固定资产", kind + ".png"))
    original = current.read_bytes()
    calls = []
    monkeypatch.setattr(agent_api.R, "start", lambda *a, **kw: calls.append(a[5]) or {"ok": True, "job_id": "test"})
    result = agent_api.post(app, "/api/agent/reroll", {"root": pj.root, "key": kind})
    assert calls == [[kind]] and result["job_id"] == "test"
    assert not current.exists()
    assert A.inside(pj.root, result["backup"]).read_bytes() == original
    assert "_抽卡备份/" in result["backup"]
    assert pj.tasks() == before
    for row in A.task_rows(before, legacy=True):
        if row["key"] != kind:
            assert A.inside(pj.root, row["output"]).is_file()


@pytest.mark.parametrize("failure", ["blocked", "exception", "no_job"])
def test_image_is_restored_when_reroll_cannot_start(project, monkeypatch, failure):
    current = Path(project.p("02_固定资产", "asset.png"))
    original = current.read_bytes()
    def start(*a, **kw):
        if failure == "exception":
            raise RuntimeError("test failure")
        return {"ok": failure == "no_job", "blocked": [{"reason": "test blocked"}]}
    monkeypatch.setattr(agent_api.R, "start", start)
    with pytest.raises((ValueError, RuntimeError)):
        agent_api.post(app, "/api/agent/reroll", {"root": project.root, "key": "asset"})
    assert current.read_bytes() == original
    assert not list(current.parent.glob("_抽卡备份/*.png"))


def test_running_image_cannot_be_rerolled_or_deleted(project, monkeypatch):
    monkeypatch.setattr(agent_api.R, "busy", lambda *a: {"asset": object()})
    for path, payload in [("reroll", {"key": "asset"}), ("delete", {"keys": ["asset", "board"]})]:
        with pytest.raises(ValueError, match="正在生成"):
            agent_api.post(app, "/api/agent/" + path, {"root": project.root, **payload})
    assert Path(project.p("02_固定资产", "asset.png")).is_file()
    assert Path(project.p("02_固定资产", "board.png")).is_file()


def test_batch_plan_defaults_to_exact_selected_items_and_delete_keeps_tasks(project):
    payload = {"root": project.root, "keys": ["asset", "board", "asset"]}
    result = agent_api.post(app, "/api/agent/delete-plan", payload)
    assert {x["key"] for x in result["items"]} == {"asset", "board"}
    before = project.tasks()
    result = agent_api.post(app, "/api/agent/delete", payload)
    assert len(result["moved"]) == 2
    for row in result["moved"]:
        assert A.inside(project.root, row["saved_as"]).is_file()
        assert not A.inside(project.root, row["output"]).exists()
    assert project.tasks() == before
    assert Path(project.p("02_固定资产", "scstate.png")).is_file()


@pytest.mark.parametrize("keys", [[], None, "asset", [1], ["unknown"]])
def test_batch_plan_rejects_invalid_selection(project, keys):
    with pytest.raises(ValueError):
        recycle.plan_many(project, keys)


def test_repeated_rerolls_keep_both_backups(project, monkeypatch):
    current = Path(project.p("02_固定资产", "asset.png"))
    original = current.read_bytes()
    monkeypatch.setattr(assemble.time, "strftime", lambda *a: "same-second")
    first = assemble.reroll(project, "asset")
    current.write_bytes(original)
    second = assemble.reroll(project, "asset")
    assert first["backup"] != second["backup"]
    assert all(A.inside(project.root, r["backup"]).read_bytes() == original for r in [first, second])
