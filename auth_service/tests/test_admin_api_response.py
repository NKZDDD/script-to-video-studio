"""Exercise the browser API helper against actual HTTP-style error responses."""
from pathlib import Path
import re
import shutil
import subprocess

import pytest

from studio_auth.adapter_format import MAX_BYTES

ROOT = Path(__file__).resolve().parents[1]


def test_proxy_allows_application_import_budget():
    template = (ROOT / 'deploy/nginx-location.conf').read_text(encoding='utf-8')
    value, unit = re.search(r'client_max_body_size\s+(\d+)([km]?);', template).groups()
    proxy_limit = int(value) * {'': 1, 'k': 1024, 'm': 1024 * 1024}[unit]
    # JSON envelope is additional to the permitted 2 MB bundle.
    assert proxy_limit >= MAX_BYTES + 64


@pytest.mark.skipif(not shutil.which('node'), reason='Node.js is needed to exercise browser JavaScript')
def test_browser_reports_proxy_413_without_exposing_response_body():
    source = (ROOT / 'studio_auth/web/app.js').read_text(encoding='utf-8')
    helper = source.split('async function authorized(', 1)[0]
    script = "const assert=require('node:assert/strict');\n" + helper + r'''
(async()=>{
 const raw='<html>private upstream details</html>';
 for(const status of [413,502,200]){
  global.fetch=async()=>new Response(raw,{status,headers:{'Content-Type':'text/html'}});
  await assert.rejects(()=>api('adapters/import',{bundle:{}}),e=>{
   assert.ok(e.message.includes('HTTP '+status));
   assert.ok(!e.message.includes('private upstream details'));
   if(status===413){assert.ok(e.message.includes('client_max_body_size'));assert.ok(e.message.includes('3m'));}
   return true;
  });
 }
 global.fetch=async()=>new Response(JSON.stringify({error:{message:'application validation failed'}}),{status:422});
 await assert.rejects(()=>api('adapters/import',{}),{message:'application validation failed'});
 global.fetch=async()=>new Response(JSON.stringify({ok:true,version:'example'}),{status:200});
 assert.deepEqual(await api('adapters/import',{}),{ok:true,version:'example'});
 console.log('proxy and application responses passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
'''
    result = subprocess.run(['node', '-'], input=script, capture_output=True, encoding='utf-8', timeout=20)
    assert result.returncode == 0, result.stderr
