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
    with pytest.raises(ValueError, match="已有运行任务"):
        R.start(pj, {}, CAPS, resolve, jobs)
    release.set();job=jobs.get(started["job_id"]);wait_job(job)
    assert calls == [("asset0", "生成测试图片"), ("asset1", "生成测试图片")]
    assert job.status == "done"
    assert read_json(pj.p("07_检查与记录", "jobs", job.id, "result.json"))["status"] == "done"
    assert "not-a-real-secret" not in json.dumps(read_json(pj.p("07_检查与记录", "jobs", job.id, "snapshot.json")))


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
    thread=threading.Thread(target=srv.serve_forever,daemon=True);thread.start()
    url=f"http://127.0.0.1:{srv.server_port}"
    try:
        with urlopen(url) as r: assert b'agent.js' in r.read()
        with urlopen(url+"/api/agent/bootstrap") as r:
            data=json.load(r)
            assert "llm" not in data and data["projects"]==[]
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


def test_subtitles_never_inherit_old_text_model_configuration(pj,monkeypatch):
    from core import agent_post as P
    meta=pj.meta();meta["subtitle"]={"optimize":True,"translate":True};pj.save_meta(meta)
    master=Path(pj.p("06_成片","EP01_MASTER.mp4"));master.write_bytes(b"\x00\x00\x00\x18ftypisom"+b"\x00"*8192)
    monkeypatch.setattr(P.subtitle,"find_cli",lambda:["fake-caption"])
    monkeypatch.setattr(P.captions,"install",lambda:None)
    monkeypatch.setattr(P.captions,"ensure_ffmpeg",lambda:None)
    cmds=[]
    class Proc:
        returncode=0
        def __init__(self,cmd,**kw):
            cmds.append(cmd)
            dest=Path(cmd[cmd.index("-o")+1])
            if "transcribe" in cmd:dest.write_text("1\n00:00:00,000 --> 00:00:01,000\n对白\n",encoding="utf-8")
            else:dest.write_bytes(master.read_bytes())
        def poll(self):return 0
    monkeypatch.setattr(P.subprocess,"Popen",Proc)
    jobs=JobManager();d=P.run(pj,{"file":"06_成片/EP01_MASTER.mp4"},jobs);j=jobs.get(d["job_id"]);wait_job(j)
    assert j.status=="done",j.snapshot()
    cfg=Path(pj.p("07_检查与记录","jobs",j.id,"caption.toml")).read_text(encoding="utf-8")
    import tomllib
    parsed = tomllib.loads(cfg)
    assert parsed["subtitle"]["optimize"] is False and parsed["subtitle"]["translate"] is False
    assert not parsed.get("llm", {}).get("api_key")
    assert pj.meta()["subtitle"]["optimize"] is True
    assert len(cmds)==2 and P.options(pj)["asr"]=="bijian"
    assert master.with_name("EP01_MASTER_SUB.mp4").exists()


def test_packaged_agent_resources_are_complete():
    from core.packagecheck import _check_agent_assets
    assert "51" in _check_agent_assets()
