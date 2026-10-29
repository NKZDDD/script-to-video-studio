'use strict';
(() => {
 const button=document.createElement('button');
 button.textContent='客户端版本';
 document.querySelector('#key-library').before(button);
 const dialog=document.createElement('dialog');
 dialog.id='release-manager';
 dialog.setAttribute('aria-labelledby','release-title');
 const flavors={all:'全体系',v34:'电影级十七章',v61:'通用十二环节',batch:'对外批量版'};
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
   <button type="button" role="tab" id="release-tab-statistics" aria-controls="release-statistics" aria-selected="false" tabindex="-1" data-view="statistics">版本统计</button>
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
 <section class="release-panel" id="release-statistics" role="tabpanel" aria-labelledby="release-tab-statistics" tabindex="0" hidden>
   <div class="release-panel-body">
     <div class="release-section-heading"><h3>了解用户的升级进度</h3><p>按账号去重，查看最近一次通过登录或会话校验的版本。</p></div>
     <div class="release-stats-toolbar">
       <label>观察范围<select data-stats-days><option value="7">最近 7 天</option><option value="30" selected>最近 30 天</option><option value="90">最近 90 天</option><option value="0">全部记录</option></select></label>
       <label>客户端体系<select data-stats-flavor><option value="*">所有体系</option>${flavorOptions}<option value="unknown">未上报体系</option></select></label>
       <button type="button" data-stats-refresh>刷新统计</button>
     </div>
     <p class="release-feedback" data-stats-message role="status"></p>
     <div data-stats-content hidden>
       <div class="release-stats-grid" data-stats-summary></div>
       <p class="release-caption" data-stats-coverage></p>
       <div class="release-section-label"><h4>版本分布</h4><button type="button" class="release-text-button" data-stats-publish>去设置更新 →</button></div>
       <ul class="release-group release-build-list release-distribution" data-stats-distribution></ul>
       <div class="release-section-label"><h4>账号与设备明细</h4><span data-stats-total></span></div>
       <div class="release-stats-toolbar">
         <label>查看账号<select data-stats-segment><option value="all">全部上报账号</option><option value="old">仍有旧版设备</option><option value="required">低于强制门槛</option><option value="attention">版本或构建待核实</option></select></label>
         <label>搜索账号<input type="search" data-stats-search maxlength="64" placeholder="输入账号，按回车"></label>
       </div>
       <div class="release-stats-selection" data-stats-selection hidden><span></span><button type="button" data-stats-clear>清除版本筛选</button></div>
       <ul class="release-group release-build-list release-device-list" data-stats-details></ul>
       <div class="release-stats-pagination"><button type="button" data-stats-prev>上一页</button><span data-stats-page></span><button type="button" data-stats-next>下一页</button></div>
       <p class="release-caption">只统计仍有效的账号。设备记录按「账号＋设备＋体系」去重，版本人数与各卡片人数可能重叠，不能相加。灰色覆盖数用于区分未上线和未采集。</p>
       <p class="release-caption">从服务端升级后开始采集，无历史版本可补录。版本与校验码由客户端上报，可被伪造；统计是排查线索，不能证明是否破解。被版本门槛提前拦截、未通过身份验证的请求不计入账号人数。</p>
     </div>
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
   if(view==='statistics')loadStatistics();
 };
 for(const tab of tabs)tab.onclick=()=>showView(tab.dataset.view);
 dialog.querySelector('[role=tablist]').onkeydown=e=>{
   const index=tabs.indexOf(e.target);if(index<0)return;
   const next={ArrowRight:(index+1)%tabs.length,ArrowLeft:(index+tabs.length-1)%tabs.length,Home:0,End:tabs.length-1}[e.key];
   if(next!==undefined){e.preventDefault();showView(tabs[next].dataset.view,true);}
 };
 const feedback=(element,text,error=false)=>{element.textContent=text;element.dataset.error=String(error);};
 let catalog={releases:[],builds:[],enforcement_enabled:true};
 const stats=selector=>dialog.querySelector('[data-stats-'+selector+']');
 let statsPage=1, statsVersion='', statsRequest=0;
 const stateNames={current:'当前推荐',outdated:'低于推荐',required:'低于强制门槛',ahead:'高于推荐 · 已登记',unpublished:'已登记 · 未发布',unknown:'上报信息不足',unregistered:'构建未登记',mismatch:'构建校验码不符'};
 const time=value=>new Date(value*1000).toLocaleString('zh-CN',{hour12:false});
 const element=(tag,text,className='')=>{const el=document.createElement(tag);el.textContent=text;el.className=className;return el;};
 const emptyList=(list,text)=>list.append(element('li',text,'release-empty'));
 const loadStatistics=async(focusDetails=false)=>{
   const ticket=++statsRequest;
   const query=new URLSearchParams({days:stats('days').value,flavor:stats('flavor').value,segment:stats('segment').value,search:stats('search').value.trim(),version:statsVersion,page:String(statsPage)});
   stats('content').hidden=true;feedback(stats('message'),'正在读取统计…');
   try{
     const data=await api('releases/statistics?'+query);
     if(ticket!==statsRequest)return;
     statsPage=data.page;const s=data.summary;
     catalog.enforcement_enabled=data.enforcement_enabled;showEnforcement();
     const summary=stats('summary');summary.replaceChildren();
     for(const [key,label,count,note] of [['all','上报账号',s.accounts,`${s.installations} 条设备记录`],['old','仍有旧版设备',s.old,'低于当前推荐版本'],['required','低于强制门槛',s.required,data.enforcement_enabled?'当前最低版本规则':'规则已暂停 · 恢复后适用'],['attention','版本或构建待核实',s.attention,'缺少信息、未登记或校验码不符']]){
       const card=element('button','','release-stat-card');card.type='button';card.dataset.metric=key;
       card.setAttribute('aria-pressed',String(stats('segment').value===key));
       card.append(element('span',label),element('strong',String(count)),element('small',note));
       card.onclick=()=>{stats('segment').value=key;statsVersion='';stats('search').value='';statsPage=1;loadStatistics(true);};summary.append(card);
     }
     stats('coverage').textContent=`截至 ${time(data.as_of)} · 全站有效账号 ${s.eligible_accounts}，其中尚未采集 ${s.unobserved_accounts}；所选体系在本观察期内未再上报 ${s.inactive_accounts}。已排除停用或到期账号 ${s.excluded_accounts}。`;
     const distribution=stats('distribution');distribution.replaceChildren();
     for(const row of data.distribution){
       const item=document.createElement('li'),copy=document.createElement('div'),action=element('button','查看账号');action.type='button';
       copy.append(element('strong',row.version||'版本未知'),element('small',`${flavors[row.flavor]||'未上报体系'} · ${stateNames[row.state]}`));
       const counts=element('div','','release-stats-counts');counts.append(element('strong',`${row.accounts} 个账号`),element('small',`${row.installations} 条设备记录`));
       action.onclick=()=>{stats('flavor').value=row.flavor;statsVersion=row.version||'unknown';stats('segment').value='all';stats('search').value='';statsPage=1;loadStatistics(true);};
       item.append(copy,counts,action);distribution.append(item);
     }
     if(!data.distribution.length)emptyList(distribution,'本范围内暂无已验证的客户端上报，用户下次登录或校验后会出现。');
     stats('selection').hidden=!statsVersion;stats('selection').querySelector('span').textContent='版本筛选：'+(statsVersion==='unknown'?'版本未知':statsVersion);
     const details=stats('details');details.replaceChildren();
     for(const row of data.details){
       const item=document.createElement('li'),copy=document.createElement('div'),meta=element('div','','release-device-meta');
       copy.append(element('strong',row.username),element('small',`${row.device_name} · 设备 ${row.device_label}`));
       const states=[stateNames[row.state]];if(row.required&&row.state!=='required')states.push('低于强制门槛');else if(row.old&&row.state!=='outdated'&&row.state!=='required')states.push('低于推荐');
       meta.append(element('strong',`${row.version||'版本未知'} · ${flavors[row.flavor]||'未上报体系'}`),element('small',states.join(' · ')),element('small','最后上报 '+time(row.last_seen_at)));
       item.append(copy,meta);details.append(item);
     }
     if(!data.details.length)emptyList(details,'没有符合筛选条件的设备记录。');
     stats('total').textContent=`${data.total} 条设备记录`;
     stats('page').textContent=`${data.page} / ${Math.max(1,Math.ceil(data.total/data.page_size))}`;
     stats('prev').disabled=data.page<=1;stats('next').disabled=data.page*data.page_size>=data.total;
     stats('content').hidden=false;feedback(stats('message'),'');
     if(focusDetails&&!dialog.querySelector('#release-statistics').hidden)stats('segment').closest('.release-stats-toolbar').scrollIntoView({block:'start'});
   }catch(error){if(ticket===statsRequest)feedback(stats('message'),error.message,true);}
 };
 for(const field of ['days','flavor'])stats(field).onchange=()=>{statsPage=1;statsVersion='';loadStatistics();};
 stats('segment').onchange=()=>{statsPage=1;loadStatistics(true);};
 stats('search').onchange=()=>{statsPage=1;loadStatistics(true);};
 stats('search').onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();statsPage=1;loadStatistics(true);}};
 stats('clear').onclick=()=>{statsVersion='';statsPage=1;loadStatistics(true);};
 stats('refresh').onclick=()=>loadStatistics();
 stats('prev').onclick=()=>{statsPage--;loadStatistics(true);};stats('next').onclick=()=>{statsPage++;loadStatistics(true);};
 stats('publish').onclick=()=>{const flavor=flavors[stats('flavor').value]?stats('flavor').value:'all';fill(catalog.releases.find(r=>r.flavor===flavor)||{flavor});feedback(status,'请根据统计确认版本策略，保存后才会生效。');showView('publish');};
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
     if(!['all','v34','v61','batch'].includes(r.flavor)||!/^\d{1,5}(\.\d{1,5}){3}$/.test(r.version)||!/^[a-f0-9]{64}$/.test(r.build_sha256))throw new Error();
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
