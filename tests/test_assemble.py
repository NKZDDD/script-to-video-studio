"""拼接清单（去段、改顺序、插外部视频）与重新抽卡。"""
import copy
import json
from pathlib import Path
import subprocess

import pytest

from core import assemble, probe
from core import agent_projects as A
from core.store import Project, read_json

FF = probe.find_ffmpeg()
pytestmark = pytest.mark.skipif(not FF, reason="需要 ffmpeg")


def clip(path, size="320x240", audio=True, seconds=1):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [FF, "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc=size={size}:rate=25:duration={seconds}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", "-c:a", "aac", "-shortest"]
    subprocess.run(cmd + ["-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)], check=True, capture_output=True)
    return path


@pytest.fixture
def pj(tmp_path):
    pj = Project(str(tmp_path))
    pj.init_dirs()
    pj.save_meta({"system": "v61"})
    pj.save_tasks({"video_tasks": [
        {"key": f"EP01-SEG0{i}", "episode": "EP01", "output": f"05_分段视频/EP01-SEG0{i}.mp4",
         "prompt_ref": "03_提示词/p.txt", "params": {"ratio": "4:3", "duration": 1}} for i in (1, 2, 3)]})
    for i in (1, 2):
        clip(pj.p("05_分段视频", f"EP01-SEG0{i}.mp4"))
    return pj


def test_plan_lists_segments_in_script_order_and_marks_missing(pj):
    d = assemble.plan(pj, "EP01")
    assert [r["key"] for r in d["rows"]] == ["EP01-SEG01", "EP01-SEG02", "EP01-SEG03"]
    assert [r["ok"] for r in d["rows"]] == [True, True, False] and d["episodes"] == ["EP01"]


def test_default_still_refuses_a_master_with_missing_segments(pj):
    with pytest.raises(ValueError, match="尚未完整生成"):
        assemble.build(pj, "EP01")


def test_chosen_segments_in_chosen_order_are_copied_losslessly(pj):
    items = ["05_分段视频/EP01-SEG02.mp4", "05_分段视频/EP01-SEG01.mp4"]
    d = assemble.build(pj, "EP01", items)
    assert d["mode"] == "直接拼接" and d["count"] == 2
    out = Path(pj.p(*d["rel"].split("/")))
    assert probe.media_info(str(out))["duration"] == pytest.approx(2, abs=0.3)
    assert read_json(str(out.with_suffix(".json")))["items"] == items


def test_external_intro_with_other_size_and_no_audio_is_fitted_to_the_segments(pj):
    ext = clip(pj.p("06_成片", "插入素材", "片头.mp4"), size="640x360", audio=False)
    assert probe.media_info(str(ext))["audio"] is False
    d = assemble.build(pj, "EP01", ["06_成片/插入素材/片头.mp4", "05_分段视频/EP01-SEG01.mp4"])
    info = probe.media_info(pj.p(*d["rel"].split("/")))
    assert d["mode"] == "统一规格重新编码"
    assert (info["width"], info["height"]) == (320, 240) and info["audio"]
    assert info["duration"] == pytest.approx(2, abs=0.3)


@pytest.mark.parametrize("rel", ["02_固定资产/a.png", "../outside.mp4", "05_分段视频/EP01-SEG03.mp4"])
def test_only_valid_project_videos_can_be_assembled(pj, rel):
    with pytest.raises(ValueError):
        assemble.build(pj, "EP01", [rel])
    assert not list(Path(pj.p("06_成片")).glob("*.mp4"))


def test_reroll_moves_current_take_to_backup_and_selects_the_segment(pj):
    d = assemble.reroll(pj, "EP01-SEG01")
    assert not Path(pj.p("05_分段视频", "EP01-SEG01.mp4")).exists()
    assert Path(pj.p(*d["backup"].split("/"))).is_file()
    assert read_json(pj.p("07_检查与记录", "selections.json"))["EP01-SEG01"] is True
    assert [x["rel"] for x in assemble.plan(pj, "EP01")["extras"]] == [d["backup"]]
    with pytest.raises(ValueError, match="还没有有效视频"):
        assemble.reroll(pj, "EP01-SEG01")
    assemble.build(pj, "EP01", [d["backup"], "05_分段视频/EP01-SEG02.mp4"])


def test_inserted_upload_must_be_a_video_and_never_overwrites(pj):
    raw = Path(pj.p("05_分段视频", "EP01-SEG01.mp4")).read_bytes()
    a = assemble.save_insert(pj, "片尾.mp4", raw)
    b = assemble.save_insert(pj, "片尾.mp4", raw)
    assert a["rel"] != b["rel"] and all(Path(pj.p(*x["rel"].split("/"))).is_file() for x in (a, b))
    with pytest.raises(ValueError):
        assemble.save_insert(pj, "说明.txt", b"hello")
    with pytest.raises(ValueError, match="有效的视频"):
        assemble.save_insert(pj, "坏的.mp4", b"not a video" * 200)


def test_routes_through_the_http_layer(pj, monkeypatch):
    import base64, os
    from server import agent_api, app
    cfg = {"projects_dir": os.path.dirname(pj.root), "providers": {}, "agent_features": {"post": True},
           "limits": {"global": 8, "per_provider": {}}}
    monkeypatch.setattr(app, "load_config", lambda: copy.deepcopy(cfg))
    monkeypatch.setattr(app, "caps_of", lambda cfg=None: [])
    root = pj.root
    assert len(agent_api.get(app, "/api/agent/assemble-plan", {"root": [root], "episode": ["EP01"]})["rows"]) == 3
    raw = base64.b64encode(Path(pj.p("05_分段视频", "EP01-SEG02.mp4")).read_bytes()).decode()
    up = agent_api.post(app, "/api/agent/assemble/upload", {"root": root, "filename": "x.mp4", "content_b64": raw})
    d = agent_api.post(app, "/api/agent/assemble", {"root": root, "episode": "EP01",
                                                    "items": [up["rel"], "05_分段视频/EP01-SEG01.mp4"]})
    assert d["mode"] == "统一规格重新编码"
    assert agent_api.post(app, "/api/agent/reroll", {"root": root, "key": "EP01-SEG02"})["backup"]
