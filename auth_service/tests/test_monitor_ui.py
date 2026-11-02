from pathlib import Path
import shutil
import subprocess

import pytest


def test_auto_form_uses_server_rules_and_preserves_manual_values():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node required for UI test')
    source = (Path(__file__).resolve().parents[1] / 'studio_auth/web/provider-monitor.js').read_text(encoding='utf-8')
    functions = source[source.index(' function suggest()'):source.index(' function resetForm()')]
    script = r'''
const assert=require('node:assert/strict');
const controls=Object.fromEntries(['provider','key_id','mode','url','kind','result_path','auth','key_field'].map(k=>[k,{value:'',disabled:false}]));
const f={elements:controls},manual={hidden:false,querySelectorAll:()=>['url','kind','result_path','auth','key_field'].map(k=>controls[k])},note={textContent:'',classList:{toggle(){}}};
const q=s=>s==='[data-form]'?f:s==='[data-manual]'?manual:note;
let data={presets:[{key_id:'known',available:true,url:'https://provider.example/api/v1/models',kind:'models',result_path:'data.models',auth:'bearer',key_field:'api_key'}, {key_id:'unknown',available:false,reason:'适配包缺少规则'}]};
controls.mode.value='auto';controls.key_id.value='known';
''' + functions + r'''
suggest();assert(manual.hidden);assert(controls.url.disabled);assert(!controls.url.required);
assert.equal(controls.url.value,'https://provider.example/api/v1/models');assert(note.textContent.includes('已识别'));
controls.mode.value='manual';updateMode();assert(!manual.hidden);assert(!controls.url.disabled);assert(controls.url.required);
controls.url.value='https://custom.example/health';updateMode();assert.equal(controls.url.value,'https://custom.example/health');
controls.mode.value='auto';controls.key_id.value='unknown';suggest();assert.equal(controls.url.value,'');assert(note.textContent.includes('缺少规则'));
'''
    result = subprocess.run([node], input=script, encoding='utf-8', capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr


def test_page_route_discards_stale_responses_and_refresh_preserves_form():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node required for UI test')
    source = (Path(__file__).resolve().parents[1] / 'studio_auth/web/provider-monitor.js').read_text(encoding='utf-8')
    functions = source[source.index(' async function refresh('):source.index(' button.onclick=')]
    script = r'''
const assert=require('node:assert/strict');
let csrf='',location={hash:'#provider-monitor'},epoch=0,request=0,timer=null,data=null,keys=null;
let mounted=0,cleared=0,rendered=0,reset=0,updated=0,pending=[],scheduled=[];
const page={hidden:true,removeEventListener(){},replaceChildren(){cleared++;}};
const workspace={hidden:true},main={classList:{toggle(){}}};
const document={querySelector:s=>s==='main'?main:workspace};
const active=()=>!page.hidden&&Boolean(csrf),mount=()=>{mounted++;},edit=()=>{};
const api=path=>new Promise(resolve=>pending.push({path,resolve}));
const renderResults=()=>{rendered++;},resetForm=()=>{reset++;},updateMode=()=>{updated++;};
const note=()=>{},clearTimeout=()=>{},setTimeout=cb=>{scheduled.push(cb);return scheduled.length;};
const settle=async()=>{await Promise.resolve();await Promise.resolve();await Promise.resolve();};
''' + functions + r'''
(async()=>{
route();assert(page.hidden);assert.equal(mounted,0);
csrf='test';route();assert(!page.hidden);assert(workspace.hidden);assert.equal(mounted,1);
pending.splice(0).forEach(p=>p.resolve({}));await settle();assert.equal(reset,1);assert.equal(rendered,1);
const poll=refresh();pending.splice(0).forEach(p=>p.resolve({changed:true}));await poll;
assert.equal(reset,1);assert.equal(updated,1);assert.equal(rendered,2);
const force=refresh(true);pending.splice(0).forEach(p=>p.resolve({changed:true}));await force;
assert.equal(reset,1);assert.equal(updated,2);
const old=refresh();const oldRequests=pending.splice(0);
location.hash='#accounts';route();assert(page.hidden);assert(!workspace.hidden);assert.equal(cleared,1);
oldRequests.forEach(p=>p.resolve({old:true}));await old;assert.equal(data,null);assert.equal(rendered,3);
location.hash='#provider-monitor';route();assert.equal(mounted,2);
const loginRequests=pending.splice(0);csrf='';route();assert(page.hidden);assert(workspace.hidden);
loginRequests.forEach(p=>p.resolve({private:true}));await settle();assert.equal(data,null);assert.equal(rendered,3);
})();
'''
    result = subprocess.run([node], input=script, encoding='utf-8', capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr
