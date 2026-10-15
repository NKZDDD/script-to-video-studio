'use strict';
(() => {
 const $=s=>document.querySelector(s);
 let authenticated=false,resolveReady,checking=null,revision=0;
 const ready=new Promise(resolve=>resolveReady=resolve);
 const account=document.createElement('button');account.id='studio-account';account.className='rv-button';account.type='button';
 document.querySelector('.rv-nav').append(account);
 const message=(text,error=false)=>{const el=$('#login-message');el.textContent=text;el.classList.toggle('is-error',error);};
 const lock=(text='请登录后继续使用',retry=false)=>{
   revision++;
   authenticated=false;document.body.classList.add('studio-locked');
   const dialog=$('#dialog');if(dialog?.open)dialog.close();
   message(text,true);$('#login-retry').hidden=!retry;
 };
 const unlock=data=>{
   authenticated=true;document.body.classList.remove('studio-locked');$('#login-password').value='';
   account.textContent=(data.user?.username||'已登录')+' · 退出';
   account.title=data.user?.expires_at?'有效期至 '+new Date(data.user.expires_at*1000).toLocaleString():'账号无到期日';
   resolveReady();
 };
 async function request(action,body){
   const response=await fetch('/api/auth/'+action,body===undefined?{cache:'no-store'}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
   const data=await response.json();
   if(!response.ok)throw Error(data.error||'登录服务暂不可用');
   return data;
 }
 async function check(force=false){
   if(checking)return checking;
   const version=revision;
   checking=(async()=>{
     try{const data=await request(force?'verify':'status',force?{}:undefined);if(version!==revision)return;if(data.authenticated)unlock(data);else lock(data.message||'请登录后继续使用',data.can_retry);}
     catch(e){if(version===revision)lock(e.message||'无法连接本机 Studio，请确认程序仍在运行',true);}
     finally{checking=null;}
   })();return checking;
 }
 $('#studio-login-form').addEventListener('submit',async event=>{
   event.preventDefault();if(!event.target.reportValidity())return;
   revision++;
   const button=$('#login-submit');button.disabled=true;message('正在登录…');
   try{const data=await request('login',{username:$('#login-username').value.trim(),password:$('#login-password').value});if(data.authenticated)unlock(data);else lock('登录未完成，请重试');}
   catch(e){message(e.message,true);$('#login-password').value='';}
   finally{button.disabled=false;}
 });
 $('#login-retry').addEventListener('click',()=>check(true));
 account.addEventListener('click',async()=>{
   account.disabled=true;$('#login-submit').disabled=true;lock('正在退出…');
   try{await request('logout',{});lock('已退出，请登录后继续使用');}
   catch(e){lock('本机已退出。'+e.message);}
   finally{account.disabled=false;$('#login-submit').disabled=false;}
 });
 window.StudioLogin={ready,isAuthenticated:()=>authenticated,lock};
 setInterval(()=>{if(authenticated)check(true);},60000);
 window.addEventListener('focus',()=>{if(authenticated)check();});
 check();
})();
