'use strict';
(() => {
 const button=document.createElement('button');button.textContent='服务商状态';
 document.querySelector('#workspace .heading .actions').prepend(button);
 const dialog=document.createElement('dialog');dialog.id='provider-monitor';
 dialog.setAttribute('aria-label','服务商状态监测');document.body.append(dialog);
 let timer=null,generation=0,data=null,keys=null;
 const labels={ok:'接口正常',authentication:'鉴权拒绝',rate_limited:'限流',server_error:'服务商错误',http_error:'HTTP 错误',redirect:'接口重定向',dns:'DNS 失败',tls:'TLS / 证书错误',timeout:'超时',connection:'连接失败',invalid_response:'响应不符合约定',unsafe_target:'目标地址不可用',not_configured:'凭据未配置'};
 const result=r=>!r?'尚无结果':`${r.stale?'结果已过期 · ':''}${esc(labels[r.category]||r.category)}${r.status?' · HTTP '+esc(r.status):''}<br>${(r.duration_ms/1000).toFixed(3)} 秒${r.model_count==null?'':' · '+esc(r.model_count)+' 个模型'}<br><small>${esc(date(r.at))}</small>`;
 const q=s=>dialog.querySelector(s);
 function render(){
  const names=keys.providers||{};
  dialog.innerHTML=`<div class="dialog-title"><h2>服务商状态</h2><button data-close>关闭</button></div>
  <p class="help">服务器与客户端分别直连检测；每家使用一把指定 Key，约每 5 分钟一次。只调用配置的 GET 接口，不生成图片或视频。接口正常不等于所有模型可生成。</p>
  <div class="actions"><button data-run>立即检测服务器</button><button data-refresh>刷新结果</button></div><p data-note role="status">${data.recording_error?'监测执行或保存异常，部分结果可能缺失。':''}</p>
  <div class="table-wrap"><table><thead><tr><th>服务商 / 检测 Key</th><th>检测范围</th><th>服务器结果</th><th>配置</th></tr></thead><tbody>${data.monitors.map(s=>`<tr><td>${esc(names[s.provider]||s.provider)}<br><small>${esc(s.key_name)}</small></td><td>${s.auth==='none'?'未携带 Key，仅检查入口':'携带 Key 请求，权限由接口判定'}<br><small>${esc(s.url)}</small></td><td>${!s.enabled?'已停用':!s.configured?'Key 停用、归属或地址已变化，请重新配置':result(s.server)}${data.pending.includes(s.provider)?'<br>正在检测…':''}</td><td><button data-edit="${esc(s.provider)}">编辑</button></td></tr>`).join('')||'<tr><td colspan="4">尚未配置监测。在下方选择服务商和检测 Key。</td></tr>'}</tbody></table></div>
  <details data-config><summary>新增 / 编辑监测</summary><form data-form>
  <div class="toolbar"><label>服务商<select name="provider">${Object.entries(names).filter(([id])=>keys.keys.some(k=>k.provider===id&&k.enabled)).map(([id,name])=>`<option value="${esc(id)}">${esc(name)}</option>`).join('')}</select></label><label>检测 Key<select name="key_id"></select></label></div>
  <label>完整检测地址（HTTPS）<input name="url" type="url" required maxlength="1000" placeholder="https://服务商域名/v1/models"></label>
  <div class="toolbar"><label>响应检查<select name="kind"><option value="models">模型列表 JSON</option><option value="health">健康状态 JSON</option><option value="http">仅检查 HTTP 2xx</option></select></label><label>JSON 结果路径<input name="result_path" value="data" maxlength="100" placeholder="data / ok / data.models；根值留空"></label><label>携带凭据<select name="auth"><option value="bearer">Authorization: Bearer</option><option value="x-api-key">x-api-key</option><option value="api-key">api-key</option><option value="none">不携带 Key</option></select></label><label>Key 字段<select name="key_field"><option>api_key</option><option>image_1k_api_key</option><option>image_4k_api_key</option><option>video_api_key</option></select></label></div>
  <label><input type="checkbox" name="enabled" checked> 启用服务器及获授权客户端监测</label><p class="help">模型列表路径须返回数组；健康路径须为 true、ok、healthy、success 或 up。仅 HTTP 模式不验证正文。Gate 公开列表可用 /public/model_group/info；请按服务商实际文档填写，不把生成接口作为检测地址。</p>
  <button class="primary">保存监测配置</button></form></details>
  <h3>客户端最近结果</h3><p class="help">最多显示最近 200 个账号设备 / 服务商组合。只有已获授权使用指定检测 Key 的新版 Studio 才参与；无记录可能是未启动、未授权、旧版本或尚未补报，不能据此认定用户断网。以下为客户端自行上报，时间受设备时钟影响。</p>
  <div class="table-wrap"><table><thead><tr><th>账号 / 设备</th><th>服务商</th><th>客户端结果</th><th>服务器收到</th></tr></thead><tbody>${data.clients.map(r=>`<tr><td>${esc(r.username)}<br><small>${esc(r.device)} · ${esc(r.version||'未知版本')}</small></td><td>${esc(names[r.provider]||r.provider)}</td><td>${result(r)}</td><td>${esc(date(r.received_at))}</td></tr>`).join('')||'<tr><td colspan="4">暂无客户端结果</td></tr>'}</tbody></table></div><p class="help">记录最多保留 7 天、全站最多 50,000 条。超过 15 分钟的结果标记过期；状态不会自动阻止生产或停用 Key。</p>`;
  q('[data-close]').onclick=()=>dialog.close();q('[data-refresh]').onclick=()=>refresh(true);
  q('[data-run]').onclick=async e=>{e.target.disabled=true;try{const d=await api('provider-monitor/run',{});q('[data-note]').textContent=d.queued?`已安排 ${d.queued} 家，稍后刷新结果。`:'正在检测、未启用或距离上次不足 30 秒。';}catch(err){q('[data-note]').textContent=err.message;}finally{e.target.disabled=false;}};
  const f=q('[data-form]');
  function fillKeys(){const pid=f.elements.provider.value;f.elements.key_id.innerHTML=keys.keys.filter(k=>k.enabled&&k.provider===pid).map(k=>`<option value="${esc(k.id)}">${esc(k.name)}</option>`).join('');}
  function suggest(){const k=keys.keys.find(k=>k.id===f.elements.key_id.value);const path=f.elements.provider.value==='gate'?'/public/model_group/info':'/v1/models';f.elements.url.value=k?.base_url?new URL(path,k.base_url).href:'';}
  f.elements.provider.onchange=()=>{fillKeys();suggest();};f.elements.key_id.onchange=suggest;
  f.elements.kind.onchange=()=>{f.elements.result_path.value=f.elements.kind.value==='health'?'ok':f.elements.kind.value==='models'?'data':'';};
  fillKeys();suggest();
  dialog.querySelectorAll('[data-edit]').forEach(b=>b.onclick=()=>{const s=data.monitors.find(x=>x.provider===b.dataset.edit);f.elements.provider.value=s.provider;fillKeys();for(const key of ['key_id','url','kind','result_path','auth','key_field'])f.elements[key].value=s[key];f.elements.enabled.checked=s.enabled;q('[data-config]').open=true;f.scrollIntoView({block:'nearest'});});
  f.onsubmit=async e=>{e.preventDefault();const save=f.querySelector('button');save.disabled=true;try{await api('provider-monitor',{...Object.fromEntries(new FormData(f)),enabled:f.elements.enabled.checked});await refresh(true);}catch(err){q('[data-note]').textContent=err.message;}finally{save.disabled=false;}};
 }
 async function refresh(force=false){
  clearTimeout(timer);const ticket=++generation;
  try{const [d,k]=await Promise.all([api('provider-monitor'),!keys||force?api('keys'):Promise.resolve(keys)]);if(ticket!==generation||!dialog.open)return;data=d;keys=k;
   // Polling must not discard a partially edited configuration.
   if(force||!q('[data-config]')?.open)render();
  }catch(e){if(dialog.open){const note=q('[data-note]');if(note)note.textContent=e.message;else dialog.textContent=e.message;}}
  if(dialog.open)timer=setTimeout(()=>refresh(),5000);
 }
 button.onclick=()=>{dialog.showModal();refresh(true);};
 dialog.addEventListener('close',()=>{generation++;clearTimeout(timer);dialog.replaceChildren();});
})();
