// Exercise the real update screen with a minimal DOM, including forced failures.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const nodes = new Map();
const querySelector = selector => {
  if (!nodes.has(selector)) nodes.set(selector, {textContent:'', hidden:false, disabled:false});
  return nodes.get(selector);
};
let tick, click, visible = false;
const screen = {setAttribute(){}, querySelector, addEventListener(type, callback){click=callback;}};
let data = {current:'2026.10.22.3', allowed:false, code:'update_required', gate_message:'必须更新',
  phase:'error', message:'安装包下载失败（HTTP 404）', release:{version:'2026.10.23.1', bytes:100}, desktop:true};
const requests = [];
const context = {document:{createElement:()=>screen, querySelector:()=>null,
  body:{append(){},classList:{add(){visible=true;},remove(){visible=false;}}}},
  window:{}, setInterval:callback=>{tick=callback;}, confirm:()=>true,
  fetch:async url=>{requests.push(url);return {ok:true,json:async()=>data};}};
vm.runInNewContext(fs.readFileSync(require('node:path').join(__dirname,'../web/updates.js'),'utf8'),context);
const flush = () => new Promise(resolve=>setImmediate(resolve));
(async()=>{
  await flush();
  assert(visible);
  assert.match(querySelector('[data-message]').textContent,/HTTP 404/);
  assert.match(querySelector('[data-message]').textContent,/必须更新/);
  assert.equal(querySelector('[data-action="download"]').textContent,'重试下载');
  data = {...data,phase:'ready',message:'更新已准备好，重启即可完成',received:100,active_jobs:1};
  await tick();
  assert.equal(querySelector('progress').hidden,false);
  assert.equal(querySelector('progress').value,100);
  assert.equal(querySelector('[data-action="install"]').disabled,true);
  data.active_jobs=0;
  await tick();
  assert.equal(querySelector('[data-action="install"]').disabled,false);
  data={...data,phase:'installing',message:'正在重启并应用更新，请稍候'};
  await click({target:{dataset:{action:'install'}}});
  assert.equal(requests.at(-1),'/api/updates/install');
  context.fetch=async()=>{throw Error('closed');};
  await tick();
  assert.match(querySelector('[data-message]').textContent,/正在重启/);
  assert.doesNotMatch(querySelector('[data-message]').textContent,/无法连接/);
  console.log('update screen: forced errors, progress, busy jobs and restart verified');
})().catch(error=>{console.error(error);process.exitCode=1;});
