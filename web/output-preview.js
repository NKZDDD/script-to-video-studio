'use strict';
// Local output links stay in the authenticated WebView; external URLs keep their normal behavior.
(() => {
 const preview=document.createElement('dialog');preview.id='studio-file-preview';
 preview.setAttribute('aria-labelledby','studio-file-title');
 const header=document.createElement('div');header.className='studio-file-toolbar';
 const title=document.createElement('h3');title.id='studio-file-title';
 const download=document.createElement('a');download.textContent='下载原文件';download.className='rv-button';
 const close=document.createElement('button');close.type='button';close.textContent='关闭';close.className='rv-button';
 const content=document.createElement('div');content.className='studio-file-content';
 header.append(title,download,close);preview.append(header,content);document.body.append(preview);
 let revision=0,controller;
 function clear(){
   revision++;controller?.abort();controller=null;
   for(const media of content.querySelectorAll('video,audio')){media.pause();media.removeAttribute('src');media.load();}
   content.replaceChildren();
 }
 preview.addEventListener('close',clear);
 close.addEventListener('click',()=>preview.close());
 async function open(url){
   clear();const current=revision;controller=new AbortController();
   title.textContent=(url.searchParams.get('rel')||'原文件').split(/[\\/]/).pop();
   download.href=url.href;download.download=title.textContent;download.hidden=true;
   content.textContent='正在读取文件…';if(!preview.open)preview.showModal();
   try{
     // One-byte range checks the file and local session, without downloading a whole video.
     const response=await fetch(url.href,{headers:{Range:'bytes=0-0'},credentials:'same-origin',
       cache:'no-store',signal:controller.signal});
     if(current!==revision){await response.body?.cancel();return;}
     if(!response.ok){
       const data=await response.json().catch(()=>({}));
       if(data.login_required){preview.close();window.StudioLogin?.lock(data.error||'请重新登录',response.status===503);return;}
       throw Error(data.error||`文件读取失败（HTTP ${response.status}）`);
     }
     const type=(response.headers.get('Content-Type')||'').split(';')[0];
     await response.body?.cancel();if(current!==revision)return;
     download.hidden=false;
     let media;
     if(type.startsWith('image/')){media=document.createElement('img');media.alt=title.textContent;}
     else if(type.startsWith('video/')||type.startsWith('audio/')){
       media=document.createElement(type.startsWith('video/')?'video':'audio');media.controls=true;media.preload='metadata';
     }else if(type==='text/plain'||type==='application/json'){
       media=document.createElement('iframe');media.title=title.textContent;
       media.setAttribute('sandbox','allow-same-origin');
     }
     if(media){media.src=url.href;content.replaceChildren(media);}
     else content.textContent='此文件可下载后用本机应用打开。';
   }catch(error){if(current===revision&&error.name!=='AbortError')content.textContent=error.message||'文件读取失败，请重试';}
 }
 function intercept(event){
   if(event.type==='auxclick'&&event.button!==1)return;
   const link=event.target.closest?.('a[href]');if(!link||link.hasAttribute('download'))return;
   let url;try{url=new URL(link.href,window.location.href);}catch{return;}
   if(url.origin!==window.location.origin||url.pathname!=='/file')return;
   event.preventDefault();event.stopImmediatePropagation();void open(url);
 }
 document.addEventListener('click',intercept,true);
 document.addEventListener('auxclick',intercept,true);
})();
