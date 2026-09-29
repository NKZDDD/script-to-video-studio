"""Batch abandonment is explicit, scoped to a project and never generates."""
import copy
import json
from pathlib import Path
import subprocess

import pytest

from core import agent_projects as A, submitted
from core.executor import JobManager
from server import agent_api, app
from test_agent_workspace import CAPS, plan_for, publish, image
from test_submitted_recovery import env, run


@pytest.fixture
def batch(tmp_path, monkeypatch):
    pj = A.create_project(str(tmp_path), {'title': 'batch', 'source': 'x', 'options': {}}, CAPS)
    publish(pj, plan_for(pj, count=4)); A.scan(pj)
    tasks = A.view(pj)['tasks']
    for row in tasks[:3]:
        with submitted.tracking(pj.root, row['output'], key=row['key'], provider='chaomo', model='test'):
            submitted.remember('id-' + row['key'], path='/images/{id}', picker='extract_image_items')
    cfg = {'projects_dir': str(tmp_path), 'providers': {}, 'limits': {'global': 8, 'per_provider': {}}}
    monkeypatch.setattr(app, 'load_config', lambda: copy.deepcopy(cfg))
    monkeypatch.setattr(app, 'caps_of', lambda cfg=None: copy.deepcopy(CAPS))
    monkeypatch.setattr(app, 'JOBS', JobManager())
    return pj, tasks


def clear(pj, items):
    return agent_api.post(app, '/api/agent/submitted/clear-batch', {'root': pj.root, 'items': items})


def item(row):
    return {'key': row['key'], 'task_id': 'id-' + row['key']}


def test_only_chosen_pending_records_are_cleared_no_job_created(batch):
    pj, rows = batch
    image(pj.p(*rows[2]['output'].split('/')))
    original = Path(pj.p(*rows[2]['output'].split('/'))).read_bytes()
    d = clear(pj, [item(rows[0]), item(rows[0]), item(rows[2]), item(rows[3])])
    assert d == {'ok': True, 'count': 1, 'cleared': ['asset0'], 'skipped': ['asset2', 'asset3']}
    assert submitted.load(pj.root, rows[0]['output']) is None
    assert submitted.load(pj.root, rows[1]['output'])['task_id'] == 'id-asset1'
    assert Path(pj.p(*rows[2]['output'].split('/'))).read_bytes() == original
    assert not app.JOBS.jobs


@pytest.mark.parametrize('bad', [None, [], {}, [{'key': 'missing', 'task_id': 'x'}],
                              [{'key': 'asset1'}], [{'key': [], 'task_id': 'x'}]])
def test_bad_request_never_partially_clears(batch, bad):
    pj, rows = batch
    items = [item(rows[0]), *bad] if isinstance(bad, list) and bad else bad
    with pytest.raises(ValueError):
        clear(pj, items)
    assert all(submitted.load(pj.root, r['output']) for r in rows[:3])


def test_stale_id_rejects_whole_batch(batch):
    pj, rows = batch
    with pytest.raises(ValueError, match='ID 已变化'):
        clear(pj, [item(rows[0]), {'key': rows[1]['key'], 'task_id': 'old-id'}])
    assert all(submitted.load(pj.root, r['output']) for r in rows[:3])


def test_running_project_cannot_abandon(batch):
    pj, rows = batch
    app.JOBS.create('production', 1, 1, project_root=pj.root)
    with pytest.raises(ValueError, match='正在生产'):
        clear(pj, [item(rows[0])])
    assert submitted.load(pj.root, rows[0]['output'])


def test_other_project_running_does_not_block(batch):
    pj, rows = batch
    app.JOBS.create('production', 1, 1, project_root=pj.root + '-other')
    assert clear(pj, [item(rows[0])])['count'] == 1


def test_cancelled_but_inflight_job_must_finish_before_clear(batch):
    pj, rows = batch
    job = app.JOBS.create('production', 1, 1, project_root=pj.root)
    job.cancel()
    with pytest.raises(ValueError, match='等待任务退出'):
        clear(pj, [item(rows[0])])
    assert submitted.load(pj.root, rows[0]['output'])
    job.finished_at = job.started_at + 1
    assert clear(pj, [item(rows[0])])['count'] == 1


def test_old_record_without_identity_still_resumes(env):
    pj, provider, task = env
    with pytest.raises(Exception):
        run(pj, task)
    rec = submitted.load(pj.root, task['output'])
    rec.pop('identity')
    from core.store import write_json
    write_json(submitted._path(pj.root, task['output']), rec)
    provider.state = 'completed'
    assert run(pj, task)[0]['resumed']
    assert provider.posts == 1


def test_after_abandon_actual_new_submission_uses_latest_parameters(env, monkeypatch):
    pj, provider, task = env
    with pytest.raises(Exception):
        run(pj, task)
    submitted.clear(pj.root, task['output'])
    task['params'].update(duration=20, ratio='16:9', resolution='720p')
    p = Path(pj.p(*task['prompt_ref'].split('/')))
    p.write_text(p.read_text(encoding='utf-8') + '\nNew prompt after explicit abandonment', encoding='utf-8')
    seen = []
    original = provider.generate_video
    def generate(t, *a, **kw):
        seen.append(t)
        return original(t, *a, **kw)
    monkeypatch.setattr(provider, 'generate_video', generate)
    provider.state = 'completed'
    run(pj, task)
    assert provider.posts == 2 and len(seen) == 1
    assert seen[0].duration == 20 and seen[0].ratio == '16:9' and seen[0].resolution == '720p'
    assert seen[0].prompt.endswith('New prompt after explicit abandonment')


@pytest.mark.parametrize('confirmed', [True, False])
def test_ui_confirmation_selection_and_no_automatic_start(confirmed):
    source = (Path(__file__).resolve().parents[1] / 'web/agent.js').read_text(encoding='utf-8')
    function = source[source.index('async function dropSubmittedBatch()'):source.index('async function preview()')]
    script = '''
const assert=require('node:assert/strict');
const S={root:'project',visibleKeys:['a'],data:{tasks:[
 {key:'a',selected:true,submitted:{task_id:'id-a'}},
 {key:'b',selected:true,submitted:{task_id:'id-b'}},
 {key:'c',selected:false,submitted:{task_id:'id-c'}},
 {key:'d',selected:true,done:true,submitted:{task_id:'id-d'}},
 {key:'e',selected:true}]}};
const selectionQueue=Promise.resolve();let message='',posts=[],invalidations=0;
const confirm=m=>{message=m;return CONFIRMED};
const post=async(path,body)=>{posts.push({path,body});return {count:2,skipped:[]}};
const invalidatePlan=()=>invalidations++;
const loadProject=async()=>{};const notice=()=>{};
FUNCTION
dropSubmittedBatch().then(()=>{
 assert(message.includes('2')&&message.includes('1'));
 assert.equal(posts.length,CONFIRMED?1:0);
 assert.equal(invalidations,CONFIRMED?1:0);
 if(CONFIRMED){assert.equal(posts[0].path,'submitted/clear-batch');assert.deepEqual(posts[0].body.items,[{key:'a',task_id:'id-a'},{key:'b',task_id:'id-b'}])}
}).catch(e=>{console.error(e);process.exitCode=1});
'''.replace('CONFIRMED', json.dumps(confirmed)).replace('FUNCTION', function)
    result = subprocess.run(['node', '-'], input=script, capture_output=True, encoding='utf-8', timeout=20)
    assert result.returncode == 0, result.stderr
