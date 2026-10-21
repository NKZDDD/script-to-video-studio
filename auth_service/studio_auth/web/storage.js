'use strict';
(() => {
 const button=document.createElement('button');button.textContent='R2 对象存储';
 document.querySelector('#key-library').before(button);
 const dialog=document.createElement('dialog');dialog.className='r2-dialog';dialog.id='r2-manager';document.body.append(dialog);
 const error=e=>{const target=dialog.querySelector('[data-r2-message]');if(target){target.textContent=e.message;target.dataset.error='true';}};
 const field=(name,label,value='',type='text',extra='')=>`<label>${label}<input name="${name}" type="${type}" value="${esc(value)}" ${extra}></label>`;
 function shell(title,body){dialog.innerHTML=`<div class="r2-header"><div><small>RESPECT STUDIO</small><h2>${esc(title)}</h2></div><button type="button" data-r2-close aria-label="关闭">×</button></div>${body}`;dialog.querySelector('[data-r2-close]').onclick=()=>dialog.close();if(!dialog.open)dialog.showModal();}
 dialog.addEventListener('close',()=>{if(!dialog.open)dialog.replaceChildren();});
 async function settings(){
   const d=await api('storage'),c=d.config;
   shell('R2 对象存储',`<form id="r2-form"><div class="r2-body"><p class="muted">统一保存，按账号授权。客户端只获取临时上传链接，图片和音频直接传到 R2。</p><div class="r2-import"><label>导入旧版 config.json<input type="file" accept=".json" data-r2-import></label><small>只提取 upload 字段；确认保存后生效。</small></div><div class="r2-group r2-grid">${field('endpoint','S3 Endpoint',c.endpoint||'','url','required')}${field('bucket','Bucket',c.bucket||'','text','required')}${field('public_base_url','公开访问域名',c.public_base_url||'','url','required')}${field('prefix','对象目录前缀',c.prefix||'studio-references','text','required pattern="[a-zA-Z0-9_-]{1,64}" maxlength="64"')}</div><div class="r2-group r2-grid">${field('access_key','Access Key · '+(d.access_key_set?'已保存':'未配置'),'','password','autocomplete="new-password" placeholder="留空保留已保存值"')}${field('secret_key','Secret Key · '+(d.secret_key_set?'已保存':'未配置'),'','password','autocomplete="new-password" placeholder="留空保留已保存值"')}</div><div class="r2-group"><label class="check"><input type="checkbox" name="enabled" ${d.enabled?'checked':''}>启用后台 R2 上传</label><p class="help">已授权 ${d.assigned_accounts} 个账号。保存后，到账号列表点击「R2 授权」分配。更换配置会更新客户端上传缓存范围；已上传文件不会自动删除。</p></div><p class="help">停用或取消授权后，不再签发上传链接；已签发链接最长约 5 分钟内失效。公开参考图链接与安装包下载保持独立。</p></div><div class="r2-footer"><p data-r2-message role="status"></p><button class="primary" type="submit">保存 R2 配置</button></div></form>`);
   const form=dialog.querySelector('#r2-form');
   form.querySelector('[data-r2-import]').onchange=async e=>{try{const file=e.target.files[0];if(!file)return;if(file.size>2097152)throw Error('配置文件最多 2 MB');const data=JSON.parse(await file.text()),u=data.upload;if(!u||typeof u!=='object')throw Error('未找到 upload 配置');for(const key of ['endpoint','bucket','public_base_url','prefix','access_key','secret_key'])if(typeof u[key]==='string')form.elements[key].value=u[key];form.elements.enabled.checked=true;const msg=dialog.querySelector('[data-r2-message]');msg.textContent='已读取 R2 配置，确认后保存；其他服务商 Key 不会提交。';msg.dataset.error='false';}catch(e){error(e);}finally{e.target.value='';}};
   form.onsubmit=async e=>{e.preventDefault();const submit=form.querySelector('[type=submit]');submit.disabled=true;try{const values=Object.fromEntries(new FormData(form));values.enabled=form.elements.enabled.checked;await api('storage',values);await settings();dialog.querySelector('[data-r2-message]').textContent='已保存。已授权的客户端登录或刷新后台配置后获取新设置。';}catch(e){error(e);}finally{submit.disabled=false;}};
 }
 async function grant(user){
   const path='users/'+encodeURIComponent(user.username)+'/storage',d=await api(path);
   shell('R2 授权 · '+user.username,`<form id="r2-grant"><div class="r2-body"><div class="r2-group"><label class="check"><input name="enabled" type="checkbox" ${d.assigned?'checked':''}>允许此账号使用后台 R2 上传</label><p class="help">客户端登录或主动刷新后接收配置，不需要手动填写存储密钥。账号仍须启用且在有效期内。</p></div><p class="help">取消后不再签发上传链接；已公开的参考图不会删除。客户端自有的本地存储配置不属于此授权。</p></div><div class="r2-footer"><p data-r2-message role="status"></p><button type="submit" class="primary">保存账号授权</button></div></form>`);
   const form=dialog.querySelector('form');form.onsubmit=async e=>{e.preventDefault();const submit=form.querySelector('[type=submit]');submit.disabled=true;try{await api(path,{enabled:form.elements.enabled.checked});dialog.close();message('R2 授权已保存，客户端可点击“刷新后台配置”。');}catch(e){error(e);}finally{submit.disabled=false;}};
 }
 button.onclick=()=>safe(settings);
 document.querySelector('#users').addEventListener('click',e=>{const b=e.target.closest('[data-action=storage]');if(!b)return;e.stopImmediatePropagation();safe(()=>grant(rows[Number(b.dataset.index)]));},true);
})();
