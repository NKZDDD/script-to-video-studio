'use strict';
(() => {
 const endpoint=document.currentScript?.dataset.endpoint||'/api/provider-model-stats';
 const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const entries=new Map(),states=new WeakMap();
 const known=r=>typeof r?.rate==='number'&&Number.isFinite(r.rate)&&r.rate>=0&&r.rate<=100;
 const rate=r=>known(r)?r.rate.toFixed(2)+'%':'暂无数据';
 const duration=r=>typeof r?.seconds==='number'&&Number.isFinite(r.seconds)?r.seconds.toFixed(2)+' 秒':'—';
 const when=d=>d?.source_at||d?.fetched_at?new Date((d.source_at||d.fetched_at)*1000).toLocaleString():'—';
 const sample=r=>r?.samples==null?'样本数未提供':r.samples+' 条有效样本';
 function markup(provider,model,models,mode='choose'){
  return `<div class="pms" data-pms-provider="${esc(provider)}" data-pms-model="${esc(model)}" data-pms-models="${esc(JSON.stringify(models))}" data-pms-mode="${esc(mode)}"></div>`;
 }
 function rows(box,data){
  let allowed;try{allowed=JSON.parse(box.dataset.pmsModels||'null');}catch{allowed=[];}
  const published=Array.isArray(data?.models)?data.models:[];
  // The admin endpoint owns the Key catalog. Public statistics never add models.
  if(box.dataset.pmsMode!=='choose')allowed=data?.catalog?.models||[];
  if(allowed===null)return published;
  const names=new Set(allowed),found=new Set(published.map(r=>r.model));
  return [...published.filter(r=>names.has(r.model)),...allowed.filter(n=>!found.has(n)).map(model=>({model,rate:null}))];
 }
 function meter(r,stale){
  const valid=known(r),lit=valid?Math.round(r.rate*40/100):0;
  return `<div class="pms-meter ${valid&&!stale?'pms-known':''} ${valid&&r.rate===0&&!stale?'pms-zero':''} ${stale?'pms-stale':''}" aria-label="成功率 ${rate(r)}${stale?'，旧数据':''}"><div class="pms-metric"><span class="pms-light" aria-hidden="true"></span><span>成功率</span><strong>${rate(r)}</strong></div><div class="pms-bars" aria-hidden="true">${Array.from({length:40},(_,i)=>`<i class="${i<lit?'is-lit':''}"></i>`).join('')}</div></div>`;
 }
 function fit(box){
  const panel=box.querySelector('.pms-panel');
  if(getComputedStyle(panel).position!=='absolute')return;
  const dialog=box.closest('dialog'),bounds=dialog?.getBoundingClientRect();
  const left=Math.max(16,(bounds?.left||0)+16),right=Math.min(innerWidth-16,(bounds?.right||innerWidth)-16);
  const width=Math.min(760,right-left),anchor=box.querySelector('details').getBoundingClientRect().left;
  panel.style.width=width+'px';panel.style.left=(Math.max(left,Math.min(anchor,right-width))-anchor)+'px';
 }
 function list(box){
  const s=states.get(box),data=s?.data;if(!s)return;
  const q=box.querySelector('[data-pms-search]').value.trim().toLowerCase(),all=rows(box,data).filter(r=>(r.model+' '+(r.variant||'')).toLowerCase().includes(q));
  s.visible=all.slice(0,100);
  box.querySelector('[data-pms-count]').textContent=`${all.length} 个模型条目${all.length>100?'，先显示 100 条，请输入模型名筛选':''}`;
  box.querySelector('.pms-cards').innerHTML=s.visible.map((r,i)=>`<article class="pms-card"><header><strong>${esc(r.model)}</strong>${r.variant?`<small>${esc(r.variant)}</small>`:''}</header><div class="pms-card-metrics">${meter(r,data?.stale)}<div class="pms-duration"><small>平均耗时</small><strong>${duration(r)}</strong></div></div><small class="pms-basis">${esc(r.window||'统计窗口未提供')} · ${esc(sample(r))}<br>${esc(r.basis||'暂无服务商统计')}${data?.stale?' · 旧数据':''}</small>${box.dataset.pmsMode==='view'?'':`<button type="button" data-pms-pick="${i}">${box.dataset.pmsMode==='bind'?'添加绑定':'使用此模型'}</button>`}</article>`).join('')||`<p class="pms-empty">${esc(data?.catalog?.message||'暂无匹配的模型')}</p>`;
 }
 function paint(box){
  const s=states.get(box);if(!s)return;
  const d=s.data||{},model=box.dataset.pmsModel||'',selected=rows(box,d).filter(r=>r.model===model);
  const note=box.querySelector('[data-pms-note]'),metric=d.scope==='request'?'服务商请求成功率':'服务商任务成功率';
  let text=d.state==='loading'?'服务商统计读取中…':d.state==='unsupported'?(d.message||'暂无服务商统计'):d.state==='error'&&!d.fetched_at?'服务商统计暂不可用':!model?'按当前可用模型展示服务商成功率':selected.some(known)?`${metric} · ${selected.map(r=>rate(r)).join(' / ')}`:'暂无该模型的服务商统计';
  if(d.stale)text+=' · 旧数据'+(d.error?'，刷新失败':'，等待更新');
  else if(d.refreshing&&d.fetched_at)text+=' · 更新中';
  if(d.catalog)text=d.catalog.message+' · '+text;
  note.textContent=text;
  note.classList.toggle('pms-stale',Boolean(d.stale));
  box.querySelector('.pms-selected').innerHTML=model?selected.map(r=>`<div class="pms-selected-row">${meter(r,d.stale)}<div class="pms-duration"><small>${esc(r.variant||'平均耗时')}</small><strong>${duration(r)}</strong></div></div>`).join(''):'';
  box.parentElement.querySelectorAll('datalist option').forEach(option=>{
   const match=rows(box,d).filter(r=>r.model===option.value);
   option.label=option.value+' · '+(match.length>1?'多个分组，查看统计':rate(match[0]))+(d.stale?'（旧数据）':'');
  });
  box.querySelector('[data-pms-time]').textContent=`来源：服务商平台 · 更新：${when(d)}${d.scope==='request'?' · 请求统计口径，未必代表最终成片':''}${d.stale?' · 当前为旧数据':''}`;
  if(box.querySelector('details').open)list(box);
 }
 async function refresh(box){
  if(!box.isConnected||!box.getClientRects().length)return;
  const provider=box.dataset.pmsProvider||'',base=box.dataset.pmsBase||'',kid=box.dataset.pmsKey||'',key=provider+'|'+base+'|'+kid+'|'+(box.dataset.pmsRevision||''),s=states.get(box);
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
     const q=new URLSearchParams({provider});if(base)q.set('base_url',base);if(kid)q.set('key_id',kid);
     const r=await fetch(endpoint+'?'+q,{cache:'no-store',signal:controller.signal});
     if(!r.ok)throw Error('statistics unavailable');
     entry.data=await r.json();
    }catch{entry.data={models:[],state:'error',error:'统计暂不可用',catalog:box.dataset.pmsMode!=='choose'?{models:[],message:'Key 模型读取失败，请刷新后重试'}:undefined};}
    finally{clearTimeout(timer);entry.pending=null;}
   })();
  }
  await entry.pending;
  if(box.isConnected&&s.key===key){s.data=entry.data;paint(box);}
 }
 function mount(box){
  if(states.has(box)){paint(box);refresh(box);return;}
  states.set(box,{key:'',data:{state:'loading',models:[]},visible:[]});
  const choose=box.dataset.pmsMode==='choose';
  if(choose){const input=box.parentElement.querySelector('input[data-setting="model"],input[data-pms-input]');if(input){input.removeAttribute('list');input.autocomplete='off';}}
  box.innerHTML=`<small data-pms-note role="status"></small><div class="pms-selected"></div><details ${choose?'':'open'}><summary>${choose?'选择模型 · 查看成功率':'此 Key 的模型 · 服务商成功率'}</summary><div class="pms-panel"><p>模型范围来自当前 Key / 账号配置；成功率来自服务商平台。绿条表示成功比例，不代表实时连通状态。</p><small data-pms-time></small><input type="search" data-pms-search placeholder="搜索完整模型名" aria-label="筛选服务商模型统计"><small data-pms-count></small><div class="pms-cards"></div></div></details>`;
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
   }else{
    const input=box.parentElement.querySelector('input[data-setting="model"],input[data-pms-input]');if(!input)return;
    input.value=r.model;box.dataset.pmsModel=r.model;input.dispatchEvent(new Event('change',{bubbles:true}));
    box.querySelector('details').open=false;
   }
   paint(box);
  });
  refresh(box);
 }
 function scan(root){if(root.matches?.('[data-pms-provider]'))mount(root);root.querySelectorAll?.('[data-pms-provider]').forEach(mount);}
 const observer=new MutationObserver(changes=>{for(const c of changes){if(c.type==='attributes')mount(c.target);else for(const n of c.addedNodes)if(n.nodeType===1)scan(n);}});
 observer.observe(document.body,{childList:true,subtree:true,attributes:true,attributeFilter:['data-pms-provider','data-pms-model','data-pms-models','data-pms-base','data-pms-key','data-pms-revision']});
 const modelInput=e=>e.target.matches('input[data-setting="model"],input[data-pms-input]')?e.target.parentElement.querySelector('[data-pms-provider]'):null;
 document.addEventListener('input',e=>{const box=modelInput(e);if(box)box.dataset.pmsModel=e.target.value;});
 document.addEventListener('focusin',e=>{const box=modelInput(e);if(box){box.querySelector('details').open=true;list(box);fit(box);}});
 document.addEventListener('click',e=>document.querySelectorAll('.pms[data-pms-mode="choose"] details[open]').forEach(d=>{if(!d.closest('.pms-model-field')?.contains(e.target))d.open=false;}));
 document.addEventListener('keydown',e=>{if(e.key==='Escape')document.querySelectorAll('.pms[data-pms-mode="choose"] details[open]').forEach(d=>d.open=false);});
 window.addEventListener('resize',()=>document.querySelectorAll('.pms:has(details[open])').forEach(fit));
 setInterval(()=>{if(!document.hidden)document.querySelectorAll('[data-pms-provider]').forEach(refresh);},10000);
 window.ProviderModelStats={markup,rate,duration,rows};scan(document.body);
})();
