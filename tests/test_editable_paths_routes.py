import copy
from pathlib import Path

import pytest

from core import agent_projects as A, agent_runtime as R, paths, submitted, workspace_paths as W
from core.apiutil import ApiError
from core.executor import JobManager
from core.store import read_json, write_json
from server import app, agent_api
from test_agent_workspace import pj, CAPS, plan_for, publish, image, resolve, wait_job


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    data = tmp_path/'data'; data.mkdir()
    monkeypatch.setitem(paths._forced, 'data', str(data))
    monkeypatch.setattr(app, 'JOBS', JobManager())
    cfg = app.load_config();cfg['projects_dir'] = str(tmp_path/'projects');app.save_config(cfg)
    return data


def test_data_location_persists_after_restart_with_keys_and_plugins(isolated, tmp_path, monkeypatch):
    cfg = app.load_config();cfg['providers']['test'] = {'api_key':'secret-test'};app.save_config(cfg)
    (isolated/'providers').mkdir();(isolated/'providers/test.py').write_text('# provider')
    target = tmp_path/'new-data'
    W.change_data(app, str(target))
    assert app.load_config()['providers']['test']['api_key'] == 'secret-test'
    assert (target/'providers/test.py').read_text() == '# provider'
    assert (isolated/'config.json').exists()
    monkeypatch.setitem(paths._forced, 'data', str(isolated))
    assert paths.data_dir() == str(target)
    assert app.load_config()['projects_dir'] == cfg['projects_dir']


def test_data_move_conflict_and_running_guard(isolated, tmp_path):
    dest=tmp_path/'occupied';dest.mkdir();(dest/'keep').write_text('keep')
    with pytest.raises(ValueError, match='非空'):W.change_data(app,str(dest))
    app.JOBS.create('production',1,1)
    with pytest.raises(ValueError, match='批次'):W.change_data(app,str(tmp_path/'new'))
    assert paths.data_dir()==str(isolated)


def test_move_project_preserves_materials_and_updates_contract(isolated, tmp_path):
    cfg=app.load_config();project=A.create_project(cfg['projects_dir'],{'title':'move','source':'original script'},CAPS)
    publish(project,plan_for(project));assert A.scan(project).get('revision') == 'r0001', A.scan(project)
    (Path(project.root)/'keep.txt').write_text('keep')
    previous=project.root;dest=tmp_path/'new-parent'/'new-project'
    result=W.relocate_project(app, project,str(dest))
    assert result['root']==str(dest) and not Path(previous).exists()
    assert (dest/'keep.txt').read_text()=='keep'
    assert str(dest) in (dest/'00_项目说明/production-contract.md').read_text(encoding='utf-8')
    assert read_json(str(dest/'00_项目说明/project-lock.json'))['project_root']==str(dest)
    assert result['root'] in [p['root'] for p in agent_api.projects(app.load_config())]
    publication = A.scan(agent_api.project(str(dest),app.load_config()))
    assert publication.get('revision')=='r0001', publication


def test_project_move_rejects_nested_existing_and_busy(isolated, tmp_path):
    project=A.create_project(app.load_config()['projects_dir'],{'title':'move'},CAPS)
    for target in (project.root+'/nested',str(tmp_path)):
        with pytest.raises(ValueError):W.relocate_project(app,project,target)
    app.JOBS.create('production',1,1,project_root=project.root)
    with pytest.raises(ValueError,match='批次'):W.relocate_project(app,project,str(tmp_path/'new'))
    assert Path(project.root).is_dir()


def test_project_move_rolls_back_contract_if_config_save_fails(isolated, tmp_path, monkeypatch):
    project=A.create_project(app.load_config()['projects_dir'],{'title':'rollback'},CAPS)
    lock=Path(project.p('00_项目说明','project-lock.json'));before=lock.read_bytes()
    monkeypatch.setattr(app,'save_config',lambda cfg: (_ for _ in ()).throw(OSError('disk full')))
    dest=tmp_path/'new-root'
    with pytest.raises(OSError):W.relocate_project(app,project,str(dest))
    assert lock.read_bytes()==before and not dest.exists()


