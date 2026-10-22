import copy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from auth_service.studio_auth.adapter_format import digest, select, validate
from core import cloud_adapters as C, paths, providers as P
from tools.build_provider_bundle import build


def example(version='1', value='one'):
    return dict(protocol=1, version=version, modules={'sample': '''from .base import Provider
class Sample(Provider):
    id = 'sample'
    name = 'Example'
    supports = ('image',)
    def capabilities(self):
        return dict(id=self.id, name=self.name, supports=['image'])
    def generate_image(self, *args, **kwargs):
        return '''+repr(value)+'\n'}, providers=[dict(id='sample', name='Example', module='sample', **{'class':'Sample'})])


def meta(b):
    return dict(enabled=True, protocol=1, version=b['version'], sha256=digest(b),
                providers=[p['id'] for p in b['providers']], missing=[])


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, 'data_dir', lambda: str(tmp_path))
    old = [dict(P.REGISTRY), dict(P.ALIASES), dict(P.SOURCES), list(P.ERRORS), list(P.WARNINGS), P._remote, P._remote_version, C._owner, C._active]
    yield tmp_path
    for target, saved in zip((P.REGISTRY,P.ALIASES,P.SOURCES,P.ERRORS,P.WARNINGS),old):
        target.clear()
        target.update(saved) if isinstance(target,dict) else target.extend(saved)
    P._remote,P._remote_version,C._owner,C._active=old[5:]


def test_all_24_existing_adapters_and_dependencies_load_without_builtin_imports():
    bundle = build('test')
    assert len(bundle['providers']) == 24
    classes = C.load(bundle)
    assert set(classes) == {p['id'] for p in bundle['providers']}
    assert not {pid:P._check(cls) for pid,cls in classes.items() if P._check(cls)}
    banana = select(bundle, {'chaomo_banana'})
    assert set(banana['modules']) == {'chaomo','chaomo_banana'}
    assert set(C.load(banana)) == {'chaomo_banana'}
    assert not any('api_key' in p for p in bundle['providers'])


def test_refresh_atomic_swap_live_instance_and_rollback(isolated):
    owner=object(); first=example(); second=example('2','two')
    C.install(owner,'account',meta(first),lambda h:{'bundle':first})
    running=P.build('sample','test-key')
    assert running.generate_image() == 'one'
    C.install(owner,'account',meta(second),lambda h:{'bundle':second})
    assert P.build('sample','test-key').generate_image() == 'two'
    assert running.generate_image() == 'one'
    assert P.reload_all()['managed'] and set(P.REGISTRY) == {'sample'}
    C.install(owner,'account',meta(first),lambda h:{'bundle':first})
    assert P.build('sample','test-key').generate_image() == 'one'
    assert len(list((isolated/'adapter-cache').glob('*.json'))) == 1


def test_failed_download_or_load_keeps_registry_and_never_executes_mismatched_code(isolated):
    owner=object(); b=example()
    C.install(owner,'account',meta(b),lambda h:{'bundle':b})
    before=P.REGISTRY['sample']
    wrong=example('2','tampered')
    with pytest.raises(ValueError,match='校验'):
        C.install(owner,'account',meta(example('3')),lambda h:{'bundle':wrong})
    invalid=example('4');invalid['modules']['sample']+='\nraise RuntimeError("bad adapter")\n'
    with pytest.raises(ValueError,match='加载失败'):
        C.install(owner,'account',meta(invalid),lambda h:{'bundle':invalid})
    assert P.REGISTRY['sample'] is before


def test_logout_account_switch_and_corrupted_cache(isolated):
    a=object();b=example()
    C.install(a,'user-a',meta(b),lambda h:{'bundle':b})
    C.clear(a)
    assert not P.REGISTRY
    cache=next((isolated/'adapter-cache').glob('*.json'))
    cache.write_text('{}')
    calls=[]
    C.install(a,'user-a',meta(b),lambda h:(calls.append(h) or {'bundle':b}))
    assert calls
    other=example('2','other')
    C.install(a,'user-b',meta(other),lambda h:{'bundle':other})
    C.clear(object())
    assert P.build('sample','').generate_image() == 'other'
    C.clear(a)
    assert not P.REGISTRY


def test_managed_client_fails_closed_without_published_bundle(isolated,monkeypatch):
    monkeypatch.setenv('STV_MANAGED_ADAPTERS','1')
    with pytest.raises(ValueError,match='尚未发布'):
        C.install(object(),'uid',{'enabled':False},lambda h:None)
    with pytest.raises(ValueError,match='缺少'):
        C.install(object(),'uid',dict(meta(example()),missing=['unpublished']),lambda h:None)


def test_login_refresh_installs_adapter_before_credentials_and_refreshes_model_routing(isolated,monkeypatch):
    from core.studio_login import LoginManager
    from core import cloud_config, cloud_usage
    manager=LoginManager(); manager.tokens={'access_token':'a'*48,'refresh_token':'b'*48}
    b=example();calls=[]
    def post(action,body,access=None):
        calls.append(action)
        if action=='adapters': return {'bundle':b}
        return {'valid':True,'user':{'id':'u','username':'tester'},'adapter_config':meta(b),
                'provider_config':{'sample':{'keys':[dict(api_key='test-key',base_url='https://example.test',key_name='我的图片通道',models=['image-test'])]}}}
    monkeypatch.setattr(manager,'_post',post)
    monkeypatch.setattr(cloud_usage,'start_sender',lambda *a:None)
    try:
        manager._verify()
        assert calls==['verify','adapters']
        assert cloud_config.display_name('sample')=='我的图片通道'
        assert cloud_config.provider('sample',{})['api_key']=='test-key'
        manager._verify()
        assert calls==['verify','adapters','verify']  # Same metadata does not redownload.
        manager._clear()
        assert not P.REGISTRY and not cloud_config.assigned_ids()
    finally:
        cloud_config.clear(manager)


def test_managed_mode_boots_empty_and_loads_all_adapters_in_clean_process(isolated):
    f=isolated/'bundle.json';f.write_text(json.dumps(build('test')),encoding='utf-8')
    script='''import json,sys,importlib.abc
from core import providers
assert providers.REGISTRY == {}
class DenyBuiltins(importlib.abc.MetaPathFinder):
 def find_spec(self, name, *args):
  if name.startswith('core.providers.') and name != 'core.providers.base': raise ImportError(name)
sys.meta_path.insert(0,DenyBuiltins())
from core.cloud_adapters import load
classes=load(json.load(open(sys.argv[1],encoding='utf-8')))
assert len(classes)==24
providers.activate_remote(classes,'test')
from server import app
assert len(app.caps_of({}))==24
'''
    r=subprocess.run([sys.executable,'-c',script,str(f)],env=dict(os.environ,STV_MANAGED_ADAPTERS='1',STV_DATA_DIR=str(isolated/'data')),
                     capture_output=True,text=True,timeout=45)
    assert r.returncode==0,r.stderr


@pytest.mark.parametrize('change', ['path','duplicate','dependency','secret','syntax','protocol'])
def test_invalid_bundles_rejected(change):
    b=example()
    if change=='path': b['modules']['../oops']=''
    if change=='duplicate': b['providers']*=2
    if change=='dependency': b['modules']['sample']+='\nfrom .missing import nope\n'
    if change=='secret': b['modules']['sample']+='\nsecret="sk-'+('A'*40)+'"\n'
    if change=='syntax': b['modules']['sample']+='\n!invalid'
    if change=='protocol': b['protocol']=2
    with pytest.raises(ValueError): validate(b)
