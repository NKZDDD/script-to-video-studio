"""External patch must bypass preview and preserve every multipart reference."""
import base64
from copy import deepcopy
import io
from pathlib import Path
import shutil

from PIL import Image
import pytest

from core import model_catalog, paths, providers
from core.apiutil import ApiError
from core.providers.base import ImageTask
from core.providers.chaomo import ChaomoProvider, MAX_REFS

PATCH = Path(__file__).resolve().parents[1] / "provider_patches/chaomo_image_refs_hotfix.py"


@pytest.fixture
def patched(monkeypatch, tmp_path):
    with monkeypatch.context() as m:
        m.setattr(paths, "plugins_dir", lambda: str(tmp_path))
        original_catalog = model_catalog.apply_catalog
        shutil.copy2(PATCH, tmp_path)
        try:
            state = providers.reload_all()
            assert not state["errors"]
            yield providers.REGISTRY["chaomo"]
        finally:
            model_catalog.apply_catalog = original_catalog
            m.undo()
            providers.reload_all()


@pytest.mark.parametrize("model", ["gpt-image-1k-th", "gpt-image-2.5-sunburst-4K-Native"])
@pytest.mark.parametrize("count", [10, 11, 30])
def test_all_references_reach_multipart_in_order(patched, model, count):
    p = patched(api_key="offline-fixture-only")
    refs, expected = [], []
    for i in range(count):
        out = io.BytesIO()
        Image.new("RGB", (16, 16), (i, 100, 120)).save(out, "PNG")
        refs.append("data:image/png;base64," + base64.b64encode(out.getvalue()).decode())
        expected.append((i, 100, 120))
    sent = []
    def request(method, path, **kw):
        assert path == "/v1/images/edits"
        for name, part in kw["files"]:
            if name == "image[]":
                with Image.open(io.BytesIO(part[1])) as im:
                    sent.append(im.convert("RGB").getpixel((0, 0)))
        return {"data": [{"url": "https://offline.invalid/result.png"}]}
    p.session.request = request
    p.session.save_item = lambda *a, **kw: None
    p.check_meta = lambda *a, **kw: None
    p.generate_image(ImageTask("test", refs=refs, model=model), "unused.png", log=lambda _: None)
    assert sent == expected
    assert MAX_REFS == ChaomoProvider.generate_image.__globals__["MAX_REFS"] == 9


def test_catalog_cannot_restore_limit_and_other_capabilities_preserved(patched):
    original = deepcopy(ChaomoProvider().capabilities())
    cap = patched().capabilities()
    rows = [{"id": "gpt-image-1k-th", "type": "image", "max_reference_images": 9},
            {"id": "gpt-image-2.5-sunburst-4K-Native", "type": "image", "max_images": 9}]
    merged = model_catalog.apply_catalog(cap, {"rows": rows}, "fixture")
    for row in rows:
        assert merged["image"]["model_options"][row["id"]]["max_refs"] > 30
    assert merged["image"]["max_refs"] > 30
    assert cap["video"] == original["video"]
    assert patched.generate_video is ChaomoProvider.generate_video
    assert ChaomoProvider().capabilities() == original
    plain = model_catalog.apply_catalog(original, {"rows": rows}, "fixture")
    assert plain["image"]["model_options"][rows[0]["id"]]["max_refs"] == 9


def test_reload_and_removal_restore_builtin(patched, tmp_path):
    first = model_catalog.apply_catalog
    before = dict(providers.REGISTRY)
    providers.reload_all()
    assert model_catalog.apply_catalog is first
    assert providers.REGISTRY["chaomo"] is not ChaomoProvider
    assert {k: v for k, v in before.items() if k != "chaomo"} == {
        k: v for k, v in providers.REGISTRY.items() if k != "chaomo"}
    (tmp_path / PATCH.name).unlink()
    providers.reload_all()
    assert providers.REGISTRY["chaomo"] is ChaomoProvider
    assert providers.REGISTRY["chaomo"]().capabilities()["image"]["max_refs"] == 9


def test_invalid_tenth_reference_still_fails_before_submission(patched):
    out = io.BytesIO()
    Image.new("RGB", (8, 8)).save(out, "PNG")
    ref = "data:image/png;base64," + base64.b64encode(out.getvalue()).decode()
    p = patched()
    p.session.request = lambda *a, **kw: pytest.fail("invalid reference was submitted")
    with pytest.raises(ApiError):
        p.generate_image(ImageTask("test", refs=[ref] * 9 + ["data:image/png;base64,PGh0bWw+"]),
                         "unused.png", log=lambda _: None)


@pytest.mark.parametrize("count", [10, 11])
def test_real_production_preview_accepts_more_than_nine(patched, tmp_path, count):
    from core import agent_projects as A, agent_runtime as R
    from test_agent_workspace import plan_for, publish, image, resolve
    cap = model_catalog.apply_catalog(patched().capabilities(), {"rows": [
        {"id": "gpt-image-1k-th", "max_reference_images": 9}]}, "fixture")
    pj = A.create_project(str(tmp_path / "projects"), {"title": "参考图张数验收", "source": "fixture",
        "options": {"image_provider": "chaomo", "image_model": "gpt-image-1k-th"}}, [cap])
    plan = plan_for(pj, count=count + 1)
    for task in plan["asset_tasks"][:-1]:
        task["reference_images"] = []
        image(pj.p(task["output"]))
    plan["asset_tasks"][-1]["reference_images"] = [
        {"asset_id": t["key"], "file_ref": t["output"], "image_n": i}
        for i, t in enumerate(plan["asset_tasks"][:-1], 1)]
    publish(pj, plan)
    assert not A.scan(pj)["errors"]
    preview = R.preview(pj, {}, [cap], resolve)
    assert not preview["blocked"], preview
    assert preview["todo"] == 1
