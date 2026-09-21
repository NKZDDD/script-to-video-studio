"""External Julun repair must replace one provider and remain removable."""
import importlib.util
from pathlib import Path
import shutil

from core import apiutil, paths, providers
from core.providers.julun import JulunProvider


PATCH = Path(__file__).resolve().parents[1] / "provider_patches" / "julun_result_hotfix.py"


def test_loader_overrides_only_julun_and_removal_restores_builtin(monkeypatch, tmp_path):
    with monkeypatch.context() as m:
        m.setattr(paths, "plugins_dir", lambda: str(tmp_path))
        try:
            providers.reload_all()
            before = dict(providers.REGISTRY)
            target = tmp_path / PATCH.name
            shutil.copy2(PATCH, target)
            status = providers.reload_all()
            assert not status["errors"]
            julun = next(p for p in status["providers"] if p["id"] == "julun")
            assert not julun["builtin"] and Path(julun["source"]) == target
            patched = providers.build("巨轮", "fixture-key")
            assert type(patched) is not JulunProvider
            assert patched.capabilities() == JulunProvider().capabilities()
            assert type(patched).generate_video is JulunProvider.generate_video
            assert type(patched).generate_image is JulunProvider.generate_image
            assert type(patched).upload_asset is JulunProvider.upload_asset
            assert patched.session.api_key == "fixture-key"
            assert {k: v for k, v in providers.REGISTRY.items() if k != "julun"} == {
                k: v for k, v in before.items() if k != "julun"}
            target.unlink()
            providers.reload_all()
            assert providers.REGISTRY == before
            assert providers.SOURCES["julun"] == "内置"
        finally:
            m.undo()
            providers.reload_all()


def test_loads_on_early_hosts_without_running_states(monkeypatch):
    monkeypatch.delattr(apiutil, "RUNNING_STATES")
    spec = importlib.util.spec_from_file_location("julun_early_host_fixture", PATCH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert "in_progress" in module.RUNNING_STATES
    assert "running" in module.RUNNING_STATES
    p = module.JulunResultHotfix()
    replies = iter([{"status": "running", "result_url": "https://cdn.example/preview.mp4"},
                    {"status": "completed", "result_url": "https://cdn.example/final.mp4"}])
    monkeypatch.setattr(p.session, "request", lambda *a, **kw: next(replies))
    assert p._poll("fixture", 0, 3, log=lambda _: None) == "https://cdn.example/final.mp4"
