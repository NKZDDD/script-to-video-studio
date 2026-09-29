"""Only explicit abandonment permits a second paid submission."""
from pathlib import Path

import pytest

from core import agent_projects as A, agent_runtime as R, diagnose, produce, soften, submitted
from core.apiutil import ApiError
from core.store import write_json, write_text
from test_submitted_recovery import env, run
from test_agent_workspace import CAPS, plan_for, publish, resolve, pj, wait_job, image


def test_resume_skips_changed_or_missing_generation_materials(env, monkeypatch):
    project, provider, task = env
    with pytest.raises(ApiError):
        run(project, task)
    task['params'] = {'duration': 'invalid', 'ratio': 'invalid', 'resolution': 'invalid'}
    task['reference_images'] = [{'file_ref': 'missing.png'}]
    Path(project.p(*task['prompt_ref'].split('/'))).unlink()
    from core.relay import Relay
    assert Relay(project).ready_of('video')(task) == (True, '')
    monkeypatch.setattr(produce, 'make_ref_resolver', lambda *a, **kw: lambda *a: pytest.fail('must not upload'))
    provider.state = 'completed'
    result, _ = run(project, task)
    assert result['resumed'] and provider.posts == 1


def test_changed_provider_preserves_id_without_querying_wrong_service(env):
    project, provider, task = env
    with pytest.raises(ApiError):
        run(project, task)
    worker = produce.make_video_worker(project, {'provider': 'other', 'api_key': 'k', 'model': 'different'})
    with pytest.raises(ApiError, match='切回') as exc:
        worker(task, lambda _: None, lambda: False)
    assert not diagnose.should_failover(diagnose.build(exc.value))
    assert provider.posts == 1
    assert submitted.load(project.root, task['output'])['task_id'] == 'task-1'


def test_missing_original_account_does_not_submit(env):
    project, provider, task = env
    with pytest.raises(ApiError):
        run(project, task)
    rec = submitted.load(project.root, task['output'])
    rec['account'] = 'deleted-account'
    write_json(submitted._path(project.root, task['output']), rec)
    with pytest.raises(ApiError, match='原账号'):
        run(project, task)
    assert provider.posts == 1 and submitted.load(project.root, task['output'])


def test_invalid_saved_result_keeps_task_id(env):
    project, provider, task = env
    with pytest.raises(ApiError):
        run(project, task)
    provider.state = 'completed'
    provider.session.save_item = lambda *a, **kw: None
    with pytest.raises(ApiError, match='文件检查'):
        run(project, task)
    assert provider.posts == 1 and submitted.load(project.root, task['output'])


def test_abandoning_after_preview_does_not_turn_resume_batch_into_generation(env):
    project, provider, task = env
    with pytest.raises(ApiError):
        run(project, task)
    task['resume_only'] = True
    submitted.clear(project.root, task['output'])
    with pytest.raises(ApiError, match='重新查看生产计划'):
        run(project, task)
    assert provider.posts == 1


@pytest.mark.parametrize('code', ['upstream_rejected', 'content_policy_violation'])
def test_no_softening_or_second_submission_after_getting_id(tmp_path, code):
    posts = []
    def generate(prompt):
        posts.append(prompt)
        submitted.remember('already-paid', path='/video/{id}', picker='extract_video_url')
        raise ApiError('内容审核失败', err_code=code)
    with submitted.tracking(str(tmp_path), 'video.mp4', provider='julun'):
        with pytest.raises(ApiError):
            soften.run_with_softening(generate, 'prompt', pj=None, llm=object(), kind='video', key='v', log=lambda _: None)
    assert posts == ['prompt']
    assert submitted.load(str(tmp_path), 'video.mp4')['task_id'] == 'already-paid'


def test_image_resume_does_not_read_prompt_or_references(env):
    project, provider, _ = env
    task = {'key': 'img', 'output': '02_固定资产/new.png', 'prompt_ref': 'missing.txt',
            'params': {'size': 'invalid'}, 'reference_images': [{'file_ref': 'missing.png'}]}
    with submitted.tracking(project.root, task['output'], provider='slow', model='old', identity='old'):
        submitted.remember('old-image', path='/images/{id}', picker='extract_image_items')
    queried = []
    def resume(rec, dest, **kw):
        from test_no_storyboard_no_video import PNG
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(PNG)
        queried.append(rec['task_id'])
        return {'provider': 'slow', 'model': 'old', 'resumed': True}
    provider.resume = resume
    worker = produce.make_image_worker(project, {'provider': 'slow', 'api_key': 'k', 'model': 'new'}, 'asset')
    assert worker(task, lambda _: None, lambda: False)['resumed']
    assert queried == ['old-image'] and provider.posts == 0


def test_preview_pending_skips_new_model_params_prompt_and_dependencies(pj):
    plan = plan_for(pj)
    publish(pj, plan); A.scan(pj)
    row = next(t for t in A.view(pj)['tasks'] if t['key'] == 'asset1')
    with submitted.tracking(pj.root, row['output'], provider='chaomo', model='removed-model'):
        submitted.remember('original-id', path='/images/{id}', picker='extract_image_items')
    # asset1's required asset0 does not exist and is not selected.
    Path(pj.p(*row['prompt_ref'].split('/'))).unlink()
    cfg = {'providers': {}, 'defaults': {}}
    result = R.preview(pj, cfg, CAPS, resolve, only=['asset1'])
    assert result['blocked'] == [] and result['resuming'] == 1
    assert result['_tasks'][0]['resume_only']
    assert result['_providers']['asset']['model'] == 'removed-model'


def test_start_uses_original_model_credentials_for_each_pending_task(pj, monkeypatch):
    from core.executor import JobManager
    plan = plan_for(pj)
    publish(pj, plan); A.scan(pj)
    rows = A.view(pj)['tasks']
    for row in rows:
        with submitted.tracking(pj.root, row['output'], provider='chaomo', model='old-' + row['key']):
            submitted.remember('id-' + row['key'], path='/images/{id}', picker='extract_image_items')
        Path(pj.p(*row['prompt_ref'].split('/'))).unlink()
    seen = []
    def resolve_old(cfg, selection, kind):
        return {**selection, 'api_key': 'key-for-' + selection['model']}
    def make_worker(project, pc, kind, *args):
        def worker(task, log, cancel):
            assert pc['api_key'] == 'key-for-old-' + task['key']
            seen.append(task['key'])
            image(project.p(*task['output'].split('/')))
            return {'resumed': True}
        return worker
    monkeypatch.setattr(produce, 'make_image_worker', make_worker)
    jobs = JobManager()
    result = R.start(pj, {'defaults': {}}, CAPS, resolve_old, jobs, only=[r['key'] for r in rows])
    assert result['ok']
    job = list(jobs.jobs.values())[-1]
    wait_job(job)
    assert sorted(seen) == ['asset0', 'asset1']
