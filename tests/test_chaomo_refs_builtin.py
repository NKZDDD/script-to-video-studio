"""超模图片的参考图张数本地不限制 —— 内置就是这样，不再依赖 9/21 那个外挂。

外挂只在数据目录 providers/ 里有它的时候生效。换了机器、清过目录，
内置的 9 张上限就回来了：生产计划超过 9 张就拦，出图时只传前 9 张，
提示词里的「图 10」指不到任何图。
"""
import base64
import io

from PIL import Image

from core import agent_projects as A, agent_runtime as R, model_catalog
from core.providers.base import ImageTask
from core.providers.chaomo import ChaomoProvider
from test_agent_workspace import image, plan_for, publish, resolve


def _png(i):
    out = io.BytesIO()
    Image.new("RGB", (16, 16), (i, 100, 120)).save(out, "PNG")
    return "data:image/png;base64," + base64.b64encode(out.getvalue()).decode()


def test_builtin_image_sends_every_reference_in_order():
    p = ChaomoProvider(api_key="offline-fixture-only")
    refs = [_png(i) for i in range(12)]
    sent = []

    def request(method, path, **kw):
        assert path == "/v1/images/edits"
        for name, part in kw["files"]:
            if name == "image[]":
                with Image.open(io.BytesIO(part[1])) as im:
                    sent.append(im.convert("RGB").getpixel((0, 0))[0])
        return {"data": [{"url": "https://offline.invalid/result.png"}]}
    p.session.request = request
    p.session.save_item = lambda *a, **kw: None
    p.check_meta = lambda *a, **kw: None
    p.generate_image(ImageTask("test", refs=refs, model="gpt-image-2.5-flare"), "unused.png", log=lambda _: None)
    assert sent == list(range(12))


def test_online_catalog_nine_does_not_bring_the_cap_back(tmp_path):
    cap = model_catalog.apply_catalog(ChaomoProvider().capabilities(), {"rows": [
        {"id": "gpt-image-2.5-flare", "type": "image", "max_reference_images": 9}]}, "fixture")
    assert "max_refs" not in cap["image"]["model_options"]["gpt-image-2.5-flare"]
    assert cap["video"]["max_refs"] == 9
    pj = A.create_project(str(tmp_path / "projects"), {"title": "参考图张数", "source": "fixture",
        "options": {"image_provider": "chaomo", "image_model": "gpt-image-2.5-flare"}}, [cap])
    plan = plan_for(pj, count=12)
    for task in plan["asset_tasks"][:-1]:
        task["reference_images"] = []
        image(pj.p(task["output"]))
    plan["asset_tasks"][-1]["reference_images"] = [
        {"asset_id": t["key"], "file_ref": t["output"], "image_n": i}
        for i, t in enumerate(plan["asset_tasks"][:-1], 1)]
    publish(pj, plan)
    assert not A.scan(pj)["errors"]
    preview = R.preview(pj, {}, [cap], resolve)
    assert not preview["blocked"], preview["blocked"]
    assert preview["todo"] == 1
