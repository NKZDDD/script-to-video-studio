"""Only a provider's explicit failed status releases a pending ID automatically."""
import json
from pathlib import Path
import subprocess

import pytest

from core import agent_projects as A, apiutil, diagnose, produce, submitted
from core.executor import Job, run_batch
from core.store import write_json, write_text
from test_submitted_recovery import env, run


def test_legacy_pending_id_is_released_on_failure_then_new_parameters_are_used(env, monkeypatch):
    pj, provider, task = env
    with pytest.raises(apiutil.ApiError):
        run(pj, task)
    rec = submitted.load(pj.root, task['output'])
    rec.pop('identity')
    write_json(submitted._path(pj.root, task['output']), rec)
    provider.state = 'failed'
    with pytest.raises(apiutil.ApiError) as caught:
        run(pj, task)
    assert caught.value.err_code == 'provider_task_failed'
    assert caught.value.kind == apiutil.TASK_FATAL
    assert 'task-1' in str(caught.value) and '审核未过' in str(caught.value)
    assert provider.posts == 1 and submitted.load(pj.root, task['output']) is None

    task['params'].update(duration=20, ratio='16:9', resolution='720p')
    prompt = 'Image 1 = P__SCSTATE_FOREST\nImage 2 = P__CHAR_001\n最新提示词'
    write_text(pj.p(*task['prompt_ref'].split('/')), prompt)
    seen = []
    generate = provider.generate_video
    def capture(t, *args, **kw):
        seen.append(t)
        return generate(t, *args, **kw)
    monkeypatch.setattr(provider, 'generate_video', capture)
    provider.state = 'completed'
    worker = produce.make_video_worker(pj, {'provider': 'slow', 'api_key': 'k', 'model': 'new-model'})
    worker(task, lambda _: None, lambda: False)
    assert provider.posts == 2 and len(seen) == 1
    assert (seen[0].prompt, seen[0].model, seen[0].duration, seen[0].ratio, seen[0].resolution) == (
        prompt, 'new-model', 20, '16:9', '720p')


def test_initial_failure_stops_batch_and_ui_shows_failed_after_reload(env, monkeypatch):
    pj, provider, task = env
    provider.state = 'failed'
    monkeypatch.setattr(produce.soften, 'soften', lambda *a, **kw: pytest.fail('must not rewrite after a failed task ID'))
    worker = produce.make_video_worker(pj, {'provider': 'slow', 'api_key': 'k', 'model': 'm'},
                                      llm_factory=lambda: object())
    job = Job('video', 1, 1, project_root=pj.root, provider='slow')
    run_batch(job, [task], worker, key_of=lambda t: t['key'], max_retry=4)
    assert provider.posts == 1 and job.items[task['key']]['state'] == 'failed'
    assert submitted.load(pj.root, task['output']) is None
    failures = diagnose.load(pj.root)
    assert failures[-1]['code'] == 'PROVIDER_TASK_FAILED'
    assert not diagnose.should_failover(failures[-1])
    assert 'task-1' in failures[-1]['raw'] and '审核未过' in failures[-1]['raw']
    # Reload the disk-backed view and execute the real page's status function.
    data = A.view(pj)
    data.update(failures=failures, running={})
    row = next(t for t in data['tasks'] if t['key'] == task['key'])
    js = Path(__file__).resolve().parents[1].joinpath('web/agent.js').read_text(encoding='utf-8')
    fn = js[js.index('function taskState('):js.index('\nfunction linker(')]
    script = 'const S=' + json.dumps({'data': data}) + ';\n' + fn
    script += '\nconsole.log(JSON.stringify(taskState(' + json.dumps(row) + ')));'
    result = subprocess.run(['node', '-'], input=script, capture_output=True, text=True, encoding='utf-8', check=True)
    assert json.loads(result.stdout) == {'label': '失败', 'cls': 'rv-bad'}


def test_image_worker_also_releases_confirmed_failed_id(env, monkeypatch):
    pj, provider, _ = env
    task = {'key': 'img', 'output': '02_固定资产/not-created.png', 'prompt_ref': 'missing.txt',
            'params': {}, 'reference_images': []}
    with submitted.tracking(pj.root, task['output'], provider='slow'):
        submitted.remember('old-image')
    def fail(*args, **kw):
        raise apiutil.task_failed({'error': {'message': 'image decoding failed'}})
    monkeypatch.setattr(provider, 'resume', fail)
    worker = produce.make_image_worker(pj, {'provider': 'slow', 'api_key': 'k', 'model': 'm'}, 'asset')
    with pytest.raises(apiutil.ApiError) as caught:
        worker(task, lambda _: None, lambda: False)
    assert caught.value.err_code == 'provider_task_failed'
    assert not submitted.load(pj.root, task['output']) and provider.posts == 0


@pytest.mark.parametrize('status', [0, 404, 403, 500])
def test_query_or_download_error_never_releases_id_based_on_message_or_finished_flag(env, monkeypatch, status):
    pj, provider, task = env
    with submitted.tracking(pj.root, task['output'], provider='slow'):
        submitted.remember('keep-me')
    def fail(*args, **kw):
        exc = apiutil.ApiError('任务失败 / result download failed', status=status)
        exc.task_finished = True
        raise exc
    monkeypatch.setattr(provider, 'resume', fail)
    with pytest.raises(apiutil.ApiError) as caught:
        run(pj, task)
    assert caught.value.err_code == 'submitted_pending'
    assert submitted.load(pj.root, task['output'])['task_id'] == 'keep-me'
    assert provider.posts == 0


@pytest.mark.parametrize('provider_id', ['julun', 'gate', 'haomanju', 'hongniao', 'xmd'])
def test_custom_pollers_mark_actual_failed_status(provider_id, monkeypatch, tmp_path):
    from core.providers import REGISTRY
    p = REGISTRY[provider_id]('offline-fixture')
    row = {'status': 'failed', 'error': {'message': 'provider failure'}, 'progress': 30}
    monkeypatch.setattr(p.session, 'request', lambda *a, **kw: {'data': row} if provider_id == 'haomanju' else row)
    log = lambda _: None
    dest = str(tmp_path / 'unused.mp4')
    with pytest.raises(apiutil.ApiError) as caught:
        if provider_id == 'julun':
            p._poll('old-id', 0, 1, log=log)
        elif provider_id == 'gate':
            p._poll_result('seedance-2.5', 'old-id', 0, 1, log=log)
        elif provider_id == 'haomanju':
            p._poll_generations('old-id', 0, 1, log=log)
        elif provider_id == 'hongniao':
            p._follow('old-id', row, dest, 'model', log, None, 0, 1)
        else:
            monkeypatch.setattr(p, '_request', lambda *a, **kw: row)
            p._follow('old-id', dest, log=log, cancel=None, poll_interval=0, poll_timeout=1)
    assert caught.value.provider_task_failed is True
    assert submitted.finished(caught.value)
