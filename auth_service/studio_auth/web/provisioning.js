'use strict';
const provisionDialog=document.createElement('dialog');
provisionDialog.id='provisioning';document.body.append(provisionDialog);
let usagePage=1,usageQuery='',providerNames={};
const providerLabel=id=>providerNames[id]||id;
const keyFields={api_key:'Key / 多账号凭据',image_1k_api_key:'原 TH 图片 Key',image_4k_api_key:'原 Native 图片 Key',video_api_key:'原视频 Key'};
function shell(title,body){
 provisionDialog.innerHTML=`<div class="dialog-title"><h2>${esc(title)}</h2><button data-close>关闭</button></div><p id="provision-error" role="alert"></p>${body}`;
 provisionDialog.querySelector('[data-close]').onclick=()=>provisionDialog.close();
 if(!provisionDialog.open)provisionDialog.showModal();
 provisionDialog.scrollTop=0;
}
function pError(e){const n=provisionDialog.querySelector('#provision-error');if(n)n.textContent=e.message;}
async function keyLibrary(){
 const d=await api('keys');providerNames=d.providers;
 shell('Key 库',`<p class="help">统一保存并命名，再到账号中勾选授权。客户端分别读取每把 Key 的模型，选择模型时自动使用对应 Key；不会因失败自动更换 Key。</p>
 <div class="table-wrap"><table><thead><tr><th>名称</th><th>服务商</th><th>模型来源 / 状态</th><th>操作</th></tr></thead><tbody>${d.keys.map((k,i)=>`<tr><td>${esc(k.name)}</td><td>${esc(providerLabel(k.provider))}</td><td>${k.models.length?'绑定 '+k.models.length+' 个模型':'按 Key 自动拉取'} · ${k.enabled?'启用':'停用'}</td><td><button data-key-edit="${i}">编辑</button></td></tr>`).join('')||'<tr><td colspan="4">尚无 Key，请在下方新建。</td></tr>'}</tbody></table></div>
 <h3 id="key-heading">新增 Key</h3><form id="key-edit"><input type="hidden" name="id"><label>Key 名称<input name="name" required maxlength="100" placeholder="例如：巨轮视频模型组 A"></label><label>服务商<select name="provider">${Object.entries(providerNames).map(([id,label])=>`<option value="${esc(id)}">${esc(label)}</option>`).join('')}<option value="__custom__">自定义外挂…</option></select></label><label id="custom-provider" hidden>外挂服务商 ID<input name="custom_provider" maxlength="64" pattern="[a-zA-Z0-9_-]{1,64}"></label><label>Base URL<input name="base_url" type="url" placeholder="留空使用 Studio 内置 HTTPS 地址"></label><label class="check"><input name="enabled" type="checkbox" checked>启用此 Key</label><div id="key-secrets"></div><label>绑定模型（可选，一行一个完整模型 ID）<textarea name="models" rows="4" placeholder="留空：客户端用这把 Key 拉取模型。若服务商清单不区分 Key 权限或没有模型接口，在这里填写准确的模型 ID。"></textarea></label><p class="help">每把 Key 建成独立条目。编辑时密钥留空保留，不回显。停用后停止下发，已运行批次继续使用当时配置。绑定模型优先于自动拉取。</p><div class="actions"><button type="button" id="key-new">清空并新建</button><button class="primary">保存 Key</button></div></form>`);
 const f=provisionDialog.querySelector('#key-edit');
 function secrets(configured=[]){
  const fields=configured.length?configured:['api_key'];
  provisionDialog.querySelector('#key-secrets').innerHTML=fields.map(k=>`<label>${esc(keyFields[k]||k)}<textarea name="${esc(k)}" rows="2" autocomplete="off" maxlength="${k==='api_key'?8000:2000}" placeholder="${configured.includes(k)?'已保存；留空保留':'填写凭据'}"></textarea></label>`).join('');
  const custom=f.elements.provider.value==='__custom__';provisionDialog.querySelector('#custom-provider').hidden=!custom;f.elements.custom_provider.required=custom;
 }
 f.elements.provider.onchange=()=>secrets();secrets();
 provisionDialog.querySelector('#key-new').onclick=()=>{f.reset();f.elements.provider.disabled=false;f.elements.id.value='';provisionDialog.querySelector('#key-heading').textContent='新增 Key';secrets();};
 provisionDialog.querySelectorAll('[data-key-edit]').forEach(b=>b.onclick=()=>{
  const k=d.keys[+b.dataset.keyEdit];f.reset();f.elements.id.value=k.id;f.elements.name.value=k.name;
  if(!providerNames[k.provider])f.elements.provider.add(new Option(k.provider,k.provider));
  f.elements.provider.value=k.provider;f.elements.provider.disabled=true;f.elements.base_url.value=k.base_url;
  f.elements.enabled.checked=k.enabled;f.elements.models.value=k.models.join('\n');
  provisionDialog.querySelector('#key-heading').textContent='编辑 Key · '+k.name;secrets(k.configured);f.scrollIntoView({block:'start',behavior:'smooth'});
 });
 f.onsubmit=async e=>{
  e.preventDefault();const button=f.querySelector('button.primary');button.disabled=true;
  try{const v=Object.fromEntries(new FormData(f));v.provider=f.elements.provider.value==='__custom__'?v.custom_provider:f.elements.provider.value;delete v.custom_provider;v.enabled=f.elements.enabled.checked;v.models=v.models.split(/\r?\n/).map(x=>x.trim()).filter(Boolean);await api('keys',v);f.reset();await keyLibrary();}catch(e){pError(e);}finally{button.disabled=false;}
 };
}
async function provisionProviders(name){
 const [d,a]=await Promise.all([api('keys'),api('users/'+encodeURIComponent(name)+'/keys')]);providerNames=d.providers;
 let selected=[...a.key_ids];const byId=new Map(d.keys.map(k=>[k.id,k]));
 shell('分配 Key · '+name,`<p class="help">勾选账号可使用的 Key。不同 Key 对应的模型会在客户端合并显示，客户端按模型选 Key；同一模型存在于多把 Key 时使用列表中靠前的一把，不在失败后换 Key。保存后在客户端点击“刷新后台配置”。未分配的服务商仍可使用个人配置。</p><h3>已选 Key</h3><div id="selected-keys"></div><h3>勾选 Key 库</h3><div class="table-wrap"><table><thead><tr><th>授权</th><th>名称</th><th>服务商</th><th>状态</th></tr></thead><tbody>${d.keys.map(k=>`<tr><td><input class="key-checkbox" type="checkbox" data-key-id="${esc(k.id)}" aria-label="授权 ${esc(k.name)}" ${selected.includes(k.id)?'checked':''}></td><td>${esc(k.name)}</td><td>${esc(providerLabel(k.provider))}</td><td>${k.enabled?'启用':'停用'}</td></tr>`).join('')||'<tr><td colspan="4">请先在 Key 库新建 Key。</td></tr>'}</tbody></table></div><div class="actions"><button id="save-key-assignment" class="primary">保存授权</button></div>`);
 function render(){
  provisionDialog.querySelector('#selected-keys').innerHTML=selected.map((id,i)=>{const k=byId.get(id);return `<div class="key-order"><span><strong>${esc(k.name)}</strong><small>${esc(providerLabel(k.provider))}${k.enabled?'':' · 已停用'}</small></span><button data-move="${i}" data-step="-1" ${i===0?'disabled':''} aria-label="上移 ${esc(k.name)}">上移</button><button data-move="${i}" data-step="1" ${i===selected.length-1?'disabled':''} aria-label="下移 ${esc(k.name)}">下移</button></div>`;}).join('')||'<p class="muted">未分配 Key</p>';
  provisionDialog.querySelectorAll('[data-move]').forEach(b=>b.onclick=()=>{const i=+b.dataset.move,j=i+(+b.dataset.step);[selected[i],selected[j]]=[selected[j],selected[i]];render();});
 }
 render();provisionDialog.querySelectorAll('[data-key-id]').forEach(c=>c.onchange=()=>{selected=selected.filter(id=>id!==c.dataset.keyId);if(c.checked)selected.push(c.dataset.keyId);render();});
 const save=provisionDialog.querySelector('#save-key-assignment');save.onclick=async()=>{save.disabled=true;try{await api('users/'+encodeURIComponent(name)+'/keys',{key_ids:selected});provisionDialog.close();}catch(e){pError(e);}finally{save.disabled=false;}};
}
const usageStatus={attempted:'提交尝试',submitted:'已获取任务 ID',completed:'已完成并落盘',failed:'失败',pending:'待取回',unknown:'提交结果不明'};
const usageSource={managed:'后台分配',personal:'个人配置',unknown:'旧任务来源未知'};
async function showUsage(name,reset=true){
 if(reset){usagePage=1;usageQuery='';}
 const url='users/'+encodeURIComponent(name)+'/usage';
 const [d,lib]=await Promise.all([api(url+'?page='+usagePage+usageQuery),api('keys')]);providerNames=lib.providers;
 const params=new URLSearchParams(usageQuery),totals=new Map();
 for(const r of d.summary){const id=[r.key_id||'',r.provider,r.source,r.resumed].join('|');if(!totals.has(id))totals.set(id,{...r,counts:{}});const t=totals.get(id);t.counts[r.status]=(t.counts[r.status]||0)+r.count;}
 shell('用量对账 · '+name,`<p class="help">${esc(d.notice)} 当前账号：${esc(name)}。成功、失败按生成尝试统计；同一任务反复查询不会增加次数。</p><form id="usage-filter" class="toolbar"><label>服务商<select name="provider"><option value="">全部服务商</option>${Object.entries(providerNames).map(([id,label])=>`<option value="${esc(id)}" ${params.get('provider')===id?'selected':''}>${esc(label)}</option>`).join('')}</select></label><label>Key<select name="key_id"><option value="">全部 Key</option>${lib.keys.map(k=>`<option value="${esc(k.id)}" ${params.get('key_id')===k.id?'selected':''}>${esc(k.name)}</option>`).join('')}</select></label><label>来源<select name="source"><option value="">全部</option>${Object.entries(usageSource).map(([id,label])=>`<option value="${id}" ${params.get('source')===id?'selected':''}>${label}</option>`).join('')}</select></label><label>开始时间<input name="since" type="datetime-local"></label><label>结束时间<input name="until" type="datetime-local"></label><button>筛选</button><button type="button" id="export-usage">导出 CSV</button></form><p>共 ${d.total} 条记录；第 ${d.page} 页</p>
 <div class="table-wrap"><table><thead><tr><th>Key / 服务商</th><th>成功</th><th>失败</th><th>处理中 / 待取回</th><th>结果不明</th></tr></thead><tbody>${[...totals.values()].map(r=>`<tr><td>${esc(lib.keys.find(k=>k.id===r.key_id)?.name||r.key_name||'旧记录 / 未标记 Key')}<br><small>${esc(providerLabel(r.provider))} · ${esc(usageSource[r.source])}${r.resumed?' · 仅取回':''}</small></td><td>${r.counts.completed||0}</td><td>${r.counts.failed||0}</td><td>${(r.counts.pending||0)+(r.counts.submitted||0)+(r.counts.attempted||0)}</td><td>${r.counts.unknown||0}</td></tr>`).join('')||'<tr><td colspan="5">暂无上报</td></tr>'}</tbody></table>
 <table><thead><tr><th>时间 / Key</th><th>模型 / 状态</th><th>服务商任务 ID</th></tr></thead><tbody>${d.events.map(e=>`<tr><td>${esc(date(e.started_at))}<br>${esc(e.key_name||'旧记录 / 未标记 Key')}<br><small>${esc(e.key_id||'')}</small></td><td>${esc(providerLabel(e.provider)+' / '+e.model)}<br>${esc(usageStatus[e.status])}</td><td>${esc(e.task_id||'未获得任务 ID')}${e.resumed?'<br>仅取回':''}</td></tr>`).join('')||'<tr><td colspan="3">暂无记录</td></tr>'}</tbody></table></div><div class="pager"><button id="usage-prev" ${usagePage<=1?'disabled':''}>上一页</button><button id="usage-next" ${usagePage*50>=d.total?'disabled':''}>下一页</button></div>`);
 const f=provisionDialog.querySelector('form');for(const k of ['since','until'])if(params.has(k))f.elements[k].value=localTime(Number(params.get(k)));
 f.onsubmit=e=>{e.preventDefault();const v=new FormData(f),q=new URLSearchParams();for(const k of ['provider','source','key_id'])if(v.get(k))q.set(k,v.get(k));for(const k of ['since','until'])if(v.get(k))q.set(k,Math.floor(new Date(v.get(k)).getTime()/1000));usageQuery='&'+q;usagePage=1;showUsage(name,false).catch(pError);};
 provisionDialog.querySelector('#usage-prev').onclick=()=>{usagePage--;showUsage(name,false).catch(pError);};
 provisionDialog.querySelector('#usage-next').onclick=()=>{usagePage++;showUsage(name,false).catch(pError);};
 provisionDialog.querySelector('#export-usage').onclick=async()=>{try{const r=await fetch('/admin/api/'+url+'?export=true'+usageQuery,{cache:'no-store'});if(!r.ok){const d=await r.json();throw Error(d.error?.message||'导出失败');}const u=URL.createObjectURL(await r.blob()),a=document.createElement('a');a.href=u;a.download='studio-usage.csv';a.click();setTimeout(()=>URL.revokeObjectURL(u),1000);}catch(e){pError(e);}};
}
document.querySelector('#key-library').onclick=()=>safe(keyLibrary);
document.querySelector('#users').addEventListener('click',e=>{const b=e.target.closest('[data-action]');if(!b||!['providers','usage'].includes(b.dataset.action))return;e.stopImmediatePropagation();const name=rows[+b.dataset.index].username;safe(()=>b.dataset.action==='providers'?provisionProviders(name):showUsage(name));},true);
provisionDialog.addEventListener('close',()=>provisionDialog.replaceChildren());
