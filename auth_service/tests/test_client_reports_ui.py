from pathlib import Path
import shutil
import subprocess

import pytest


def test_admin_report_view_filters_details_escapes_and_ignores_closed_requests():
    if not shutil.which('node'):
        pytest.skip('Node.js required')
    web=Path(__file__).resolve().parents[1]/'studio_auth/web'
    assert "['client-reports'," in (web/'app.js').read_text(encoding='utf-8')
    script=r'''
const assert=require('node:assert/strict');const elements=new Map();let listener,dialog,deferred;const calls=[];
function element(id){if(!elements.has(id))elements.set(id,{innerHTML:'',textContent:'',open:false,value:'',
 reset(){this.fields={device:''};},replaceChildren(){this.innerHTML='';},showModal(){this.open=true;},close(){this.open=false;this.onclose?.();},
 querySelector:element,addEventListener(type,fn){listener=fn;}});return elements.get(id);}
global.document={createElement(){dialog=element('dialog');return dialog;},body:{append(){}},querySelector:element};
global.window={addEventListener(){}};global.FormData=class{constructor(f){this.f=f;}*[Symbol.iterator](){yield*Object.entries(this.f.fields);}};
global.esc=v=>String(v??'').replaceAll('<','&lt;').replaceAll('>','&gt;');global.date=v=>'time-'+v;global.rows=[{username:'customer'}];
global.api=async path=>{calls.push(path);if(deferred)return new Promise(r=>deferred=r);if(path.includes('?'))return {total:26,reports:[{report_id:'report-1',device:'device-a',created_at:1,received_at:2,reason:'task',snapshot:{version:'1.2.3'},events:1,errors:1}]};
 return {report_id:'report-1',device:'device-a',hours:1,snapshot:{version:'1.2.3',dropped:0,omitted:0},events:[{at:1,kind:'provider',operation:'query',outcome:'error',duration_ms:1,details:{provider:'<script>',model:'test-model',task_id:'task-1',http_status:200,upstream_status:401}}]};};
'''+(web/'client-reports.js').read_text(encoding='utf-8')+r'''
(async()=>{const tick=()=>new Promise(r=>setImmediate(r));listener({target:{closest:()=>({dataset:{index:'0'}})},stopImmediatePropagation(){}});await tick();assert(dialog.open&&calls.length===1);
 element('#reports-list').onclick({target:{closest:()=>({dataset:{report:'report-1'}})}});await tick();assert.equal(element('#report-detail').hidden,false);
 assert(element('#report-events').innerHTML.includes('&lt;script&gt;'));assert(!element('#report-events').innerHTML.includes('<script>'));
 assert(element('#report-events').innerHTML.includes('401'));
 element('#report-search').value='other-model';element('#report-search').oninput();assert(!element('#report-events').innerHTML.includes('task-1'));
 element('#reports-next').onclick();await tick();assert(calls.at(-1).includes('page=2'));
 deferred=true;element('#reports-list').onclick({target:{closest:()=>({dataset:{report:'report-1'}})}});await tick();dialog.close();deferred({events:[]});await tick();assert.equal(element('#report-events').innerHTML,'');
})().catch(e=>{console.error(e);process.exitCode=1;});
'''
    result=subprocess.run(['node','-'],input=script,text=True,encoding='utf-8',capture_output=True,timeout=15)
    assert result.returncode==0,result.stderr
