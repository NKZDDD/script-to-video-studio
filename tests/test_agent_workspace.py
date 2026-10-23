"""Contract boundaries, cost guards, production snapshots and real HTTP surface."""
import copy
import json
from pathlib import Path
import threading
import time
from urllib.error import HTTPError
from urllib.request import urlopen, Request

from PIL import Image
import pytest

from core import agent_projects as A, agent_runtime as R
from core.executor import Gate, JobManager
from core.store import Project, read_json, write_json, write_text
from server import app, agent_api

CAPS = [{"id": "chaomo", "name": "图片服务商", "supports": ["image", "video"],
         "image": {"models": ["test"], "default_model": "test", "sizes": ["16:9"], "max_refs": 4},
         "video": {"models": ["test-video"], "default_model": "test-video", "ratios": ["16:9"], "durations": [10, 30], "max_refs": 4}}]


@pytest.fixture
def pj(tmp_path):
    return A.create_project(str(tmp_path), {"title": "测试项目", "source": "测试原文", "options": {
        "image_provider": "chaomo", "image_model": "test", "video_provider": "chaomo", "video_model": "test-video"}}, CAPS)


def plan_for(pj, revision="r0001", count=2):
    plan = {"schema": A.SCHEMA, "project_id": pj.meta()["project_code"], "revision": revision,
            "resources": [], **{k + "_tasks": [] for k in A.KINDS}}
    for i in range(count):
        key = f"asset{i}"
        plan["resources"].append({"id": key, "name": "资产" + str(i), "semantic": "CHAR" if i == 0 else "LOOK",
                                  "parent_id": "asset0" if i else None, "episodes": ["EP01"], "task_key": key})
        plan["asset_tasks"].append({"key": key, "output": f"02_固定资产/{key}.png",
                                    "prompt_ref": f"03_提示词/releases/{revision}/images/{key}.txt",
                                    "params": {"size": "16:9"}, "reference_images":
                                    [{"asset_id": "asset0", "file_ref": "02_固定资产/asset0.png", "image_n": 1}] if i else []})
    return plan


def publish(pj, plan, text="生成测试图片", ready=True):
    revision = plan["revision"]
    folder = Path(pj.p("03_提示词", "releases", revision))
    folder.mkdir(parents=True, exist_ok=True)
    write_json(str(folder / "tasks.json"), plan)
    files = {"tasks.json": A.digest((folder / "tasks.json").read_bytes())}
    for row in A.task_rows(plan):
        rel = row["prompt_ref"].split(revision + "/", 1)[1]
        target = folder / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        files[rel] = A.digest(target.read_bytes())
    lock = read_json(pj.p("00_项目说明", "project-lock.json"))
    marker = {"schema": A.SCHEMA, "project_id": lock["project_id"], "revision": revision,
              "options_hash": lock["options_hash"], "skill_hash": lock["skill_hash"], "files": files}
    if ready:
        write_json(str(folder / "READY.json"), marker)
    return folder, marker


def image(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Image.effect_noise((128, 128), 100).convert("RGB").save(path)


def resolve(cfg, selection, kind):
    return {**selection, "api_key": "not-a-real-secret"}


def wait_job(job):
    until = time.monotonic() + 6
    while job.status == "running" and time.monotonic() < until:
        time.sleep(.03)
    assert job.status != "running", job.snapshot()


def test_creation_locks_complete_skill_and_absolute_paths(pj):
    lock = read_json(pj.p("00_项目说明", "project-lock.json"))
    assert len(lock["skill_files"]) == 51
    assert lock["project_root"] == pj.root
    assert lock["skill_hash"] == A.digest(A.file_manifest(Path(pj.p("00_项目说明", "skill", "production-skill"))))
    text = Path(pj.p("00_项目说明", "AGENT_TASK.md")).read_text(encoding="utf-8")
    assert pj.root in text and "Agent" in text
    assert "Codex" not in text and "老李" not in text
    assert not Path(pj.p("04_故事板")).exists()


def test_no_ready_does_not_activate(pj):
    publish(pj, plan_for(pj), ready=False)
    assert not A.scan(pj).get("revision")
    assert pj.tasks() == {}


def test_partial_or_mismatched_release_preserves_previous_active(pj):
    publish(pj, plan_for(pj))
    assert A.scan(pj)["revision"] == "r0001"
    folder, _ = publish(pj, plan_for(pj, "r0002"))
    (folder / "images/asset0.txt").write_text("changed", encoding="utf-8")
    state = A.scan(pj)
    assert state["errors"] and state["revision"] == "r0001"
    assert pj.tasks()["revision"] == "r0001"


def test_accepted_prompts_are_independent_of_agent_working_files(pj):
    folder, _ = publish(pj, plan_for(pj))
    A.scan(pj)
    (folder / "images/asset0.txt").write_text("Agent 修改", encoding="utf-8")
    prompt = A.inside(pj.root, pj.tasks()["asset_tasks"][0]["prompt_ref"])
    assert prompt.read_text(encoding="utf-8") == "生成测试图片"


def test_content_changes_must_use_new_output_filename(pj):
    publish(pj, plan_for(pj)); A.scan(pj)
    publish(pj, plan_for(pj, "r0002"), "新的提示词")
    assert "新输出文件名" in A.scan(pj)["errors"][0]["message"]


def test_unchanged_publication_can_reuse_paths_and_preserve_selection(pj):
    publish(pj, plan_for(pj)); A.scan(pj)
    A.select(pj, {"asset0": False})
    publish(pj, plan_for(pj, "r0002", count=3))
    state = A.view(pj)
    assert state["publication"]["revision"] == "r0002"
    assert [r["selected"] for r in state["tasks"]] == [False, True, True]


@pytest.mark.parametrize("rel", ["../escape.png", "C:/outside/a.png", "C:escape", "/etc/passwd", "a/../../b", "a\\..\\b", "a//b"])
def test_path_escapes_rejected(pj, rel):
    with pytest.raises(ValueError):
        A.inside(pj.root, rel)


@pytest.mark.parametrize("mutation", ["duplicate-key", "duplicate-output", "cycle", "resource-cycle", "bad-reference", "bad-number", "legacy-array"])
def test_malformed_graph_never_activates(pj, mutation):
    plan = plan_for(pj)
    a, b = plan["asset_tasks"]
    if mutation == "duplicate-key": b["key"] = a["key"]
    if mutation == "duplicate-output": b["output"] = a["output"].upper().replace("02_", "02_")
    if mutation == "cycle": a["reference_images"] = [{"asset_id": b["key"], "file_ref": b["output"], "image_n": 1}]
    if mutation == "resource-cycle": plan["resources"][0]["parent_id"] = "asset1"
    if mutation == "bad-reference": b["reference_images"][0]["file_ref"] = "02_固定资产/unknown.png"
    if mutation == "bad-number": b["reference_images"][0]["image_n"] = 3
    if mutation == "legacy-array": plan["storyboard_tasks"] = []
    publish(pj, plan)
    result = A.scan(pj)
    assert result["errors"] and not result.get("revision")


def test_unselected_missing_parent_blocks_before_provider_call(pj):
    publish(pj, plan_for(pj)); A.scan(pj); A.select(pj, {"asset0": False})
    p = R.preview(pj, {}, CAPS, resolve)
    assert len(p["blocked"]) == 1 and "缺少勾选" in p["blocked"][0]["reason"]
    image(pj.p("02_固定资产", "asset0.png"))
    p = R.preview(pj, {}, CAPS, resolve)
    assert not p["blocked"] and p["todo"] == 1


def test_empty_selection_does_not_expand_to_everything(pj):
    publish(pj, plan_for(pj)); A.scan(pj)
    A.select(pj, {"asset0": False, "asset1": False})
    p = R.preview(pj, {}, CAPS, resolve)
    assert p["todo"] == p["selected"] == 0
    assert R.preview(pj, {}, CAPS, resolve, only=[])["todo"] == 0


def test_invalid_existing_image_is_read_only_and_not_reused(pj):
    publish(pj, plan_for(pj)); A.scan(pj)
    target = Path(pj.p("02_固定资产", "asset0.png")); target.write_bytes(b"<html>" * 1000)
    assert not R.valid_output(str(target)) and target.exists()
    p = R.preview(pj, {}, CAPS, resolve)
    assert any("校验失败" in e["reason"] for e in p["blocked"])
    assert target.read_bytes() == b"<html>" * 1000


def test_unknown_model_and_missing_key_are_preflight_errors(pj):
    publish(pj, plan_for(pj)); A.scan(pj)
    def missing(*a): raise ValueError("缺少密钥")
    p = R.preview(pj, {}, CAPS, missing)
    assert all("密钥" in e["reason"] for e in p["blocked"])
    cfg = R.settings(pj, CAPS);cfg["asset"]["model"] = "not-known"
    write_json(pj.p("07_检查与记录", "production-settings.json"), cfg)
    assert all("模型不在" in e["reason"] for e in R.preview(pj, {}, CAPS, resolve)["blocked"])


def test_production_respects_dependencies_and_freezes_prompts(pj, monkeypatch):
    publish(pj, plan_for(pj)); A.scan(pj)
    calls, entered, release = [], threading.Event(), threading.Event()
    def make_worker(project, pc, kind, llm_factory=None):
        assert llm_factory is None
        def worker(task, log, cancel):
            if task["key"] == "asset0":
                entered.set(); assert release.wait(4)
            else:
                assert R.valid_output(project.p("02_固定资产", "asset0.png"))
            calls.append((task["key"], A.inside(project.root, task["prompt_ref"]).read_text(encoding="utf-8")))
            image(str(A.inside(project.root, task["output"])))
            return {"output": task["output"]}
        return worker
    monkeypatch.setattr(R.produce, "make_image_worker", make_worker)
    jobs = JobManager()
    started = R.start(pj, {}, CAPS, resolve, jobs, expected_revision="r0001")
    assert entered.wait(3)
    A.select(pj, {"asset1": False})
    A.inside(pj.root, pj.tasks()["asset_tasks"][0]["prompt_ref"]).write_text("被改动", encoding="utf-8")
    assert not any(A.view(pj)["tasks"][i]["selected"] for i in range(2)), "交出去的这一批应当取消勾选"
    again = R.start(pj, {}, CAPS, resolve, jobs, only=["asset0"])
    assert again["ok"] and "另一批" in again["message"] and "job_id" not in again
    release.set();job=jobs.get(started["job_id"]);wait_job(job)
    assert calls == [("asset0", "生成测试图片"), ("asset1", "生成测试图片")]
    assert job.status == "done"
    assert read_json(pj.p("07_检查与记录", "jobs", job.id, "result.json"))["status"] == "done"
    assert "not-a-real-secret" not in json.dumps(read_json(pj.p("07_检查与记录", "jobs", job.id, "snapshot.json")))


def test_a_second_batch_runs_alongside_and_waits_only_for_its_own_input(pj, monkeypatch):
    """重新抽卡、补跑几项不用等整批结束；它要的参考图在另一批里生成时，等那一项做好再开跑。"""
    publish(pj, plan_for(pj)); A.scan(pj)
    calls, entered, release = [], threading.Event(), threading.Event()
    def make_worker(project, pc, kind, llm_factory=None):
        def worker(task, log, cancel):
            if task["key"] == "asset0":
                entered.set(); assert release.wait(6)
            else:
                assert R.valid_output(project.p("02_固定资产", "asset0.png"))
            calls.append(task["key"])
            image(str(A.inside(project.root, task["output"])))
            return {"output": task["output"]}
        return worker
    monkeypatch.setattr(R.produce, "make_image_worker", make_worker)
    jobs = JobManager()
    first = jobs.get(R.start(pj, {}, CAPS, resolve, jobs, only=["asset0"])["job_id"])
    assert entered.wait(3)
    plan = R.preview(pj, {}, CAPS, resolve, only=["asset1"], running=R.busy(jobs, pj.root))
    assert not plan["blocked"] and plan["todo"] == 1
    second = jobs.get(R.start(pj, {}, CAPS, resolve, jobs, only=["asset1"])["job_id"])
    until = time.monotonic() + 3
    while "等待上游" not in second.items["asset1"]["msg"] and time.monotonic() < until:
        time.sleep(.03)
    assert "asset0" in second.items["asset1"]["msg"] and "另一批" in second.items["asset1"]["msg"]
    assert calls == []
    release.set(); wait_job(first); wait_job(second)
    assert calls == ["asset0", "asset1"] and second.status == "done"


def test_video_seconds_chosen_on_the_video_row_is_what_the_provider_gets(pj):
    publish(pj, board_plan(pj)); A.scan(pj)
    saved = R.settings(pj, CAPS)
    saved.update(R.validate_settings({"video": dict(saved["video"], duration="30")}, CAPS))
    write_json(pj.p("07_检查与记录", "production-settings.json"), saved)
    video = next(t for t in R.preview(pj, {}, CAPS, resolve)["_tasks"] if t["key"] == "video")
    assert video["params"]["duration"] == 30
    saved["video"]["duration"] = 20
    write_json(pj.p("07_检查与记录", "production-settings.json"), saved)
    assert "视频那一行选的是 20 秒" in _video_reason(pj, {}, CAPS)
    with pytest.raises(ValueError):
        R.validate_settings({"video": dict(saved["video"], duration=0)}, CAPS)


def test_a_content_rejection_is_rewritten_by_the_llm_and_resent(pj, monkeypatch):
    """LLM 只做一件事：被审核拒绝后改写提示词再重发。"""
    from core.providers.base import Provider
    publish(pj, plan_for(pj, count=1)); A.scan(pj)
    sent = []

    class Rejecting(Provider):
        id = "chaomo"
        def generate_image(self, task, dest, **kw):
            sent.append(task.prompt)
            if len(sent) == 1:
                raise RuntimeError("content policy violation")
            image(dest)
            return {"provider": "chaomo", "model": "test"}

    class Chat:
        def chat(self, system, user, **kw):
            return "生成测试画面"

    monkeypatch.setattr(R.produce, "build_provider", lambda *a, **kw: Rejecting("k"))
    assert R.preview(pj, {}, CAPS, resolve)["soften_rounds"] == 12
    jobs = JobManager()
    job = jobs.get(R.start(pj, {}, CAPS, resolve, jobs, llm_factory=lambda: Chat())["job_id"])
    wait_job(job)
    assert job.status == "done", job.snapshot()
    assert sent == ["生成测试图片", "生成测试画面"]
    assert list(Path(pj.p("03_提示词", "自动改写")).glob("asset0_第1版.txt"))
    assert not A.view(pj)["tasks"][0]["selected"]
    sent.clear()
    Path(pj.p("02_固定资产", "asset0.png")).unlink()
    job = jobs.get(R.start(pj, {"defaults": {"soften_rounds": 0, "max_retry": 0}}, CAPS, resolve, jobs,
                           only=["asset0"], llm_factory=lambda: Chat())["job_id"])
    wait_job(job)
    assert job.status == "error" and sent == ["生成测试图片"], "轮数 0 时不改写"


def test_video_clarity_chosen_on_the_row_is_what_the_provider_gets(pj):
    publish(pj, board_plan(pj)); A.scan(pj)
    caps = copy.deepcopy(CAPS); caps[0]["video"]["resolutions"] = ["720p", "1080p"]
    saved = R.settings(pj, caps)
    saved.update(R.validate_settings({"video": dict(saved["video"], resolution="1080p")}, caps))
    write_json(pj.p("07_检查与记录", "production-settings.json"), saved)
    video = next(t for t in R.preview(pj, {}, caps, resolve)["_tasks"] if t["key"] == "video")
    assert video["params"]["resolution"] == "1080p"
    saved["video"]["resolution"] = "4k"
    write_json(pj.p("07_检查与记录", "production-settings.json"), saved)
    assert "清晰度 4k 不在这个视频模型的能力里" in _video_reason(pj, {}, caps)


def test_llm_settings_are_accepted_but_nothing_else(tmp_path, monkeypatch):
    cfg = {"projects_dir": str(tmp_path), "providers": {}, "limits": {"global": 8, "per_provider": {}}}
    monkeypatch.setattr(app, "load_config", lambda: copy.deepcopy(cfg))
    monkeypatch.setattr(app, "caps_of", lambda cfg=None: copy.deepcopy(CAPS))
    saved = []
    monkeypatch.setattr(app, "api_post", lambda path, body: saved.append(body) or {"ok": True})
    agent_api.post(app, "/api/agent/settings", {"llm": {"base_url": " https://x.test ", "api_key": "", "model": "m"},
                                                "defaults": {"soften_rounds": 0}})
    assert saved[-1] == {"llm": {"base_url": "https://x.test", "api_key": "", "model": "m"},
                         "defaults": {"soften_rounds": 0}}
    for bad in ({"llm": {"base_url": "x", "stream": True}}, {"defaults": {"soften_rounds": -1}}):
        with pytest.raises(ValueError):
            agent_api.post(app, "/api/agent/settings", bad)


def test_ratio_chosen_on_the_row_is_what_the_provider_gets(pj):
    """比例放在生产页服务商那一行：选了就按选的发，不再依赖每条任务里写的值。"""
    publish(pj, board_plan(pj)); A.scan(pj)
    caps = copy.deepcopy(CAPS)
    caps[0]["image"]["sizes"] = ["16:9", "9:16", "3:2"]; caps[0]["video"]["ratios"] = ["16:9", "9:16"]
    saved = R.settings(pj, caps)
    saved.update(R.validate_settings({"asset": dict(saved["asset"], size="3:2"),
                                      "video": dict(saved["video"], ratio="9:16")}, caps))
    write_json(pj.p("07_检查与记录", "production-settings.json"), saved)
    tasks = {t["key"]: t for t in R.preview(pj, {}, caps, resolve)["_tasks"]}
    assert tasks["asset0"]["params"]["size"] == "3:2" and tasks["video"]["params"]["ratio"] == "9:16"
    assert tasks["board"]["params"]["size"] == "16:9"
    saved["asset"]["size"] = "1:1"
    write_json(pj.p("07_检查与记录", "production-settings.json"), saved)
    reason = next(b["reason"] for b in R.preview(pj, {}, caps, resolve)["blocked"] if b["key"] == "asset0")
    assert "这一行选的 1:1" in reason and "这一行的比例" in reason
    with pytest.raises(ValueError):
        R.validate_settings({"asset": dict(saved["asset"], size="16:9;rm")}, caps)


def test_plan_and_job_show_the_retry_limit(pj, monkeypatch):
    publish(pj, plan_for(pj)); A.scan(pj)
    assert R.preview(pj, {"defaults": {"max_retry": 4}}, CAPS, resolve)["max_retry"] == 4
    def maker(*args):
        def worker(t, *rest):
            image(str(A.inside(pj.root, t["output"]))); return {"output": t["output"]}
        return worker
    monkeypatch.setattr(R.produce, "make_image_worker", maker)
    jobs = JobManager(); job = jobs.get(R.start(pj, {"defaults": {"max_retry": 9}}, CAPS, resolve, jobs)["job_id"])
    wait_job(job)
    assert job.snapshot()["max_retry"] == 9
    assert R.validate_settings({"asset": dict(R.settings(pj, CAPS)["asset"], concurrency=2000)}, CAPS)["asset"]["concurrency"] == 2000
    with pytest.raises(ValueError):
        R.validate_settings({"asset": dict(R.settings(pj, CAPS)["asset"], concurrency=0)}, CAPS)


def test_retry_and_concurrency_settings_have_no_upper_cap(tmp_path, monkeypatch):
    cfg = {"projects_dir": str(tmp_path), "providers": {}, "limits": {"global": 8, "per_provider": {}}}
    monkeypatch.setattr(app, "load_config", lambda: copy.deepcopy(cfg))
    monkeypatch.setattr(app, "caps_of", lambda cfg=None: copy.deepcopy(CAPS))
    saved = []
    monkeypatch.setattr(app, "api_post", lambda path, body: saved.append(body) or {"ok": True})
    agent_api.post(app, "/api/agent/settings", {"limits": {"global": 5000, "per_provider": {"chaomo": 3000}},
                                                "defaults": {"max_retry": 50}})
    assert saved[-1]["defaults"] == {"max_retry": 50} and saved[-1]["limits"]["global"] == 5000
    assert R.retries({"defaults": {"max_retry": 50}}) == 50
    for bad in ({"defaults": {"max_retry": -1}}, {"limits": {"global": 0, "per_provider": {}}}):
        with pytest.raises(ValueError):
            agent_api.post(app, "/api/agent/settings", bad)


def test_delete_moves_files_to_recycle_bin_with_the_chosen_chains(pj):
    from core import recycle
    publish(pj, plan_for(pj, count=3)); A.scan(pj)
    for i in range(3):
        image(pj.p("02_固定资产", f"asset{i}.png"))
    rel = lambda **kw: {x["key"]: x["relation"] for x in recycle.plan(pj, "asset1", **kw)["items"]}
    assert rel() == {"asset1": "本身"}
    assert rel(siblings=True) == {"asset1": "本身", "asset2": "兄弟"}
    assert rel(up=True, down=False) == {"asset1": "本身", "asset0": "上游"}
    assert rel(up=True) == {"asset1": "本身", "asset0": "上游", "asset2": "下游"}
    assert {x["key"] for x in recycle.plan(pj, "asset0")["items"]} == {"asset0", "asset1", "asset2"}
    with pytest.raises(ValueError, match="正在生成"):
        recycle.delete(pj, ["asset0"], running={"asset0": object()})
    d = recycle.delete(pj, ["asset0", "asset1"])
    assert [x["key"] for x in d["moved"]] == ["asset0", "asset1"]
    assert not Path(pj.p("02_固定资产", "asset0.png")).exists()
    for x in d["moved"]:
        assert A.inside(pj.root, x["saved_as"]).is_file() and x["saved_as"].startswith("07_检查与记录/回收站/")
    assert read_json(str(A.inside(pj.root, d["folder"] + "/删除清单.json")))["items"] == d["moved"]
    rows = {t["key"]: t for t in A.view(pj)["tasks"]}
    assert set(rows) == {"asset0", "asset1", "asset2"}
    assert R.preview(pj, {}, CAPS, resolve, only=["asset0", "asset1"])["todo"] == 2


def test_file_route_serves_byte_ranges_so_videos_can_seek(tmp_path, monkeypatch):
    from urllib.parse import urlencode
    project = A.create_project(str(tmp_path), {"title": "拖动进度", "source": "x"}, CAPS)
    data = bytes(range(256)) * 40
    Path(project.p("05_分段视频", "a.mp4")).write_bytes(data)
    cfg = {"projects_dir": str(tmp_path), "providers": {}, "limits": {"global": 8, "per_provider": {}}}
    monkeypatch.setattr(app, "load_config", lambda: copy.deepcopy(cfg))
    srv = app.ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
    # Range behavior is tested after authorization; auth boundaries have their own HTTP tests.
    from types import SimpleNamespace
    srv.studio_login = SimpleNamespace(require=lambda *a, **k: None)
    thread = threading.Thread(target=srv.serve_forever, daemon=True); thread.start()
    url = f"http://127.0.0.1:{srv.server_port}/file?" + urlencode({"root": project.root, "rel": "05_分段视频/a.mp4"})
    try:
        with urlopen(Request(url, headers={"Range": "bytes=100-199"})) as r:
            assert r.status == 206 and r.headers["Content-Range"] == f"bytes 100-199/{len(data)}"
            assert r.read() == data[100:200]
        with urlopen(Request(url, headers={"Range": "bytes=-10"})) as r:
            assert r.read() == data[-10:]
        with urlopen(url) as r:
            assert r.status == 200 and r.headers["Accept-Ranges"] == "bytes" and r.read() == data
        with pytest.raises(HTTPError) as e:
            urlopen(Request(url, headers={"Range": f"bytes={len(data)}-"}))
        assert e.value.code == 416
    finally:
        srv.shutdown(); srv.server_close(); thread.join(3)


def test_failure_blocks_downstream_without_request(pj, monkeypatch):
    publish(pj, plan_for(pj)); A.scan(pj)
    calls=[]
    def maker(*args):
        def worker(t, *rest):
            calls.append(t["key"])
            raise ValueError("模拟生成失败")
        return worker
    monkeypatch.setattr(R.produce,"make_image_worker",maker)
    jobs=JobManager();d=R.start(pj,{"defaults":{"max_retry":0}},CAPS,resolve,jobs);j=jobs.get(d["job_id"]);wait_job(j)
    assert calls == ["asset0"] and j.status == "error"
    assert j.items["asset1"]["state"] == "failed"


def test_legacy_projects_keep_existing_task_files(tmp_path):
    pj=Project(str(tmp_path));pj.init_dirs();pj.save_meta({"title":"旧项目","system":"v34"})
    data={"storyboard_tasks":[{"key":"old","prompt_ref":"03_提示词/old.txt","output":"04_故事板/old.png"}]}
    pj.save_tasks(data)
    view=A.view(pj)
    assert view["publication"]["legacy"] and view["tasks"][0]["kind"] == "storyboard"
    assert pj.tasks() == data


def test_lowering_global_limit_waits_for_existing_tasks():
    gate=Gate(2,{"p":2});both=threading.Barrier(3);release=threading.Event();third=threading.Event();errors=[]
    def old():
        try:
            with gate.slot("p"):
                both.wait(timeout=3);release.wait(3)
        except Exception as e: errors.append(e)
    threads=[threading.Thread(target=old) for _ in range(2)]
    for t in threads:t.start()
    both.wait(timeout=3);gate.configure(1,{"p":1})
    def new():
        with gate.slot("p"):third.set()
    thread=threading.Thread(target=new);thread.start()
    assert not third.wait(.1)
    release.set()
    for t in threads+[thread]:t.join(3)
    assert third.is_set() and not errors and gate.snapshot()["global_inflight"]==0


def test_account_quota_survives_config_refresh():
    gate=Gate(8,{"p":6});gate.set_provider_limit("p",2);gate.configure(8,{"p":6})
    assert gate.snapshot()["per_provider_limit"]["p"]==2
    gate.configure(8,{"p":1})
    assert gate.snapshot()["per_provider_limit"]["p"]==1


def test_http_has_agent_entry_and_no_legacy_text_endpoints(tmp_path, monkeypatch):
    cfg={"projects_dir":str(tmp_path),"providers":{},"limits":{"global":8,"per_provider":{}}}
    monkeypatch.setattr(app,"load_config",lambda:copy.deepcopy(cfg))
    monkeypatch.setattr(app,"caps_of",lambda cfg=None:copy.deepcopy(CAPS))
    srv=app.ThreadingHTTPServer(("127.0.0.1",0),app.Handler)
    from types import SimpleNamespace
    srv.studio_login=SimpleNamespace(require=lambda *a,**k:None)
    thread=threading.Thread(target=srv.serve_forever,daemon=True);thread.start()
    url=f"http://127.0.0.1:{srv.server_port}"
    try:
        with urlopen(url) as r: assert b'agent.js' in r.read()
        with urlopen(url+"/output-preview.js") as r:
            assert b'studio-file-preview' in r.read()
        with urlopen(url+"/api/agent/bootstrap") as r:
            data=json.load(r)
            assert set(data["llm"]) == {"base_url", "model", "api_key_set", "ready", "rounds"} and data["projects"]==[]
            assert not any("llm" in c.get("supports", []) for c in data["capabilities"])
        with pytest.raises(HTTPError) as e:
            urlopen(Request(url+"/api/stage/run",data=b'{}',headers={"Content-Type":"application/json"}))
        assert e.value.code == 410
    finally:
        srv.shutdown();srv.server_close();thread.join(3)


def board_plan(pj):
    p = plan_for(pj, count=1)
    def add(kind, key, semantic, output, refs, **extra):
        p["resources"].append({"id":key,"name":key,"semantic":semantic,"parent_id":None,"episodes":["EP01"],"task_key":key})
        p[kind+"_tasks"].append({"key":key,"output":output,"prompt_ref":f"03_提示词/releases/r0001/images/{key}.txt",
                                 "params":{"ratio":"16:9","duration":10} if kind=="video" else {"size":"16:9"},
                                 "reference_images":refs,**extra})
    add("board","board","ABC","04_交接板/board.png",[{"asset_id":"asset0","file_ref":"02_固定资产/asset0.png","image_n":1}])
    for name in ("B","C"):
        add("region","region"+name,"REGION",f"04_交接板/区域图/{name}.png",
            [{"asset_id":"board","file_ref":"04_交接板/board.png","image_n":1}],board="board",region=name)
    handoff=[{"asset_id":"region"+name,"file_ref":f"04_交接板/区域图/{name}.png","image_n":i+1,"board":"board","role":"IN_"+name} for i,name in enumerate(("B","C"))]
    add("video","video","VIDEO","05_分段视频/video.mp4",[{"asset_id":"asset0","file_ref":"02_固定资产/asset0.png","image_n":3}],handoff_refs=handoff,episode="EP01")
    return p


def test_handoff_upload_order_and_common_source_are_validated(pj):
    plan=board_plan(pj);publish(pj,plan)
    assert not A.scan(pj)["errors"]
    # A mismatched board declaration is a real contract error even with all files present.
    plan["video_tasks"][0]["handoff_refs"][1]["board"]="another-board"
    publish(pj,plan)
    with pytest.raises(ValueError,match="同源整板"):
        A.validate_release(pj,"r0001")


def test_region_cannot_add_extra_reference_to_its_board(pj):
    plan=board_plan(pj)
    plan["region_tasks"][0]["reference_images"].append({"asset_id":"asset0","file_ref":"02_固定资产/asset0.png","image_n":2})
    publish(pj,plan)
    assert "仅引用指定整板" in A.scan(pj)["errors"][0]["message"]


def test_video_without_episode_cannot_mix_different_episodes_in_master(pj):
    plan=board_plan(pj);plan["video_tasks"][0].pop("episode")
    publish(pj,plan)
    assert "指定唯一的集" in A.scan(pj)["errors"][0]["message"]


def test_same_aspect_conversion_allowed_but_nearest_shape_blocked(pj):
    publish(pj,plan_for(pj));A.scan(pj)
    caps=copy.deepcopy(CAPS);caps[0]["image"]["sizes"]=["1536x1024"]
    assert all("不兼容" in e["reason"] for e in R.preview(pj,{},caps,resolve)["blocked"])
    caps[0]["image"]["sizes"]=["1920x1080"]
    assert not R.preview(pj,{},caps,resolve)["blocked"]


def test_cancelling_queued_job_does_not_wait_for_unrelated_provider():
    gate=Gate(1);cancel=threading.Event();finished=threading.Event();result=[]
    def queued():
        with gate.slot("b",cancel=cancel.is_set) as acquired:
            result.append(acquired)
        finished.set()
    with gate.slot("a"):
        t=threading.Thread(target=queued);t.start();cancel.set()
        assert finished.wait(2)
        assert gate.snapshot()["global_inflight"]==1
    t.join(2);assert result==[False]




def test_packaged_agent_resources_are_complete():
    from core.packagecheck import _check_agent_assets
    assert "51" in _check_agent_assets()


def _video_reason(pj, cfg, caps):
    return next(b["reason"] for b in R.preview(pj, cfg, caps, resolve)["blocked"] if b["key"] == "video")


def test_duration_block_says_which_model_where_from_and_what_to_do(pj):
    plan = board_plan(pj);plan["video_tasks"][0]["params"]["duration"] = 30
    publish(pj, plan);A.scan(pj)
    caps = copy.deepcopy(CAPS);caps[0]["video"]["durations"] = list(range(4, 13))
    reason = _video_reason(pj, {}, caps)
    assert "要 30 秒" in reason and "「图片服务商 / test-video」" in reason
    assert "4–12 秒" in reason and "服务商的模型能力声明" in reason and "重新切段" in reason
    reason = _video_reason(pj, {"providers": {"chaomo": {"durations": "test-video=5,10"}}}, caps)
    assert "5、10 秒" in reason and "手填" in reason


PLUGIN = '''from core.providers.base import Provider
class QaImport(Provider):
    id = "qa_import"
    name = "导入测试"
    supports = ("video",)
    def capabilities(self):
        return {"id": self.id, "name": self.name, "supports": ["video"], "video": {"models": ["m"]}}
    def generate_video(self, task, dest, **kw):
        raise RuntimeError("qa only")
'''


@pytest.fixture
def plugin_home(tmp_path):
    from core import paths, providers
    before = paths._forced["data"]
    paths.set_data_dir(str(tmp_path / "data"))
    try:
        yield Path(paths.plugins_dir())
    finally:
        paths._forced["data"] = before
        providers.reload_all()


def _upload(name, text):
    import base64
    return agent_api.import_provider(app, {"filename": name, "content_b64": base64.b64encode(text.encode()).decode()})


def test_provider_file_import_loads_it_and_backs_up_same_name(plugin_home):
    from core import providers
    d = _upload("qa_import.py", PLUGIN)
    assert d["ok"] and [p["name"] for p in d["providers"]] == ["导入测试"] and not d["backup"]
    assert "qa_import" in providers.REGISTRY
    d = _upload("qa_import.py", PLUGIN.replace("导入测试", "导入测试二"))
    assert d["ok"] and d["backup"] and Path(d["backup"]).read_text(encoding="utf-8") == PLUGIN
    assert providers.REGISTRY["qa_import"].name == "导入测试二"


def test_broken_provider_file_restores_previous_one(plugin_home):
    from core import providers
    assert _upload("qa_import.py", PLUGIN)["ok"]
    d = _upload("qa_import.py", "import not_a_module_here\n")
    assert not d["ok"] and d["restored"] and d["errors"]
    assert (plugin_home / "qa_import.py").read_text(encoding="utf-8") == PLUGIN
    assert providers.REGISTRY["qa_import"].name == "导入测试"
    d = _upload("fresh_broken.py", "import not_a_module_here\n")
    assert not d["ok"] and not d["restored"] and not (plugin_home / "fresh_broken.py").exists()


@pytest.mark.parametrize("name", ["_hidden.py", "a.txt", "../x.py", "sub/x.py", ".x.py"])
def test_provider_import_rejects_bad_names(plugin_home, name):
    with pytest.raises(ValueError):
        _upload(name, PLUGIN)
