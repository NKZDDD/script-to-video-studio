"""2026-09-28 public specs: options, real submission and standalone old-host loading."""
import builtins
import importlib.util
from pathlib import Path
import shutil

import pytest

from core import apiutil, paths, providers
from core.model_catalog import apply_catalog
from core.providers.base import VideoTask
from core.providers.julun import JulunProvider, VIDEO_MODELS

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "provider_patches" / "zz_julun_latest.py"
THIRTY = ["SD 2.0-933", "SD 2.5", "sd2.5", "sd2.5_30",
          "seedance-2.5-720p", "seedance-2.5-pro-480",
          "seedance-2.5-pro-720", "seedance-2.5-deal-720", "seedance-2.5-deal"]


@pytest.mark.parametrize("model", THIRTY)
@pytest.mark.parametrize("seconds", [20, 30])
def test_actual_submission_keeps_selected_duration_and_references(model, seconds, monkeypatch):
    p = JulunProvider(api_key="fixture")
    requests = []

    def request(method, path, **kw):
        requests.append((method, path, kw))
        if method == "POST":
            return {"task_id": "fixture-id"}
        return {"status": "completed", "progress": 95,
                "result_url": "https://cdn.example/final.mp4"}

    saved = []
    monkeypatch.setattr(p.session, "request", request)
    monkeypatch.setattr(p.session, "save_item", lambda u, d, **kw: saved.append((u, d)))
    refs = ["https://cdn.example/a.jpg", "https://cdn.example/b.jpg"]
    p.generate_video(VideoTask(model=model, prompt="@Image1 @Image2 对话", refs=refs,
                               duration=seconds), "fixture.mp4", log=lambda _: None)
    body = requests[0][2]["json_body"]
    assert body.get("seconds", body.get("duration")) == seconds
    assert (body.get("images") or body.get("image_refs") or body.get("image_urls")) == refs
    assert saved == [("https://cdn.example/final.mp4", "fixture.mp4")]
    cap = p.capabilities()["video"]["model_options"][model]
    if model == "sd2.5":
        assert "durations" not in cap  # New document does not publish a range.
    else:
        assert seconds in cap["durations"]


def test_catalog_refresh_retains_updated_options():
    cap = apply_catalog(JulunProvider().capabilities(),
                        {"rows": [{"id": m} for m in THIRTY]}, "fixture")
    for model in THIRTY:
        options = cap["video"]["model_options"][model]
        if model == "sd2.5":
            assert "durations" not in options
        else:
            assert 30 in options["durations"]
    for model in ("seedance_v2.5", "seedance_v2.0-933", "seedance-2.5-deal"):
        assert model in VIDEO_MODELS


def test_deal_updated_document_keeps_30s_and_720p():
    p = JulunProvider()
    opts = p.capabilities()["video"]["model_options"]["seedance-2.5-deal"]
    assert opts["durations"] == list(range(4, 31))
    assert opts["resolutions"] == ["720p"]
    said = []
    _, body = p.build_video_body(VideoTask(model="seedance-2.5-deal", prompt="风景", duration=30),
                                 log=said.append)
    assert body["seconds"] == 30 and body["resolution"] == "720p"
    assert said == []
    with pytest.raises(apiutil.ApiError, match="清晰度"):
        p.build_video_body(VideoTask(model="seedance-2.5-deal", prompt="风景", resolution="480p"))


def test_no_video_refs_for_sd25_30_and_no_dropped_media_on_pro():
    task = VideoTask(model="sd2.5_30", prompt="风景", duration=30,
                     extra={"video_refs": ["https://cdn.example/a.mp4"]})
    with pytest.raises(apiutil.ApiError, match="不支持参考视频"):
        JulunProvider().build_video_body(task)
    task.model = "seedance-2.5-pro-720"
    task.extra["audio_refs"] = ["https://cdn.example/a.wav"]
    _, body = JulunProvider().build_video_body(task)
    assert body["videos"] == task.extra["video_refs"]
    assert body["audios"] == task.extra["audio_refs"]


def test_fifteen_second_models_do_not_advertise_30():
    opts = JulunProvider().capabilities()["video"]["model_options"]
    for model in ("SD 2.0", "seedance-2.0-fast-15s", "Seedance-933",
                  "sd2.0-933-720p-fast-x5-15s"):
        assert max(opts[model]["durations"]) == 15


def test_plugin_is_same_source_and_overrides_only_julun(monkeypatch, tmp_path):
    assert PATCH.read_bytes() == (ROOT / "core/providers/julun.py").read_bytes()
    with monkeypatch.context() as m:
        m.setattr(paths, "plugins_dir", lambda: str(tmp_path))
        try:
            providers.reload_all()
            before = dict(providers.REGISTRY)
            shutil.copy2(PATCH, tmp_path / PATCH.name)
            status = providers.reload_all()
            assert not status["errors"]
            assert providers.SOURCES["julun"] == str(tmp_path / PATCH.name)
            assert providers.REGISTRY["julun"]().capabilities() == JulunProvider().capabilities()
            assert {k: v for k, v in providers.REGISTRY.items() if k != "julun"} == {
                k: v for k, v in before.items() if k != "julun"}
            (tmp_path / PATCH.name).unlink()
            providers.reload_all()
            assert providers.REGISTRY == before
        finally:
            m.undo()
            providers.reload_all()


def test_old_host_without_submitted_or_running_states_can_generate(monkeypatch):
    original_import = builtins.__import__

    def old_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "core" and "submitted" in fromlist:
            raise ImportError("Old EXE does not include core.submitted")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", old_import)
    monkeypatch.delattr(apiutil, "RUNNING_STATES")
    spec = importlib.util.spec_from_file_location("julun_old_host_latest", PATCH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.submitted is None
    p = mod.JulunProvider(api_key="fixture")
    replies = iter([{"task_id": "fixture"},
                    {"status": "running", "result_url": "https://cdn.example/preview.mp4"},
                    {"data": {"status": "completed", "progress": 95}}])
    saved = []
    monkeypatch.setattr(p.session, "request", lambda *a, **kw: next(replies))
    monkeypatch.setattr(p.session, "save_item", lambda u, d, **kw: saved.append(u))
    result = p.generate_video(VideoTask(model="sd2.5", prompt="风景", duration=30),
                              "out.mp4", poll_interval=0, log=lambda _: None)
    assert result["task_id"] == "fixture"
    assert saved == ["https://julun.cc/v1/videos/fixture/content"]
