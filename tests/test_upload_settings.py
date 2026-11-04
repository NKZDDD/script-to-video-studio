import json
import shutil
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from core import cloud_storage, uploader
from core.store import read_json, write_json
from server import agent_api, app


@pytest.fixture
def config(tmp_path, monkeypatch):
    path = tmp_path / 'config.json'
    write_json(str(path), {'projects_dir': str(tmp_path / 'projects'), 'upload': {
        'bucket': 'personal', 'access_key': 'fake-access', 'secret_key': 'fake-secret'}})
    monkeypatch.setattr(app.paths, 'config_path', lambda: str(path))
    monkeypatch.setattr(app, 'caps_of', lambda *a: [])
    monkeypatch.setattr(uploader, '_UPLOAD_SLOTS', uploader.UploadSlots())
    return path


def assert_capacity(count):
    gate = uploader._UPLOAD_SLOTS
    acquired = 0
    try:
        for _ in range(count):
            assert gate.acquire(timeout=0)
            acquired += 1
        assert not gate.acquire(timeout=0)
    finally:
        for _ in range(acquired):
            gate.release()


@pytest.mark.parametrize('managed', [False, True])
def test_setting_round_trip_and_restart_preserve_credentials(config, monkeypatch, managed):
    manager = object()
    descriptor = dict(assigned=True, available=True, revision='a'*32,
        endpoint='https://test.r2.cloudflarestorage.com', bucket='managed',
        public_base_url='https://cdn.test', prefix='references')
    cloud_storage.install(manager, 'user-a', descriptor if managed else None)
    try:
        assert app.load_config()['upload']['concurrency'] == 4
        before = app.load_config()['upload']
        result = agent_api.post(app, '/api/agent/settings', {'upload': {'concurrency': '7'}})
        assert result == {'ok': True}
        assert_capacity(7)  # Takes effect on save, before any new batch/bootstrap.
        saved = read_json(str(config))['upload']
        assert saved == dict(before, concurrency=7)
        exposed = app.api_get('/api/bootstrap', {})['config']['upload']
        assert exposed['concurrency'] == 7 and not exposed['secret_key']
        assert exposed['managed']['assigned'] == managed
        assert 'fake-secret' not in json.dumps(exposed)
        # A fresh process starts at the default, then applies the persisted setting.
        monkeypatch.setattr(uploader, '_UPLOAD_SLOTS', uploader.UploadSlots())
        assert app.load_config()['upload']['concurrency'] == 7
        assert_capacity(7)
        # Saving the personal upload form without this field must preserve the limit.
        agent_api.post(app, '/api/agent/settings', {'upload': {'prefix': 'changed', 'secret_key': ''}})
        assert read_json(str(config))['upload']['concurrency'] == 7
        assert read_json(str(config))['upload']['secret_key'] == before['secret_key']
    finally:
        cloud_storage.clear(manager)


@pytest.mark.parametrize('value', [0, -1, True, 1.5, '1.5', '', 'bad', None])
def test_invalid_setting_does_not_change_disk_or_active_limit(config, value):
    app.api_post('/api/config', {'upload': {'concurrency': 2}})
    before = config.read_bytes()
    with pytest.raises(ValueError, match='同时上传文件数'):
        app.api_post('/api/config', {'upload': {'concurrency': value}})
    assert config.read_bytes() == before
    assert_capacity(2)


def test_failed_save_does_not_apply_unsaved_limit(config, monkeypatch):
    app.api_post('/api/config', {'upload': {'concurrency': 2}})
    def fail(*a, **kw):
        raise OSError('disk unavailable')
    monkeypatch.setattr(app, 'write_json', fail)
    with pytest.raises(OSError):
        app.api_post('/api/config', {'upload': {'concurrency': 9}})
    assert_capacity(2)
    assert read_json(str(config))['upload']['concurrency'] == 2


def test_increase_wakes_waiter_and_decrease_drains_existing_uploads(config):
    app.api_post('/api/config', {'upload': {'concurrency': 1}})
    gate = uploader._UPLOAD_SLOTS
    assert gate.acquire(timeout=0)
    entering, acquired, release = threading.Event(), threading.Event(), threading.Event()

    def wait_for_slot():
        entering.set()
        assert gate.acquire(timeout=4)
        acquired.set()
        try:
            assert release.wait(5)
        finally:
            gate.release()

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(wait_for_slot)
        try:
            assert entering.wait(2)
            assert not acquired.wait(.05)
            app.api_post('/api/config', {'upload': {'concurrency': 3}})
            assert acquired.wait(2)  # No need to finish the first active upload.
            assert gate is uploader._UPLOAD_SLOTS
            assert gate.acquire(timeout=0)  # Three active now.
            app.api_post('/api/config', {'upload': {'concurrency': 1}})
            assert not gate.acquire(timeout=0)
            gate.release()  # Two active, still above the new limit.
            assert not gate.acquire(timeout=0)
        finally:
            release.set()
            gate.release()
        future.result(timeout=3)
    assert_capacity(1)


