from pathlib import Path
import shutil
import subprocess

import pytest


def test_manual_client_controls_never_submit_on_open_and_retry_reuses_report_id():
    if not shutil.which('node'):
        pytest.skip('Node.js required')
    source=(Path(__file__).resolve().parents[1]/'web/agent.js').read_text(encoding='utf-8')
    functions=source[source.index('function reportForm()'):source.index('async function monitorForm()')]
    script=r'''
const assert=require('node:assert/strict');const elements=new Map(),calls=[];
const $=s=>{if(!elements.has(s))elements.set(s,{innerHTML:'',disabled:false});return elements.get(s);};
const S={page:'settings',tab:'reports'},esc=v=>String(v??'').replaceAll('<','&lt;').replaceAll('>','&gt;');
const btn=(label,action,extra='')=>`<button data-action="${action}" ${extra}>${label}</button>`;
const field=(label,html)=>label+html;let status={state:'idle',message:'no report'};
const get=async()=>status,api=async(path,body)=>{calls.push({path,body});status={state:'sent',message:'received',report_id:'report-1'};};
global.FormData=class{*[Symbol.iterator](){yield ['reason','network'];yield ['hours','24'];}};
const notice=()=>{};
'''+functions+r'''
(async()=>{const tick=()=>new Promise(r=>setImmediate(r));reportForm();await tick();assert.equal(calls.length,0);
 await sendReport();assert.deepEqual(calls[0],{path:'client-reports',body:{reason:'network',hours:24}});
 status={state:'error',message:'<img>',report_id:'report-1'};await refreshReport();assert($('#report-status').innerHTML.includes('report-retry'));
 assert(!$('#report-status').innerHTML.includes('<img>'));
 await sendReport('report-1');assert.deepEqual(calls[1].body,{report_id:'report-1'});
 status={state:'sending',message:'sending'};await refreshReport();assert($('#report-form [data-action="report-send"]').disabled);
 clearTimeout(refreshReport.timer);S.tab='upload';const old=$('#report-status').innerHTML;await refreshReport();assert.equal($('#report-status').innerHTML,old);
})().catch(e=>{console.error(e);process.exitCode=1;});
'''
    result=subprocess.run(['node','-'],input=script,text=True,encoding='utf-8',capture_output=True,timeout=15)
    assert result.returncode==0,result.stderr


def test_frontend_errors_only_post_safe_locations_locally_with_rate_bound():
    if not shutil.which('node'):
        pytest.skip('Node.js required')
    source=(Path(__file__).resolve().parents[1]/'web/agent.js').read_text(encoding='utf-8')
    functions=source[source.index('let clientErrorWindow='):source.index('const btn =')]
    script=r'''
const assert=require('node:assert/strict'),calls=[],listeners={};
const window={addEventListener:(name,fn)=>listeners[name]=fn},location={href:'http://127.0.0.1:8770/'};
const fetch=(url,options)=>{calls.push({url,body:JSON.parse(options.body)});return Promise.resolve({});};
'''+functions+r'''
for(let i=0;i<10;i++)listeners.error({error:{name:'TypeError',message:'secret prompt',stack:'TypeError secret at http://127.0.0.1:8770/agent.js:52:3'},filename:'http://127.0.0.1:8770/agent.js?key=secret',lineno:52,colno:3});
assert.equal(calls.length,5);assert(calls.every(c=>c.url==='/api/agent/client-error'));
assert.deepEqual(calls[0].body,{error_type:'TypeError',file:'agent.js',line:52,column:3});
assert(!JSON.stringify(calls).includes('secret'));
'''
    result=subprocess.run(['node','-'],input=script,text=True,encoding='utf-8',capture_output=True,timeout=15)
    assert result.returncode==0,result.stderr
