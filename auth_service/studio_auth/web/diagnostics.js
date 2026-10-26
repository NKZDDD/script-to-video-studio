'use strict';
(() => {
 const dialog=document.createElement('dialog');dialog.id='request-diagnostics';
 dialog.innerHTML=`<div class="dialog-title"><h2 id="diagnostics-title">请求诊断</h2><button id="diagnostics-close">关闭</button></div>
 <p class="help">服务端记录表示请求已到达登录服务；客户端记录由升级后的 Studio 上报，断网期间暂存，恢复登录后补报。没有记录不能证明用户未操作或本地断网。</p>
 <form id="diagnostics-filter" class="toolbar">
 <label>来源<select name="source"><option value="">全部</option><option value="server">服务端收到</option><option value="client">客户端上报</option></select></label>
 <label>类型<select name="kind"><option value="">全部</option><option value="auth">登录服务</option><option value="llm">LLM 调用</option><option value="provider">生产任务</option><option value="local">本地操作</option></select></label>
 <label>结果<select name="outcome"><option value="">全部</option><option value="error">失败</option><option value="success">成功</option></select></label>
 <label>收到时间<select name="hours"><option value="24">24 小时内</option><option value="168">7 天内</option><option value="720">30 天内</option></select></label>
 <label>请求编号<input name="request_id" maxlength="36" placeholder="可从下方复制编号"></label><button>查询 / 刷新</button></form>
 <p id="diagnostics-message" role="status"></p><div class="table-wrap"><table><thead><tr><th>发生 / 收到时间</th><th>来源 / 操作</th><th>耗时 / 结果</th><th>错误类型</th><th>设备 / 版本 / 目标</th><th>请求编号</th></tr></thead><tbody id="diagnostics-rows"></tbody></table></div>
 <div class="pager"><span id="diagnostics-count"></span><button id="diagnostics-prev">上一页</button><button id="diagnostics-next">下一页</button></div>
 <p class="help">登录请求可用相同编号对照客户端总耗时和服务端处理耗时；差值还包含代理、传输及客户端处理，不能据此单独认定哪方网络故障。LLM、生产任务耗时包含等待、轮询和重试，并非单次 HTTP 耗时。本地操作耗时包含磁盘读写，不代表网络耗时。</p>
 <p class="help">仅保留最近 30 天且全站最多 50,000 条诊断。超限删除最旧记录。登录失败显示的账号可能只是请求方填写的名称。不会记录密码、Key、提示词、请求正文或素材路径。</p>`;
 document.body.append(dialog);
 const q=s=>dialog.querySelector(s),form=q('#diagnostics-filter');
 let account='',page=1,total=0,generation=0;
 const labels={auth:'登录服务',llm:'LLM 调用',provider:'生产任务',local:'本地操作',
   none:'—',dns:'DNS 解析失败',tls:'TLS / 证书错误',timeout:'超时',connection:'连接失败',http:'HTTP 错误',
   authentication:'认证拒绝',validation:'响应或参数格式异常',cancelled:'已取消',unknown:'未分类错误'};
 async function refresh(){
   const ticket=++generation,params=new URLSearchParams(new FormData(form));params.set('page',String(page));
   q('#diagnostics-message').textContent='正在读取记录…';
   try{const d=await api('users/'+encodeURIComponent(account)+'/diagnostics?'+params);
     if(ticket!==generation||!dialog.open)return;
     total=d.total;
     q('#diagnostics-message').textContent=d.server_recording_error?'服务端诊断写入异常，部分记录可能缺失。':d.server_dropped_since_start?`本次服务运行期间有 ${d.server_dropped_since_start} 条诊断未保存（队列满或存储异常）。`:'';
     q('#diagnostics-rows').innerHTML=d.events.map(e=>`<tr><td>${esc(date(e.at))}<br><small>收到：${esc(date(e.received_at))}</small></td>
       <td>${e.source==='server'?'服务端收到':'客户端上报'}<br>${esc(labels[e.kind])} · ${esc(e.operation)}${e.identity==='claimed'?'<br><small>账号名称未经认证</small>':''}</td>
       <td>${(e.duration_ms/1000).toFixed(3)} 秒<br>${e.outcome==='error'?'失败':'成功'}${e.status?' · HTTP '+esc(e.status):''}</td>
       <td>${esc(labels[e.category]||e.category)}${e.client_recording_error?'<br>客户端暂存异常':''}${e.client_dropped_since_start?'<br>客户端诊断缺失 '+esc(e.client_dropped_since_start)+' 条':''}</td><td>${esc(e.device||'—')}<br>${esc(e.version||'版本未提供')}<br>${esc(e.host||e.peer||'—')}</td>
       <td>${esc(e.request_id||'—')}</td></tr>`).join('')||'<tr><td colspan="6">暂无匹配记录。客户端诊断需要升级客户端；断网期间不会实时到达后台。</td></tr>';
     q('#diagnostics-count').textContent=`共 ${total} 条，第 ${page} / ${Math.max(1,Math.ceil(total/50))} 页`;
     q('#diagnostics-prev').disabled=page<=1;q('#diagnostics-next').disabled=page*50>=total;
   }catch(e){if(ticket===generation)q('#diagnostics-message').textContent=e.message;}
 }
 form.onsubmit=e=>{e.preventDefault();page=1;refresh();};
 q('#diagnostics-close').onclick=()=>dialog.close();dialog.onclose=()=>{generation++;q('#diagnostics-rows').replaceChildren();};
 q('#diagnostics-prev').onclick=()=>{page--;refresh();};q('#diagnostics-next').onclick=()=>{page++;refresh();};
 document.querySelector('#users').addEventListener('click',e=>{
   const b=e.target.closest('[data-action="diagnostics"]');if(!b)return;e.stopImmediatePropagation();
   account=rows[Number(b.dataset.index)].username;page=1;form.reset();
   q('#diagnostics-title').textContent='请求诊断 · '+account;dialog.showModal();refresh();
 },true);
})();
