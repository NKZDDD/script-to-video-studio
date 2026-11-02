from pathlib import Path
import shutil
import subprocess

import pytest


def test_local_monitor_escapes_results_and_does_not_overwrite_other_settings():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node required for UI tests')
    source = (Path(__file__).resolve().parents[1] / 'web/agent.js').read_text(encoding='utf-8')
    fn = source[source.index('async function monitorForm(){'):source.index('async function updateForm(')]
    script = r'''
const assert=require('node:assert/strict');
const S={page:'settings',tab:'monitor'};
const panel={innerHTML:''};const $=()=>panel;
const esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const btn=()=>'',providerName=()=>'<img src=x>',notice=msg=>{throw Error(msg)};
const clearTimeout=()=>{},setTimeout=()=>1;
let fetch=async()=>({ok:true,json:async()=>({state:'ready',results:[{provider:'gate',key_name:'<script>secret</script>',auth:'none',category:'timeout',status:0,duration_ms:10000,at:1,stale:true}]})});
''' + fn + r'''
(async()=>{
 await monitorForm();
 assert(panel.innerHTML.includes('超时'));
 assert(panel.innerHTML.includes('结果已过期'));
 assert(panel.innerHTML.includes('&lt;script&gt;'));
 assert(!panel.innerHTML.includes('<script>'));
 let finish;
 fetch=()=>new Promise(resolve=>{finish=resolve;});
 const waiting=monitorForm();S.tab='providers';panel.innerHTML='provider form';
 finish({ok:true,json:async()=>({state:'waiting',results:[]})});await waiting;
 assert.equal(panel.innerHTML,'provider form');
})().catch(e=>{console.error(e);process.exitCode=1;});
'''
    done = subprocess.run([node], input=script, encoding='utf-8', capture_output=True, timeout=20)
    assert done.returncode == 0, done.stderr
