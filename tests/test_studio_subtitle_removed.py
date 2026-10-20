"""Studio no longer executes captions, even with legacy project/config switches."""
import importlib.util
from pathlib import Path
import subprocess
import sys

import pytest
from core import agent_projects as A, pipeline, pipeline_v34
from core.store import Project
from server import app, agent_api


def test_legacy_projects_finish_at_assembly(tmp_path, monkeypatch):
    pj = Project(str(tmp_path))
    pj.save_meta({"subtitle": {"enabled": True}})
    monkeypatch.setattr(app, "load_config", lambda: {"subtitle": {"enabled": True}})
    for module in (pipeline, pipeline_v34):
        rows = module.plan(pj, include_llm=False)
        assert rows
        assert all(r["kind"] != "subtitle" and r["stage"] not in ("s13", "d3") for r in rows)
        assert rows[-1]["stage"] in ("s12", "d2")


def test_old_subtitle_api_cannot_create_jobs(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "load_config", lambda: {"agent_features": {"post": True}})
    monkeypatch.setattr(agent_api, "project", lambda *_: Project(str(tmp_path)))
    before = app.JOBS.active_count()
    with pytest.raises(ValueError, match="未知"):
        agent_api.get(app, "/api/agent/post-options", {"root": [str(tmp_path)]})
    with pytest.raises(ValueError, match="未知"):
        agent_api.post(app, "/api/agent/subtitle", {"root": str(tmp_path)})
    assert app.JOBS.active_count() == before


def test_cli_no_longer_dispatches_caption():
    result = subprocess.run([sys.executable, "run.py", "caption", "--version"],
                            capture_output=True, encoding="utf-8")
    assert result.returncode == 2
    assert "unrecognized arguments" in result.stderr


def test_release_excludes_caption_dependencies_but_keeps_video_assembly():
    spec = importlib.util.spec_from_file_location("packaging_test", "打包exe.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    for name in ("videocaptioner", "audioop", "pydub"):
        assert name in build.EXCLUDE
        assert name not in build.REQUIRED
        assert name not in build.COLLECT_ALL
    assert "imageio_ffmpeg" in build.REQUIRED
    assert "imageio_ffmpeg" in build.COLLECT_ALL
    assert A.FEATURES["post"] == "成片合成"
    from core.packagecheck import resource_inventory
    assert not any(p.startswith("字幕样式/") for p in resource_inventory())
