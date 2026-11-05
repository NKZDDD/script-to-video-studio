'use strict';
(() => {
 const endpoint=document.currentScript?.dataset.endpoint||'/api/provider-model-stats';
 const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const entries=new Map(),states=new WeakMap();
 const rate=r=>typeof r?.rate==='number'&&Number.isFinite(r.rate)?r.rate.toFixed(2)+'%':'暂无数据';
 const duration=r=>typeof r?.seconds==='number'?r.seconds>=60?(r.seconds/60).toFixed(1)+' 分钟':r.seconds.toFixed(1)+' 秒':'—';
 const when=d=>d?.source_at||d?.fetched_at?new Date((d.source_at||d.fetched_at)*1000).toLocaleString():'—';
 const sample=r=>r?.samples==null?'样本数未提供':r.samples+' 条有效样本';
 function markup(provider,model,models,mode='choose'){
  return `<div class="pms" data-pms-provider="${esc(provider)}" data-pms-model="${esc(model)}" data-pms-models="${esc(JSON.stringify(models))}" data-pms-mode="${esc(mode)}"></div>`;
 }
 function rows(box,data){
  let allowed;try{allowed=JSON.parse(box.dataset.pmsModels||'null');}catch{allowed=[];}
  const published=Array.isArray(data?.models)?data.models:[];
  if(allowed===null)return published;
  const names=new Set(allowed),found=new Set(published.map(r=>r.model));
  return [...published.filter(r=>names.has(r.model)),...allowed.filter(n=>!found.has(n)).map(model=>({model,rate:null}))];
 }
 function fit(box){
  const panel=box.querySelector('.pms-panel');
  if(getComputedStyle(panel).position!=='absolute')return;
  const dialog=box.closest('dialog'),bounds=dialog?.getBoundingClientRect();
  const left=Math.max(16,(bounds?.left||0)+16),right=Math.min(innerWidth-16,(bounds?.right||innerWidth)-16);
  const width=Math.min(780,right-left),anchor=box.querySelector('details').getBoundingClientRect().left;
  panel.style.width=width+'px';panel.style.left=(Math.max(left,Math.min(anchor,right-width))-anchor)+'px';
 }
 function list(box){
  const s=states.get(box),data=s?.data;
  if(!s)return;
  const q=box.querySelector('[data-pms-search]').value.trim().toLowerCase(),all=rows(box,data).filter(r=>(r.model+' '+(r.variant||'')).toLowerCase().includes(q));
  s.visible=all.slice(0,100);
  box.querySelector('[data-pms-count]').textContent=`${all.length} 个模型条目${all.length>100?'，先显示 100 条，请输入模型名筛选':''}`;
  box.querySelector('tbody').innerHTML=s.visible.map((r,i)=>`<tr><td><strong>${esc(r.model)}</strong>${r.variant?`<small>${esc(r.variant)}</small>`:''}</td><td>${rate(r)}<small>${esc(sample(r))}</small></td><td>${duration(r)}</td><td><small>${esc(r.window||'—')}<br>${esc(r.basis||'暂无服务商统计')}</small></td><td><button type="button" data-pms-pick="${i}">${box.dataset.pmsMode==='bind'?'添加绑定':'使用'}</button></td></tr>`).join('')||'<tr><td colspan="5">暂无匹配的模型统计</td></tr>';
 }
 function paint(box){
  const s=states.get(box);if(!s)return;
  const d=s.data||{},model=box.dataset.pmsModel||'',selected=rows(box,d).filter(r=>r.model===model);
  const note=box.querySelector('[data-pms-note]'),metric=d.scope==='request'?'服务商请求成功率':'服务商任务成功率';
  let text=d.state==='loading'?'服务商统计读取中…':d.state==='unsupported'?(d.message||'暂无服务商统计'):d.state==='error'&&!d.fetched_at?'服务商统计暂不可用':!model?'查看服务商统计后选择模型':selected.some(r=>r.rate!=null)?selected.map(r=>`${r.variant?r.variant+' · ':''}${metric} ${rate(r)} · ${r.window} · ${sample(r)}${r.seconds!=null?' · 平均耗时 '+duration(r):''}`).join('；'):'暂无该模型的服务商统计';
  if(d.stale)text+=' · 旧数据'+(d.error?'，刷新失败':'，等待更新');
  else if(d.refreshing&&d.fetched_at)text+=' · 更新中';
  note.textContent=text;
  note.title=selected.map(r=>r.basis||'').join('；');
  note.classList.toggle('pms-stale',Boolean(d.stale));
  // Native suggestions keep the exact submitted value; only the visible label changes.
  box.parentElement.querySelectorAll('datalist option').forEach(option=>{
   const match=rows(box,d).filter(r=>r.model===option.value);
   option.label=option.value+' · '+(match.length>1?'多个分组，查看统计':rate(match[0]))+(d.stale?'（旧数据）':'');
  });
  box.querySelector('[data-pms-time]').textContent=`来源：服务商平台 · 更新：${when(d)}${d.scope==='request'?' · 请求统计口径，未必代表最终成片':''}${d.stale?' · 当前为旧数据':''}`;
  if(box.querySelector('details').open)list(box);
 }
 async function refresh(box){
  if(!box.isConnected||!box.getClientRects().length)return;
  const provider=box.dataset.pmsProvider||'',base=box.dataset.pmsBase||'',key=provider+'|'+base,s=states.get(box);
  if(!s)return;
  if(s.key!==key){s.key=key;s.data={state:'loading',models:[]};paint(box);}
  let entry=entries.get(key);
  if(!entry){entry={at:0,data:null,pending:null};entries.set(key,entry);if(entries.size>64)entries.delete(entries.keys().next().value);}
  if(entry.data){s.data=entry.data;paint(box);}
  if(!entry.pending&&Date.now()-entry.at>5000){
   entry.at=Date.now();
   entry.pending=(async()=>{
    const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),8000);
    try{
     const q=new URLSearchParams({provider});if(base)q.set('base_url',base);
     const r=await fetch(endpoint+'?'+q,{cache:'no-store',signal:controller.signal});
     if(!r.ok)throw Error('statistics unavailable');
     entry.data=await r.json();
    }catch{entry.data={...(entry.data||{models:[]}),state:'error',error:'统计暂不可用',stale:Boolean(entry.data?.fetched_at)};}
    finally{clearTimeout(timer);entry.pending=null;}
   })();
  }
  await entry.pending;
  if(box.isConnected&&s.key===key){s.data=entry.data;paint(box);}
 }
 function mount(box){
  if(states.has(box)){paint(box);refresh(box);return;}
  states.set(box,{key:'',data:{state:'loading',models:[]},visible:[]});
  box.innerHTML='<small data-pms-note role="status"></small><details><summary>查看模型成功率</summary><div class="pms-panel"><p>数据来自服务商平台，各家的统计窗口与口径不同；成功率供选型参考。</p><small data-pms-time></small><input type="search" data-pms-search placeholder="搜索完整模型名" aria-label="筛选服务商模型统计"><small data-pms-count></small><div class="pms-scroll"><table><thead><tr><th>模型</th><th>成功率 / 样本</th><th>平均耗时</th><th>范围 / 口径</th><th>选择</th></tr></thead><tbody></tbody></table></div></div></details>';
  if(box.dataset.pmsMode==='bind')box.querySelector('.pms-panel p').textContent+=' 模型可用权限仍以这把 Key 为准。';
  box.querySelector('details').addEventListener('toggle',()=>{if(box.querySelector('details').open){list(box);fit(box);refresh(box);}});
  box.querySelector('[data-pms-search]').addEventListener('input',()=>list(box));
  box.addEventListener('click',e=>{
   const button=e.target.closest('[data-pms-pick]');if(!button)return;
   e.preventDefault();e.stopPropagation();
   const r=states.get(box).visible[Number(button.dataset.pmsPick)];if(!r)return;
   if(box.dataset.pmsMode==='bind'){
    const input=box.closest('form')?.elements.models;if(!input)return;
    const lines=input.value.split(/\r?\n/).map(x=>x.trim()).filter(Boolean);
    if(!lines.includes(r.model))lines.push(r.model);input.value=lines.join('\n');input.dispatchEvent(new Event('input',{bubbles:true}));
    box.dataset.pmsModel=r.model;
   }else{
    const input=box.parentElement.querySelector('input[data-setting="model"],input[data-pms-input]');if(!input)return;
    // Only the exact model ID is saved, never a label or the percentage.
    input.value=r.model;box.dataset.pmsModel=r.model;input.dispatchEvent(new Event('change',{bubbles:true}));
   }
   box.querySelector('details').open=false;paint(box);
  });
  refresh(box);
 }
 function scan(root){if(root.matches?.('[data-pms-provider]'))mount(root);root.querySelectorAll?.('[data-pms-provider]').forEach(mount);}
 const observer=new MutationObserver(changes=>{for(const c of changes){if(c.type==='attributes')mount(c.target);else for(const n of c.addedNodes)if(n.nodeType===1)scan(n);}});
 observer.observe(document.body,{childList:true,subtree:true,attributes:true,attributeFilter:['data-pms-provider','data-pms-model','data-pms-models','data-pms-base']});
 document.addEventListener('input',e=>{if(!e.target.matches('input[data-setting="model"],input[data-pms-input]'))return;const box=e.target.parentElement.querySelector('[data-pms-provider]');if(box)box.dataset.pmsModel=e.target.value;});
 window.addEventListener('resize',()=>document.querySelectorAll('.pms:has(details[open])').forEach(fit));
 setInterval(()=>{if(!document.hidden)document.querySelectorAll('[data-pms-provider]').forEach(refresh);},10000);
 window.ProviderModelStats={markup,rate,duration,rows};scan(document.body);
})();