def routes(pj):
    caps=copy.deepcopy(CAPS);backup=copy.deepcopy(CAPS[0]);backup['id']='backup';caps.append(backup)
    row={'provider':'chaomo','model':'test','concurrency':1,'fallbacks':[{'provider':'backup','model':'test','size':'16:9'}]}
    value=R.validate_settings({'asset':row},caps)
    write_json(pj.p('07_检查与记录','production-settings.json'),value)
    publish(pj,plan_for(pj));A.scan(pj)
    return caps


def test_fallback_handles_dependencies_and_keeps_frozen_prompts(pj,monkeypatch):
    caps=routes(pj);seen=[]
    def make(project,pc,*args):
        def worker(t,log,cancel):
            seen.append((pc['provider'],t['key']))
            assert '/jobs/' in t['prompt_ref']
            if pc['provider']=='chaomo':raise ApiError('quota',status=402,kind='batch_fatal')
            image(project.p(*t['output'].split('/')))
        return worker
    monkeypatch.setattr(R.produce,'make_image_worker',make)
    jobs=JobManager();r=R.start(pj,{'defaults':{'max_retry':0}},caps,resolve,jobs)
    assert r['ok'],r
    j=jobs.get(r['job_id']);wait_job(j)
    assert j.status=='done',j.snapshot()
    assert ('backup','asset0') in seen and ('backup','asset1') in seen
    snapshot=read_json(pj.p('07_检查与记录','jobs',j.id,'snapshot.json'))
    assert snapshot['settings']['asset']['fallbacks'][0]['provider']=='backup'


@pytest.mark.parametrize('record,code',[(True,'quota_exhausted'),(False,'submit_uncertain'),(False,'content_rejected')])
def test_pending_uncertain_and_content_errors_never_failover(pj,monkeypatch,record,code):
    caps=routes(pj);seen=[]
    def make(project,pc,*args):
        def worker(t,log,cancel):
            seen.append(pc['provider'])
            if record:
                with submitted.tracking(pj.root,t['output'],provider=pc['provider'],model='test'):
                    submitted.remember('keep-id',path='/tasks/{id}',picker='extract_image_items')
            raise ApiError(code,kind='task_fatal',err_code=code)
        return worker
    monkeypatch.setattr(R.produce,'make_image_worker',make)
    jobs=JobManager();r=R.start(pj,{'defaults':{'max_retry':0}},caps,resolve,jobs,only=['asset0'])
    wait_job(jobs.get(r['job_id']))
    assert seen==['chaomo']


def test_provider_change_keeps_original_id_provider(pj,monkeypatch):
    caps=routes(pj)
    row=A.view(pj)['tasks'][0]
    with submitted.tracking(pj.root,row['output'],provider='chaomo',model='old-model'):
        submitted.remember('keep-id',path='/tasks/{id}',picker='extract_image_items')
    write_json(pj.p('07_检查与记录','production-settings.json'),{'asset':{'provider':'backup','model':'test','concurrency':1}})
    seen=[]
    def make(project,pc,*args):
        def worker(t,log,cancel):
            seen.append((pc['provider'],pc['model']));image(project.p(*t['output'].split('/')))
        return worker
    monkeypatch.setattr(R.produce,'make_image_worker',make)
    jobs=JobManager();r=R.start(pj,{},caps,resolve,jobs,only=['asset0']);assert r['ok'],r
    wait_job(jobs.get(r['job_id']))
    assert seen==[('chaomo','old-model')]


def test_backup_parameters_are_validated(pj):
    caps=routes(pj);caps[1]['image']['sizes']=['1:1']
    r=R.preview(pj,{},caps,resolve)
    assert r['blocked'] and '备用 1' in r['blocked'][0]['reason']
