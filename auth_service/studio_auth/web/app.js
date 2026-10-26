'use strict';
const $=s=>document.querySelector(s);
let csrf='',page=1,rows=[],editing=null,total=0;
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const date=v=>v==null?'永久有效':new Date(v*1000).toLocaleString();
function message(text,error=false){$('#message').textContent=text;$('#message').classList.toggle('error',error);$('#message').hidden=!text;}
function locked(){csrf='';$('#login').hidden=false;$('#workspace').hidden=true;$('#identity').hidden=true;$('#editor').close();document.querySelector('#provisioning')?.close();document.querySelector('#r2-manager')?.close();document.querySelector('#adapter-manager')?.close();document.querySelector('#request-diagnostics')?.close();}
async function api(path,body){
 const response=await fetch('/admin/api/'+path,body===undefined?{cache:'no-store'}:{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify(body)});
 let data;try{data=await response.json();}catch{throw Error(response.status===413?'上传被拒绝（HTTP 413）：请将本站反向代理 location 中的 client_max_body_size 改为 3m，保存并重载 Nginx 后重试。':`服务返回了无法解析的响应（HTTP ${response.status}），请检查反向代理和服务状态。`);}
 if(!response.ok){if(response.status===401&&path!=='login')locked();throw Error(data.error?.message||'操作失败，请重试');}return data;
}
async function authorized(data){csrf=data.csrf_token;$('#admin-name').textContent=data.username;$('#login').hidden=true;$('#workspace').hidden=false;$('#identity').hidden=false;$('#login-form').reset();message('');await load();}
async function load(){const d=await api('users?q='+encodeURIComponent($('#query').value.trim())+'&page='+page);rows=d.users;total=d.total;$('#count').textContent=`共 ${total} 个账号`;
 $('#users').innerHTML=rows.map((u,i)=>{const expired=u.expires_at!==null&&u.expires_at<=d.server_time,status=!u.enabled?'已停用':expired?'已到期':'正常';return `<tr><td>${esc(u.username)}</td><td><span class="badge ${status==='正常'?'':'bad'}">${status}</span></td><td>${esc(date(u.expires_at))}</td><td>${u.max_devices||'不限'}</td><td>${[['edit','编辑'],['toggle',u.enabled?'停用':'启用'],['password','重置密码'],['sessions','登录设备'],['revoke','撤销登录'],['providers','分配 Key'],['storage','R2 授权'],['usage','用量对账'],['diagnostics','请求诊断']].map(([action,label])=>`<button data-action="${action}" data-index="${i}">${label}</button>`).join('')}</td></tr>`;}).join('')||'<tr><td colspan="5" class="muted">没有匹配的账号，点击“创建账号”开始。</td></tr>';
 $('#page-label').textContent=`第 ${page} 页 / 共 ${Math.max(1,Math.ceil(total/50))} 页`;$('#prev').disabled=page<=1;$('#next').disabled=page*50>=total;
}
async function safe(fn){try{await fn();}catch(e){message(e.message,true);}}
$('#login-form').addEventListener('submit',e=>{e.preventDefault();safe(async()=>{const b=e.target.querySelector('button');b.disabled=true;try{const f=new FormData(e.target);await authorized(await api('login',{username:f.get('username').trim(),password:f.get('password')}));}finally{b.disabled=false;e.target.elements.password.value='';}});});
$('#logout').onclick=()=>safe(async()=>{await api('logout',{});locked();message('已退出后台');});
$('#search-form').onsubmit=e=>{e.preventDefault();page=1;safe(load);};$('#refresh').onclick=()=>safe(load);
$('#prev').onclick=()=>{page--;safe(load);};$('#next').onclick=()=>{page++;safe(load);};
function localTime(ts){if(ts==null)return '';const d=new Date(ts*1000);return new Date(d.getTime()-d.getTimezoneOffset()*60000).toISOString().slice(0,16);}
function open(mode,user){editing={mode,user};$('#editor-title').textContent=mode==='create'?'创建账号':mode==='password'?'重置密码 · '+user.username:'编辑账号 · '+user.username;$('#editor-error').textContent='';$('#session-list').hidden=true;$('#editor-form').hidden=false;
 let html=mode==='create'?'<label>账号<input name="username" required minlength="3" maxlength="64" pattern="[a-zA-Z0-9][a-zA-Z0-9_.@+\\-]{2,63}" autocomplete="off"></label>':'';
 if(mode==='create'||mode==='password')html+='<label>新密码<input name="password" type="password" required minlength="12" maxlength="128" autocomplete="new-password"></label><label>再次输入密码<input name="confirm" type="password" required minlength="12" maxlength="128" autocomplete="new-password"></label><p class="help">至少 12 个字符。重置密码会撤销该账号全部旧登录。</p>';
 if(mode!=='password')html+=`<label>到期时间（当前电脑时区）<input name="expires" type="datetime-local" value="${localTime(user?.expires_at)}"></label><p class="help">留空表示永久有效。</p><label>设备数量上限<input name="max_devices" type="number" min="0" max="100000" step="1" required value="${user?.max_devices||0}"></label><p class="help">0 表示不限；修改设备上限会撤销该账号全部旧登录。</p>`;
 $('#fields').innerHTML=html;$('#editor').showModal();$('#fields input')?.focus();
}
$('#create').onclick=()=>open('create');$('#close').onclick=$('#cancel').onclick=()=>$('#editor').close();
$('#editor').addEventListener('close',()=>{$('#fields').replaceChildren();editing=null;});
$('#editor-form').onsubmit=async e=>{e.preventDefault();const b=$('#save');b.disabled=true;$('#editor-error').textContent='';try{const f=new FormData(e.target),{mode,user}=editing,body={};if(mode==='create'||mode==='password'){if(f.get('password')!==f.get('confirm'))throw Error('两次密码不一致');body.password=f.get('password');}if(mode!=='password'){body.expires_at=f.get('expires')?Math.floor(new Date(f.get('expires')).getTime()/1000):null;const limit=Number(f.get('max_devices'));if(mode==='create'||limit!==user.max_devices)body.max_devices=limit;}if(mode==='create')body.username=f.get('username').trim();
 await api(mode==='create'?'users':'users/'+encodeURIComponent(user.username)+(mode==='password'?'/password':'/settings'),body);$('#editor').close();message('已保存');if(mode==='create'){page=1;$('#query').value='';}await load();}catch(e){$('#editor-error').textContent=e.message;}finally{b.disabled=false;}};
