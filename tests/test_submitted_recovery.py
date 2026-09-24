"""已提交、没取回的任务：重跑续查，不重新付费；服务商给了结论才重新提交。"""
from pathlib import Path
import time

import pytest

from core import accounts, apiutil, produce, submitted
from core.apiutil import ApiError, RETRYABLE, extract_video_url
from core.providers.base import Provider, VideoTask
from core.store import Project, write_text
from test_handoff_production import material

MP4 = b"\x00\x00\x00\x18ftypisom" + bytes(4096)
_sleep = time.sleep


class SlowProvider(Provider):
    """提交返回任务号，查询走公共轮询；state 决定查询时服务商怎么回。"""
    id = name = "slow"

    def __init__(self):
        super().__init__("k", "https://example.invalid")
        self.posts, self.state, self.saved = 0, "processing", []
        self.session.request = self._request
        self.session.save_item = self._save

    def _request(self, method, path, **kw):
        if method == "POST":
            self.posts += 1
            return {"id": f"task-{self.posts}"}
        if self.state == "gone":
            raise ApiError("HTTP 404: not found", 404, "task_fatal")
        return {"processing": {"status": "processing"},
                "failed": {"status": "failed", "error": {"message": "审核未过"}},
                "completed": {"status": "completed", "video_url": "https://cdn.invalid/v.mp4"}}[self.state]

    def _save(self, item, dest, retries=3, *, log=None):
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(MP4)
        self.saved.append(item)

    def generate_video(self, task, dest, *, log=print, cancel=None, poll_interval=1, poll_timeout=1):
        data = self.session.request("POST", "/v1/videos")
        url = self.session.poll("/v1/videos/{id}", data["id"], picker=extract_video_url,
                                interval=poll_interval, timeout=poll_timeout, log=log, cancel=cancel)
        self.session.save_item(url, dest)
        return {"task_id": data["id"]}


@pytest.fixture
def env(tmp_path, monkeypatch):
    pj = Project(str(tmp_path))
    pj.init_dirs()
    prov = SlowProvider()
    monkeypatch.setattr(produce, "build_provider", lambda *a, **k: prov)
    monkeypatch.setattr(produce, "_ratio_warn", lambda *a, **k: None)
    monkeypatch.setattr(apiutil.time, "sleep", lambda s: _sleep(0.01))
    task = material(pj)["video_tasks"][0]
    return pj, prov, task


def run(pj, task):
    logs = []
    worker = produce.make_video_worker(pj, {"provider": "slow", "api_key": "k", "model": "m",
                                            "poll_interval": 1, "poll_timeout": 1})
    return worker(task, logs.append, lambda: False), logs


def test_poll_timeout_then_rerun_resumes_the_same_task_without_paying_again(env):
    pj, prov, task = env
    with pytest.raises(ApiError) as caught:
        run(pj, task)
    assert caught.value.kind == RETRYABLE and prov.posts == 1
    rec = submitted.load(pj.root, task["output"])
    assert rec["task_id"] == "task-1" and rec["resume"]["picker"] == "extract_video_url"
    assert set(rec) == {"output", "key", "provider", "model", "identity", "task_id", "resume", "submitted_at"}
    prov.state = "completed"
    result, logs = run(pj, task)
    assert result["output"] == task["output"] and prov.posts == 1 and prov.saved
    assert any("继续查询这个任务，不重新提交" in line for line in logs)
    assert submitted.load(pj.root, task["output"]) is None
    assert pj.registry("video")[0]["resumed"] is True


@pytest.mark.parametrize("state", ["failed", "gone"])
def test_provider_verdict_drops_the_task_so_next_run_submits(env, state):
    pj, prov, task = env
    with pytest.raises(ApiError):
        run(pj, task)
    prov.state = state
    with pytest.raises(ApiError) as caught:
        run(pj, task)
    assert submitted.finished(caught.value) and prov.posts == 1
    assert submitted.load(pj.root, task["output"]) is None
    prov.state = "completed"
    run(pj, task)
    assert prov.posts == 2


def test_changed_prompt_abandons_the_old_task_and_says_why(env):
    pj, prov, task = env
    with pytest.raises(ApiError):
        run(pj, task)
    write_text(pj.p(*task["prompt_ref"].split("/")),
               "Image 1 = P__SCSTATE_FOREST\nImage 2 = P__CHAR_001\n改成奔跑")
    prov.state = "completed"
    _, logs = run(pj, task)
    assert prov.posts == 2 and any("task-1" in line and "改过" in line for line in logs)


