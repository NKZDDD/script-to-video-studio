'use strict';
(() => {
 const button=document.createElement('button');
 button.textContent='客户端版本';
 document.querySelector('#key-library').before(button);
 const dialog=document.createElement('dialog');
 dialog.innerHTML=`<div class="dialog-title"><h2>发布 Studio 新版本</h2><button type="button" data-close>关闭</button></div>
 <p class="help">先把安装包上传到本站 /downloads/，再发布版本信息。已安装新版 Studio 的用户可在设置中检查更新。</p>
 <form id="release-form">
 <label>发布体系<select name="flavor"><option value="all">全体系</option><option value="v34">电影级十七章</option><option value="v61">通用十二环节</option></select></label>
 <label>导入打包生成的版本信息<input type="file" name="manifest" accept=".json"></label>
 <label>版本号<input name="version" required placeholder="2026.10.20.0"></label>
 <label>安装包 HTTPS 地址<input name="url" type="url" required></label>
 <label>SHA256<input name="sha256" required minlength="64" maxlength="64"></label>
 <label>文件大小（字节）<input name="bytes" type="number" min="1" required></label>
 <label>更新说明<textarea name="notes" rows="4" maxlength="5000"></textarea></label>
 <p data-status role="status"></p><button class="primary">发布版本</button></form>`;
 document.body.append(dialog);
 const form=dialog.querySelector('form'), status=dialog.querySelector('[data-status]');
 const fill=r=>{for(const key of ['flavor','version','url','sha256','bytes','notes'])form.elements[key].value=r?.[key]??(key==='flavor'?'all':'');};
 dialog.querySelector('[data-close]').onclick=()=>dialog.close();
 button.onclick=()=>safe(async()=>{
   const data=await api('releases');fill(data.releases.find(r=>r.flavor==='all'));
   status.textContent='';dialog.showModal();
 });
 form.elements.flavor.onchange=()=>safe(async()=>{const f=form.elements.flavor.value,d=await api('releases');fill(d.releases.find(r=>r.flavor===f)||{flavor:f});});
 form.elements.manifest.onchange=async e=>{try{const r=JSON.parse(await e.target.files[0].text());fill(r);status.textContent='已导入，请确认安装包已上传再发布。';}catch{status.textContent='版本信息文件无效';}};
 form.onsubmit=async e=>{e.preventDefault();const submit=form.querySelector('button');submit.disabled=true;try{
   const data={};for(const key of ['flavor','version','url','sha256','notes'])data[key]=form.elements[key].value.trim();
   data.bytes=Number(form.elements.bytes.value);await api('releases',data);status.textContent='已发布，客户端下次检查更新即可看到。';
 }catch(error){status.textContent=error.message;}finally{submit.disabled=false;}};
})();
