import copy

import pytest

from core import agent_runtime as R
from core.executor import JobManager
from core.store import Project
from server import agent_api, app


def test_runtime_lists_other_projects_before_newer_finished_batches(monkeypatch):
    jobs = JobManager()
    active = jobs.create('production', 3, 1, project_root='D:/甲', project_name='甲项目')
    active.set_item('图片1', state='ok')
    done = jobs.create('production', 1, 1, project_root='D:/乙', project_name='乙项目')
    done.status = 'done'
    done.set_item('图片2', state='ok')
    monkeypatch.setattr(R.resources, 'sample', lambda _: None)
    data = R.runtime(jobs)
    assert data['active_jobs'] == 1
    assert [j['id'] for j in data['jobs']] == [active.id, done.id]
    assert data['jobs'][0]['project_name'] == '甲项目'
    assert data['jobs'][1]['project_root'] == 'D:/乙'
    assert data['jobs'][0]['finished'] == 1
    assert all('items' not in j and 'logs' not in j for j in data['jobs'])


def test_global_runtime_does_not_hide_active_batches_after_default_limit(monkeypatch):
    jobs = JobManager()
    for i in range(45):
        jobs.create('production', 1, 1, project_root=f'D:/项目{i}', project_name=f'项目{i}')
    monkeypatch.setattr(R.resources, 'sample', lambda _: None)
    data = R.runtime(jobs)
    assert data['active_jobs'] == len(data['jobs']) == 45
    assert len(jobs.list()) == 40  # Existing project-list default is unchanged.


def test_global_job_detail_still_requires_its_own_registered_project(tmp_path, monkeypatch):
    for name in ('甲', '乙'):
        pj = Project(str(tmp_path / name))
        pj.init_dirs()
        pj.save_meta({'title': name, 'system': 'v34'})
    cfg = {'projects_dir': str(tmp_path)}
    jobs = JobManager()
    job = jobs.create('production', 1, 1, project_root=str(tmp_path / '乙'), project_name='乙')
    monkeypatch.setattr(app, 'JOBS', jobs)
    monkeypatch.setattr(app, 'load_config', lambda: copy.deepcopy(cfg))
    assert agent_api.get(app, '/api/agent/runtime', {})['jobs'][0]['id'] == job.id
    with pytest.raises(ValueError, match='任务不存在'):
        agent_api.get(app, '/api/agent/job', {'root': [str(tmp_path / '甲')], 'id': [job.id]})
    assert agent_api.get(app, '/api/agent/job', {'root': [job.project_root], 'id': [job.id]})['project_name'] == '乙'