$('#users').onclick=e=>{const button=e.target.closest('[data-action]');if(!button)return;const user=rows[Number(button.dataset.index)],action=button.dataset.action;if(!['edit','password','sessions','toggle','revoke'].includes(action))return;if(action==='edit'||action==='password'){open(action,user);return;}
 safe(async()=>{button.disabled=true;try{const url='users/'+encodeURIComponent(user.username);if(action==='sessions'){const d=await api(url+'/sessions');$('#editor-title').textContent='登录设备 · '+user.username;$('#editor-form').hidden=true;$('#session-list').hidden=false;$('#session-list').innerHTML='<p class="help">显示最近 100 条登录记录；未过期且未撤销的登录占用设备名额。</p><table><thead><tr><th>设备</th><th>登录时间</th><th>状态</th></tr></thead><tbody>'+d.sessions.map(s=>`<tr><td>${esc(s.device_name)}</td><td>${esc(date(s.created_at))}</td><td>${s.revoked_at!==null?'已撤销':s.expires_at<=d.server_time?'已过期':'有效'}</td></tr>`).join('')+'</tbody></table>';if(!d.sessions.length)$('#session-list').innerHTML='<p class="muted">暂无登录设备。</p>';$('#editor').showModal();return;}
 if(!confirm(action==='revoke'?`撤销 ${user.username} 的全部登录？客户端需要重新登录。`:`确认${user.enabled?'停用':'启用'} ${user.username}？`))return;
 await api(url+(action==='revoke'?'/revoke':'/settings'),action==='revoke'?{}:{enabled:!user.enabled});message('操作已生效');await load();}finally{button.disabled=false;}});
};
safe(async()=>{try{await authorized(await api('me'));}catch(e){locked();if(e.message!=='请先登录管理后台')message(e.message,true);}});