def test_existing_output_clears_a_stale_record(env):
    pj, prov, task = env
    with pytest.raises(ApiError):
        run(pj, task)
    Path(pj.p(*task["output"].split("/"))).write_bytes(MP4)
    result, _ = run(pj, task)
    assert result["skipped"] and submitted.load(pj.root, task["output"]) is None


def test_poll_outside_production_records_nothing(tmp_path):
    prov = SlowProvider()
    prov.state = "completed"
    prov.session.poll("/v1/videos/{id}", "t", picker=extract_video_url, interval=0, timeout=1)
    assert not (tmp_path / "07_检查与记录").exists()


def test_unknown_parser_is_refused_with_a_way_out(tmp_path):
    rec = {"task_id": "t9", "resume": {"path": "/x/{id}", "picker": "pick"}}
    with pytest.raises(ApiError) as caught:
        SlowProvider().resume(rec, str(tmp_path / "v.mp4"))
    assert caught.value.err_code == "resume_unsupported" and "放弃" in str(caught.value)


def test_pool_slot_can_wait_for_the_account_that_submitted():
    pool = accounts._Pool("x", [accounts.Account("a", "账号1"), accounts.Account("b", "账号2")])
    with pool.slot(prefer="账号2") as acct:
        assert acct.label == "账号2"
    assert pool.labels() == ["账号1", "账号2"]


def _tracked(tmp_path, fn):
    with submitted.tracking(str(tmp_path), "05_分段视频/x.mp4", key="x", identity="i"):
        return fn()


def test_julun_remembers_and_resumes_with_its_own_poll(tmp_path, monkeypatch):
    from core.providers.julun import JulunProvider
    p = JulunProvider("k")
    monkeypatch.setattr(p.session, "request", lambda *a, **k: {"task_id": "jl-1"})
    monkeypatch.setattr(p, "_poll", lambda *a, **k: (_ for _ in ()).throw(
        ApiError("巨轮任务超时", kind="retryable")))
    with pytest.raises(ApiError):
        _tracked(tmp_path, lambda: p.generate_video(VideoTask("走路", model="sd2.5"),
                                                    str(tmp_path / "v.mp4"), log=lambda _: None))
    rec = submitted.load(str(tmp_path), "05_分段视频/x.mp4")
    assert rec["task_id"] == "jl-1" and rec["resume"]["custom"] == "julun"
    saved = []
    monkeypatch.setattr(p, "_poll", lambda *a, **k: "https://cdn.invalid/j.mp4")
    monkeypatch.setattr(p.session, "request", lambda *a, **k: pytest.fail("续查不能再提交"))
    monkeypatch.setattr(p.session, "save_item", lambda url, dest, **k: saved.append(url))
    assert p.resume(rec, str(tmp_path / "v.mp4"))["resumed"] and saved == ["https://cdn.invalid/j.mp4"]


def test_gate_failure_is_a_verdict_and_resume_keeps_model(tmp_path, monkeypatch):
    from core.providers.gate import GateProvider
    p = GateProvider("k")
    monkeypatch.setattr(p.session, "request", lambda *a, **k: {"status": "failed", "error": "x"})
    with pytest.raises(ApiError) as caught:
        p._poll_result("seedance-2.5", "g-1", 0, 5, log=lambda _: None)
    assert submitted.finished(caught.value)
    seen = {}
    monkeypatch.setattr(p, "_poll_result", lambda model, tid, *a, **k: seen.update(model=model, tid=tid) or "https://cdn.invalid/g.mp4")
    monkeypatch.setattr(p.session, "save_item", lambda *a, **k: None)
    p.resume({"task_id": "g-1", "resume": {"custom": "gate", "model": "seedance-2.5"}}, str(tmp_path / "v.mp4"))
    assert seen == {"model": "seedance-2.5", "tid": "g-1"}


class _Resp:
    def __init__(self, status, body=b'{"id":"t1"}'):
        self.status_code, self.content, self.encoding = status, body, "utf-8"
        self.text, self.headers = body.decode(), {}

    def json(self):
        return __import__("json").loads(self.content)


