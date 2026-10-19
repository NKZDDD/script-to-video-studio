"""2026-10-19 document: real request construction, reference preservation and catalog intersection."""
import json
from pathlib import Path

import pytest

from core import model_catalog as MC, providers
from core.apiutil import ApiError
from core.providers.base import VideoTask
from core.providers import julun as J

DOCUMENT_MODELS = json.loads((Path(__file__).parent / 'fixtures/julun_20261019_models.json').read_text(encoding='utf8'))


def test_every_document_model_is_registered_and_has_options():
    assert set(DOCUMENT_MODELS) == set(J.CURRENT_SPEC)
    cap = J.JulunProvider().capabilities()['video']
    assert set(DOCUMENT_MODELS) <= set(cap['models'])
    assert set(DOCUMENT_MODELS) <= set(cap['model_options'])


@pytest.mark.parametrize('model', DOCUMENT_MODELS)
def test_all_document_models_submit_poll_and_download_without_losing_media(model, monkeypatch):
    p = J.JulunProvider(api_key='fixture')
    calls, downloads = [], []
    def request(method, path, **kw):
        calls.append((method, path, kw))
        return {'id':'task-fixture'} if method=='POST' else {'status':'completed','progress':95}
    monkeypatch.setattr(p.session, 'request', request)
    monkeypatch.setattr(p.session, 'save_item', lambda url, dest, **kw: downloads.append(url))
    fmt, rule, resolutions, ratios, ci, cv, ca, _ = J.CURRENT_SPEC[model]
    seconds = rule[0] if rule else 10
    refs = ['https://fixture.example/a.png']
    vids = ['https://fixture.example/v.mp4'] if cv != 0 else []
    auds = ['https://fixture.example/a.mp3'] if ca != 0 else []
    task = VideoTask(model=model, prompt='Image 1 = 人物参考，保持外观。', refs=refs,
        duration=seconds, ratio=ratios[0] if ratios else '16:9',
        resolution=resolutions[0] if resolutions else '', extra={'video_refs':vids,'audio_refs':auds})
    result = p.generate_video(task, 'unused.mp4', poll_interval=0, log=lambda _:None)
    assert calls[0][:2] == ('POST','/v1/videos')
    body = calls[0][2]['json_body']
    assert body['model'] == model and body.get('seconds',body.get('duration')) == seconds
    assert calls[0][2]['headers']['Idempotency-Key']
    if fmt == 'standard':
        assert body['images'] == refs
        assert body.get('videos',[]) == vids and body.get('audios',[]) == auds
        assert body['aspect_ratio'] == task.ratio
    else:
        assert body['input_reference'] == refs[0]
    assert calls[1][:2] == ('GET','/v1/videos/task-fixture')
    assert downloads == ['https://julun.cc/v1/videos/task-fixture/content']
    assert result['task_id'] == 'task-fixture'


def test_sd25_preserves_all_30_images_10_videos_and_10_audios_and_controls():
    refs = [f'https://fixture.example/{i}.png' for i in range(30)]
    vids = [f'https://fixture.example/{i}.mp4' for i in range(10)]
    auds = [f'https://fixture.example/{i}.mp3' for i in range(10)]
    _, body = J.JulunProvider().build_video_body(VideoTask(model='JL-seedance-2.5-1080p', prompt='风景',
        duration=30, refs=refs, resolution='1080p', extra={'video_refs':vids,'audio_refs':auds,
        'enable_audio':False,'face':True,'negative_prompt':'字幕','additional_parameters':{'seed':1}}))
    assert (body['images'],body['videos'],body['audios']) == (refs,vids,auds)
    assert body['enable_audio'] is False and body['face'] is True
    assert body['negative_prompt']=='字幕' and body['additional_parameters']=={'seed':1}


@pytest.mark.parametrize('changes', [
    {'refs':['https://fixture.example/a.png']*31}, {'duration':31}, {'duration':4.5},
    {'resolution':'480p'}, {'extra':{'video_refs':['local.mp4']}},
    {'extra':{'enable_audio':'false'}}, {'extra':{'additional_parameters':'invalid'}},
])
def test_invalid_parameters_stop_before_post(changes, monkeypatch):
    p=J.JulunProvider();calls=[]
    monkeypatch.setattr(p.session,'request',lambda *a,**k:calls.append(a))
    data=dict(model='JL-seedance-2.5-720p',prompt='风景',duration=15)
    data.update(changes)
    with pytest.raises(ApiError):p.generate_video(VideoTask(**data),'unused.mp4',log=lambda _:None)
    assert not calls


