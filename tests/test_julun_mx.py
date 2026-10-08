"""User-supplied SD2.5720MX contract; no live or paid requests."""
import importlib.util
from pathlib import Path

import pytest

from core import submitted
from core.apiutil import ApiError, TASK_FATAL
from core.model_catalog import apply_catalog
from core.providers import julun
from core.providers.base import VideoTask
from test_julun_download_resume import VIDEO, Response, install_transport


@pytest.fixture(params=['builtin', 'plugin'])
def mod(request):
    if request.param == 'builtin':
        return julun
    path = Path(__file__).resolve().parents[1] / 'provider_patches/zz_julun_latest.py'
    spec = importlib.util.spec_from_file_location('mx_plugin_fixture', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def task(**kw):
    return VideoTask(**dict({'model':'SD2.5720MX','prompt':'参考人物进入房间','duration':30}, **kw))


def test_model_survives_old_catalog_with_fixed_duration(mod):
    cap = apply_catalog(mod.JulunProvider().capabilities(), {'rows':[{'id':'sd2.5'}]}, 'old snapshot')
    opts = cap['video']['model_options'][mod.MX_MODEL]
    assert mod.MX_MODEL in cap['video']['models']
    assert opts['durations'] == [30]
    assert set(opts['ratios']) == {'21:9','16:9','4:3','1:1','3:4','9:16'}
    assert (opts['max_refs'],opts['max_video_refs'],opts['max_audio_refs']) == (30,10,10)


@pytest.mark.parametrize('seconds',[1,15,30])
def test_request_matches_document_and_keeps_all_references(mod, seconds):
    refs = ['https://cdn.example/%s.png'%i for i in range(30)]
    vids = ['https://cdn.example/%s.mp4'%i for i in range(10)]
    auds = ['https://cdn.example/%s.wav'%i for i in range(10)]
    path, body = mod.JulunProvider().build_video_body(task(duration=seconds, ratio='', refs=refs,
        extra={'video_refs':vids,'audio_refs':auds,'store':False,'face_split':True}))
    assert path == '/v1/videos/generations'
    assert body == {'model':mod.MX_MODEL,'prompt':'参考人物进入房间','duration':30,'aspect_ratio':'16:9',
                    'image_refs':refs,'video_refs':vids,'audio_refs':auds,'store':False,'face_split':True}


@pytest.mark.parametrize('seconds',[0,31,1.5,True,None])
def test_invalid_duration_does_not_silently_submit(mod, seconds):
    with pytest.raises(ApiError,match='整数秒'):
        mod.JulunProvider().build_video_body(task(duration=seconds))


@pytest.mark.parametrize('extra',[
    {'refs':['https://cdn.example/a.png']*31},
    {'extra':{'video_refs':['https://cdn.example/a.mp4']*11}},
    {'extra':{'audio_refs':['https://cdn.example/a.wav']*11}},
    {'extra':{'audio_refs':['local.wav']}},
])
def test_over_limit_or_local_media_are_rejected_before_post(mod, extra):
    with pytest.raises(ApiError):
        mod.JulunProvider().build_video_body(task(**extra))


@pytest.mark.parametrize('status',['done','succeeded'])
@pytest.mark.parametrize('url,expected',[
    ('/v1/videos/generations/task-mx/content','https://julun.cc/v1/videos/generations/task-mx/content'),
    ('videos/generations/task-mx/content','https://julun.cc/v1/videos/generations/task-mx/content'),
    ('https://cdn.example/video.mp4','https://cdn.example/video.mp4'),
])
def test_poll_path_url_and_actual_download_auth(mod, monkeypatch, tmp_path, status, url, expected):
    p=mod.JulunProvider(api_key='fixture',base_url='https://julun.cc/v1/')
    calls=[]
    def request(method,path,**kw):
        calls.append((method,path))
        return {'id':'task-mx','status':'queued'} if method=='POST' else {'data':{'status':status,'video':{'url':url}}}
    monkeypatch.setattr(p.session,'request',request)
    downloads=install_transport(monkeypatch,mod,lambda n,h:Response(VIDEO))
    dest=tmp_path/'video.mp4'
    p.generate_video(task(),str(dest),log=lambda _:None)
    assert calls == [('POST','/v1/videos/generations'),('GET','/v1/videos/generations/task-mx')]
    assert dest.read_bytes()==VIDEO and downloads[0][0]==expected
    headers=downloads[0][1]
    assert headers.get('Authorization') == ('Bearer fixture' if expected.startswith('https://julun.cc/') else None)


def test_queued_poll_waits_at_least_thirty_seconds(mod,monkeypatch):
    p=mod.JulunProvider(); pauses=[]
    replies=iter([{'status':'queued'},{'status':'in_progress'},{'status':'done','video':{'url':'/result.mp4'}}])
    monkeypatch.setattr(p.session,'request',lambda *a,**k:next(replies))
    monkeypatch.setattr(mod.time,'sleep',pauses.append)
    assert p._poll('task-mx',1,100,model=mod.MX_MODEL,log=lambda _:None)=='https://julun.cc/result.mp4'
    assert pauses==[30,30]


def test_failed_is_terminal_but_timeout_is_not_provider_confirmed_failure(mod,monkeypatch):
    p=mod.JulunProvider()
    monkeypatch.setattr(p.session,'request',lambda *a,**k:{'status':'failed','error':{'message':'模型拒单','code':'reject'}})
    with pytest.raises(ApiError,match='模型拒单') as error:
        p._poll('task-mx',1,100,model=mod.MX_MODEL,log=lambda _:None)
    assert error.value.provider_task_failed and error.value.kind==TASK_FATAL
    now=[0]
    monkeypatch.setattr(mod.time,'time',lambda:now[0])
    monkeypatch.setattr(mod.time,'sleep',lambda n:now.__setitem__(0,now[0]+n))
    monkeypatch.setattr(p.session,'request',lambda *a,**k:{'status':'pending'})
    with pytest.raises(ApiError,match='保留 ID') as timeout:
        p._poll('task-mx',1,9999,model=mod.MX_MODEL,log=lambda _:None)
    assert now[0]==3600 and not getattr(timeout.value,'provider_task_failed',False)


def test_network_failure_retains_id_and_resume_only_queries(mod,monkeypatch,tmp_path):
    p=mod.JulunProvider(); calls=[]
    def request(method,path,**kw):
        calls.append((method,path))
        if method=='POST':return {'id':'task-mx'}
        raise ApiError('gateway unavailable',status=502)
    monkeypatch.setattr(p.session,'request',request)
    with submitted.tracking(str(tmp_path),'video.mp4',provider='julun',model=mod.MX_MODEL):
        with pytest.raises(ApiError,match='task-mx') as error:
            p.generate_video(task(),str(tmp_path/'video.mp4'),log=lambda _:None)
    assert error.value.kind==TASK_FATAL and not getattr(error.value,'provider_task_failed',False)
    record=submitted.load(str(tmp_path),'video.mp4')
    assert record['resume']['model']==mod.MX_MODEL
    monkeypatch.setattr(p.session,'request',lambda method,path,**k:(calls.append((method,path)) or {'status':'done','video':{'url':'/result.mp4'}}))
    monkeypatch.setattr(p.session,'save_item',lambda *a,**k:None)
    assert p.resume(record,'video.mp4',log=lambda _:None)['resumed']
    assert [m for m,_ in calls].count('POST')==1
    assert calls[-1]==('GET','/v1/videos/generations/task-mx')


def test_completed_without_url_retains_task_id(mod,monkeypatch):
    p=mod.JulunProvider()
    monkeypatch.setattr(p.session,'request',lambda *a,**k:{'status':'done'})
    with pytest.raises(ApiError,match='未返回 video.url') as error:
        p._poll('task-mx',1,100,model=mod.MX_MODEL,log=lambda _:None)
    assert not getattr(error.value,'provider_task_failed',False)


def test_old_host_without_done_alias_or_submitted_can_generate_mx(mod,monkeypatch):
    monkeypatch.setattr(mod,'DONE_STATES',('success',))
    monkeypatch.setattr(mod,'submitted',None)
    p=mod.JulunProvider()
    replies=iter([{'id':'task-mx'},{'status':'done','video':{'url':'/result.mp4'}}])
    monkeypatch.setattr(p.session,'request',lambda *a,**k:next(replies))
    saved=[]
    monkeypatch.setattr(p.session,'save_item',lambda url,*a,**k:saved.append(url))
    assert p.generate_video(task(),'video.mp4',log=lambda _:None)['task_id']=='task-mx'
    assert saved==['https://julun.cc/result.mp4']
