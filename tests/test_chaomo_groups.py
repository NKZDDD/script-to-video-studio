import base64
import importlib.util
import io
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from core import providers, model_catalog
from core.providers.base import ImageTask
from core.providers.chaomo import (ChaomoProvider, NATIVE_SIZED_MODELS,
                                   TH_SIZED_MODELS, PINNED_IMAGE_MODELS)
from server.app import resolve_provider_cfg


@pytest.mark.parametrize("model", PINNED_IMAGE_MODELS)
@pytest.mark.parametrize("resolution", ["1K", "2K", "4K"])
def test_key_group_independent_of_resolution(model, resolution):
    cfg = {"providers": {"chaomo": {"image_1k_api_key": "th-fixture",
                                    "image_4k_api_key": "native-fixture"}}}
    out = resolve_provider_cfg(cfg, {"provider": "chaomo", "model": model,
                                    "resolution": resolution}, "asset")
    assert out["api_key"] == ("native-fixture" if model in NATIVE_SIZED_MODELS else "th-fixture")


@pytest.mark.parametrize("model", NATIVE_SIZED_MODELS + TH_SIZED_MODELS)
@pytest.mark.parametrize("resolution", ["1K", "2K", "4K"])
@pytest.mark.parametrize("edit", [False, True])
def test_actual_request_sends_resolution_and_keeps_ratio(monkeypatch, model, resolution, edit):
    p = ChaomoProvider()
    stream = io.BytesIO(); Image.new("RGB", (12, 12), "red").save(stream, "PNG")
    ref = "data:image/png;base64," + base64.b64encode(stream.getvalue()).decode()
    seen = []
    def request(method, path, **kw):
        seen.append((path, kw))
        return {"data": [{"url": "https://example.com/result.png"}]}
    monkeypatch.setattr(p.session, "request", request)
    monkeypatch.setattr(p.session, "save_item", lambda *a, **kw: None)
    monkeypatch.setattr(p, "check_meta", lambda *a, **kw: None)
    p.generate_image(ImageTask("fixture", model=model, size="16:9", refs=[ref] * 12 if edit else [],
                                extra={"resolution": resolution}), "out.png", log=lambda _: None)
    path, kw = seen[0]
    if edit:
        assert path == "/v1/images/edits"
        body = {k: v[1] for k, v in kw["files"] if k != "image[]"}
        assert sum(k == "image[]" for k, _ in kw["files"]) == 12
    else:
        body = kw["json_body"]
    assert body["model"] == model
    assert body["size"] == resolution
    assert body["ratio"] == "16:9"


def test_old_snapshot_does_not_hide_unified_models():
    cap = model_catalog.apply_catalog(ChaomoProvider().capabilities(),
                                     {"rows": [{"id": "gpt-image-1k-th"}]}, "old snapshot")
    assert set(PINNED_IMAGE_MODELS) <= set(cap["image"]["models"])
    assert cap["image"]["model_options"]["gpt-image-1k-th"]["resolutions"] == ["1K"]
    for model in NATIVE_SIZED_MODELS:
        assert cap["image"]["model_options"][model]["resolutions"] == ["1K", "2K", "4K"]


def test_plugin_routes_on_old_host_and_restores_when_removed(monkeypatch):
    root = Path(__file__).resolve().parents[1]
    patch = root / "provider_patches/zz_chaomo_groups.py"
    assert patch.read_bytes() == (root / "core/providers/chaomo.py").read_bytes()
    spec = importlib.util.spec_from_file_location("stv_plugin_groups_fixture", patch)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    original = lambda pid, media, model="": ("image_4k" if "4k" in model.lower() else "image_1k") if pid == "chaomo" and media == "image" else media
    legacy = SimpleNamespace(_provider_key_slot=original)
    monkeypatch.setitem(sys.modules, "server.app", legacy)
    # Restore the global catalog wrapper after exercising old-host compatibility.
    monkeypatch.setattr(model_catalog, "apply_catalog", model_catalog.apply_catalog)
    monkeypatch.setitem(providers.REGISTRY, "chaomo", module.ChaomoProvider)
    cap = module.ChaomoProvider().capabilities()
    routed = legacy._provider_key_slot
    assert routed("chaomo", "image", "gpt-image-2-Native") == "image_4k"
    assert routed("chaomo", "image", "gpt-image-2-th") == "image_1k"
    assert routed("chaomo", "video", "seedance2") == "video"
    module.ChaomoProvider().capabilities()
    assert legacy._provider_key_slot is routed
    result = model_catalog.apply_catalog(cap, {"rows": [{"id": "gpt-image-2-th", "max_reference_images": 9}]}, "old")
    assert set(PINNED_IMAGE_MODELS) <= set(result["image"]["models"])
    assert result["image"]["model_options"]["gpt-image-2-th"]["max_refs"] > 9
    monkeypatch.setitem(providers.REGISTRY, "chaomo", type("OldBuiltin", (), {}))
    assert routed("chaomo", "image", "gpt-image-2-Native") == "image_1k"
