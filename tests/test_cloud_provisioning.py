import json
import pytest

from core import cloud_config as C, cloud_usage as U, paths, submitted
from core.store import Project
from server import app


@pytest.fixture
def cloud(tmp_path, monkeypatch):
    monkeypatch.setattr(paths,'data_dir',lambda:str(tmp_path))
    manager=object()
    C.install(manager,'user-a',{'julun':{'api_key':'managed-test','assignment_id':'revision-a','base_url':'https://provider.test'}})
    yield tmp_path,manager
    C.clear(manager)


def test_cloud_priority_does_not_save_secrets_or_use_local_url(cloud):
    root,m=cloud
    cfg={'providers':{'julun':{'api_key':'personal-test','base_url':'https://personal.test'}}}
    out=app.resolve_provider_cfg(cfg,{'provider':'julun','model':'model-a','api_key':'override','base_url':'https://override.test'},'video')
    assert out['api_key']=='managed-test' and out['base_url']=='https://provider.test'
    assert out['_usage']['source']=='managed'
    assert cfg['providers']['julun']['api_key']=='personal-test'
    assert C.provider('hzyfai',{'api_key':'personal'})['api_key']=='personal'
    assert app._provider_key_status('julun',{})['source']=='managed'
    C.clear(m)
    assert app.resolve_provider_cfg(cfg,{'provider':'julun','model':'model-a'},'video')['api_key']=='personal-test'


def test_group_keys_do_not_fall_back_to_personal(cloud):
    root,m=cloud
    C.install(m,'user-a',{'chaomo':{'image_1k_api_key':'cloud-th','assignment_id':'rev'}})
    local={'image_1k_api_key':'local-th','image_4k_api_key':'local-native'}
    assert app._provider_api_key('chaomo',local,'image','gpt-image-2-th')=='cloud-th'
    assert not app._provider_api_key('chaomo',local,'image','gpt-image-2-Native')


def test_usage_attempt_retry_resume_and_account_binding(cloud):
    root,m=cloud
    pj=Project(str(root/'project'));task={'output':'out.mp4','key':'SEG01'}
    cfg=app.resolve_provider_cfg({}, {'provider':'julun','model':'model-a'},'video')
    meta={'provider':'julun','model':'model-a','_usage':cfg['_usage'],'_usage_kind':'video'}
    @U.tracked
    def generate(pj,task,out,prov,rec,*,meta,**kw):
        with submitted.tracking(pj.root,task['output'],**meta):
            submitted.new_attempt()
            submitted.new_attempt()
            submitted.remember('task-123')
            raise RuntimeError('download interrupted')
    with pytest.raises(RuntimeError):generate(pj,task,'',None,None,meta=meta)
    events=U.pending('user-a')
    assert len(events)==2 and {e['status'] for e in events}=={'failed','pending'}
    assert 'managed-test' not in json.dumps(events)
    rec=submitted.load(pj.root,task['output'])
    original=rec['_usage_ref']['event']['event_id']
    @U.tracked
    def resume(*a,**kw):return {'ok':True}
    resume(pj,task,'',None,rec,meta=meta)
    events=U.pending('user-a')
    assert len(events)==2
    assert next(e for e in events if e['event_id']==original)['status']=='completed'
    assert not U.pending('user-b')
    with pytest.raises(Exception,match='另一个 Studio'):
        resume(pj,task,'',None,rec,meta={**meta,'_usage':{**meta['_usage'],'owner':'user-b'}})


def test_usage_ack_does_not_erase_newer_state_and_old_recovery_is_separate(cloud):
    root,m=cloud
    pj=Project(str(root/'project'));task={'output':'out.mp4','key':'SEG01'}
    rec={'task_id':'old-task','provider':'julun','model':'old-model'}
    @U.tracked
    def resume(*a,**kw):return {}
    meta={'provider':'julun','model':'old-model','_usage':{'owner':'user-a','source':'managed'},'_usage_kind':'video'}
    resume(pj,task,'',None,rec,meta=meta)
    e=U.pending('user-a')[0]
    assert e['resumed'] and e['source']=='unknown'
    newer=dict(e,sequence=e['sequence']+1)
    U.persist('user-a',newer)
    U.acknowledge('user-a',[e])
    assert U.pending('user-a')==[newer]
    U.acknowledge('user-a',[newer])
    assert U.pending('user-a')==[]


def test_data_migration_keeps_outbox(cloud, tmp_path, monkeypatch):
    from core import workspace_paths, executor
    from types import SimpleNamespace
    root,m=cloud
    e={'event_id':'test','sequence':1,'status':'pending'}
    monkeypatch.setitem(paths._forced, 'data', str(root))
    U.persist('user-a',e)
    destination=tmp_path.parent/(tmp_path.name+'-moved')
    fake=SimpleNamespace(JOBS=executor.JobManager(),load_config=lambda:{})
    workspace_paths.change_data(fake,str(destination))
    monkeypatch.setattr(paths,'data_dir',lambda:str(destination))
    assert U.pending('user-a')==[e]


def test_pending_reports_keep_owner_and_recover_after_failed_upload(cloud, monkeypatch):
    from core.studio_login import LoginManager, LoginRequired
    root,m=cloud
    manager=LoginManager()
    manager.tokens={'access_token':'a'*43,'refresh_token':'b'*64}
    manager.user={'id':'user-a'}
    manager.checked_until=float('inf')
    e={'event_id':'test','sequence':1}
    U.persist('user-a',e)
    U.persist('user-b',dict(e,event_id='another'))
    def unavailable(*args,**kw):raise LoginRequired('auth_unavailable','offline',503)
    monkeypatch.setattr(manager,'_post',unavailable)
    with pytest.raises(LoginRequired):manager.send_usage()
    assert U.pending('user-a')==[e]
    def success(action,body,access):
        assert action=='usage' and body['events']==[e]
        return {'accepted':['test']}
    monkeypatch.setattr(manager,'_post',success)
    manager.send_usage()
    assert not U.pending('user-a') and len(U.pending('user-b'))==1
