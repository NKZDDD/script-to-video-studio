"""Offline handoff: export text-only package, import releases produced on another machine."""
import io
import shutil
from pathlib import Path
import zipfile

import pytest

from core import agent_handoff as H, agent_projects as A, docparse
from core.store import read_json, write_text
from test_agent_workspace import image, plan_for, publish, pj  # noqa: F401  (pj is a fixture)


def released(pj, revision, text="生成测试图片", layout="project"):
    """Publish in the project, zip the release folder as a remote Agent would, then remove it locally."""
    folder, _ = publish(pj, plan_for(pj, revision), text=text)
    prefix = {"project": f"03_提示词/releases/{revision}/", "folder": f"{revision}/", "flat": ""}[layout]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for p in folder.rglob("*"):
            if p.is_file():
                z.write(p, prefix + p.relative_to(folder).as_posix())
    shutil.rmtree(folder)
    return buf.getvalue()


def test_export_is_text_only_with_relative_instructions(pj):
    publish(pj, plan_for(pj));A.scan(pj)
    image(pj.p("02_固定资产", "asset0.png"))
    write_text(pj.p("07_检查与记录", "agent-revisions", "fix.md"), "改一下")
    d = H.export_package(pj)
    names = zipfile.ZipFile(d["file"]).namelist()
    assert d["next_revision"] == "r0002" and Path(d["file"]).is_file()
    for must in ("AGENT_TASK.md", "00_项目说明/production-contract.md", "00_项目说明/project-lock.json",
                 "00_项目说明/production.schema.json", "01_剧本与分段/原始素材.txt",
                 "03_提示词/releases/r0001/READY.json", "07_检查与记录/agent-revisions/fix.md",
                 "00_项目说明/skill/production-skill/SKILL.md"):
        assert must in names, must
    assert not any(n.endswith((".png", ".mp4")) for n in names)
    z = zipfile.ZipFile(d["file"])
    task = z.read("AGENT_TASK.md").decode()
    script, contract = z.read("剧本.txt").decode(), z.read("生产材料契约.md").decode()
    assert script == "测试原文" and "生产材料.jsonl" in contract
    assert "不要再向用户要剧本" in task and pj.root not in task


@pytest.mark.parametrize("layout", ["project", "folder", "flat"])
def test_release_from_another_machine_is_validated_and_activated(pj, layout):
    d = H.import_package(pj, released(pj, "r0001", layout=layout))
    assert d["imported"] == ["r0001"] and not d["rejected"] and d["active"] == "r0001"
    assert read_json(pj.p("07_检查与记录", "active-plan.json"))["revision"] == "r0001"


def test_same_release_twice_is_unchanged_but_different_content_is_refused(pj):
    raw, other = released(pj, "r0001"), released(pj, "r0001", text="另一个版本的提示词")
    assert H.import_package(pj, raw)["imported"] == ["r0001"]
    assert H.import_package(pj, raw)["unchanged"] == ["r0001"]
    with pytest.raises(ValueError, match="已经存在且内容不同.*r0002"):
        H.import_package(pj, other)


def test_revision_not_newer_than_active_is_refused_before_extracting(pj):
    H.import_package(pj, released(pj, "r0002"))
    with pytest.raises(ValueError, match="不比当前已接收的 r0002 新.*r0003"):
        H.import_package(pj, released(pj, "r0001"))
    assert not Path(pj.p("03_提示词", "releases", "r0001")).exists()


def test_failed_validation_is_withdrawn_so_the_same_revision_can_be_fixed(pj):
    raw = released(pj, "r0001")
    broken = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(raw)) as src, zipfile.ZipFile(broken, "w") as dst:
        for info in src.infolist():
            data = src.read(info)
            dst.writestr(info, data + b"tampered" if info.filename.endswith(".txt") else data)
    d = H.import_package(pj, broken.getvalue())
    assert not d["imported"] and d["rejected"][0]["revision"] == "r0001" and "散列" in d["rejected"][0]["message"]
    assert not Path(pj.p("03_提示词", "releases", "r0001")).exists()
    assert H.import_package(pj, raw)["imported"] == ["r0001"]


@pytest.mark.parametrize("name", ["../evil/READY.json", "C:/evil/READY.json", "/abs/READY.json"])
def test_unsafe_paths_are_refused(pj, name):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(name, '{"revision":"r0001"}')
    with pytest.raises(ValueError, match="不安全"):
        H.import_package(pj, buf.getvalue())


def test_zip_without_ready_says_what_to_pack(pj):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("tasks.json", "{}")
    with pytest.raises(ValueError, match="READY.json"):
        H.import_package(pj, buf.getvalue())
    with pytest.raises(ValueError, match="zip"):
        H.import_package(pj, b"not a zip")


def test_handoff_routes_work_through_the_http_layer(pj, monkeypatch):
    import base64, copy, os
    from server import agent_api, app
    from test_agent_workspace import CAPS
    cfg = {"projects_dir": os.path.dirname(pj.root), "providers": {}, "limits": {"global": 8, "per_provider": {}}}
    monkeypatch.setattr(app, "load_config", lambda: copy.deepcopy(cfg))
    monkeypatch.setattr(app, "caps_of", lambda cfg=None: copy.deepcopy(CAPS))
    assert agent_api.post(app, "/api/agent/handoff/export", {"root": pj.root})["next_revision"] == "r0001"
    raw = base64.b64encode(released(pj, "r0001")).decode()
    d = agent_api.post(app, "/api/agent/handoff/import", {"root": pj.root, "filename": "r.zip", "content_b64": raw})
    assert d["imported"] == ["r0001"] and d["active"] == "r0001"


def test_jsonl_material_becomes_visible_tasks_on_an_agent_project(pj, monkeypatch):
    import base64
    import copy
    from core import matspec
    from server import agent_api, app
    from test_agent_workspace import CAPS
    raw = matspec.jsonl_schema("v34").encode("utf-8")
    cfg = {"projects_dir": str(Path(pj.root).parent), "providers": {}, "limits": {"global": 8, "per_provider": {}}}
    monkeypatch.setattr(app, "load_config", lambda: copy.deepcopy(cfg))
    monkeypatch.setattr(app, "caps_of", lambda cfg=None: copy.deepcopy(CAPS))
    monkeypatch.setattr(app, "_material_limits", lambda *a: {})
    d = agent_api.post(app, "/api/agent/handoff/import", {
        "root": pj.root, "filename": "生产材料.jsonl", "content_b64": base64.b64encode(raw).decode()})
    assert d["ok"] and d["revision"]
    assert A.view(pj)["tasks"]
    assert A.view(pj)["publication"]["source"] == "jsonl"


def test_jsonl_source_reads_as_plain_text():
    raw = '{"场":1,"台词":"你好"}\n{"场":2}\n'.encode("utf-8")
    assert docparse.parse_bytes("剧本.jsonl", raw) == raw.decode("utf-8").strip()
    assert ".jsonl" in docparse.SUPPORTED
