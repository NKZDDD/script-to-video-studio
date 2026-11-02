from pathlib import Path
import shutil
import subprocess

import pytest


def test_auto_form_uses_server_rules_and_preserves_manual_values():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node required for UI test')
    source = (Path(__file__).resolve().parents[1] / 'studio_auth/web/provider-monitor.js').read_text(encoding='utf-8')
    functions = source[source.index('  function suggest()'):source.index('  f.elements.provider.onchange')]
    script = r'''
const assert=require('node:assert/strict');
const controls=Object.fromEntries(['provider','key_id','mode','url','kind','result_path','auth','key_field'].map(k=>[k,{value:'',disabled:false}]));
const f={elements:controls},manual={hidden:false,querySelectorAll:()=>['url','kind','result_path','auth','key_field'].map(k=>controls[k])},note={textContent:''};
const q=s=>s==='[data-manual]'?manual:note;
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