def test_new_upload_limit_does_not_invalidate_image_cache():
    cfg = {'bucket': 'b', 'access_key': 'fake', 'secret_key': 'fake'}
    assert uploader._image_key(dict(cfg, concurrency=2), 'image-hash') == \
        uploader._image_key(dict(cfg, concurrency=8), 'image-hash')


def test_settings_form_visible_with_managed_r2_and_saves_only_limit():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node required for UI tests')
    source = (Path(__file__).resolve().parents[1] / 'web/agent.js').read_text(encoding='utf-8')
    helpers = source[source.index('const esc ='):source.index('const S =')]
    helpers += source[source.index('const btn ='):source.index('const fileButton =')]
    form = source[source.index('function uploadForm()'):source.index('function featureForm()')]
    script = r'''
const assert=require('node:assert/strict');
const S={boot:{upload:{concurrency:7,managed:{assigned:true,available:true,public_base_url:'https://cdn.test'}}}};
const panel={innerHTML:''},form={reportValidity:()=>true};
const $=s=>s==='#settings-content'?panel:form;
let calls=[],notices=[],reloads=0,redraws=0;
const api=async(path,body)=>{calls.push({path,body});S.boot.upload.concurrency=+body.upload.concurrency;};
const reloadBoot=async()=>reloads++,settings=()=>{redraws++;uploadForm();},notice=m=>notices.push(m);
class FormData{constructor(f){assert.equal(f,form);} *[Symbol.iterator](){yield ['concurrency','11'];}}
''' + helpers + form + r'''
(async()=>{
 uploadForm();
 assert(panel.innerHTML.indexOf('id="upload-queue-form"')<panel.innerHTML.indexOf('<details'));
 assert(panel.innerHTML.includes('name="concurrency" type="number" value="7"'));
 assert(panel.innerHTML.includes('min="1" step="1" required'));
 assert(panel.innerHTML.includes('按文件计数'));
 assert(panel.innerHTML.includes('完成一份立即接续下一份'));
 await saveUploadConcurrency();
 assert.deepEqual(calls,[{path:'settings',body:{upload:{concurrency:'11'}}}]);
 assert.equal(reloads,1);assert.equal(redraws,1);
 assert(panel.innerHTML.includes('value="11"'));
 form.reportValidity=()=>false;await saveUploadConcurrency();assert.equal(calls.length,1);
 form.reportValidity=()=>true;
 S.boot.upload.managed={assigned:false};delete S.boot.upload.concurrency;uploadForm();
 assert(panel.innerHTML.includes('name="concurrency" type="number" value="4"'));
})().catch(e=>{console.error(e);process.exitCode=1;});
'''
    done = subprocess.run([node], input=script, encoding='utf-8', capture_output=True, timeout=20)
    assert done.returncode == 0, done.stderr


def test_legacy_upload_setting_saves_only_limit_and_keeps_error_visible():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node required for UI tests')
    source = (Path(__file__).resolve().parents[1] / 'web/index.html').read_text(encoding='utf-8')
    action = source[source.index("$('#saveUploadConcurrency').onclick"):source.index("$('#refreshUploadConfig').onclick")]
    script = r'''
const assert=require('node:assert/strict');
const elements={saveUploadConcurrency:{},upConcurrency:{value:'9',reportValidity:()=>true},upResult:{}};
const $=s=>elements[s.slice(1)];let BOOT={},saved=[],renders=0,error=false;
const renderUpload=()=>renders++;
const api={post:async(path,body)=>{saved.push(body);return error?{error:'save failed'}:{ok:true};},get:async()=>({})};
''' + action + r'''
(async()=>{
 await elements.saveUploadConcurrency.onclick();
 assert.deepEqual(saved,[{upload:{concurrency:'9'}}]);assert.equal(renders,1);
 error=true;await elements.saveUploadConcurrency.onclick();assert.equal(renders,1);
 assert.equal(elements.upResult.textContent,'save failed');
 elements.upConcurrency.reportValidity=()=>false;await elements.saveUploadConcurrency.onclick();
 assert.equal(saved.length,2);
})().catch(e=>{console.error(e);process.exitCode=1;});
'''
    done = subprocess.run([node], input=script, encoding='utf-8', capture_output=True, timeout=20)
    assert done.returncode == 0, done.stderr
