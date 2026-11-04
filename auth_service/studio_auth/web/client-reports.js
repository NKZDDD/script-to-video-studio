'use strict';
(() => {
 const dialog=document.createElement('dialog');dialog.id='client-reports';
 dialog.innerHTML=`<div class="dialog-title"><h2 id="reports-title">客户端日志</h2><button id="reports-close">关闭</button></div>
 <p class="help">用户从 Studio“设置 → 错误日志上报”主动提交。详细日志不自动发送；报告是客户端提供的排错证据，不能单凭某一条记录认定网络或服务商故障。</p>
 <form id="reports-filter" class="toolbar"><label>设备标识<input name="device" maxlength="16" placeholder="可粘贴下方设备标识"></label><button>查询 / 刷新</button></form>
 <p id="reports-message" role="status"></p><div id="reports-list"></div>
 <div class="pager"><span id="reports-count"></span><button id="reports-prev">上一页</button><button id="reports-next">下一页</button></div>
 <section id="report-detail" hidden><h3>报告详情</h3><div id="report-summary"></div><div class="toolbar"><label>筛选服务商、模型、任务 ID 或错误<input id="report-search" placeholder="输入关键词"></label><button id="report-download" type="button">下载脱敏 JSON</button></div><div id="report-events" class="table-wrap"></div></section>
 <p class="help">报告保留 30 天，全站最多 500 份。每份最多 200 条；超限或缺失数量在详情中显示。Key 指纹仅用于比对实际使用的密钥是否相同，不是密钥内容。HTTP 为接口返回状态；“上游状态”是从错误内容提取的状态，不等于本机请求的 HTTP 状态。</p>`;
 document.body.append(dialog);
 const q=s=>dialog.querySelector(s),form=q('#reports-filter');
 const reasons={task:'生产任务失败',network:'连接或超时',interface:'界面操作异常或卡顿',other:'其他问题'};
 const stages={submit:'提交生成',query:'查询结果',download:'下载产物',complete:'任务结束',request:'请求'};
 const categories={none:'—',dns:'DNS',tls:'TLS',timeout:'超时',connection:'连接失败',authentication:'认证拒绝',http:'HTTP 错误',validation:'格式异常',unknown:'未分类错误',cancelled:'已取消'};
 let account='',page=1,generation=0,report=null;
 async function refresh(){const ticket=++generation,params=new URLSearchParams(new FormData(form));params.set('page',String(page));q('#reports-message').textContent='正在读取…';
  try{const d=await api('users/'+encodeURIComponent(account)+'/client-reports?'+params);if(ticket!==generation||!dialog.open)return;
   q('#reports-message').textContent='';q('#reports-list').innerHTML=`<div class="table-wrap"><table><thead><tr><th>上报 / 收到时间</th><th>设备 / 版本</th><th>问题 / 记录数</th><th>上报编号</th></tr></thead><tbody>${d.reports.map(r=>`<tr><td>${esc(date(r.created_at))}<br><small>收到 ${esc(date(r.received_at))}</small></td><td>${esc(r.device)}<br>${esc(r.snapshot.version)}</td><td>${esc(reasons[r.reason])}<br>${r.events} 条 · ${r.errors} 条失败</td><td><button data-report="${esc(r.report_id)}">查看报告</button><br><small>${esc(r.report_id)}</small></td></tr>`).join('')||'<tr><td colspan="4">暂无报告。请用户在升级后的客户端点击“上报日志”。</td></tr>'}</tbody></table></div>`;
   q('#reports-count').textContent=`共 ${d.total} 份，第 ${page} / ${Math.max(1,Math.ceil(d.total/25))} 页`;q('#reports-prev').disabled=page<=1;q('#reports-next').disabled=page*25>=d.total;
  }catch(e){if(ticket===generation)q('#reports-message').textContent=e.message;}
 }
 function renderEvents(){const term=q('#report-search').value.toLowerCase(),events=(report?.events||[]).filter(e=>JSON.stringify(e).toLowerCase().includes(term));
  q('#report-events').innerHTML=`<table><thead><tr><th>时间 / 阶段</th><th>服务商 / 模型 / 任务</th><th>结果 / 耗时 / 请求编号</th><th>Key / 适配器 / 连接</th></tr></thead><tbody>${events.map(e=>{const d=e.details||{};return `<tr><td>${esc(date(e.at))}<br>${esc(stages[d.stage]||e.operation)}<br>${esc(e.kind)}</td><td>${esc(d.provider||'—')}<br>${esc(d.model||'—')}<br>任务 ${esc(d.task_id||d.task_fingerprint||'—')}<br>${esc(e.host||'')} ${esc(d.method||'')} ${esc(d.endpoint||'')}</td><td>${e.outcome==='error'?'失败':'成功'} · ${(e.duration_ms/1000).toFixed(3)} 秒<br>HTTP ${esc(d.http_status||e.status||'无响应状态')}${d.upstream_status?'<br>错误内容中的上游状态 '+esc(d.upstream_status):''}<br>${esc(categories[e.category]||e.category)} ${esc(d.error_code||d.exception_type||'')} ${esc(d.error_hint||'')}<br>服务商请求编号 ${esc(d.provider_request_id||'—')}<br>上游请求编号 ${esc(d.upstream_request_id||'—')}<br>状态 ${esc(d.result_state||'—')}${d.frames?.length?'<details><summary>代码位置</summary>'+d.frames.map(esc).join('<br>')+'</details>':''}</td><td>Key 来源 ${esc(({managed:'后台',personal:'本机',unknown:'未记录'})[d.key_source]||'—')}<br>Key ID ${esc(d.key_id||'—')}<br>指纹 ${esc(d.key_fingerprint||'—')}<br>适配器 ${esc(d.adapter_source||'—')} ${esc(d.adapter_version||'')}<br><small>${esc(d.adapter_hash||'')}</small><br>连接 ${esc(({direct:'直连',explicit:'指定代理',system:'跟随系统代理'})[d.proxy_mode]||'—')} ${esc(d.proxy_host||'')}<br>鉴权方式 ${esc(d.auth_style||'—')}</td></tr>`;}).join('')||'<tr><td colspan="4">没有匹配记录；升级前的请求无法补录。</td></tr>'}</tbody></table>`;
 }
 async function openReport(id){const ticket=++generation;q('#reports-message').textContent='正在读取报告…';
  try{const d=await api('users/'+encodeURIComponent(account)+'/client-reports/'+encodeURIComponent(id));if(ticket!==generation||!dialog.open)return;report=d;q('#reports-message').textContent='';q('#report-detail').hidden=false;
   const s=d.snapshot;q('#report-summary').innerHTML=`<p>上报编号 <code>${esc(d.report_id)}</code> · 设备 <code>${esc(d.device)}</code><br>客户端 ${esc(s.version)} · ${esc(s.system)} · 构建 <code>${esc(s.build||'未记录')}</code><br>当前适配包 ${esc(s.adapter_version||'本地适配器')} · 最近 ${d.hours} 小时</p><p class="help">截取省略 ${s.omitted} 条；本次运行丢失 ${s.dropped} 条；本地记录${s.recording_error?'异常':'正常'}。重复成功的轮询只保留状态变化和首次查询。</p>`;q('#report-search').value='';renderEvents();
  }catch(e){if(ticket===generation)q('#reports-message').textContent=e.message;}
 }
 q('#reports-list').onclick=e=>{const b=e.target.closest('[data-report]');if(b)openReport(b.dataset.report);};
 q('#report-search').oninput=renderEvents;
 q('#report-download').onclick=()=>{if(!report)return;const url=URL.createObjectURL(new Blob([JSON.stringify(report,null,2)],{type:'application/json'})),a=document.createElement('a');a.href=url;a.download='Studio-report-'+report.report_id+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
 form.onsubmit=e=>{e.preventDefault();page=1;refresh();};q('#reports-prev').onclick=()=>{page--;refresh();};q('#reports-next').onclick=()=>{page++;refresh();};
 q('#reports-close').onclick=()=>dialog.close();dialog.onclose=()=>{generation++;report=null;q('#reports-list').replaceChildren();q('#report-events').replaceChildren();q('#report-summary').replaceChildren();};
 window.addEventListener('studio-admin-auth',()=>dialog.close());
 document.querySelector('#users').addEventListener('click',e=>{const b=e.target.closest('[data-action="client-reports"]');if(!b)return;e.stopImmediatePropagation();account=rows[Number(b.dataset.index)].username;page=1;report=null;form.reset();q('#report-detail').hidden=true;q('#reports-title').textContent='客户端日志 · '+account;dialog.showModal();refresh();},true);
})();
