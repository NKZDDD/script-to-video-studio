from pathlib import Path
import shutil
import subprocess

import pytest


WEB = Path(__file__).resolve().parents[1]/'studio_auth/web'


def run_js(script):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node required for browser handler tests')
    result = subprocess.run([node], input=script, encoding='utf-8', capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr


def test_remove_chip_syncs_visible_checkbox_and_saved_permissions():
    source = (WEB/'provisioning.js').read_text(encoding='utf-8')
    fn = source[source.index('async function provisionKeyUsers('):source.index('async function importKeyConfig(')]
    run_js(r'''
const assert=require('node:assert/strict');
const nodes=new Map();let drops=[],checks=[],saved=null;
function element(id){
 if(!nodes.has(id))nodes.set(id,{value:'',textContent:'',disabled:false,
  set innerHTML(html){
   if(id==='#key-user-chosen')drops=[...html.matchAll(/data-drop="([^"]+)"/g)].map(m=>({dataset:{drop:m[1]}}));
   if(id==='#key-user-list')checks=[...html.matchAll(/data-user="([^"]+)"\s*(checked)?/g)].map(m=>({dataset:{user:m[1]},checked:!!m[2]}));
  }});
 return nodes.get(id);
}
const provisionDialog={querySelector:element,querySelectorAll:s=>s==='[data-drop]'?drops:s==='[data-user]'?checks:[]};
const shell=()=>{},esc=x=>x,safe=fn=>fn(),keyLibrary=async()=>{},pError=e=>{throw e;};
const api=async(path,body)=>{
 if(body){saved=body;return {};}
 if(path.startsWith('keys/'))return {usernames:['customer','another']};
 // another is on a different search page; editing customer must preserve it.
 return {users:[{username:'customer',enabled:true}]};
};
''' + fn + r'''
(async()=>{
 await provisionKeyUsers({id:'fake-key',name:'fake'});
 assert.equal(checks[0].checked,true);
 drops.find(b=>b.dataset.drop==='customer').onclick();
 assert.equal(checks[0].checked,false);
 await element('#save-key-users').onclick({currentTarget:element('#save-key-users')});
 assert.deepEqual(saved.usernames,['another']);
 checks[0].checked=true;checks[0].onchange();
 assert(drops.some(b=>b.dataset.drop==='customer'));
 await element('#save-key-users').onclick({currentTarget:element('#save-key-users')});
 assert.deepEqual(new Set(saved.usernames),new Set(['customer','another']));
 checks[0].checked=false;checks[0].onchange();
 assert(!drops.some(b=>b.dataset.drop==='customer'));
})().catch(e=>{console.error(e);process.exitCode=1;});
''')


def test_backdrop_dismiss_works_for_dynamic_dialogs_without_closing_inside_clicks():
    source = (WEB/'app.js').read_text(encoding='utf-8')
    fn = source[source.index('function installDialogDismiss('):source.index('installDialogDismiss(document);')]
    run_js(r'''
const assert=require('node:assert/strict');
const handlers={};const root={addEventListener:(n,f)=>handlers[n]=f};
''' + fn + r'''
installDialogDismiss(root);
const makeDialog=()=>({tagName:'DIALOG',open:true,closed:0,close(){this.closed++;this.open=false;},getBoundingClientRect:()=>({left:20,right:200,top:20,bottom:300})});
const first=makeDialog(),second=makeDialog();
function event(target,x,y){return {target,clientX:x,clientY:y,button:0};}
function click(start,end=start){handlers.pointerdown(start);handlers.click(end);}
click(event(first,30,30));assert.equal(first.closed,0); // dialog padding
click(event({tagName:'INPUT'},50,50));assert.equal(first.closed,0);
click(event(first,50,50),event(first,5,5));assert.equal(first.closed,0); // drag out
click(event(first,5,5),event(first,30,30));assert.equal(first.closed,0); // drag in
handlers.pointerdown(event(first,5,5));handlers.pointercancel();handlers.click(event(first,5,5));assert.equal(first.closed,0);
click(event(second,5,5));assert.equal(second.closed,1);assert.equal(first.closed,0);
click(event(first,5,5));assert.equal(first.closed,1);
''')
