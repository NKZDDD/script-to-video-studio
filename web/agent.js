'use strict';
(() => {
const $ = (s, p=document) => p.querySelector(s), $$ = (s,p=document) => [...p.querySelectorAll(s)];
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const S = {boot:null, root:'', data:null, page:'projects', tab:'providers', provider:'', detail:'', wizard:null, step:0, plan:null, treeMode:'family', filters:{}, groupKeys:{}, folders:new Map(), planRequest:0, runtime:null, job:'', request:0};
const main = $('#main');
const btn = (text, action, extra='', primary=false) => `<button type="button" class="rv-button ${primary?'rv-primary':''}" data-action="${action}" ${extra}>${text}</button>`;
const option = (value, text, selected) => `<option value="${esc(value)}" ${value===selected?'selected':''}>${esc(text)}</option>`;
const choices = (values, selected) => values.map(v=>option(v,v,selected)).join('');
const input = (name,value,type='text',extra='') => `<input name="${esc(name)}" type="${type}" value="${esc(value)}" ${extra}>`;
const field = (label,html,note='') => `<label>${label}${html}${note?`<small>${note}</small>`:''}</label>`;
const select = (name,values,value) => `<select name="${name}">${choices(values,value)}</select>`;
const heading = (eye,title,note,actions='') => `<div class="rv-heading"><div><span class="rv-eyebrow">${esc(eye)}</span><h2>${title}</h2><p>${note}</p></div><div class="rv-actions">${actions}</div></div>`;
const fileURL = rel => '/file?'+new URLSearchParams({root:S.root,rel});
const fileLink = (rel,label) => `<a href="${esc(fileURL(rel))}" target="_blank" rel="noopener">${esc(label||rel)}</a>`;
async function api(path, body) {
 const response = await fetch('/api/agent/'+path, body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
 const data = await response.json();
 if(!response.ok || data.error) throw Error(data.error||`请求失败 ${response.status}`);
 return data;
}
const get = (path,args={}) => api(path+'?'+new URLSearchParams(args));
const post = (path,data={}) => api(path,{root:S.root,...data});
function notice(text,error=false) {const e=$('#message'); e.hidden=false; e.className='rv-notice '+(error?'is-error':'');e.textContent=text;clearTimeout(notice.timer);notice.timer=setTimeout(()=>e.hidden=true,error?15000:6000);}
function dialog(title,body) {$('#dialog-title').textContent=title;$('#dialog-body').innerHTML=body;S.job='';S.editor=null;if(!$('#dialog').open)$('#dialog').showModal();}
function closeDialog() {$('#dialog').close();S.job='';S.editor=null;}
function safe(fn) {return (...a)=>Promise.resolve().then(()=>fn(...a)).catch(e=>notice(e.message,true));}
async function reloadBoot() {S.boot=await api('bootstrap');S.provider ||= S.boot.capabilities[0]?.id||'';picker();}
function picker() {$('#project-picker').innerHTML=option('','选择项目',S.root)+S.boot.projects.map(p=>option(p.root,p.title,S.root)).join('');}
async function loadProject(root=S.root,paint=true,background=false) {
 if(!root)return; const seq=++S.request;const data=await get('project',{root});
 if(seq!==S.request || root!==S.root)return;
 if(background&&($('#dialog').open||document.activeElement?.matches('input,select,textarea')||!['production','handoff','outputs'].includes(S.page)))return;
 const previous=S.data;
 S.data=data;
 if(previous?.root!==data.root||productionStamp(previous)!==productionStamp(data))invalidatePlan();
 if(paint)render();
 return previous;
}
function updateHTML(element,html) {
 if(element._html===html)return false;
 element.innerHTML=html;element._html=html;return true;
}
let observedDock=null;
function updateStickyOffsets() {
 const style=document.documentElement.style;
 style.setProperty('--studio-header-height',$('#sticky-header').offsetHeight+'px');
 style.setProperty('--studio-dock-height',($('.rv-production-dock')?.offsetHeight||0)+'px');
}
const stickyObserver=new ResizeObserver(updateStickyOffsets);
function observeProductionDock() {
 const dock=$('.rv-production-dock');
 if(dock!==observedDock){if(observedDock)stickyObserver.unobserve(observedDock);if(dock)stickyObserver.observe(dock);observedDock=dock;}
 updateStickyOffsets();
}
function backToTop() {
 const behavior=matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth';
 for(const el of $$('#resource-tree,#resource-detail,#production-plan',main))el.scrollTo({top:0,behavior});
 window.scrollTo({top:0,behavior});
}
const productionStamp = d => JSON.stringify([d?.publication,d?.tasks,d?.production]);
function invalidatePlan() {
 S.plan=null;S.planRequest++;
 if(S.page==='production'&&$('#production-plan'))renderPlan();
}
function refreshProjectView(previous) {
 if(S.page==='production') {
  renderTree();renderDetail();renderJobs();renderPlan();
  if(JSON.stringify(previous?.production)!==JSON.stringify(S.data.production))renderServices();
  renderPublication();
 } else if(S.page==='outputs') {outputList();loadMasters();}
 else if(S.page==='handoff'&&JSON.stringify(previous)!==JSON.stringify(S.data))handoff();
}
function show(page) {if(['production','outputs','handoff'].includes(page)&&!S.root){notice('先选择或创建项目');page='projects';}if(page!==S.page)invalidatePlan();S.page=page;render();}
function render() {
 const retain=main.dataset.projectRoot===S.root&&main.dataset.viewPage===S.page;
 const position=retain?{tree:$('#resource-tree')?.scrollTop||0,detail:$('#resource-detail')?.scrollTop||0,x:scrollX,y:scrollY}:null;
 $$('[data-page]').forEach(b=>b.setAttribute('aria-current',b.dataset.page===S.page?'page':'false'));
 if(!S.boot)return;
 if(S.page==='projects')projects();else if(S.page==='wizard')wizard();else if(S.page==='settings')settings();
 else if(!S.data)main.innerHTML='<p>正在读取项目…</p>';else if(S.page==='handoff')handoff();else if(S.page==='production')production();else outputs();
 main.dataset.projectRoot=S.root;main.dataset.viewPage=S.page;
 observeProductionDock();
 if(position){if($('#resource-tree'))$('#resource-tree').scrollTop=position.tree;if($('#resource-detail'))$('#resource-detail').scrollTop=position.detail;scrollTo(position.x,position.y);}
}
function projects() {
 main.innerHTML=heading('项目工作区','从一个项目，开始制作','锁定设定与本机路径，让 Agent 把材料直接交到这里。',
 (S.boot.features.batch?btn('批量创建','batch'):'')+btn('＋ 创建项目','new','',true))+
 `<div class="rv-toolbar"><label class="rv-search"><input id="project-search" aria-label="搜索项目" type="search" placeholder="搜索项目名、编号、标签或路径" value="${esc(S.filters.project||'')}"></label><select id="project-status" aria-label="项目状态">${choices(['全部状态','待 Agent 产出','可查看生产','有失败','已归档'],S.filters.status||'全部状态')}</select><select id="project-type" aria-label="项目类型">${choices(['全部项目','Agent 项目','旧项目'],S.filters.type||'全部项目')}</select><select id="project-sort" aria-label="项目排序">${choices(['最近活动','项目名称'],S.filters.sort||'最近活动')}</select></div><div class="rv-listhead"><span>项目 / 位置</span><span>材料与生产</span><span>最近活动</span><span>下一步</span></div><div id="project-list"></div><p class="rv-caption" id="project-count"></p><details class="rv-disclosure"><summary>这条新流程如何交接</summary><div class="rv-flow">${[['01','创建项目','选项、完整技能、本机路径'],['02','交给 Agent','模板包含生产契约与绝对路径'],['03','材料自动就绪','发布完成后校验与接收'],['04','勾选并生产','依赖就绪后出图、出片']].map(a=>`<div><b>${a[0]}</b><strong>${a[1]}</strong><small>${a[2]}</small></div>`).join('')}</div></details>`;
 projectList();
}
function projectList() {
 let rows=S.boot.projects.filter(p=>`${p.title} ${p.project_code} ${p.root} ${p.tags.join(' ')}`.toLowerCase().includes((S.filters.project||'').toLowerCase()));
 if(S.filters.status&&S.filters.status!=='全部状态')rows=rows.filter(p=>p.state===S.filters.status);
 else rows=rows.filter(p=>!p.archived);
 if(S.filters.type==='Agent 项目')rows=rows.filter(p=>p.workflow==='agent');if(S.filters.type==='旧项目')rows=rows.filter(p=>p.workflow!=='agent');
 rows.sort((a,b)=>S.filters.sort==='项目名称'?a.title.localeCompare(b.title,'zh'):b.updated.localeCompare(a.updated));
 $('#project-list').innerHTML=rows.map(p=>`<div class="rv-projectrow"><div><strong>${esc(p.title)}</strong><small>${esc(p.project_code)} · ${esc(p.tags.join(' · '))}</small><code>${esc(p.root)}</code></div><div><span class="rv-badge">${esc(p.state)}</span><small>${p.workflow==='agent'?'Agent 完整技能项目':'旧项目 · 保留现有产物'}</small></div><span class="rv-projecttime">${esc(p.updated)}</span><div>${btn('打开','open',`data-root="${esc(p.root)}"`)} ${btn(p.archived?'恢复':'归档','archive',`data-root="${esc(p.root)}" data-archived="${!p.archived}"`)}</div></div>`).join('')||'<div class="rv-panel">没有匹配项目。可以调整筛选，或创建第一个项目。</div>';
 $('#project-count').textContent=`显示 ${rows.length} 个项目 · 生产父目录 ${S.boot.projects_dir}`;
}
function beginWizard(batch=false) {S.wizard={...S.boot.defaults,title:'',source:'',base:S.boot.projects_dir,tags:'',batch:batch?'':''};S.step=0;show('wizard');}
function wizardSave() {const f=$('#wizard-form');if(!f)return;new FormData(f).forEach((v,k)=>S.wizard[k]=v);$$('input[type=checkbox][name]',f).forEach(e=>S.wizard[e.name]=e.checked);}
function providerSelect(media,name,value) {return `<select name="${name}">${option('','稍后配置',value)}${S.boot.capabilities.filter(c=>c.supports.includes(media)).map(c=>option(c.id,c.name,value)).join('')}</select>`;}
function wizard() {
 const w=S.wizard; if(!w)return show('projects');
 const steps=['项目与原文','创作边界','视频与语言','技能与资产','确认创建'];
 let html='';
 if(S.step===0)html=`<h3>项目放在哪里，原文从哪里来</h3><div class="rv-fields">${field('项目名称',input('title',w.title,'text','required maxlength="100"'),'编号自动生成，避免跨项目冲突。')}${field('标签',input('tags',w.tags),'用逗号分隔，用于项目搜索。')}${field('生产父目录',input('base',w.base,'text','required'),'将自动建立“名称_项目编号”子目录；确认后显示实际绝对路径。')}${field('原文类型',select('source_type',['剧本','小说','大纲','已有资产'],w.source_type))}${field('集数策略',select('episode_strategy',['按原文','指定集数'],w.episode_strategy))}${field('指定集数',input('episode_count',w.episode_count,'number','min="1" max="999"'),'仅在选择指定集数时采用。')}<label class="rv-span">原文<textarea name="source" rows="7" placeholder="粘贴原始剧本或选择文件；留空可先创建，后续补齐原文。">${esc(w.source)}</textarea></label><label>读取原文文件<input type="file" id="source-file" accept=".txt,.md,.docx,.pdf"></label>${w.batch!==undefined&&w.batch!==false&&w.batch!==''?field('批量项目名（每行一个）',`<textarea name="batch" rows="5">${esc(w.batch)}</textarea>`):''}</div>`;
 if(S.step===1)html=`<h3>画面和创作边界</h3><div class="rv-fields">${field('拍成什么形式',select('medium',['真人影视','3D 漫剧','二维动画','混合形式'],w.medium))}${field('视觉风格',input('style',w.style))}${field('文化与地域',input('culture',w.culture))}${field('剧情改编',select('adaptation',['忠于原文','保留事实，优化节奏与镜头','允许适度改编'],w.adaptation))}<label class="rv-span">额外要求<textarea name="notes" rows="5">${esc(w.notes)}</textarea></label></div>`;
 if(S.step===2)html=`<h3>视频、声音与语言</h3><div class="rv-fields">${field('图片服务商',providerSelect('image','image_provider',w.image_provider))}${field('图片模型',input('image_model',w.image_model))}${field('视频服务商',providerSelect('video','video_provider',w.video_provider))}${field('视频模型',input('video_model',w.video_model))}${field('成片比例',select('ratio',['16:9','9:16','1:1','4:3','3:4','21:9'],w.ratio))}${field('每段目标时长（秒）',input('duration',w.duration,'number','min="1" max="600"'),'目标值，生产前会按具体模型校验，不能静默截短。')}${field('声音',select('audio',['模型原生音频','无声视频','外部后期配音'],w.audio))}${field('对白语言',input('dialogue_language',w.dialogue_language))}${field('提示词语言',input('prompt_language',w.prompt_language))}<label class="rv-inline"><input name="subtitles" type="checkbox" ${w.subtitles?'checked':''}> 需要后期字幕</label></div><p class="rv-notice">这里锁定创作目标，不发生成请求。密钥在设置中填写；未知或不兼容的模型会在生产前阻止提交。</p>`;
 if(S.step===3)html=`<h3>完整技能包与资产范围</h3><div class="rv-skill"><strong>完整生产技能包</strong><span class="rv-badge rv-good">创建时固定文件与校验和</span></div><p>身份 → 造型 / 连续状态；场景 → 场景状态；按需交接板 → A / B / C 独立区域图 → 分段视频。共享资产在所有集里复用。</p><div class="rv-fields">${field('服装规划',select('costume',['按剧情需要','独立服装资产','直接完整造型'],w.costume))}${field('交接策略',select('handoff',['按边界需要','逐边界人工复核'],w.handoff))}${[['reuse','规划时复用匹配的共享资产'],['atlas','增加资产图册'],['offline_board','增加离线画面草图'],['emotion_curve','增加情绪曲线'],['report','增加审计报告']].map(([k,l])=>`<label class="rv-inline"><input type="checkbox" name="${k}" ${w[k]?'checked':''}> ${l}</label>`).join('')}</div><p class="rv-caption">核心人物、场景、道具等按剧情需要规划；声音身份作为元数据。完整规则、脚本和许可一起复制进项目，不只复制入口文件。</p>`;
 if(S.step===4)html=`<h3>确认项目契约</h3><dl><dt>项目</dt><dd>${esc(w.title)}</dd><dt>生产父目录</dt><dd><code>${esc(w.base)}</code></dd><dt>原文</dt><dd>${w.source.trim()?`${w.source.length} 字`:'尚未提供，创建后须补齐'}</dd><dt>创作</dt><dd>${esc(w.medium)} · ${esc(w.style)} · ${esc(w.adaptation)}</dd><dt>视频</dt><dd>${esc(w.ratio)} · ${esc(w.duration)} 秒 · ${esc(w.audio)}</dd><dt>语言</dt><dd>对白 ${esc(w.dialogue_language)} / 提示词 ${esc(w.prompt_language)}</dd></dl><div class="rv-notice">创建后生成 AGENT_TASK.md、本机生产契约、结构定义、选项锁和完整技能快照。将模板交给 Agent；Studio 自动接收新发布，生产由您启动。</div><details><summary>查看全部选项</summary><pre>${esc(JSON.stringify(wizardOptions(),null,2))}</pre></details>`;
 main.innerHTML=heading('新项目','创建生产项目','默认值可逐步修改，最后确认后写入契约。',btn('返回项目','back-projects'))+`<div class="rv-steps">${steps.map((s,i)=>`<button type="button" class="${i===S.step?'active':''}" data-action="wizard-step" data-step="${i}">${i+1} · ${s}</button>`).join('')}</div><form id="wizard-form" class="rv-formpage">${html}<div class="rv-row wizard-footer">${btn('上一步','wizard-prev',S.step?'':'disabled')}${btn(S.step===4?'创建项目与 Agent 模板':'下一步',S.step===4?'wizard-create':'wizard-next','',true)}</div></form>`;
}
function wizardOptions() {return Object.fromEntries(Object.keys(S.boot.defaults).map(k=>[k,S.wizard[k]]));}
async function wizardCreate() {
 wizardSave();const w=S.wizard;if(!w.title.trim()||!w.base.trim())throw Error('填写项目名称和生产父目录');
 const names=w.batch?.trim()?w.batch.split('\n').map(x=>x.trim()).filter(Boolean):[w.title];
 const out=[];for(const title of names)out.push(await api('create',{title,base:w.base,source:w.source,options:wizardOptions(),tags:w.tags.split(/[,，]/).map(x=>x.trim()).filter(Boolean)}));
 S.root=out.at(-1).root;await reloadBoot();await loadProject(S.root,false);S.wizard=null;show('handoff');notice(`已创建 ${out.length} 个项目，契约已保存到本机目录。`);
}
function handoff() {
 const d=S.data,p=d.publication;
 main.innerHTML=heading(d.meta.title,'Agent 项目交接','材料直接写进本机项目，Studio 每隔数秒检查完整发布。',btn('刷新发布','refresh')+btn('进入生产','production','',true))+
 `<div class="rv-handoff-grid"><div class="rv-panel"><h3>交接内容已经明确</h3><dl><dt>项目编号</dt><dd>${esc(d.meta.project_code)}</dd><dt>生产路径</dt><dd><code>${esc(d.root)}</code></dd><dt>当前发布</dt><dd>${esc(p.revision||'等待 Agent')}</dd><dt>结构状态</dt><dd>${p.revision?'已接收，视觉验收仍需检查':'等待完整材料'}</dd></dl>${p.errors.map(e=>`<p class="rv-notice is-error">${esc(e.revision)}：${esc(e.message)}</p>`).join('')}${btn('补充 / 更新原文','source')}${d.template?fileLink('00_项目说明/production-contract.md','查看生产契约'):''}<p class="rv-caption">${p.legacy?'旧项目可继续查看和生产现有任务，不调用内部文字分析。':'Agent 先读取完整技能与锁定选项，最后写 READY.json；新版本不会修改已排队批次。'}</p></div><div class="rv-panel"><div class="rv-row"><h3>AGENT_TASK.md</h3>${d.template?btn('复制完整模板','copy-template','',true):''}</div><textarea id="agent-template" readonly rows="16">${esc(d.template||'这是旧项目。原来的任务和成品已保留；可在生产页查看、勾选和继续生成。')}</textarea>${d.template?fileLink('00_项目说明/AGENT_TASK.md','打开 / 下载模板'):''}</div></div>`;
}
function production() {
 const d=S.data;const cats={...S.boot.kinds};if(d.tasks.some(t=>t.kind==='storyboard'))cats.storyboard='旧项目故事板';
 main.innerHTML=`<section class="rv-production-dock" aria-label="生产操作与计划"><div class="rv-panel rv-production-actions"><div><strong id="plan-summary"></strong><small id="production-version"></small></div><div class="rv-actions">${btn('查看生产计划','preview')}${btn('开始生产','start','disabled',true)}</div><small>勾选后先查看生产计划，确认后开始；筛选不清除隐藏勾选。</small></div><div id="production-plan" tabindex="0" aria-label="生产计划详情"></div></section>`+
 heading(d.meta.title,'选择这次要生产什么','资产和分集任务在同一棵树中管理，有效成品自动复用。',btn('Agent 契约','handoff')+btn('刷新','refresh'))+
 `<div id="publication-errors"></div>
 <div class="rv-panel"><div class="rv-row"><h3>本次服务商与并发</h3>${btn('填写 / 管理密钥','providers')}</div><div class="rv-service-head"><span>生产类型</span><span>服务商</span><span>模型</span><span>图片清晰度</span><span>并发</span></div><div id="production-services"></div><p class="rv-caption">各行共享全局总上限与服务商上限；批内并发不能相加当作可用额度。设置变更只影响下次开始的批次。</p></div>
 <div id="jobs"></div>
 <div class="rv-toolbar"><select id="tree-mode" aria-label="树形分组">${[['family','按资源分类'],['episode','按集查看'],['disk','按磁盘目录']].map(([v,n])=>option(v,n,S.treeMode)).join('')}</select><label class="rv-search"><input id="asset-search" type="search" aria-label="搜索资源" placeholder="搜索人物、场景、任务 ID、文件名" value="${esc(S.filters.asset||'')}"></label><select id="asset-status" aria-label="资源状态">${choices(['全部状态','待生产','已完成','失败','选择有阻塞'],S.filters.assetStatus||'全部状态')}</select><select id="asset-type" aria-label="资源类型">${option('','全部类型',S.filters.assetType||'')}${Object.entries(cats).map(([k,v])=>option(k,v,S.filters.assetType)).join('')}</select></div>
 <div class="rv-row rv-selection"><span id="selection-count"></span><div class="rv-actions">${btn('选中当前结果','select-visible')}${btn('取消当前结果','clear-visible')}${btn('全项目全选','select-all')}${btn('补选依赖','dependencies')}</div></div><div id="dependency-alert"></div>
 <div class="rv-production-grid"><div class="rv-panel rv-tree-panel"><div id="resource-tree" role="region" aria-label="生产资源列表" tabindex="0"></div></div><aside class="rv-panel rv-detail" id="resource-detail" aria-label="资源详情" tabindex="0"></aside></div>`;
 renderServices();renderPublication();renderTree();renderDetail();renderJobs();renderPlan();
}
function renderServices() {
 const cats={...S.boot.kinds};if(S.data.tasks.some(t=>t.kind==='storyboard'))cats.storyboard='旧项目故事板';
 updateHTML($('#production-services'),Object.entries(cats).map(([k,n])=>serviceRow(k,n)).join(''));
}
function renderPublication() {
 $('#production-version').textContent='材料版本 '+(S.data.publication.revision||'等待 Agent 发布');
 updateHTML($('#publication-errors'),S.data.publication.errors.map(e=>`<p class="rv-notice is-error">新发布 ${esc(e.revision)} 未接收：${esc(e.message)}。当前已接收版本保持不变。</p>`).join(''));
}
function renderJobs() {
 updateHTML($('#jobs'),S.data.jobs.slice(0,8).map(j=>`<div class="rv-notice rv-row"><div><strong>${esc(j.kind==='production'?'生产批次':j.kind)} · ${esc(j.status)}</strong> ${j.finished}/${j.total} · 已用 ${j.elapsed} 秒</div><div>${btn('任务明细','job',`data-id="${esc(j.id)}"`)}${!['done','error','cancelled','aborted','interrupted'].includes(j.status)?btn('停止','cancel',`data-id="${esc(j.id)}"`):''}</div></div>`).join(''));
}
function serviceRow(kind,label) {
 const row=S.data.production[kind],media=kind==='video'?'video':'image';if(!row)return '';
 const cs=S.boot.capabilities.filter(c=>c.supports.includes(media)),cap=cs.find(c=>c.id===row.provider)||{};
 const models=cap[media]?.models||[];
 const resolutions=(cap[media]?.model_options?.[row.model]?.resolutions||cap[media]?.resolutions||[]).filter(v=>['1K','2K','4K'].includes(v));
 const clarity=media==='image'?`<label>清晰度<select aria-label="${label}清晰度" data-setting="image_resolution">${option('','跟随任务',row.image_resolution||'')}${resolutions.map(v=>option(v,v,row.image_resolution)).join('')}</select></label>`:'<span></span>';
 return `<div class="rv-service-row" data-kind="${kind}"><strong>${label}</strong><select aria-label="${label}服务商" data-setting="provider">${cs.map(c=>option(c.id,c.name,row.provider)).join('')}</select><input aria-label="${label}模型" data-setting="model" list="models-${kind}" value="${esc(row.model)}"><datalist id="models-${kind}">${models.map(m=>option(m,m,'')).join('')}</datalist>${clarity}<input aria-label="${label}并发" type="number" min="1" max="512" data-setting="concurrency" value="${row.concurrency}">${S.boot.features.advanced?`<div class="row-advanced"><span>参数覆盖（留空沿用任务）</span>${(media==='video'?['ratio','duration','resolution']:['size']).map(k=>`<label>${esc({ratio:'画幅',duration:'秒数',resolution:'清晰度',size:'尺寸'}[k])}<input data-override="${k}" aria-label="${label}${k}覆盖" value="${esc(row.override?.[k]||'')}"></label>`).join('')}</div>`:''}</div>`;
}
function matchedResources() {
 const tasks=new Map(S.data.tasks.map(t=>[t.key,t])),bad=new Set(S.data.failures.map(x=>x.target));
 return S.data.resources.filter(r=>{const t=tasks.get(r.task_key),q=(S.filters.asset||'').toLowerCase();
 if(!`${r.name} ${r.id} ${t?.key||''} ${t?.output||''}`.toLowerCase().includes(q))return false;
 if(S.filters.assetType&&t?.kind!==S.filters.assetType)return false;
 return !S.filters.assetStatus||S.filters.assetStatus==='全部状态'||(S.filters.assetStatus==='已完成'&&t?.done)||(S.filters.assetStatus==='待生产'&&t&&!t.done)||(S.filters.assetStatus==='失败'&&bad.has(t?.key))||(S.filters.assetStatus==='选择有阻塞'&&t?.missing_selection.length);
 });
}
function renderTree() {
 const rs=matchedResources(),tasks=new Map(S.data.tasks.map(t=>[t.key,t])),all=new Map(S.data.resources.map(r=>[r.id,r]));
 S.visibleKeys=rs.map(r=>r.task_key).filter(Boolean);S.groupKeys={};let gi=0;
 const leaf = r => {const t=tasks.get(r.task_key);return `<div class="rv-leaf ${S.detail===r.id?'active':''}">${t?`<input type="checkbox" data-key="${esc(t.key)}" aria-label="选择 ${esc(r.name)}" ${t.selected?'checked':''}>`:'<span>♪</span>'}<button class="rv-leaf-name" data-action="detail" data-id="${esc(r.id)}"><strong>${esc(r.name)}</strong><small>${esc(r.id)}</small></button><span class="rv-badge ${t?.done?'rv-good':''}">${!t?'元数据':t.done?'已完成':t.missing_selection.length?'待依赖':'待生产'}</span></div>`;};
 const folder = (name,key,children,resources) => {const id='g'+gi++;const keys=[...new Set(resources.map(r=>r.task_key).filter(Boolean))];S.groupKeys[id]=keys;const selected=keys.filter(k=>tasks.get(k)?.selected).length,state=JSON.stringify([S.root,S.treeMode,S.filters.asset||'',key]),open=S.folders.get(state)??!!S.filters.asset;return `<details class="rv-folder" data-folder="${esc(key)}" ${open?'open':''} data-folder-state="${esc(state)}"><summary data-folder-toggle><input type="checkbox" data-group="${id}" aria-label="选择 ${esc(name)} 当前结果" ${keys.length&&selected===keys.length?'checked':''} ${!keys.length?'disabled':''} data-partial="${selected>0&&selected<keys.length}"><strong>${esc(name)}</strong><small>${selected}/${keys.length}</small></summary><div class="rv-folderbody">${children}</div></details>`;};
 let html='';
 if(S.treeMode==='family') {
 const children=new Map();S.data.resources.forEach(r=>{const parent=all.has(r.parent_id)?r.parent_id:null;if(!children.has(parent))children.set(parent,[]);children.get(parent).push(r);});
 const visible=new Set(rs.map(r=>r.id));rs.forEach(r=>{let p=all.get(r.parent_id),seen=new Set();while(p&&!seen.has(p.id)){seen.add(p.id);visible.add(p.id);p=all.get(p.parent_id);}});
 function descendants(r) {return [r,...(children.get(r.id)||[]).flatMap(descendants)].filter(x=>rs.includes(x));}
 function node(r) {const cs=(children.get(r.id)||[]).filter(x=>visible.has(x.id));return cs.length?folder(r.name,'entity:'+r.id,(rs.includes(r)?leaf(r):'')+cs.map(node).join(''),descendants(r)):leaf(r);}
 const roots=(children.get(null)||[]).filter(r=>visible.has(r.id));const groups=new Map();roots.forEach(r=>{const type=r.semantic;if(!groups.has(type))groups.set(type,[]);groups.get(type).push(r);});
 html=[...groups].map(([k,v])=>folder(S.boot.semantics[k]||k,'family:'+k,v.map(node).join(''),v.flatMap(descendants))).join('');
 } else {
 const groups=new Map();rs.forEach(r=>{const t=tasks.get(r.task_key),labels=S.treeMode==='episode'?(r.episodes.length?r.episodes:['全剧共享']):[(t?.output||'声音元数据').split('/').slice(0,-1).join('/')||'声音元数据'];labels.forEach(l=>{if(!groups.has(l))groups.set(l,[]);groups.get(l).push(r);});});
 html=[...groups].sort(([a],[b])=>a.localeCompare(b)).map(([k,v])=>folder(k,S.treeMode+':'+k,v.map(leaf).join(''),v)).join('');
 }
 const tree=$('#resource-tree'),scroll=tree.scrollTop;
 if(updateHTML(tree,html||'<p class="rv-empty">没有匹配资源。等待 Agent 发布材料，或调整筛选。</p>'))tree.scrollTop=scroll;
 $$('[data-partial="true"]',main).forEach(e=>e.indeterminate=true);

 const selected=S.data.tasks.filter(t=>t.selected),missing=selected.filter(t=>t.missing_selection.length&&!t.done);
 $('#selection-count').textContent=`全项目选中 ${selected.length}/${S.data.tasks.length} 项 · 当前结果 ${S.visibleKeys.length} 项`;
 $('#plan-summary').textContent=`${selected.filter(t=>!t.done).length} 项待生产 · ${selected.filter(t=>t.done).length} 项可复用`;
 $('#dependency-alert').innerHTML=missing.length?`<div class="rv-notice is-error">${missing.length} 项缺少已勾选的上游，生产前会阻止提交。点“补选依赖”可以补齐。</div>`:'';
}
function renderDetail() {
 const r=S.data.resources.find(r=>r.id===S.detail);if(!r){updateHTML($('#resource-detail'),'<h3>资源详情</h3><p>点选树中的资源，查看来源、参考图、提示词和输出位置。</p>');return;}
 const t=S.data.tasks.find(t=>t.key===r.task_key);
 const detail=$('#resource-detail'),scroll=detail.scrollTop;
 if(updateHTML(detail,`<h3>${esc(r.name)}</h3><p class="rv-caption">${esc(r.id)}</p><dl><dt>分类</dt><dd>${esc(S.boot.semantics[r.semantic]||r.semantic)}</dd><dt>使用范围</dt><dd>${esc(r.episodes.join('、')||'全剧共享')}</dd></dl>${t?`<dl><dt>输出</dt><dd><code>${esc(t.output)}</code></dd><dt>参数</dt><dd>${esc(JSON.stringify(t.params))}</dd><dt>依赖</dt><dd>${esc(t.dependencies.join('、')||'无')}</dd></dl>${t.done?(t.kind==='video'?`<video controls preload="metadata" src="${esc(fileURL(t.output))}"></video>`:`<img loading="lazy" alt="${esc(r.name)}" src="${esc(fileURL(t.output))}">`)+fileLink(t.output,'查看原文件'):''}<div class="rv-actions">${btn('查看 / 编辑提示词','prompt',`data-key="${esc(t.key)}"`)}${btn('交给 Agent 修订','revision',`data-key="${esc(t.key)}"`)}</div><h4>参考图（上传顺序）</h4>${[...(t.handoff_refs||t.storyboard_refs||[]),...(t.reference_images||[])].map(x=>`<p>${esc(x.image_n||x.order||'')} · ${esc(x.asset_id||x.sheet_id||'')}<small>${esc(x.file_ref||x.url||'')}</small></p>`).join('')||'<p>无参考图</p>'}${t.kind!=='video'?`<label>手动放图<input type="file" id="manual-file" accept="image/png,image/jpeg,image/webp" data-key="${esc(t.key)}" data-kind="${esc(t.kind)}"></label><small>空位置可补图；已有产物的修改请发起新版本修订。</small>`:''}${S.data.failures.filter(f=>f.target===t.key).map(f=>`<p class="rv-notice is-error">${esc(f.raw||f.title)}</p>`).join('')}`:'<p>声音身份与连续性元数据，无独立收费任务。</p>'}`))detail.scrollTop=scroll;
}
let selectionQueue=Promise.resolve();
async function setSelection(keys,value) {
 const root=S.root,changes=Object.fromEntries(keys.map(k=>[k,value]));
 invalidatePlan();
 selectionQueue=selectionQueue.catch(()=>{}).then(()=>api('selections',{root,changes}));
 await selectionQueue;
 if(S.root===root)await loadProject();
}
async function editPrompt(key) {
 dialog('编辑任务提示词','<p>正在读取当前版本…</p>');
 const editor={root:S.root,key};S.editor=editor;
 try {
  const data=await get('prompt-edit',editor);
  if(S.editor!==editor||S.root!==editor.root||!$('#dialog').open)return;
  Object.assign(editor,data);
  $('#dialog-body').innerHTML=`<p><strong>${esc(key)}</strong> · 当前版本 ${esc(data.revision)}</p><label>任务提示词<textarea id="prompt-edit" rows="16" spellcheck="false">${esc(data.text)}</textarea></label><div id="prompt-edit-error" class="rv-notice is-error" role="alert" hidden></div><p class="rv-notice">保存后，本任务${data.affected.length>1?`及依赖它的 ${data.affected.length-1} 项下游`:''}使用新产物版本。旧产物保留，正在运行的批次不受影响；保存不会自动开始生产。</p><details><summary>查看受影响的 ${data.affected.length} 项任务</summary>${data.affected.map(t=>`<p>${esc(S.boot.kinds[t.kind]||t.kind)} · ${esc(t.key)}</p>`).join('')}</details><div class="rv-actions">${btn('保存新版本','save-prompt','',true)}</div>`;
  $('#prompt-edit').focus();
 } catch(e) {if(S.editor===editor)$('#dialog-body').textContent=e.message;}
}
async function savePrompt() {
 const editor=S.editor;if(!editor)return;
 const error=$('#prompt-edit-error');error.hidden=true;
 const text=$('#prompt-edit').value;
 if(!text.trim()){error.textContent='提示词不能为空';error.hidden=false;return;}
 try {
  const result=await api('prompt-edit',{root:editor.root,key:editor.key,version:editor.version,text});
  if(S.editor!==editor)return;
  invalidatePlan();closeDialog();
  if(S.root===editor.root)await loadProject(editor.root);
  notice(result.changed?`已保存 ${result.revision}，${result.affected.length} 项任务使用新版本；下次生产生效。`:'提示词没有变化，无需新建版本。');
 } catch(e) {if(S.editor===editor){error.textContent=e.message;error.hidden=false;}}
}
async function preview() {
 const root=S.root,request=++S.planRequest;await selectionQueue;
 if(root!==S.root||request!==S.planRequest)return;
 const data=await api('preview',{root});if(root!==S.root||S.page!=='production'||request!==S.planRequest)return;S.plan=data;
 renderPlan();
}
function renderPlan() {
 const data=S.plan,start=$('[data-action="start"]',main);
 if(!start)return;
 start.disabled=!data||!!data.blocked.length||!data.todo;
 start.textContent=data&&!data.todo?'全部复用，无需生成':'开始生产';
 start.title=!data?'请先查看生产计划':data.blocked.length?'请先处理生产计划中的阻塞':'';
 updateHTML($('#production-plan'),data?`<div class="rv-panel rv-plan"><h3>生产计划</h3><p>已选 ${data.selected} · 复用 ${data.reuse} · 新生成 ${data.todo} · 材料 ${esc(data.revision||'未发布')}</p>${data.rows.map(r=>`<p>${esc(S.boot.kinds[r.kind]||r.kind)}：${esc(r.provider)} / ${esc(r.model)} · ${r.count} 项 · 批内 ${r.concurrency}，服务商 ${r.provider_limit??'未设'}，全局 ${r.global_limit}</p>`).join('')}${data.blocked.map(b=>`<p class="rv-notice is-error"><strong>${esc(b.key)}</strong>：${esc(b.reason)}</p>`).join('')}<p class="rv-caption">开始后保存本批任务、参数和提示词快照。未生成的必要上游会先生产；上游失败时下游不会提交。</p></div>`:'');
}
function outputs() {
 main.innerHTML=heading(S.data.meta.title,'产物与验收','查看实际文件；文件校验通过后仍需检查画面、连续性与声音。',btn('回到生产树','production'))+
 `<div class="rv-toolbar"><input id="output-search" type="search" aria-label="搜索产物" placeholder="搜索文件名或任务编号" value="${esc(S.filters.output||'')}"><select id="output-kind" aria-label="产物类型">${choices(['全部产物','图片','视频'],S.filters.outputKind||'全部产物')}</select>${S.boot.features.post?btn('逐集合成成片','assemble')+btn('字幕识别与烧录','subtitle'):''}${S.boot.features.legacy_import&&S.data.publication.legacy?btn('旧材料导入','import'):''}</div><div class="output-grid" id="output-list"></div><div id="master-list"></div>`;
 outputList();loadMasters();
}
async function loadMasters() {
 const root=S.root,target=$('#master-list');
 try {const d=await get('files',{root,sub:'06_成片'});if(root!==S.root||target!==$('#master-list'))return;
 updateHTML(target,'<h3>成片目录</h3>'+d.items.filter(x=>!x.dir).map(x=>`<p>${fileLink('06_成片/'+x.name,x.name)}</p>`).join(''));
 }catch(e){if(root===S.root&&target===$('#master-list'))notice(e.message,true);}
}
function outputList() {
 const q=(S.filters.output||'').toLowerCase(),type=S.filters.outputKind;
 const rows=S.data.tasks.filter(t=>t.done&&`${t.key} ${t.output}`.toLowerCase().includes(q)&&(!type||type==='全部产物'||(type==='视频')===(t.kind==='video'))),list=$('#output-list');
 const existing=new Map([...list.children].map(e=>[e.dataset.output,e])),wanted=new Set(rows.map(t=>t.output));
 for(const child of [...list.children])if(!wanted.has(child.dataset.output))child.remove();
 rows.forEach((t,i)=>{
  let item=existing.get(t.output);
  if(!item){item=document.createElement('article');item.className='rv-panel';item.dataset.output=t.output;
   item.innerHTML=`${t.kind==='video'?`<video controls preload="none" src="${esc(fileURL(t.output))}"></video>`:`<a href="${esc(fileURL(t.output))}" target="_blank" rel="noopener"><img loading="lazy" src="${esc(fileURL(t.output))}" alt="${esc(t.key)}"></a>`}<strong></strong><small></small>${fileLink(t.output,'查看原文件')}`;
  }
  $('strong',item).textContent=t.key;$('small',item).textContent=t.output;
  if(list.children[i]!==item)list.insertBefore(item,list.children[i]||null);
 });
 if(!rows.length&&!list.children.length)list.innerHTML='<p>尚无匹配的有效产物。生成成功后会自动出现。</p>';
}
function settings() {
 main.innerHTML=heading('工作台设置','生产必需配置，始终可见','所有已接入服务商继续可用。设置中只保留图片、视频与本机生产能力。')+
 `<div class="rv-toolbar">${[['providers','服务商与密钥'],['limits','并发上限'],['upload','参考图上传'],['features','功能包与路径']].map(([v,l])=>btn(l,'settings-tab',`data-tab="${v}"`,v===S.tab)).join('')}</div><div id="settings-content"></div>`;
 if(S.tab==='providers')providerForm();else if(S.tab==='limits')limitForm();else if(S.tab==='upload')uploadForm();else featureForm();
}
function providerForm() {
 const cap=S.boot.capabilities.find(c=>c.id===S.provider)||S.boot.capabilities[0];if(!cap){$('#settings-content').textContent='没有可用服务商';return;}
 S.provider=cap.id;const saved=S.boot.providers_public[cap.id]||{},status=S.boot.provider_keys_configured[cap.id]||{};
 let keys=cap.key_fields||[];if(cap.id==='chaomo')keys=[['image_1k_api_key','image_1k','图片 1K Key'],['image_4k_api_key','image_4k','图片 4K Key'],['video_api_key','video','视频 Key']];
 if(!keys.length)keys=[['api_key','api_key','API Key / 多账号凭据']];
 const keyField=(name,label,isSet,help='')=>field(`${esc(label)} · ${isSet?'已配置':'未配置'}`,`<div class="secret-field"><input type="password" name="${esc(name)}" autocomplete="new-password" placeholder="${isSet?'留空保持已存密钥':'填写密钥'}"><button type="button" class="rv-link" data-action="reveal">显示</button></div>`,esc(help));
 let credential=keys.map(([k,slot,label,help])=>keyField(k,label,status[slot]||status[k]||(k==='api_key'&&(status.image||status.video)),help)).join('');
 const af=cap.account_form||{},ac=S.boot.provider_accounts[cap.id]||{shared:{},accounts:[]};
 if(af.per?.length){
 const fields=(rows,values,prefix)=>rows.map(([k,l,secret,note])=>secret?keyField(prefix+'.'+k,l,values[k+'__set'],note):field(esc(l),input(prefix+'.'+k,values[k]||''),esc(note))).join('');
 credential=`<h4 class="rv-span">${esc(af.shared_label||'共用设置')}</h4>${fields(af.shared||[],ac.shared||{},'shared')}<div class="rv-span" id="accounts">${(ac.accounts.length?ac.accounts:[{}]).map((r,i)=>`<fieldset data-account="${i}"><legend>账号 ${i+1}</legend><div class="rv-fields">${fields(af.per,r,'account.'+i)}</div></fieldset>`).join('')}</div>${btn('增加账号','add-account')}`;
 }
 $('#settings-content').innerHTML=`<div class="rv-panel"><label>服务商<select id="provider-picker">${S.boot.capabilities.map(c=>option(c.id,c.name,cap.id)).join('')}</select></label><form id="provider-form"><div class="rv-fields">${field('Base URL',input('base_url',saved.base_url||cap.default_base_url||'','url'))}${field('等待结果上限（秒）',input('poll_timeout',saved.poll_timeout||2400,'number','min="60" max="7200"'))}${field('轮询间隔（秒）',input('poll_interval',saved.poll_interval||5,'number','min="2" max="60"'))}${credential}<label class="rv-span">已实测的模型时长档位<textarea name="durations" rows="2" placeholder="模型ID=4,5,10,15,30；只填写已经确认支持的档位">${esc(saved.durations||'')}</textarea></label></div><p class="rv-caption">密钥留空不改；保存状态不等于服务商鉴权成功。多账号可按客服提供的完整凭据格式填写。已有密钥不会回显给浏览器。</p><div class="rv-actions">${btn('保存服务商设置','save-provider','',true)}${btn('检查连接 / 刷新模型','refresh-models')}${btn('重新扫描服务商','reload-providers')}</div></form><details class="rv-disclosure"><summary>当前模型能力与来源</summary><p>${esc(cap.model_catalog?.source||'服务商适配器声明')}</p>${['image','video'].filter(m=>cap[m]).map(m=>`<h4>${m==='image'?'图片':'视频'}</h4><p>${esc((cap[m].models||[]).join(' · '))}</p>`).join('')}</details></div>`;
}
function limitForm() {$('#settings-content').innerHTML=`<form id="limits-form" class="rv-panel"><h3>所有项目共享的并发额度</h3><div class="rv-fields">${field('全局在途总上限',input('global',S.boot.limits.global,'number','min="1" max="512" required'))}${S.boot.capabilities.map(c=>field(esc(c.name),input('provider.'+c.id,S.boot.limits.per_provider[c.id]||4,'number','min="1" max="512" required'))).join('')}</div><p class="rv-caption">运行中降低上限不会撤回在途任务。新任务等额度释放后再提交；按账号串行的服务商还受账号数约束。</p>${btn('保存并发上限','save-limits','',true)}</form>`;}
function uploadForm() {const u=S.boot.upload;$('#settings-content').innerHTML=`<form id="upload-form" class="rv-panel"><h3>参考图对象存储</h3><p>仅收公网图片地址的服务商使用这里的配置；沿用原程序的上传缓存与图片检查。</p><div class="rv-fields">${[['endpoint','S3 Endpoint'],['bucket','Bucket'],['public_base_url','公开访问域名'],['region','Region'],['prefix','对象路径前缀']].map(([k,l])=>field(l,input(k,u[k]||''))).join('')}${field('Access Key · '+(u.access_key_set?'已配置':'未配置'),input('access_key','','password','autocomplete="new-password" placeholder="留空不改"'))}${field('Secret Key · '+(u.secret_key_set?'已配置':'未配置'),input('secret_key','','password','autocomplete="new-password" placeholder="留空不改"'))}</div>${btn('保存上传设置','save-upload','',true)}</form>`;}
function featureForm() {$('#settings-content').innerHTML=`<div class="rv-panel"><h3>功能包</h3><p>密钥、基础并发、当前线程、建议并发、开始 / 停止、核心报错和产物查看固定保留。</p>${Object.entries(S.boot.feature_labels).map(([k,l])=>`<div class="rv-feature"><strong>${esc(l)}</strong><label><input type="checkbox" data-feature="${k}" ${S.boot.features[k]?'checked':''}> 显示页面入口</label></div>`).join('')}<h3>数据与路径</h3><dl>${Object.entries(S.boot.paths).filter(([k,v])=>typeof v==='string'&&/dir|path/.test(k)).map(([k,v])=>`<dt>${esc(k)}</dt><dd><code>${esc(v)}</code></dd>`).join('')}</dl><p class="rv-caption">新项目可在向导指定生产父目录。更新程序时保留配置和项目目录；功能包只控制入口，不删除已有项目文件。</p></div>`;}
async function saveProvider() {const f=$('#provider-form');if(!f.reportValidity())return;const data=Object.fromEntries(new FormData(f)),cap=S.boot.capabilities.find(c=>c.id===S.provider);if(cap.account_form?.per?.length){data.account_form={shared:{},accounts:[]};Object.keys(data).forEach(k=>{if(k.startsWith('shared.')){data.account_form.shared[k.slice(7)]=data[k];delete data[k];}else if(k.startsWith('account.')){const [,i,field]=k.split('.');data.account_form.accounts[i]||={};data.account_form.accounts[i][field]=data[k];delete data[k];}});}await api('settings',{providers:{[S.provider]:data}});f.reset();await reloadBoot();settings();notice('已保存服务商设置，密钥未回显。');}
async function refreshRuntime() {S.runtime=await api('runtime');const d=S.runtime,u=d.usage,g=d.gates;$('#live-inflight').textContent=`${g.global_inflight} / ${g.global_limit}`;$('#live-jobs').textContent=d.active_jobs+' 批';$('#live-threads').textContent=u.threads??'未知';$('#live-advice').textContent=d.advice.limit==null?'待采样':'≤ '+d.advice.limit;const gb=v=>v==null?'未知':(v/1073741824).toFixed(1)+' GB';$('#live-usage').textContent=`CPU ${u.cpu_percent??'未知'}% · 系统内存 ${gb(u.mem_used)} / ${gb(u.mem_total)} · 本程序 ${u.proc_rss==null?'未知':(u.proc_rss/1048576).toFixed(0)+' MB'}`;$('#live-basis').textContent=d.advice.basis.join('；');}
async function jobDetail(id) {const root=S.root;const d=await get('job',{root,id});if(root!==S.root)return;const table=`<table><thead><tr><th>任务</th><th>状态</th><th>说明</th></tr></thead><tbody>${Object.entries(d.items).map(([k,v])=>`<tr><td>${esc(k)}</td><td>${esc(v.state)}</td><td>${esc(v.msg)}</td></tr>`).join('')}</tbody></table>${S.boot.features.logs?`<pre class="job-log">${esc(d.logs.join('\n'))}</pre>`:''}`;if(S.job===id){$('#dialog-body').innerHTML=table;}else{dialog('任务明细 · '+d.status,table);S.job=id;}}
async function readFile(file) {if(file.size>40*1024*1024)throw Error('文件超过 40MB');const data=await new Promise((resolve,reject)=>{const r=new FileReader();r.onload=()=>resolve(r.result);r.onerror=()=>reject(Error('读取文件失败'));r.readAsDataURL(file);});return data.split(',')[1];}
const actions={
 'back-top':backToTop,
 'new':()=>beginWizard(), 'batch':()=>{beginWizard();S.wizard.batch='项目一\n项目二';wizard();},
 'back-projects':()=>show('projects'), 'production':()=>show('production'),'handoff':()=>show('handoff'),
 'open':async e=>{S.root=e.dataset.root;S.data=null;S.detail='';picker();await loadProject(S.root,false);show(S.data.tasks.length?'production':'handoff');},
 'archive':async e=>{await api('archive',{root:e.dataset.root,archived:e.dataset.archived==='true'});await reloadBoot();projects();},
 'wizard-step':e=>{wizardSave();S.step=+e.dataset.step;wizard();},
 'wizard-prev':()=>{wizardSave();S.step=Math.max(0,S.step-1);wizard();},
 'wizard-next':()=>{if(!$('#wizard-form').reportValidity())return;wizardSave();S.step++;wizard();},
 'wizard-create':wizardCreate, 'refresh':()=>loadProject(),
 'copy-template':async()=>{await navigator.clipboard.writeText(S.data.template);notice('已复制完整 Agent 模板。');},
 'source':async()=>{const root=S.root;const d=await get('prompt',{root,rel:'01_剧本与分段/原始素材.txt'});if(root!==S.root)return;dialog('项目原文',`<textarea id="source-edit" rows="16">${esc(d.text)}</textarea>${btn('保存原文','save-source','',true)}`);},
 'save-source':async()=>{await post('source',{source:$('#source-edit').value});closeDialog();notice('已保存原文，Agent 可直接读取。');},
 'providers':()=>{S.tab='providers';show('settings');},
 'settings-tab':e=>{S.tab=e.dataset.tab;settings();},
 'reveal':e=>{const i=e.parentElement.querySelector('input');i.type=i.type==='password'?'text':'password';e.textContent=i.type==='password'?'显示':'隐藏';},
 'add-account':()=>{const cap=S.boot.capabilities.find(c=>c.id===S.provider),i=$$('[data-account]').length,fs=document.createElement('fieldset');fs.dataset.account=i;fs.innerHTML=`<legend>账号 ${i+1}</legend><div class="rv-fields">${cap.account_form.per.map(([k,l,secret,note])=>field(esc(l),input('account.'+i+'.'+k,'',secret?'password':'text'),esc(note))).join('')}</div>`;$('#accounts').append(fs);},
 'save-provider':saveProvider,
 'refresh-models':async()=>{const d=await api('models/refresh',{provider:S.provider});await reloadBoot();settings();notice(d.rows?.map(r=>`${r.provider}：${r.ok?'已刷新 '+r.count+' 个模型':r.msg||'未能刷新'}`).join('；')||d.msg, d.rows?.some(r=>!r.ok));},
 'reload-providers':async()=>{await api('providers/reload',{});await reloadBoot();settings();notice('服务商已重新扫描。');},
 'save-limits':async()=>{const f=$('#limits-form');if(!f.reportValidity())return;const d=Object.fromEntries(new FormData(f)),per={};Object.entries(d).filter(([k])=>k.startsWith('provider.')).forEach(([k,v])=>per[k.slice(9)]=+v);await api('settings',{limits:{global:+d.global,per_provider:per}});await reloadBoot();notice('已保存共享并发上限。');await refreshRuntime();},
 'save-upload':async()=>{await api('settings',{upload:Object.fromEntries(new FormData($('#upload-form')))});$('#upload-form').reset();await reloadBoot();settings();notice('已保存参考图上传设置。');},
 'detail':e=>{S.detail=e.dataset.id;renderDetail();},
 'select-visible':()=>setSelection(S.visibleKeys,true),'clear-visible':()=>setSelection(S.visibleKeys,false),'select-all':()=>setSelection(S.data.tasks.map(t=>t.key),true),
 'dependencies':async()=>{await post('dependencies');await loadProject();notice('已补选必要上游；有效成品继续复用。');},
 'prompt':e=>editPrompt(e.dataset.key),
 'save-prompt':savePrompt,
 'revision':e=>dialog('交给 Agent 定向修订',`<p>${esc(e.dataset.key)}</p><textarea id="revision-note" rows="5" placeholder="说明实际问题、服务商反馈和希望修改的地方"></textarea>${btn('生成修订请求','save-revision',`data-key="${esc(e.dataset.key)}"`,true)}`),
 'save-revision':async e=>{const d=await post('revision',{keys:[e.dataset.key],note:$('#revision-note').value});dialog('Agent 修订请求',`<textarea id="revision-result" readonly rows="14">${esc(d.text)}</textarea>${fileLink(d.rel,'打开修订请求')}`);},
 'preview':preview,
 'start':async()=>{if(!S.plan)throw Error('先查看生产计划');const d=await post('start',{revision:S.plan.revision,only:S.plan.keys});if(!d.ok)throw Error(d.blocked.map(x=>x.key+'：'+x.reason).join('\n'));await loadProject();notice(d.message||'生产批次已启动。');},
 'cancel':async e=>{await post('cancel',{id:e.dataset.id});await loadProject();notice('已请求停止，等待在途调用退出。');},
 'job':e=>jobDetail(e.dataset.id),
 'assemble':()=>{const eps=[...new Set(S.data.tasks.filter(t=>t.kind==='video').map(t=>t.episode||''))];dialog('按集顺序合成',`<label>选择集<select id="assemble-episode">${eps.map(e=>option(e,e||'未指定集','')).join('')}</select></label><p>只在该集所有视频有效时合成，按任务清单顺序拼接，不调用文字模型。</p>${btn('开始合成','assemble-run','',true)}`);},
 'subtitle':async()=>{const d=await get('post-options',{root:S.root});const o=d.options;dialog('字幕识别与烧录',`<form id="subtitle-form"><div class="rv-fields">${field('成片',`<select name="file">${d.files.map(f=>option(f,f,'')).join('')}</select>`)}${field('识别引擎',select('asr',['bijian','jianying','faster-whisper','whisper-cpp'],o.asr))}${field('识别语言',input('language',o.language))}${field('字幕模式',select('subtitle_mode',['hard','soft'],o.subtitle_mode))}${field('字幕样式',`<select name="style">${option('','默认样式',o.style)}${d.styles.map(s=>option(s.name,s.name,o.style)).join('')}</select>`)}</div><p>已有同名 SRT 时直接复用，可先交给 Agent 修订；否则先识别再合成。hard 为烧录字幕，soft 为软字幕轨。保留原成片，生成带字幕的新文件。</p><p class="rv-caption">必剪 / 剪映使用相应识别服务；faster-whisper / whisper-cpp 使用本机模型。文字优化和翻译交给 Agent。</p>${btn('开始字幕处理','subtitle-run',d.files.length?'':'disabled',true)}</form>`);},
 'subtitle-run':async()=>{const d=await post('subtitle',Object.fromEntries(new FormData($('#subtitle-form'))));closeDialog();await loadProject(S.root,false);show('production');notice('字幕后期已启动，可在任务明细查看或停止。');},
 'assemble-run':async()=>{const d=await post('assemble',{episode:$('#assemble-episode').value});dialog('成片已保存',fileLink(d.rel,'打开成片'));},
 'import':()=>dialog('旧材料导入',`<p>仅影响当前旧项目，Agent 项目使用自动发布。</p><textarea id="import-text" rows="14" placeholder="粘贴完整材料"></textarea>${btn('导入旧材料','import-run','',true)}`),
 'import-run':async()=>{const d=await post('import',{text:$('#import-text').value});if(d.ok===false)throw Error(d.msg||JSON.stringify(d.issues));closeDialog();await loadProject();notice('已导入旧材料。');}
};
main.addEventListener('click',event=>{
 const summary=event.target.closest('summary[data-folder-toggle]');
 if(!summary||event.target.closest('input'))return;
 event.preventDefault();const folder=summary.parentElement;folder.open=!folder.open;
 S.folders.set(folder.dataset.folderState,folder.open);
});
document.addEventListener('click',safe(async event=>{const page=event.target.closest('[data-page]');if(page){show(page.dataset.page);return;}const button=event.target.closest('[data-action]');if(!button||button.disabled)return;const fn=actions[button.dataset.action];if(fn){button.disabled=true;try{await fn(button);}finally{if(button.isConnected)button.disabled=false;}}}));
document.addEventListener('submit',e=>e.preventDefault());
document.addEventListener('input',safe(e=>{const el=e.target;const map={'project-search':'project','asset-search':'asset'};if(map[el.id]){S.filters[map[el.id]]=el.value;el.id==='project-search'?projectList():renderTree();}if(el.id==='output-search'){S.filters.output=el.value;outputList();}}));
document.addEventListener('change',safe(async e=>{const el=e.target;
 if(el.id==='project-picker'){S.root=el.value;S.data=null;S.detail='';if(S.root){await loadProject(S.root,false);show('production');}else show('projects');return;}
 const filters={'project-status':'status','project-type':'type','project-sort':'sort','asset-status':'assetStatus','asset-type':'assetType'};
 if(filters[el.id]){S.filters[filters[el.id]]=el.value;el.id.startsWith('project')?projectList():renderTree();return;}
 if(el.id==='tree-mode'){S.treeMode=el.value;renderTree();return;}
 if(el.dataset.key&&el.type==='checkbox'){await setSelection([el.dataset.key],el.checked);return;}
 if(el.dataset.group){await setSelection(S.groupKeys[el.dataset.group],el.checked);return;}
 if(el.id==='provider-picker'){S.provider=el.value;providerForm();return;}
 if(el.dataset.feature){await api('settings',{agent_features:{[el.dataset.feature]:el.checked}});await reloadBoot();notice('功能入口已更新，基础生产功能始终保留。');return;}
 if(el.dataset.setting||el.dataset.override){const currentRoot=S.root;invalidatePlan();const row=el.closest('[data-kind]'),kind=row.dataset.kind,setting={...S.data.production[kind],override:{...S.data.production[kind].override}};if(el.dataset.override)setting.override[el.dataset.override]=el.dataset.override==='duration'?(el.value?+el.value:''):el.value;else setting[el.dataset.setting]=el.dataset.setting==='concurrency'?+el.value:el.value;
 if(el.dataset.setting==='provider'){const cap=S.boot.capabilities.find(c=>c.id===el.value),media=kind==='video'?'video':'image';setting.model=cap?.[media]?.default_model||'';setting.image_resolution='';}
 if(el.dataset.setting==='model')setting.image_resolution='';
 const controls=[...row.querySelectorAll('input,select')];controls.forEach(control=>control.disabled=true);
 try{await post('production-settings',{settings:{[kind]:setting}});if(currentRoot!==S.root||S.page!=='production')return;S.data.production[kind]=setting;invalidatePlan();if(['provider','model'].includes(el.dataset.setting))renderServices();}
 finally{controls.forEach(control=>control.disabled=false);}return;}
 if(el.id==='source-file'&&el.files[0]){const d=await api('parse',{filename:el.files[0].name,content_b64:await readFile(el.files[0])});$('[name=source]').value=d.text;return;}
 if(el.id==='manual-file'&&el.files[0]){await post('manual',{kind:el.dataset.kind,key:el.dataset.key,content_b64:await readFile(el.files[0])});await loadProject();notice('参考图已保存。');return;}
 if(el.id==='output-kind'){S.filters.outputKind=el.value;outputList();return;}
 if(S.page==='wizard'&&['image_provider','video_provider'].includes(el.name)){const media=el.name.split('_')[0],cap=S.boot.capabilities.find(c=>c.id===el.value);$(`[name=${media}_model]`).value=cap?.[media]?.default_model||'';}
}));
$('#dialog-close').addEventListener('click',closeDialog);$('#dialog').addEventListener('close',()=>{S.job='';S.editor=null;});
$('#apply-advice').addEventListener('click',safe(()=>{const n=S.runtime?.advice?.limit;if(n==null||n<1)throw Error('当前样本不足，暂不能给出可用建议');S.tab='limits';show('settings');$('[name=global]').value=Math.min(512,n);notice('建议已填入，检查各服务商额度后点击保存。');}));
async function tick(){try{await refreshRuntime();if(S.job&&$('#dialog').open)await jobDetail(S.job);if(S.root&&['production','handoff','outputs'].includes(S.page)&&!$('#dialog').open&&!document.activeElement?.matches('input,select,textarea')){const page=S.page,root=S.root;const previous=await loadProject(root,false,true);if(page===S.page&&root===S.root&&previous)refreshProjectView(previous);}}catch(e){notice('更新失败：'+e.message,true);}finally{setTimeout(tick,4000);}}
document.addEventListener('click',event=>{if(!$('#runtime').contains(event.target))$('#runtime').open=false;});
document.addEventListener('keydown',event=>{if(event.key==='Escape')$('#runtime').open=false;});
stickyObserver.observe($('#sticky-header'));
safe(async()=>{await reloadBoot();render();tick();})();
})();
