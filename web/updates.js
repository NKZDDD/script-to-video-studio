'use strict';
(() => {
 const screen=document.createElement('section');screen.id='update-screen';screen.setAttribute('aria-label','应用版本检查');
 screen.innerHTML=`<div class="studio-login-card">
 <div class="studio-login-brand"><img src="/respect-logo.png" alt="RespectAI"> Respect Studio</div>
 <h1 data-title>正在检查应用版本</h1><p class="studio-login-note" data-version></p>
 <p data-message role="status"></p><p class="studio-login-note" data-notes></p>
 <progress hidden></progress><p class="studio-login-note" data-jobs></p>
 <div class="update-actions">
 <button data-action="check">重新检查</button><button data-action="download" hidden>下载更新</button>
 <button data-action="install" hidden>重启完成更新</button><button data-action="stop" hidden>停止当前批次</button>
 <button data-action="continue" hidden>稍后更新，继续使用</button></div>
 <p class="studio-login-note">强制更新完成前无法进入工作台或提交新任务。更新保留配置和项目。<span data-browser></span></p></div>`;
 document.body.append(screen);document.body.classList.add('studio-updating');
 const el=s=>screen.querySelector(s), button=a=>el(`[data-action="${a}"]`);
 let resolveReady,checked=false,skippedVersion='',last=null,inflight=false;
 const ready=new Promise(resolve=>resolveReady=resolve);
 function show(){document.body.classList.add('studio-updating');const dialog=document.querySelector('#dialog');if(dialog?.open)dialog.close();}
 function dismiss(){document.body.classList.remove('studio-updating');resolveReady();}
 function render(d){
   last=d;
   const busy=['checking','downloading','installing'].includes(d.phase),r=d.release;
   const available=r&&['available','ready','downloading','installing','error'].includes(d.phase);
   const open=!d.allowed||(available&&skippedVersion!==r.version);
   if(open)show();else dismiss();
   el('[data-title]').textContent=d.allowed?'发现新版本':(d.code==='checking_version'?'正在检查应用版本':'需要完成版本检查或更新');
   el('[data-version]').textContent=`当前版本 ${d.current}${r?' · 发布版本 '+r.version:''}`;
   el('[data-message]').textContent=[!d.allowed&&d.gate_message,d.message].filter((v,i,a)=>v&&a.indexOf(v)===i).join('；');
   el('[data-notes]').textContent=r?.notes||'';
   button('check').disabled=busy||inflight;
   button('download').hidden=!(r&&['available','error'].includes(d.phase));
   button('download').textContent=d.phase==='error'?'重试下载':'下载更新';
   button('install').hidden=d.phase!=='ready';
   button('install').disabled=!d.desktop||!!d.active_jobs||inflight;
   button('continue').hidden=!d.allowed;
   button('stop').hidden=!d.active_jobs;
   el('[data-jobs]').textContent=d.active_jobs?`还有 ${d.active_jobs} 个批次运行。可等待结束或停止批次，再重启更新。`:'';
   el('[data-browser]').textContent=d.desktop?'':'浏览器模式请关闭程序后手动运行安装器。';
   const progress=el('progress');progress.hidden=!['downloading','ready','installing'].includes(d.phase);progress.max=r?.bytes||1;progress.value=['ready','installing'].includes(d.phase)?progress.max:(d.received||0);
   if(d.allowed&&!available)dismiss();
 }
 async function request(action){
   const r=await fetch('/api/updates/'+action,action==='status'?{cache:'no-store'}:{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
   const d=await r.json();if(!r.ok)throw Error(d.error||'版本检查失败');return d;
 }
 async function refresh(){
   if(inflight)return;
   inflight=true;show();
   try{const d=await request('check');inflight=false;checked=true;render(d);}
   catch(e){inflight=false;el('[data-message]').textContent=e.message;button('check').disabled=false;button('continue').hidden=true;}
 }
 screen.addEventListener('click',async e=>{
   const action=e.target.dataset.action;if(!action)return;
   if(action==='continue'){if(last?.allowed){skippedVersion=last.release?.version||'';dismiss();}return;}
   if(action==='check')return refresh();
   if(action==='stop'&&!confirm('停止当前批次？已提交的服务商任务 ID 会保留。'))return;
   e.target.disabled=true;
   try{render(await request(action));}catch(error){el('[data-message]').textContent=error.message;}
   finally{e.target.disabled=false;}
 });
 window.StudioUpdates={ready,refresh,show};
 setInterval(async()=>{
   if(inflight||!checked)return;
   try{render(await request('status'));}catch{if(last?.phase==='installing')return;show();el('[data-message]').textContent='无法连接本机 Studio，请检查应用是否仍在运行';}
 },2000);
 refresh();
})();
