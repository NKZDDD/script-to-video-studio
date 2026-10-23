const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
class Element{
 constructor(tag){this.tagName=tag;this.children=[];this.events={};this.attributes={};this.open=false;}
 append(...children){this.children.push(...children);}
 replaceChildren(...children){this.children=children;}
 setAttribute(k,v){this.attributes[k]=v;}
 hasAttribute(k){return k in this.attributes||k==='download'&&this.download!==undefined;}
 querySelectorAll(){return this.children.filter(c=>['video','audio'].includes(c.tagName));}
 pause(){this.paused=true;}
 removeAttribute(k){delete this[k];}
 load(){}
 addEventListener(k,fn){this.events[k]=fn;}
 showModal(){this.open=true;}
 close(){this.open=false;this.events.close?.();}
 closest(){return this;}
}
const body=new Element('body'),listeners={},calls=[],locks=[];
let respond=()=>({ok:true,status:206,headers:{get:()=> 'image/png'},body:{cancel:async()=>{}}});
const location={href:'http://127.0.0.1:8770/',origin:'http://127.0.0.1:8770'};
const context={URL,AbortController,document:{body,createElement:tag=>new Element(tag),
 addEventListener(type,fn,capture){assert.equal(capture,true);listeners[type]=fn;}},
 window:{location,StudioLogin:{lock:(...args)=>locks.push(args)}},
 fetch:async(url,options)=>{calls.push({url,options});return respond();}};
vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../web/output-preview.js'),'utf8'),context);
const preview=body.children[0],content=preview.children[1],download=preview.children[0].children[1];
const local='/file?root='+encodeURIComponent('\\\\192.168.1.11\\素材共享盘\\项目')+'&rel='+encodeURIComponent('图片/测试.png');
function click(href=local,type='click',download=false){
 const target=new Element('a');target.href=href;if(download)target.download='test';
 const event={target,type,button:type==='auxclick'?1:0,preventDefault(){this.prevented=true;},stopImmediatePropagation(){this.stopped=true;}};
 listeners[type](event);return event;
}
const flush=()=>new Promise(r=>setImmediate(r));
(async()=>{
 let e=click();await flush();
 assert.equal(e.prevented,true);assert.equal(e.stopped,true);assert.equal(preview.open,true);
 assert.equal(content.children[0].tagName,'img');assert.equal(content.children[0].src,location.origin+local);
 assert.equal(calls[0].options.credentials,'same-origin');assert.equal(calls[0].options.headers.Range,'bytes=0-0');
 assert.equal(download.download,'测试.png');assert.equal(download.hidden,false);
 assert.equal(click(local,'click',true).prevented,undefined);
 assert.equal(click('https://example.test/file?root=x&rel=y').prevented,undefined);
 assert.equal(click('/api/agent/bootstrap').prevented,undefined);
 assert.equal(calls.length,1);
 for(const [mime,tag] of [['video/mp4','video'],['audio/wav','audio'],['text/plain; charset=utf-8','iframe'],['application/json','iframe']]){
   respond=()=>({ok:true,status:206,headers:{get:()=>mime},body:{cancel:async()=>{}}});
   click(local,'auxclick');await flush();assert.equal(content.children[0].tagName,tag);
   if(tag==='iframe')assert.equal(content.children[0].attributes.sandbox,'allow-same-origin');
   if(tag==='video'||tag==='audio'){const media=content.children[0];preview.close();assert.equal(media.paused,true);assert.equal(media.src,undefined);}
 }
 respond=()=>({ok:true,status:206,headers:{get:()=> 'text/html'},body:{cancel:async()=>{}}});
 click();await flush();assert.match(content.textContent,/下载/);assert.equal(download.hidden,false);
 respond=()=>({ok:false,status:404,json:async()=>({error:'不存在'})});
 click();await flush();assert.equal(content.textContent,'不存在');assert.equal(locks.length,0);
 respond=()=>({ok:false,status:401,json:async()=>({error:'请重新登录',login_required:true})});
 click();await flush();assert.equal(preview.open,false);assert.equal(locks.length,1);
 let release;
 respond=()=>new Promise(resolve=>release=resolve);
 click();preview.close();release({ok:true,headers:{get:()=> 'image/png'},body:{cancel:async()=>{}}});await flush();
 assert.equal(preview.open,false);assert.equal(content.children.length,0);
 assert.ok(calls.every(c=>c.url.startsWith(location.origin+'/file?')));
 console.log('file preview: same-session media/text, download, external links, expiry and close race passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
