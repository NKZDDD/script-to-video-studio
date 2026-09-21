"""Julun's live video endpoint returns both flat and data-wrapped task results."""
import json
import sys

import pytest

from core.apiutil import ApiError, RETRYABLE, TASK_FATAL
from core.executor import Job, run_batch
from core.providers.base import VideoTask
from core.providers.julun import JulunProvider
from provider_patches.julun_result_hotfix import JulunResultHotfix


@pytest.fixture(autouse=True, params=[JulunProvider, JulunResultHotfix], ids=["builtin", "hotfix"])
def implementation(request, monkeypatch):
    monkeypatch.setattr(sys.modules[__name__], "JulunProvider", request.param)


GENERATION_ERROR = {"message": "video generation failed", "code": "video_generation_failed"}
PORTRAIT_ERROR = {
    "message": "模型没有派发生成工具（ai_creation_res_code=710082031）："
               "疑似**肖像权保护**拒绝（上游要求生成「你自己的形象」）。"
               "再等也不会有产出。 上游原话：For likeness protection, ",
    "code": "task_failed",
}


def envelope(result, wrapped):
    return {"code": "success", "data": result} if wrapped else result


def provider(monkeypatch, responses):
    p = JulunProvider()
    calls, saved = [], []
    replies = iter(responses)

    def request(method, path, **kwargs):
        calls.append((method, path))
        if method == "POST":
            return {"task_id": "task-test"}
        return next(replies)  # An extra poll fails immediately, without waiting for a timeout.

    monkeypatch.setattr(p.session, "request", request)
    monkeypatch.setattr(p.session, "save_item", lambda url, dest, **kw: saved.append(url))
    return p, calls, saved


def generate(p, log=lambda _: None):
    return p.generate_video(VideoTask(prompt="离线测试", model="sd2.5", duration=30),
                            "unused.mp4", log=log, poll_interval=0, poll_timeout=3)


@pytest.mark.parametrize("wrapped", [False, True])
@pytest.mark.parametrize("status", ["completed", "SUCCESS"])
def test_completed_at_95_percent_downloads_once(monkeypatch, wrapped, status):
    url = "https://julun.cc/v1/videos/task-test/content?signature=fixture"
    p, calls, saved = provider(monkeypatch, [envelope({"status": status, "progress": 95,
                                                     "result_url": url}, wrapped)])
    result = generate(p)
    assert result["source"] == url and saved == [url]
    assert calls == [("POST", "/v1/videos"), ("GET", "/v1/videos/task-test")]


@pytest.mark.parametrize("wrapped", [False, True])
def test_running_does_not_download_early(monkeypatch, wrapped):
    url = "https://cdn.example/video.mp4"
    p, calls, saved = provider(monkeypatch, [
        envelope({"status": "IN_PROGRESS", "result_url": url}, wrapped),
        envelope({"status": "completed", "result_url": url}, wrapped),
    ])
    generate(p)
    assert len(calls) == 3 and saved == [url]


@pytest.mark.parametrize("wrapped", [False, True])
def test_completed_without_url_uses_content_endpoint(monkeypatch, wrapped):
    p, _, saved = provider(monkeypatch, [envelope({"status": "completed"}, wrapped)])
    generate(p)
    assert saved == ["https://julun.cc/v1/videos/task-test/content"]


@pytest.mark.parametrize("wrapped", [False, True])
@pytest.mark.parametrize("error,kind", [(GENERATION_ERROR, RETRYABLE), (PORTRAIT_ERROR, TASK_FATAL)])
def test_failed_preserves_reason_code_and_stops_polling(monkeypatch, wrapped, error, kind):
    p, calls, saved = provider(monkeypatch, [envelope({"status": "failed", "error": error,
                                                     "result_url": "https://cdn.example/stale.mp4"}, wrapped)])
    with pytest.raises(ApiError) as caught:
        generate(p)
    exc = caught.value
    assert error["message"].strip() in str(exc)
    assert exc.err_code == error["code"] and exc.kind == kind
    assert "task-test" in str(exc)
    assert "自动原路退回" not in str(exc)
    assert len(calls) == 2 and not saved


@pytest.mark.parametrize("message", ["肖像保护拒绝", "肖像权保护拒绝", "For likeness protection, rejected"])
def test_portrait_wording_stops_unchanged_retry(monkeypatch, message):
    p, _, _ = provider(monkeypatch, [{"status": "failed", "error": {"message": message, "code": "task_failed"}}])
    with pytest.raises(ApiError) as caught:
        generate(p)
    assert caught.value.kind == TASK_FATAL


@pytest.mark.parametrize("wrapped", [False, True])
def test_legacy_fail_reason_still_reports_content_rejection(monkeypatch, wrapped):
    p, _, saved = provider(monkeypatch, [envelope({"status": "FAILURE", "fail_reason": "内容审核不通过"}, wrapped)])
    with pytest.raises(ApiError, match="内容审核不通过") as caught:
        generate(p)
    assert caught.value.kind == TASK_FATAL and not saved


@pytest.mark.parametrize("error,expected_submissions", [(GENERATION_ERROR, 3), (PORTRAIT_ERROR, 1)])
def test_executor_retry_budget_and_failure_record(monkeypatch, tmp_path, error, expected_submissions):
    p, calls, saved = provider(monkeypatch, [{"status": "failed", "error": error}] * 3)
    job = Job("video", 2, 1, project_root=str(tmp_path), provider="julun", model="sd2.5")

    def worker(task, log, cancel):
        return generate(p, log) if task["key"] == "failed" else {"output": "unrelated.mp4"}

    run_batch(job, [{"key": "failed"}, {"key": "unrelated"}], worker,
              key_of=lambda t: t["key"], max_retry=2)
    assert sum(method == "POST" for method, _ in calls) == expected_submissions
    assert job.items["failed"]["state"] == "failed"
    assert job.items["unrelated"]["state"] == "ok" and not job.aborted
    assert not saved
    record = (tmp_path / "07_检查与记录" / "failures.json").read_text(encoding="utf-8")
    assert error["message"].strip() in json.dumps(json.loads(record), ensure_ascii=False)
