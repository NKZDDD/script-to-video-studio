'use strict';
(() => {
 const button=document.createElement('button');button.textContent='服务商适配包';
 document.querySelector('#key-library').before(button);
 const dialog=document.createElement('dialog');dialog.className='r2-dialog';dialog.id='adapter-manager';document.body.append(dialog);
 let tab='current';
 const error=e=>{const n=dialog.querySelector('[data-adapter-message]');if(n){n.textContent=e.message;n.dataset.error='true';}};
 async function render(note=''){
  const d=await api('adapters'),active=d.bundles.find(b=>b.sha256===d.active);
  const cards=d.bundles.map(b=>`<article class="r2-group"><h3>${esc(b.version)} ${b.sha256===d.active?'· 已发布':''}</h3><p>${b.providers.length} 个服务商 · ${esc(new Date(b.created_at*1000).toLocaleString())}</p><details><summary>查看服务商</summary><p>${b.providers.map(p=>esc(p.name)+' ('+esc(p.id)+')').join('、')}</p><small>SHA-256：${esc(b.sha256)}</small></details>${b.sha256===d.active?'':`<button type="button" data-publish="${esc(b.sha256)}">发布此版本</button>`}</article>`).join('');
  dialog.innerHTML=`<div class="r2-header"><div><small>RESPECT STUDIO</small><h2>服务商适配包</h2></div><button type="button" data-close aria-label="关闭">×</button></div><div class="r2-body"><div class="actions" role="tablist" aria-label="适配包管理">${[['current','当前发布'],['import','导入版本'],['history','版本历史']].map(([id,label])=>`<button type="button" role="tab" aria-selected="${tab===id}" data-tab="${id}" class="${tab===id?'primary':''}">${label}</button>`).join('')}</div><section ${tab==='current'?'':'hidden'}><div class="r2-group"><h3>${active?esc(active.version):'尚未发布适配包'}</h3><p>${active?`当前包含 ${active.providers.length} 个服务商。客户端登录或刷新后台配置时更新。`:'先导入完整适配包，再在版本历史中发布。旧版客户端保持原加载方式。'}</p><p class="help">账号通过 Key 库分配服务商和模型；显示名称沿用你为 Key 设置的名称。每个客户端只领取获授权的适配器及其依赖。</p></div><div class="r2-group"><p>图片、视频仍由客户端直接请求服务商。后台保存接口实现，不承担生成和媒体转发。</p><p class="help">已有实例继续使用领取时的代码；新实例使用当前发布版本。回退可在版本历史重新发布旧包。</p></div></section><section ${tab==='import'?'':'hidden'}><div class="r2-import"><label>导入适配包 JSON<input type="file" accept=".json" data-import></label><small>导入只登记，点击“发布此版本”才生效。最多 2 MB。</small></div><div class="r2-group"><p>适配包包含在客户端执行的 Python 代码。只上传你审查过的包，不放 Key、config.json 或其他私密文件。</p><p class="help">包内需要包含全部当前使用的服务商。需要新运行库或新协议的适配器仍需升级客户端。</p></div></section><section ${tab==='history'?'':'hidden'}>${cards||'<p class="muted">暂无导入记录。</p>'}</section></div><div class="r2-footer"><p data-adapter-message role="status">${esc(note)}</p><button type="button" data-refresh>刷新</button></div>`;
  dialog.querySelector('[data-close]').onclick=()=>dialog.close();
  dialog.querySelector('[data-refresh]').onclick=()=>render().catch(error);
  dialog.querySelectorAll('[data-tab]').forEach(b=>b.onclick=()=>{tab=b.dataset.tab;render().catch(error);});
  dialog.querySelector('[data-import]').onchange=async e=>{const f=e.target.files[0];if(!f)return;e.target.disabled=true;try{if(f.size>2097152)throw Error('适配包最多 2 MB');const bundle=JSON.parse(await f.text());const r=await api('adapters/import',{bundle});tab='history';await render(`已导入 ${r.version}，尚未发布。`);}catch(e){error(e);}finally{e.target.disabled=false;e.target.value='';}};
  dialog.querySelectorAll('[data-publish]').forEach(b=>b.onclick=async()=>{b.disabled=true;try{await api('adapters/publish',{sha256:b.dataset.publish});tab='current';await render('已发布。客户端登录或刷新后台配置后获取。');}catch(e){error(e);}finally{b.disabled=false;}});
  if(!dialog.open)dialog.showModal();
 }
 dialog.addEventListener('close',()=>dialog.replaceChildren());
 button.onclick=()=>safe(()=>render());
})();
