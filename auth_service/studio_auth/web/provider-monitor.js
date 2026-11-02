'use strict';
(() => {
 const button=document.createElement('button');button.textContent='服务商状态';
 document.querySelector('#workspace .heading .actions').prepend(button);
 const page=document.createElement('section');page.id='provider-monitor';page.hidden=true;
 page.setAttribute('aria-labelledby','monitor-title');document.querySelector('main').append(page);
 let timer=null,epoch=0,request=0,data=null,keys=null;
 const q=s=>page.querySelector(s),active=()=>!page.hidden&&Boolean(csrf);
 const labels={ok:'接口正常',authentication:'鉴权拒绝',rate_limited:'限流',server_error:'服务商错误',http_error:'HTTP 错误',redirect:'接口重定向',dns:'DNS 失败',tls:'TLS / 证书错误',timeout:'超时',connection:'连接失败',invalid_response:'响应不符合约定',unsafe_target:'目标地址不可用',not_configured:'凭据未配置'};
 const result=r=>!r?'<span class="muted">尚无结果</span>':`<span class="pm-status ${r.stale?'stale':r.category==='ok'?'ok':'bad'}">${r.stale?'结果已过期 · ':''}${esc(labels[r.category]||r.category)}</span><div>${r.status?'HTTP '+esc(r.status)+' · ':''}${(r.duration_ms/1000).toFixed(3)} 秒${r.model_count==null?'':' · '+esc(r.model_count)+' 个模型'}</div><small>${esc(date(r.at))}</small>`;
 function note(text,error=false){const n=q('[data-note]');if(n){n.textContent=text;n.hidden=!text;n.classList.toggle('error',error);}}
 function mount(){
  page.innerHTML=`<a class="pm-back" href="#accounts">← 返回账号管理</a>
  <div class="pm-heading"><div><h1 id="monitor-title" tabindex="-1">服务商状态</h1><p>对比服务器与用户电脑访问服务商的结果。</p></div><div class="actions"><button data-refresh>刷新结果</button><button data-run class="primary">立即检测服务器</button></div></div>
  <p data-note class="pm-notice" role="status" hidden></p>
  <div class="pm-adapter"><div><strong>监测规则</strong><p data-adapter-note>正在读取已发布适配包…</p></div><button data-adapters>查看适配包</button></div>
  <section class="card pm-card"><div class="pm-section-title"><div><h2>服务器检测</h2><p>每家选择一把检测 Key。启用后约每 5 分钟检查一次，不生成图片或视频。</p></div><button data-new>新增监测</button></div>
  <div class="table-wrap"><table class="pm-servers"><thead><tr><th>服务商 / 检测 Key</th><th>检测范围</th><th>服务器结果</th><th>操作</th></tr></thead><tbody data-servers><tr><td colspan="4">正在读取…</td></tr></tbody></table></div></section>
  <section class="card pm-card" id="monitor-config"><div class="pm-section-title"><div><h2 data-form-title>新增监测</h2><p>接口从已发布适配包读取；需要自定义时再切换到手动设置。</p></div></div>
  <form data-form><div class="pm-grid"><label>服务商<select name="provider" required></select></label><label>检测 Key<select name="key_id" required></select></label>
  <label class="pm-wide">接口来源<select name="mode"><option value="auto">自动读取适配包（推荐）</option><option value="manual">手动设置接口</option></select></label></div>
  <p data-rule-note class="pm-rule" role="status"></p>
  <div data-manual hidden><div class="pm-grid"><label class="pm-wide">完整检测地址（HTTPS）<input name="url" type="url" maxlength="1000" placeholder="https://服务商域名/v1/models"></label>
  <label>响应检查<select name="kind"><option value="models">模型列表 JSON</option><option value="health">健康状态 JSON</option><option value="http">仅检查 HTTP 2xx</option></select></label><label>JSON 结果路径<input name="result_path" value="data" maxlength="100" placeholder="data / ok / data.models；根值留空"></label>
  <label>携带凭据<select name="auth"><option value="bearer">Authorization: Bearer</option><option value="x-api-key">x-api-key</option><option value="api-key">api-key</option><option value="none">不携带 Key</option></select></label><label>Key 字段<select name="key_field"><option>api_key</option><option>image_1k_api_key</option><option>image_4k_api_key</option><option>video_api_key</option></select></label></div>
  <p class="help">模型列表路径须返回数组；健康路径须为 true、ok、healthy、success 或 up。仅 HTTP 模式不验证正文；不要使用生成接口作为检测地址。</p></div>
  <label class="pm-check"><input type="checkbox" name="enabled" checked><span>启用服务器及获授权客户端监测</span></label>
  <div class="pm-form-actions"><p data-form-note role="status"></p><button type="button" data-reset>重置表单</button><button class="primary" data-save>保存监测配置</button></div></form></section>
  <section class="card pm-card"><div class="pm-section-title"><div><h2>客户端最近结果</h2><p>仅已获授权使用检测 Key 的新版 Studio 参与。暂无记录可能是未启动、未授权、旧版本或尚未补报，不能据此认定用户断网。</p></div></div>
  <div class="table-wrap"><table><thead><tr><th>账号 / 设备</th><th>服务商</th><th>客户端结果</th><th>服务器收到</th></tr></thead><tbody data-clients><tr><td colspan="4">正在读取…</td></tr></tbody></table></div></section>
  <p class="pm-footer">页面每 5 秒刷新结果。接口正常不等于所有模型可生成。客户端时间受设备时钟影响；最多显示最近 200 个账号设备 / 服务商组合。记录保留最多 7 天、全站 50,000 条；超过 15 分钟标记过期，状态不会自动阻止生产或停用 Key。</p>`;
  q('[data-save]').disabled=true;q('[data-form]').onsubmit=e=>e.preventDefault();
  q('[data-refresh]').onclick=()=>refresh(true);
  q('[data-run]').onclick=async e=>{const current=epoch,b=e.currentTarget;b.disabled=true;try{const d=await api('provider-monitor/run',{});if(current===epoch&&active()){note(d.queued?`已安排 ${d.queued} 家检测，结果会自动刷新。`:'正在检测、未启用或距离上次不足 30 秒。');await refresh();}}catch(err){if(current===epoch&&active())note(err.message,true);}finally{b.disabled=false;}};
  q('[data-new]').onclick=()=>{if(!keys)return;resetForm();q('[data-form]').scrollIntoView({block:'center'});q('[name=provider]').focus();};
  q('[data-reset]').onclick=()=>{if(keys)resetForm();};
  q('[data-adapters]').onclick=()=>document.getElementById('adapter-library')?.click();
  page.addEventListener('click',edit);q('#monitor-title').focus();
 }
 function renderResults(){
  const names=keys.providers||{},count=data.adapter_rule_count||0;
  q('[data-adapter-note]').textContent=!data.adapter_version?'尚未发布适配包。请先导入带监测规则的适配包，再点击“发布此版本”。':count?`当前已发布 ${data.adapter_version} · ${count} 家提供自动监测规则。导入但未发布的版本不会生效。`:`当前已发布 ${data.adapter_version}，但其中没有自动监测规则。请导入带规则的新版适配包并发布，或使用手动设置。`;
  q('.pm-adapter').classList.toggle('warning',!count);
  q('[data-servers]').innerHTML=data.monitors.map(s=>`<tr><td data-label="服务商 / 检测 Key"><strong>${esc(names[s.provider]||s.provider)}</strong><br><small>${esc(s.key_name)}</small></td><td data-label="检测范围">${s.mode==='auto'?'适配包自动配置':'手动设置'}<br><small>${s.auth==='none'?'未携带 Key，仅检查入口':'携带 Key 请求，权限由接口判定'}</small><div class="pm-url">${esc(s.url)}</div></td><td data-label="服务器结果">${!s.enabled?'已停用':!s.configured?esc(s.configuration_error||'Key 停用、归属或地址已变化，请重新配置'):result(s.server)}${data.pending.includes(s.provider)?'<div>正在检测…</div>':''}</td><td data-label="操作"><button data-edit="${esc(s.provider)}">编辑</button></td></tr>`).join('')||'<tr><td colspan="4" class="pm-empty">尚未启用监测。在下方选择服务商和检测 Key 后保存。</td></tr>';
  q('[data-clients]').innerHTML=data.clients.map(r=>`<tr><td data-label="账号 / 设备"><strong>${esc(r.username)}</strong><br><small>${esc(r.device)} · ${esc(r.version||'未知版本')}</small></td><td data-label="服务商">${esc(names[r.provider]||r.provider)}</td><td data-label="客户端结果">${result(r)}</td><td data-label="服务器收到">${esc(date(r.received_at))}</td></tr>`).join('')||'<tr><td colspan="4" class="pm-empty">暂无客户端结果</td></tr>';
  if(data.recording_error)note('监测执行或保存异常，部分结果可能缺失。',true);
 }
 function fillKeys(){const f=q('[data-form]');f.elements.key_id.innerHTML=keys.keys.filter(k=>k.enabled&&k.provider===f.elements.provider.value).map(k=>`<option value="${esc(k.id)}">${esc(k.name)}</option>`).join('');}
 function suggest(){const f=q('[data-form]'),p=(data.presets||[]).find(p=>p.key_id===f.elements.key_id.value);for(const key of ['url','kind','result_path','auth','key_field'])f.elements[key].value=p?.available?p[key]:({url:'',kind:'models',result_path:'data',auth:'bearer',key_field:'api_key'})[key];updateMode();}
 function updateMode(){const f=q('[data-form]'),auto=f.elements.mode.value==='auto',p=(data.presets||[]).find(p=>p.key_id===f.elements.key_id.value);q('[data-manual]').hidden=auto;q('[data-manual]').querySelectorAll('input,select').forEach(x=>x.disabled=auto);f.elements.url.required=!auto;q('[data-rule-note]').textContent=!f.elements.key_id.value?'请先到 Key 库添加并启用检测 Key。':auto?(p?.available?`已识别：${p.url}；${p.auth==='none'?'公开接口，不验证 Key':'携带所选 Key 请求'}。适配包发布新规则后自动生效。`:p?.reason||'此 Key 暂无可用规则，请发布新版适配包或选择手动设置。'):'手动设置保留你填写的接口，不跟随适配包覆盖。';q('[data-rule-note]').classList.toggle('warning',auto&&!p?.available);}
 function resetForm(){
  const f=q('[data-form]');f.reset();q('[data-form-title]').textContent='新增监测';q('[data-form-note]').textContent='';
  f.elements.provider.innerHTML=Object.entries(keys.providers||{}).filter(([id])=>keys.keys.some(k=>k.provider===id&&k.enabled)).map(([id,name])=>`<option value="${esc(id)}">${esc(name)}</option>`).join('');
  fillKeys();suggest();q('[data-save]').disabled=!f.elements.key_id.value;
  f.elements.provider.onchange=()=>{fillKeys();suggest();};f.elements.key_id.onchange=suggest;f.elements.mode.onchange=updateMode;
  f.elements.kind.onchange=()=>{f.elements.result_path.value=f.elements.kind.value==='health'?'ok':f.elements.kind.value==='models'?'data':'';};
  f.onsubmit=async e=>{e.preventDefault();const save=q('[data-save]'),current=epoch;save.disabled=true;q('[data-form-note]').classList.remove('error');try{await api('provider-monitor',{...Object.fromEntries(new FormData(f)),enabled:f.elements.enabled.checked});if(current===epoch&&active()){q('[data-form-note]').textContent='监测配置已保存。';await refresh(true);}}catch(err){if(current===epoch&&active()){q('[data-form-note]').textContent=err.message;q('[data-form-note]').classList.add('error');}}finally{save.disabled=false;}};
 }
 function edit(e){const b=e.target.closest('[data-edit]');if(!b||!data)return;const s=data.monitors.find(x=>x.provider===b.dataset.edit);if(!s)return;resetForm();const f=q('[data-form]');f.elements.provider.value=s.provider;fillKeys();for(const key of ['key_id','url','kind','result_path','auth','key_field'])f.elements[key].value=s[key];f.elements.mode.value=s.mode||'manual';f.elements.enabled.checked=s.enabled;q('[data-form-title]').textContent='编辑监测 · '+(keys.providers[s.provider]||s.provider);updateMode();f.scrollIntoView({block:'center'});f.elements.key_id.focus();}
 async function refresh(force=false){
  clearTimeout(timer);const current=epoch,ticket=++request;
  try{const [d,k]=await Promise.all([api('provider-monitor'),!keys||force?api('keys'):Promise.resolve(keys)]);if(current!==epoch||ticket!==request||!active())return;const initial=!data;data=d;keys=k;renderResults();
   // Refresh results without replacing the form or discarding unsaved edits.
   if(initial)resetForm();else updateMode();
  }catch(e){if(current===epoch&&ticket===request&&active())note(e.message,true);}
  if(current===epoch&&ticket===request&&active())timer=setTimeout(()=>refresh(),5000);
 }
 function route(){
  const show=Boolean(csrf)&&location.hash==='#provider-monitor';document.querySelector('main').classList.toggle('monitor-active',show);document.querySelector('#workspace').hidden=!csrf||show;
  if(show&&page.hidden){epoch++;page.hidden=false;mount();refresh(true);}
  else if(!show&&!page.hidden){epoch++;request++;clearTimeout(timer);page.hidden=true;page.removeEventListener('click',edit);page.replaceChildren();data=null;keys=null;}
 }
 button.onclick=()=>{location.hash='provider-monitor';};window.addEventListener('hashchange',route);window.addEventListener('studio-admin-auth',route);route();
})();
