'use strict';
(() => {
 const button=document.createElement('button');
 button.textContent='客户端版本';
 document.querySelector('#key-library').before(button);
 const dialog=document.createElement('dialog');
 dialog.id='release-manager';
 dialog.setAttribute('aria-labelledby','release-title');
 const flavors={all:'全体系',v34:'电影级十七章',v61:'通用十二环节'};
 const flavorOptions=Object.entries(flavors).map(([value,name])=>`<option value="${value}">${name}</option>`).join('');
 const importField=(label,filename)=>`<label class="release-import">
   <span class="release-import-icon" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="M14 3v6h6M12 17v-5m-3 3 3-3 3 3"/></svg></span>
   <span class="release-import-copy"><strong>${label}</strong><small data-filename="${filename}">选择 release.json 或 release.r2.json</small></span>
   <span class="release-file-action" aria-hidden="true">选择文件</span><input class="release-file" type="file" name="manifest" accept=".json"></label>`;
 dialog.innerHTML=`<div class="release-header">
   <div><p class="release-eyebrow">RESPECT STUDIO</p><h2 id="release-title">客户端版本</h2></div>
   <div class="release-header-actions"><span class="release-state" data-state-badge></span><button type="button" class="release-close" data-close aria-label="关闭版本管理">×</button></div>
 </div>
 <nav class="release-tabs" role="tablist" aria-label="版本管理模块">
   <button type="button" role="tab" id="release-tab-overview" aria-controls="release-overview" aria-selected="true" data-view="overview">版本概览</button>
   <button type="button" role="tab" id="release-tab-test" aria-controls="release-test" aria-selected="false" tabindex="-1" data-view="test">测试登记</button>
   <button type="button" role="tab" id="release-tab-publish" aria-controls="release-publish" aria-selected="false" tabindex="-1" data-view="publish">正式发布</button>
   <button type="button" role="tab" id="release-tab-builds" aria-controls="release-builds" aria-selected="false" tabindex="-1" data-view="builds">构建记录</button>
 </nav>
 <section class="release-panel" id="release-overview" role="tabpanel" aria-labelledby="release-tab-overview" tabindex="0">
   <div class="release-panel-body">
     <div class="release-section-heading"><h3>发布状态，一目了然</h3><p>查看当前策略，或切换到需要的功能。</p></div>
     <div class="release-group">
       <div class="release-policy-row"><div><h4>版本校验</h4><p data-enforcement-status role="status"></p></div><button type="button" data-enforcement-toggle>读取状态…</button></div>
       <div class="release-group-note">所有体系共用。暂停期间保留发布记录并隐藏更新推荐；账号登录与 Key 授权始终开启。</div>
     </div>
     <p class="release-feedback" data-policy-status role="status"></p>
     <div class="release-section-label"><h4>当前发布</h4><span>按体系管理</span></div>
     <div class="release-overview-grid" data-release-overview></div>
     <p class="release-caption">测试包只需在「测试登记」中登记。准备向用户分发时，再进入「正式发布」。</p>
   </div>
 </section>
 <section class="release-panel" id="release-test" role="tabpanel" aria-labelledby="release-tab-test" tabindex="0" hidden>
   <form id="test-build-form" class="release-module-form">
     <div class="release-panel-body">
       <div class="release-section-heading"><h3>先测试，再发布</h3><p>登记测试包，让它通过构建校验，保持现有更新推荐。</p></div>
       ${importField('导入测试包信息','test')}
       <div class="release-group release-form-group">
         <div class="release-field-grid">
           <label>测试体系<select name="flavor">${flavorOptions}</select></label>
           <label>测试版本号<input name="version" required pattern="[0-9]{1,5}(\\.[0-9]{1,5}){3}" placeholder="2026.10.20.3"></label>
           <label class="release-full">程序构建校验码<input class="release-code" name="build_sha256" required pattern="[a-f0-9]{64}" minlength="64" maxlength="64" spellcheck="false" placeholder="从实际打包清单读取"></label>
         </div>
         <p class="release-caption">版本号和校验码须与实际包一致。重新构建请增加版本号。</p>
       </div>
       <div class="release-note"><strong>仅登记，不推送</strong><p>测试人员仍需正常登录，并满足已有最低版本要求。这里不改变推荐版本、强制更新门槛或暂停状态。</p></div>
     </div>
     <div class="release-panel-footer"><p class="release-feedback" data-test-status role="status"></p><div class="release-footer-actions"><span>安装包可单独发给测试人员</span><button type="submit" class="primary">登记测试版本</button></div></div>
   </form>
 </section>
 <section class="release-panel" id="release-publish" role="tabpanel" aria-labelledby="release-tab-publish" tabindex="0" hidden>
   <form id="release-form" class="release-module-form">
     <div class="release-panel-body">
       <div class="release-section-heading"><h3>向用户发布更新</h3><p>设置推荐版本与最低可用版本，保存后在客户端下次检查时生效。</p></div>
       ${importField('导入已上传的版本信息','publish')}
       <div class="release-group release-form-group"><div class="release-field-grid">
         <label>发布体系<select name="flavor">${flavorOptions}</select></label>
         <label>推荐更新版本<input name="version" required placeholder="2026.10.20.2"></label>
         <label class="release-full">最低可用版本<select name="minimum_version" required></select></label>
       </div><p class="release-caption" data-minimum></p></div>
       <div class="release-group release-form-group"><label><span>更新说明 <span class="release-optional">选填</span></span><textarea name="notes" rows="2" maxlength="5000" placeholder="告诉用户这次更新带来了什么"></textarea></label></div>
       <details class="release-group release-metadata"><summary>下载地址与校验信息<span>随清单自动填入</span></summary><div class="release-form-group"><div class="release-field-grid">
         <label class="release-full">安装包 HTTPS 下载地址<input name="url" type="url" required placeholder="R2 或自定义域名的公开下载地址"></label>
         <label class="release-full">安装包 SHA256<input class="release-code" name="sha256" required minlength="64" maxlength="64" spellcheck="false"></label>
         <label class="release-full">程序构建校验码<input class="release-code" name="build_sha256" required minlength="64" maxlength="64" readonly></label>
         <label class="release-full">文件大小（字节）<input name="bytes" type="number" min="1" required></label>
       </div><p class="release-caption">请先完成安装包上传与下载校验。旧安装版 2026.10.20.1 及以前首次切换 R2，需手动安装新版。</p></div></details>
     </div>
     <div class="release-panel-footer"><p class="release-feedback" data-status role="status"></p><div class="release-footer-actions"><span>低于最低版本的客户端必须更新</span><button type="submit" class="primary">发布更新设置</button></div></div>
   </form>
 </section>
 <section class="release-panel" id="release-builds" role="tabpanel" aria-labelledby="release-tab-builds" tabindex="0" hidden>
   <div class="release-panel-body">
     <div class="release-section-heading"><h3>已登记构建</h3><p>包含测试构建和历史正式版本；登记本身不代表更新推荐。</p></div>
     <div class="release-record-toolbar"><span data-build-count></span><label>筛选体系<select data-build-filter aria-label="筛选构建体系"><option value="*">所有体系</option>${flavorOptions}</select></label></div>
     <ul class="release-group release-build-list" data-build-list></ul>
   </div>
 </section>`;
 document.body.append(dialog);
 const form=dialog.querySelector('#release-form'), status=dialog.querySelector('[data-status]');
 const testForm=dialog.querySelector('#test-build-form'), testStatus=dialog.querySelector('[data-test-status]');
 const policyStatus=dialog.querySelector('[data-policy-status]');
 const tabs=[...dialog.querySelectorAll('[role=tab]')];
 const showView=(view,focus=false)=>{
   for(const tab of tabs){
     const active=tab.dataset.view===view;
     tab.setAttribute('aria-selected',String(active));tab.tabIndex=active?0:-1;
     dialog.querySelector('#'+tab.getAttribute('aria-controls')).hidden=!active;
     if(active&&focus)tab.focus();
   }
 };
 for(const tab of tabs)tab.onclick=()=>showView(tab.dataset.view);
 dialog.querySelector('[role=tablist]').onkeydown=e=>{
   const index=tabs.indexOf(e.target);if(index<0)return;
   const next={ArrowRight:(index+1)%tabs.length,ArrowLeft:(index+tabs.length-1)%tabs.length,Home:0,End:tabs.length-1}[e.key];
   if(next!==undefined){e.preventDefault();showView(tabs[next].dataset.view,true);}
 };
 const feedback=(element,text,error=false)=>{element.textContent=text;element.dataset.error=String(error);};
 let catalog={releases:[],builds:[],enforcement_enabled:true};
 const toggle=dialog.querySelector('[data-enforcement-toggle]');
 const showEnforcement=()=>{
   const enabled=catalog.enforcement_enabled!==false;
   toggle.textContent=enabled?'暂停校验':'恢复校验';
   toggle.classList.toggle('primary',!enabled);
   const badge=dialog.querySelector('[data-state-badge]');
   badge.textContent=enabled?'校验已开启':'校验已暂停';badge.dataset.paused=String(!enabled);
   dialog.querySelector('[data-enforcement-status]').textContent=enabled?
     '按最低版本和构建记录校验客户端。':
     '旧包可登录；恢复后重新执行原版本规则。';
 };
 const compare=(a,b)=>{
   const x=a.split('.').map(Number), y=b.split('.').map(Number);
   for(let i=0;i<4;i++)if(x[i]!==y[i])return x[i]-y[i];
   return 0;
 };
 const previous=()=>catalog.releases.find(r=>r.flavor===form.elements.flavor.value);
 const minimumOptions=selected=>{
   const floor=previous()?.minimum_version||'0.0.0.0', latest=form.elements.version.value.trim();
   const versions=new Set([floor,...catalog.builds.filter(r=>r.flavor===form.elements.flavor.value).map(r=>r.version)]);
   if(/^\d{1,5}(\.\d{1,5}){3}$/.test(latest))versions.add(latest);
   const select=form.elements.minimum_version;
   select.replaceChildren();
   [...versions].filter(v=>compare(v,floor)>=0&&(!latest||compare(v,latest)<=0)).sort(compare).forEach(v=>{
     select.add(new Option(v==='0.0.0.0'?'不另设最低版本（仍须通过构建登记）':v,v));
   });
   select.value=[...select.options].some(o=>o.value===selected)?selected:floor;
   dialog.querySelector('[data-minimum]').textContent='当前门槛：'+(floor==='0.0.0.0'?'未设置':floor)+'。最低版本只能提高；达到门槛的已登记版本可继续使用。';
 };
 const fill=r=>{
   for(const key of ['flavor','version','url','sha256','build_sha256','bytes','notes'])
     form.elements[key].value=r?.[key]??(key==='flavor'?'all':'');
   form.elements.manifest.value='';
   dialog.querySelector('[data-filename=publish]').textContent=r?.url?'已保存的发布信息 · 可导入新清单':'选择 release.json 或 release.r2.json';
   minimumOptions(r?.minimum_version||(r?.mandatory?r.version:previous()?.minimum_version)||'0.0.0.0');
 };
 const showBuilds=()=>{
   const list=dialog.querySelector('[data-build-list]');
   list.replaceChildren();
   const filter=dialog.querySelector('[data-build-filter]').value;
   const builds=catalog.builds.filter(r=>filter==='*'||r.flavor===filter).sort((a,b)=>compare(b.version,a.version)||a.flavor.localeCompare(b.flavor));
   dialog.querySelector('[data-build-count]').textContent=`${builds.length} 个构建`;
   for(const row of builds){
     const item=document.createElement('li');
     const recommended=catalog.releases.some(r=>r.flavor===row.flavor&&r.version===row.version);
     const copy=document.createElement('div'),version=document.createElement('strong'),name=document.createElement('small'),badge=document.createElement('span');
     version.textContent=row.version;name.textContent=flavors[row.flavor]||row.flavor;
     badge.className='release-record-badge';badge.textContent=recommended?'当前推荐':'已登记';badge.dataset.recommended=String(recommended);
     copy.append(version,name);item.append(copy,badge);
     list.append(item);
   }
   if(!builds.length){const empty=document.createElement('li');empty.className='release-empty';empty.textContent='暂无构建，可先在「测试登记」中导入版本信息。';list.append(empty);}
 };
 const refresh=async()=>{
   catalog=await api('releases');catalog.builds||=[];showEnforcement();showBuilds();
   const overview=dialog.querySelector('[data-release-overview]');overview.replaceChildren();
   for(const [flavor,name] of Object.entries(flavors)){
     const row=catalog.releases.find(r=>r.flavor===flavor);
     const card=document.createElement('div');card.className='release-group release-overview-card';
     const title=document.createElement('h4');title.textContent=name;card.append(title);
     for(const [label,value] of [['推荐版本',row?.version||'尚未发布'],['最低可用',row?.minimum_version&&row.minimum_version!=='0.0.0.0'?row.minimum_version:'未设置']]){
       const line=document.createElement('div'),term=document.createElement('span'),text=document.createElement('strong');
       term.textContent=label;text.textContent=value;line.append(term,text);card.append(line);
     }
     const manage=document.createElement('button');manage.type='button';manage.className='release-text-button';manage.textContent='管理发布 →';
     manage.onclick=()=>{fill(row||{flavor});feedback(status,'');showView('publish');};
     card.append(manage);overview.append(card);
   }
 };
 dialog.querySelector('[data-build-filter]').onchange=showBuilds;
 toggle.onclick=async()=>{
   toggle.disabled=true;
   try {
     const enabled=catalog.enforcement_enabled===false;
     await api('releases/enforcement',{enabled});await refresh();
     feedback(policyStatus,enabled?'已恢复原版本规则，客户端下次校验时生效。':'已暂停校验。发布新版后仍需手动恢复。');
   } catch(error){feedback(policyStatus,error.message,true);}
   finally{toggle.disabled=false;}
 };
 dialog.querySelector('[data-close]').onclick=()=>dialog.close();
 button.onclick=()=>safe(async()=>{
   await refresh();fill(catalog.releases.find(r=>r.flavor==='all'));
   feedback(status,'');feedback(policyStatus,'');showView('overview');dialog.showModal();
 });
 testForm.elements.manifest.onchange=async e=>{
   try {
     const r=JSON.parse(await e.target.files[0].text());
     if(!['all','v34','v61'].includes(r.flavor)||!/^\d{1,5}(\.\d{1,5}){3}$/.test(r.version)||!/^[a-f0-9]{64}$/.test(r.build_sha256))throw new Error();
     for(const key of ['flavor','version','build_sha256'])testForm.elements[key].value=r[key];
     dialog.querySelector('[data-filename=test]').textContent=e.target.files[0].name;
     feedback(testStatus,'已读取测试包信息，确认后点击“登记测试版本”。');
   }catch{testForm.reset();dialog.querySelector('[data-filename=test]').textContent='选择 release.json 或 release.r2.json';feedback(testStatus,'文件无效，请导入含构建校验码的打包清单。',true);}
 };
 testForm.onsubmit=async e=>{
   e.preventDefault();const submit=testForm.querySelector('[type=submit]');submit.disabled=true;
   try {
     const data={};for(const key of ['flavor','version','build_sha256'])data[key]=testForm.elements[key].value.trim();
     await api('releases/builds',data);await refresh();minimumOptions(form.elements.minimum_version.value);
     feedback(testStatus,`${data.version} 已登记，未推送更新。`+(catalog.enforcement_enabled===false?' 版本校验仍暂停。':''));
   }catch(error){feedback(testStatus,error.message,true);}finally{submit.disabled=false;}
 };
 form.elements.flavor.onchange=()=>safe(async()=>{const f=form.elements.flavor.value;await refresh();fill(catalog.releases.find(r=>r.flavor===f)||{flavor:f});feedback(status,'');});
 form.elements.version.onchange=()=>minimumOptions(form.elements.minimum_version.value);
 form.elements.manifest.onchange=async e=>{try{const file=e.target.files[0],r=JSON.parse(await file.text());fill(r);dialog.querySelector('[data-filename=publish]').textContent=file.name;feedback(status,'已读取版本信息，请确认推荐版本和最低可用版本。');}catch{feedback(status,'版本信息文件无效。',true);}};
 form.addEventListener('invalid',e=>{const details=e.target.closest('details');if(details)details.open=true;},true);
 form.onsubmit=async e=>{e.preventDefault();const submit=form.querySelector('[type=submit]');submit.disabled=true;try{
   const data={};for(const key of ['flavor','version','url','sha256','build_sha256','notes','minimum_version'])data[key]=form.elements[key].value.trim();
   data.bytes=Number(form.elements.bytes.value);
   await api('releases',data);await refresh();fill(catalog.releases.find(r=>r.flavor===data.flavor));
   feedback(status,catalog.enforcement_enabled===false?
     '已保存。版本校验仍暂停，分发完成后请在概览中恢复。':
     '已发布，客户端下次检查时生效。');
 }catch(error){feedback(status,error.message,true);}finally{submit.disabled=false;}};
})();
