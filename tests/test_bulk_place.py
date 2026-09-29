"""外部图片批量放图：按文件名对应任务，不替人猜同分的，不覆盖已有的。"""
import base64
import copy
import io
from pathlib import Path

from PIL import Image
import pytest

from core import agent_projects as A, bulk_place, agent_runtime as R
from core.store import write_json
from server import agent_api, app
from test_agent_workspace import CAPS, image, plan_for, publish


@pytest.fixture
def pj(tmp_path):
    project = A.create_project(str(tmp_path), {"title": "批量放图", "source": "x"}, CAPS)
    publish(project, plan_for(project, count=3)); A.scan(project)
    return project


def _rows(pj, names):
    return {r["name"]: r for r in bulk_place.plan(pj, names)["rows"]}


def test_file_names_map_to_tasks_by_output_id_or_name(pj):
    rows = _rows(pj, ["ASSET0.JPG", "资产1.png", "ａｓｓｅｔ２ .webp", "无关.png"])
    assert rows["ASSET0.JPG"]["choice"] == "asset0"
    assert rows["资产1.png"]["choice"] == "asset1"
    assert rows["ａｓｓｅｔ２ .webp"]["choice"] == "asset2"
    assert rows["无关.png"]["choice"] == "" and "对应不上" in rows["无关.png"]["note"]


def test_a_short_chinese_name_finds_candidates_but_ties_are_not_guessed(pj):
    row = _rows(pj, ["资产.png"])["资产.png"]
    assert row["choice"] == "" and "一样像" in row["note"]
    assert {c["key"] for c in row["candidates"]} == {"asset0", "asset1", "asset2"}


def test_two_files_for_one_task_keep_the_closer_one(pj):
    rows = _rows(pj, ["asset0 副本.png", "asset0.png"])
    assert rows["asset0.png"]["choice"] == "asset0"
    assert rows["asset0 副本.png"]["choice"] == "" and "同一个任务" in rows["asset0 副本.png"]["note"]


def test_a_task_that_already_has_a_file_is_not_chosen(pj):
    image(pj.p("02_固定资产", "asset1.png"))
    row = _rows(pj, ["asset1.png"])["asset1.png"]
    assert row["choice"] == "" and "已经有文件" in row["note"] and row["candidates"][0]["exists"]


def test_placing_a_jpeg_into_a_png_output_marks_it_done(pj, monkeypatch):
    cfg = {"projects_dir": str(Path(pj.root).parent), "providers": {}, "limits": {"global": 8, "per_provider": {}}}
    monkeypatch.setattr(app, "load_config", lambda: copy.deepcopy(cfg))
    monkeypatch.setattr(app, "caps_of", lambda cfg=None: copy.deepcopy(CAPS))
    plan = agent_api.post(app, "/api/agent/place-plan", {"root": pj.root, "names": ["asset0.jpg"]})
    assert plan["rows"][0]["choice"] == "asset0"
    buf = io.BytesIO()
    Image.effect_noise((64, 64), 80).convert("RGB").save(buf, "JPEG")
    agent_api.post(app, "/api/agent/manual", {"root": pj.root, "kind": "asset", "key": "asset0",
                                              "content_b64": base64.b64encode(buf.getvalue()).decode()})
    out = Path(pj.p("02_固定资产", "asset0.png"))
    assert out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n" and R.valid_output(str(out))
    with pytest.raises(ValueError, match="已有文件"):
        agent_api.post(app, "/api/agent/manual", {"root": pj.root, "kind": "asset", "key": "asset0",
                                                  "content_b64": base64.b64encode(buf.getvalue()).decode()})