def _session(monkeypatch, outcomes):
    """真实的 HttpSession.request；requests.request 依次给出 outcomes（异常或状态码）。"""
    import requests
    calls = []

    def fake(method, url, **kw):
        calls.append(method)
        got = outcomes[min(len(calls), len(outcomes)) - 1]
        if isinstance(got, Exception):
            raise got
        return _Resp(got)

    monkeypatch.setattr(apiutil.requests, "request", fake)
    monkeypatch.setattr(apiutil.time, "sleep", lambda s: None)
    return apiutil.HttpSession("k", "https://example.invalid"), calls, requests


@pytest.mark.parametrize("failure", ["read_timeout", "disconnected", "gateway"])
def test_uncertain_submission_is_not_resent(tmp_path, monkeypatch, failure):
    import requests, urllib3
    outcome = {"read_timeout": requests.ReadTimeout("read timed out"),
               "disconnected": requests.ConnectionError(urllib3.exceptions.ProtocolError(
                   "Connection aborted.", ConnectionResetError())),
               "gateway": 502}[failure]
    s, calls, _ = _session(monkeypatch, [outcome, 200])
    with pytest.raises(ApiError) as caught:
        _tracked(tmp_path, lambda: s.request("POST", "/v1/videos", json_body={}))
    assert calls == ["POST"] and caught.value.err_code == "submit_uncertain"
    assert caught.value.kind == apiutil.TASK_FATAL
    from core import diagnose
    d = diagnose.build(caught.value)
    assert d["code"] == "SUBMIT_UNCERTAIN" and not diagnose.should_failover(d)


@pytest.mark.parametrize("first", ["never_connected", 429])
def test_submission_that_never_reached_the_provider_is_retried(tmp_path, monkeypatch, first):
    import requests
    outcome = requests.ConnectionError("Failed to establish a new connection: [WinError 10061]") \
        if first == "never_connected" else first
    s, calls, _ = _session(monkeypatch, [outcome, 200])
    assert _tracked(tmp_path, lambda: s.request("POST", "/v1/videos", json_body={})) == {"id": "t1"}
    assert calls == ["POST", "POST"]


def test_only_the_submission_itself_is_protected(tmp_path, monkeypatch):
    import requests
    s, calls, _ = _session(monkeypatch, [requests.ReadTimeout("x"), 200])
    assert s.request("POST", "/v1/videos") == {"id": "t1"}            # 生产以外照旧重试
    calls.clear()
    assert _tracked(tmp_path, lambda: s.request("POST", "/upload", submit=False)) == {"id": "t1"}
    calls.clear()

    def after_task_id():
        submitted.remember("t1", path="/x/{id}", picker="extract_video_url")
        polled = s.request("POST", "/get_result")                       # Gate 用 POST 查询
        calls.clear()
        submitted.new_attempt()                                           # 改写提示词后再提交
        with pytest.raises(ApiError):
            s.request("POST", "/v1/videos")
        return polled
    assert _tracked(tmp_path, after_task_id) == {"id": "t1"} and calls == ["POST"]


def test_agent_view_shows_pending_task_and_it_can_be_dropped(tmp_path, monkeypatch):
    import copy, os
    from core import agent_projects as A
    from server import agent_api, app
    from test_agent_workspace import CAPS, plan_for, publish
    pj = A.create_project(str(tmp_path), {"title": "续查", "source": "原文", "options": {
        "image_provider": "chaomo", "image_model": "test"}}, CAPS)
    publish(pj, plan_for(pj));A.scan(pj)
    out = A.task_rows(pj.tasks())[0]["output"]
    with submitted.tracking(pj.root, out, key="asset0", provider="chaomo", identity="i"):
        submitted.remember("img-7", path="/v1/images/{id}", picker="pick")
    row = next(t for t in A.view(pj)["tasks"] if t["output"] == out)
    assert row["submitted"]["task_id"] == "img-7"
    cfg = {"projects_dir": str(tmp_path), "providers": {}, "limits": {"global": 8, "per_provider": {}}}
    monkeypatch.setattr(app, "load_config", lambda: copy.deepcopy(cfg))
    monkeypatch.setattr(app, "caps_of", lambda cfg=None: copy.deepcopy(CAPS))
    assert agent_api.post(app, "/api/agent/submitted/clear", {"root": pj.root, "key": row["key"]})["ok"]
    assert next(t for t in A.view(pj)["tasks"] if t["output"] == out)["submitted"] is None