def test_fixed_and_discrete_durations_are_not_silently_changed():
    p=J.JulunProvider()
    with pytest.raises(ApiError):p.build_video_body(VideoTask(model='SD2.0FSDT',prompt='风景',duration=15))
    with pytest.raises(ApiError):p.build_video_body(VideoTask(model='SDFAST-JL',prompt='风景',duration=12,refs=['https://x/a.png']))
    _, body=p.build_video_body(VideoTask(model='SD2.0FSDT',prompt='风景',duration=12))
    assert body['seconds']==12


def test_live_h3_total_limit_and_mp3_rule_are_distinct():
    p=J.JulunProvider()
    with pytest.raises(ApiError,match='合计最多 12'):
        p.build_video_body(VideoTask(model='JL-minimax-video-h3-2k',prompt='风景',duration=10,
            refs=['https://x/a.png']*9,extra={'video_refs':['https://x/a.mp4']*3,'audio_refs':['https://x/a.mp3']}))
    with pytest.raises(ApiError,match='MP3'):
        p.build_video_body(VideoTask(model='sd2.5_30',prompt='风景',duration=15,extra={'audio_refs':['https://x/a.wav']}))
    _, body=p.build_video_body(VideoTask(model='sd2.5_30',prompt='风景',duration=15,extra={'audio_refs':['https://x/a.MP3?token=fixture']}))
    assert body['audios']==['https://x/a.MP3?token=fixture']


def test_old_fast_super_does_not_inherit_new_jl_limits_or_portrait_rules():
    p=J.JulunProvider()
    _, body=p.build_video_body(VideoTask(model='2.0FSATjl',prompt='风景',duration=20,refs=['https://x/a.png']*15))
    assert body['seconds']==20 and len(body['images'])==15
    assert 'resolution' not in body
    _, body=p.build_video_body(VideoTask(model='SD 2.5',prompt='风景',duration=30,
        refs=['https://x/a.png']*15,extra={'face':True,'audio_refs':['https://x/a.mp3']}))
    assert body['face'] is True and len(body['images'])==15


def test_grok_first_last_never_silently_drops_a_third_image():
    with pytest.raises(ApiError,match='恰好 2'):
        J.JulunProvider().build_video_body(VideoTask(model='grok-imagine-video-1.5（按次）',prompt='风景',
            duration=6,refs=['https://x/a.png']*3,extra={'first_last':True}))


@pytest.mark.parametrize('pricing, expected', [
    ({'success':True,'data':[{'model_name':'JL-seedance-2.5-720p'},{'model_name':'pricing-only'}]}, ['JL-seedance-2.5-720p']),
    ({'success':True,'data':[]}, []),
    ({'success':False,'data':[]}, None),
    ({'data':[{'wrong':'missing model name'}]}, None),
])
def test_catalog_uses_intersection_without_restoring_retired_models(pricing, expected, monkeypatch):
    p=J.JulunProvider(api_key='fixture')
    pricing_calls=[]
    def get_pricing(method,path,**kw):
        pricing_calls.append((method,path));return pricing
    monkeypatch.setattr(p.session,'request',get_pricing)
    monkeypatch.setattr(providers,'build',lambda *a,**k:p)
    class Session:
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def get(self,url,**kw):
            assert url=='https://julun.cc/v1/models' and kw['allow_redirects'] is False
            class Reply:
                status_code=200
                def json(self):return {'data':[{'id':'JL-seedance-2.5-720p'},{'id':'retired-video'}]}
            return Reply()
    monkeypatch.setattr(MC.requests,'Session',Session)
    result=MC.fetch('julun',{'api_key':'fixture'})
    assert pricing_calls==[('GET','/api/pricing')]
    if expected is None:
        assert not result['ok'] and '本次未更新' in result['msg']
    else:
        assert result['ok'] and result['strict'] and result['models']==expected
        assert MC.apply_catalog(p.capabilities(),result,'fixture')['video']['models']==expected


def test_resume_uses_old_id_without_submitting_again(monkeypatch):
    p=J.JulunProvider();calls=[]
    monkeypatch.setattr(p.session,'request',lambda method,path,**kw:(calls.append((method,path)) or {'status':'completed'}))
    monkeypatch.setattr(p.session,'save_item',lambda *a,**k:None)
    p.resume({'task_id':'old-task','model':'JL-seedance-2.5-720p','resume':{'custom':'julun'}},'unused.mp4',log=lambda _:None)
    assert calls==[('GET','/v1/videos/old-task')]
