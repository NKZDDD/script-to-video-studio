// Execute the login UI: automatic status reads stay local; retry is explicit.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const nodes = new Map();
const node = s => {
 if(!nodes.has(s))nodes.set(s,{hidden:false,value:'',classList:{toggle(){}},append(){},
  addEventListener(type,fn){this[type]=fn;}});
 return nodes.get(s);
};
const calls=[];let tick,focus;
const window={StudioUpdates:{ready:Promise.resolve(),refresh(){}},
 addEventListener(type,fn){if(type==='focus')focus=fn;},dispatchEvent(){}};
const context={document:{querySelector:node,createElement:()=>node('created'+nodes.size),
 body:{classList:{add(){},remove(){}}}},window,Event:class{},
 setInterval(fn){tick=fn;},fetch:async(url)=>{calls.push(url);return {ok:true,status:200,
 json:async()=>({authenticated:true,user:{username:'test'}})};}};
vm.runInNewContext(fs.readFileSync(require('node:path').join(__dirname,'../web/login.js'),'utf8'),context);
const flush=()=>new Promise(r=>setImmediate(r));
(async()=>{
 await flush();
 await window.StudioLogin.ready;
 tick();await flush();focus();await flush();
 assert.deepEqual(calls,['/api/auth/status','/api/auth/status','/api/auth/status']);
 node('#login-retry').click();await flush();
 assert.equal(calls.at(-1),'/api/auth/verify');
 console.log('login screen: automatic checks local, manual verification retained');
})().catch(e=>{console.error(e);process.exitCode=1;});
