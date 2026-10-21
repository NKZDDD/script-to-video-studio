import hashlib
import json
from types import SimpleNamespace

import pytest
import requests

from core import cloud_storage as C, uploader, cloud_config, audio_refs
from core.apiutil import ApiError
from core.studio_login import LoginManager, LoginRequired


def descriptor(owner='user-a', revision='a'*32, **kw):
    return dict(assigned=True,available=True,endpoint='https://test.r2.cloudflarestorage.com',
        bucket='bucket', public_base_url='https://cdn.example.test',
        prefix=f'references/{owner}/{revision}',revision=revision,**kw)


@pytest.fixture
def managed():
    manager=SimpleNamespace()
    def ticket(body, owner):
        cfg=C.status();key=cfg['prefix']+'/'+body['sha256']+'.png'
        return dict(upload_url=cfg['endpoint']+'/bucket/'+key+'?signature=test-private-signature',
            public_url=cfg['public_base_url']+'/'+key,bytes=body['bytes'],revision=body['revision'],
            headers={'Content-Type':body['content_type']})
    manager.storage_ticket=ticket
    C.install(manager,'user-a',descriptor())
    yield manager
    C.clear(manager)
    uploader._MEM.clear()


def response():
    return SimpleNamespace(status_code=200, close=lambda:None)


def test_managed_upload_sends_only_signed_headers_and_returns_public_url(managed,monkeypatch):
    calls=[]
    monkeypatch.setattr(requests,'put',lambda url,**kw:(calls.append((url,kw)) or response()))
    local={'access_key':'personal','secret_key':'local-secret'}
    effective=uploader.effective_config(local)
    assert uploader.configured({}) and 'secret_key' not in effective
    out=uploader.put(local,b'image','reference.png')
    assert out.startswith('https://cdn.example.test/references/user-a/') and '?' not in out
    assert len(calls)==1 and calls[0][1]['headers']=={'Content-Type':'image/png'}
    assert calls[0][1]['allow_redirects'] is False and calls[0][1]['data']==b'image'
    assert local=={'access_key':'personal','secret_key':'local-secret'}


def test_image_cache_scoped_to_account_revision_and_only_public_urls(managed,monkeypatch,tmp_path):
    image=tmp_path/'ref.png';image.write_bytes(b'image')
    monkeypatch.setattr(uploader,'encode_ref',lambda *a,**kw:(b'image','image/png','.png','original'))
    calls=[]
    monkeypatch.setattr(requests,'put',lambda *a,**kw:(calls.append(a) or response()))
    def upload():return uploader.to_url(str(image),{},project_root=str(tmp_path),log=lambda *a:None)
    a=upload();assert upload()==a and len(calls)==1
    C.install(managed,'user-a',descriptor(revision='b'*32))
    b=upload();assert b!=a and len(calls)==2
    C.install(managed,'user-b',descriptor(owner='user-b',revision='b'*32))
    assert upload()!=b and len(calls)==3
    cache=(tmp_path/'07_检查与记录/upload_cache.json').read_text(encoding='utf-8')
    assert 'signature' not in cache and 'secret' not in cache


def test_logout_revocation_and_switch_do_not_fall_back_for_managed_snapshot(managed):
    snapshot=C.effective({})
    cloud_config.clear(managed)
    with pytest.raises(ApiError):C.effective(snapshot)
    assert not uploader.configured({})
    assert uploader.configured({'bucket':'own','access_key':'personal','secret_key':'private'})
    C.install(managed,'user-b',descriptor(owner='user-b'))
    with pytest.raises(ApiError):C.effective(snapshot)
    C.install(managed,'user-a',{'assigned':False})
    with pytest.raises(ApiError):C.effective(snapshot)


def test_disabled_storage_does_not_block_public_audio_or_no_upload_tasks(managed):
    C.install(managed,'user-a',dict(descriptor(),available=False))
    assert C.effective({},allow_unavailable=True)['_storage_source']=='managed'
    with pytest.raises(ApiError):C.effective({})
    assert not uploader.configured({})
    pj=SimpleNamespace(root='unused')
    task={'reference_audios':['https://cdn.example.test/music.mp3']}
    # Public URL inputs need no R2 signature.
    assert audio_refs.resolve(pj,task,{},lambda *a:None)==['https://cdn.example.test/music.mp3']


@pytest.mark.parametrize('change',[{'upload_url':'https://evil.example/path?signature=x'},
    {'public_url':'https://evil.example/path'}, {'headers':{'Authorization':'secret'}},
    {'revision':'b'*32},{'bytes':100}])
def test_invalid_tickets_never_send_bytes(managed,monkeypatch,change):
    original=managed.storage_ticket
    managed.storage_ticket=lambda body,owner:dict(original(body,owner),**change)
    monkeypatch.setattr(requests,'put',lambda *a,**kw:pytest.fail('must not upload'))
    with pytest.raises(ApiError,match='无效'):C.upload(C.effective({}),b'x','image/png')


def test_upload_errors_do_not_expose_presigned_url(managed,monkeypatch):
    def fail(url,**kw):raise requests.ConnectionError('failed '+url)
    monkeypatch.setattr(requests,'put',fail)
    with pytest.raises(ApiError) as exc:C.upload(C.effective({}),b'x','image/png')
    assert 'signature' not in str(exc.value) and 'cloudflarestorage' not in str(exc.value)


def test_owner_switch_after_sign_does_not_send_bytes(managed,monkeypatch):
    original=managed.storage_ticket
    def ticket(body,owner):
        result=original(body,owner);C.clear(managed);return result
    managed.storage_ticket=ticket
    monkeypatch.setattr(requests,'put',lambda *a,**kw:pytest.fail('must not upload'))
    with pytest.raises(ApiError,match='改变'):C.upload(C.effective({}),b'x','image/png')


def test_ticket_refreshes_expired_access_once_and_checks_owner(monkeypatch):
    m=LoginManager();m.tokens={'access_token':'old'};m.user={'id':'user-a'};m.checked_until=float('inf')
    monkeypatch.setattr(m,'device_id',lambda:'test-device-123456789')
    calls=[]
    def post(action,body,token):
        calls.append(token)
        assert action=='storage/sign' and body['device_id']=='test-device-123456789'
        if token=='old':raise LoginRequired('access_expired','expired')
        return {'ok':True}
    monkeypatch.setattr(m,'_post',post)
    monkeypatch.setattr(m,'_verify',lambda:m.tokens.update(access_token='new'))
    assert m.storage_ticket({},'user-a')=={'ok':True} and calls==['old','new']
    with pytest.raises(LoginRequired):m.storage_ticket({},'another')


def test_old_server_keeps_personal_storage(managed):
    C.install(managed,'user-a',None)
    assert C.effective({'bucket':'personal'})=={'bucket':'personal'}
    assert C.status()=={'assigned':False}
