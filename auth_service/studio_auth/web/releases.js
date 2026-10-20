'use strict';
(() => {
 const button=document.createElement('button');
 button.textContent='客户端版本';
 document.querySelector('#key-library').before(button);
 const dialog=document.createElement('dialog');
 dialog.innerHTML=`<div class="dialog-title"><h2>发布 Studio 更新</h2><button type="button" data-close>关闭</button></div>
 <section class="card"><p data-enforcement-status role="status"></p>
 <button type="button" data-enforcement-toggle>读取版本校验状态…</button>
 <p class="help">过渡期间可暂停所有体系的版本及构建校验，旧包仍须登录并通过账号有效期和 Key 授权。暂停时也隐藏更新推荐，保留已发布版本；发完新包后恢复原规则。只发布新版不会自动恢复。</p></section>
 <p class="help">先将安装包上传 R2 并完成下载校验，再导入 release.json。上传文件不会自动发布，点击下方按钮后版本规则才生效。</p>
 <form id="release-form">
 <label>发布体系<select name="flavor"><option value="all">全体系</option><option value="v34">电影级十七章</option><option value="v61">通用十二环节</option></select></label>
 <label>导入上传工具生成的版本信息<input type="file" name="manifest" accept=".json"></label>
 <label>推荐更新版本<input name="version" required placeholder="2026.10.20.2"></label>
 <label>安装包 HTTPS 下载地址（R2 / 自定义域名）<input name="url" type="url" required></label>
 <label>SHA256<input name="sha256" required minlength="64" maxlength="64"></label>
 <label>程序构建校验码<input name="build_sha256" required minlength="64" maxlength="64" readonly></label>
 <label>最低可用版本（低于此版本必须更新）<select name="minimum_version" required></select></label>
 <p class="help" data-minimum></p>
 <p class="help">例如推荐 V3、最低 V2：V2 可以稍后更新，V1 必须更新。最低版本只能提高；可选择已登记版本或本次推荐版本。旧绿色版和 2026.10.20.1 及以前的安装版首次切换 R2 时需手动安装一次新版。</p>
 <p class="help">校验码识别发布构建，不是防破解凭证；客户端直连服务商的 Key 仍可被提取。</p>
 <label>文件大小（字节）<input name="bytes" type="number" min="1" required></label>
 <label>更新说明<textarea name="notes" rows="4" maxlength="5000"></textarea></label>
 <p data-status role="status"></p><button class="primary">发布更新设置</button></form>`;
 document.body.append(dialog);
 const form=dialog.querySelector('form'), status=dialog.querySelector('[data-status]');
 let catalog={releases:[],builds:[],enforcement_enabled:true};
 const toggle=dialog.querySelector('[data-enforcement-toggle]');
 const showEnforcement=()=>{
   const enabled=catalog.enforcement_enabled!==false;
   toggle.textContent=enabled?'暂停版本校验':'恢复版本校验';
   dialog.querySelector('[data-enforcement-status]').textContent=enabled?
     '版本校验已开启：执行最低版本和构建登记规则。':
     '版本校验已暂停：旧包可正常登录；账号和 Key 授权检查仍开启。';
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
   dialog.querySelector('[data-minimum]').textContent='当前最低可用版本：'+(floor==='0.0.0.0'?'尚未设定':floor)+'。低于推荐版本、但达到最低版本的已登记客户端可以继续使用。';
 };
 const fill=r=>{
   for(const key of ['flavor','version','url','sha256','build_sha256','bytes','notes'])
     form.elements[key].value=r?.[key]??(key==='flavor'?'all':'');
   minimumOptions(r?.minimum_version||(r?.mandatory?r.version:previous()?.minimum_version)||'0.0.0.0');
 };
 const refresh=async()=>{catalog=await api('releases');catalog.builds||=[];showEnforcement();};
 toggle.onclick=async()=>{
   toggle.disabled=true;
   try {
     const enabled=catalog.enforcement_enabled===false;
     await api('releases/enforcement',{enabled});await refresh();
     status.textContent=enabled?'已恢复原版本规则，客户端下次校验时生效。':'已暂停版本校验。原版本记录已保留，发布新版也不会自动恢复。';
   } catch(error){status.textContent=error.message;}
   finally{toggle.disabled=false;}
 };
 dialog.querySelector('[data-close]').onclick=()=>dialog.close();
 button.onclick=()=>safe(async()=>{
   await refresh();fill(catalog.releases.find(r=>r.flavor==='all'));
   status.textContent='';dialog.showModal();
 });
 form.elements.flavor.onchange=()=>safe(async()=>{const f=form.elements.flavor.value;await refresh();fill(catalog.releases.find(r=>r.flavor===f)||{flavor:f});});
 form.elements.version.onchange=()=>minimumOptions(form.elements.minimum_version.value);
 form.elements.manifest.onchange=async e=>{try{const r=JSON.parse(await e.target.files[0].text());fill(r);status.textContent='已导入，请确认推荐版本、最低可用版本和已校验的下载地址。';}catch{status.textContent='版本信息文件无效';}};
 form.onsubmit=async e=>{e.preventDefault();const submit=form.querySelector('button');submit.disabled=true;try{
   const data={};for(const key of ['flavor','version','url','sha256','build_sha256','notes','minimum_version'])data[key]=form.elements[key].value.trim();
   data.bytes=Number(form.elements.bytes.value);
   await api('releases',data);await refresh();fill(catalog.releases.find(r=>r.flavor===data.flavor));
   status.textContent=catalog.enforcement_enabled===false?
     '已保存版本设置；版本校验仍处于暂停状态，发完新包后请点击“恢复版本校验”。':
     '已发布。客户端启动、登录和提交新任务时会校验；已有批次不会被强制中断。';
 }catch(error){status.textContent=error.message;}finally{submit.disabled=false;}};
})();
