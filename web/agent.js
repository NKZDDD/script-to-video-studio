'use strict';
(() => {
const $ = (s, p=document) => p.querySelector(s), $$ = (s,p=document) => [...p.querySelectorAll(s)];
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const S = {boot:null, root:'', data:null, page:'projects', tab:'providers', provider:'', detail:'', wizard:null, step:0, plan:null, treeMode:'family', filters:{}, groupKeys:{}, folders:new Map(), planRequest:0, runtime:null, job:'', jobRoot:'', jobRequest:0, request:0, del:null, watchJobs:new Set()};
const KIND_NAMES={production:'生产批次',assemble:'合成成片'};
const kindName=k=>KIND_NAMES[k]||k;
const main = $('#main');
const btn = (text, action, extra='', primary=false) => `<button type="button" class="rv-button ${primary?'rv-primary':''}" data-action="${action}" ${extra}>${text}</button>`;
const option = (value, text, selected) => `<option value="${esc(value)}" ${value===selected?'selected':''}>${esc(text)}</option>`;
const choices = (values, selected) => values.map(v=>option(v,v,selected)).join('');
const input = (name,value,type='text',extra='') => `<input name="${esc(name)}" type="${type}" value="${esc(value)}" ${extra}>`;
const field = (label,html,note='') => `<label>${label}${html}${note?`<small>${note}</small>`:''}</label>`;
const fileButton = (id,label,accept,extra='',named=true) => `<span class="rv-file"><input type="file" id="${id}" class="rv-file-input" accept="${accept}" ${extra}><label for="${id}" class="rv-button">${label}</label>${named?`<small class="rv-file-name" data-file-for="${id}">未选择文件</small>`:''}</span>`;
const select = (name,values,value) => `<select name="${name}">${choices(values,value)}</select>`;
const heading = (eye,title,note,actions='') => `<div class="rv-heading"><div><span class="rv-eyebrow">${esc(eye)}</span><h2>${title}</h2><p>${note}</p></div><div class="rv-actions">${actions}</div></div>`;
const fileURL = rel => '/file?'+new URLSearchParams({root:S.root,rel});
const fileLink = (rel,label) => `<a href="${esc(fileURL(rel))}" target="_blank" rel="noopener">${esc(label||rel)}</a>`;
async function api(path, body) {
 const response = await fetch('/api/agent/'+path, body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
 const data = await response.json();
 if(data.login_required){window.StudioLogin.lock(data.error||'请重新登录',response.status===503);throw Error(data.error||'请重新登录');}
 if(!response.ok || data.error) throw Error(data.error||`请求失败 ${response.status}`);
 return data;
}
const get = (path,args={}) => api(path+'?'+new URLSearchParams(args));
const post = (path,data={}) => api(path,{root:S.root,...data});
function notice(text,error=false) {const e=$('#message'); e.hidden=false; e.className='rv-notice '+(error?'is-error':'');e.textContent=text;clearTimeout(notice.timer);notice.timer=setTimeout(()=>e.hidden=true,error?15000:6000);}
function dialog(title,body) {$('#dialog').classList.remove('rv-job-dialog','rv-image-dialog');$('#dialog-close').textContent='关闭';$('#dialog-close').setAttribute('aria-label','关闭对话框');$('#dialog-title').textContent=title;$('#dialog-body').innerHTML=body;$('#dialog-body').scrollTop=0;$('#dialog-body').scrollLeft=0;S.job='';S.jobRequest++;S.editor=null;S.del=null;if(!$('#dialog').open)$('#dialog').showModal();}
function closeDialog() {$('#dialog').close();S.job='';S.jobRequest++;S.editor=null;S.del=null;}
const STATE_NAMES={pending:'待开始',queued:'排队中',running:'生成中',ok:'完成',skipped:'已有，跳过',failed:'失败',cancelled:'已取消',aborted:'已中止',interrupted:'中断'};
function taskState(t) {
 if(!t)return {label:'元数据',cls:''};
 const run=S.data.running?.[t.key];
 if(t.done)return {label:'已完成',cls:'rv-good'};
 if(run)return run.state==='running'?{label:'生成中',cls:'rv-warn'}:run.state==='queued'?{label:'排队中',cls:'rv-warn'}:{label:/^等待/.test(run.msg||'')?'等待上游':'待开始',cls:'rv-warn'};
 if(t.submitted)return {label:'已提交待取回',cls:'rv-warn'};
 if(S.data.failures.some(f=>f.target===t.key))return {label:'失败',cls:'rv-bad'};
 return {label:t.missing_selection.length?'待依赖':'待生产',cls:''};
}
function linker(keys) {
 if(!keys.length)return esc;
 const re=new RegExp('('+[...keys].sort((a,b)=>b.length-a.length).map(k=>k.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')).join('|')+')','g');
 return text=>String(text??'').split(re).map((part,i)=>i%2?`<button type="button" class="rv-link" data-action="jump" data-key="${esc(part)}">${esc(part)}</button>`:esc(part)).join('');
}
function jumpTo(key) {
 const r=S.data?.resources.find(r=>r.task_key===key);if(!r)throw Error(`找不到 ${key} 对应的资源，请刷新`);
 if($('#dialog').open)closeDialog();
 S.detail=r.id;
 if(S.page!=='production')show('production');else{renderTree();renderDetail();}
 const b=$$('[data-action="detail"]',main).find(e=>e.dataset.id===r.id);
 if(!b){notice('它被当前的搜索或筛选挡住了，清空后能在树里看到；右侧已显示它的详情。');return;}
 for(let d=b.closest('details');d;d=d.parentElement.closest('details'))if(!d.open){d.open=true;S.folders.set(d.dataset.folderState,true);}
 b.scrollIntoView({block:'center',behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth'});
}
function durationsOf(pid,model) {
 const v=S.boot.capabilities.find(c=>c.id===pid)?.video||{},rules={};
 String(S.boot.providers_public[pid]?.durations||'').split(/[;\n]+/).forEach(rule=>{const i=rule.indexOf('=');if(i>0)rules[rule.slice(0,i).trim()]=rule.slice(i+1).split(/[,，\s]+/).map(Number).filter(n=>n>0);});
 return rules[model]?.length?rules[model]:rules['*']?.length?rules['*']:(v.model_options?.[model]?.durations||v.durations||[]);
}
function safe(fn) {return (...a)=>Promise.resolve().then(()=>fn(...a)).catch(e=>notice(e.message,true));}
async function reloadBoot() {S.boot=await api('bootstrap');S.provider ||= S.boot.capabilities[0]?.id||'';picker();}
function picker() {$('#project-picker').innerHTML=option('','选择项目',S.root)+S.boot.projects.map(p=>option(p.root,p.title,S.root)).join('');}
async function loadProject(root=S.root,paint=true,background=false) {
 if(!root)return; const seq=background?S.request:++S.request;const data=await get('project',{root});
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
 if((S.page==='production'&&!$('#resource-tree',main))||(S.page==='outputs'&&!$('#output-list',main)))return render();
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
 $('#project-list').innerHTML=rows.map(p=>`<div class="rv-projectrow"><div><strong>${esc(p.title)}</strong><small>${esc(p.project_code)} · ${esc(p.tags.join(' · '))}</small><code>${esc(p.root)}</code></div><div><span class="rv-badge">${esc(p.state)}</span><small>${p.workflow==='agent'?'Agent 完整技能项目':'旧项目 · 保留现有产物'}</small></div><span class="rv-projecttime">${esc(p.updated)}</span><div>${btn('打开','open',`data-root="${esc(p.root)}"`)} ${btn('修改目录','project-path',`data-root="${esc(p.root)}"`)} ${btn(p.archived?'恢复':'归档','archive',`data-root="${esc(p.root)}" data-archived="${!p.archived}"`)}</div></div>`).join('')||'<div class="rv-panel">没有匹配项目。可以调整筛选，或创建第一个项目。</div>';
 $('#project-count').textContent=`显示 ${rows.length} 个项目 · 生产父目录 ${S.boot.projects_dir}`;
}
function beginWizard(batch=false) {S.wizard={...S.boot.defaults,title:'',source:'',base:S.boot.projects_dir,tags:'',batch:batch?'':''};S.step=0;show('wizard');}
function wizardSave() {const f=$('#wizard-form');if(!f)return;new FormData(f).forEach((v,k)=>S.wizard[k]=v);$$('input[type=checkbox][name]',f).forEach(e=>S.wizard[e.name]=e.checked);}
function providerSelect(media,name,value) {return `<select name="${name}">${option('','稍后配置',value)}${S.boot.capabilities.filter(c=>c.supports.includes(media)).map(c=>option(c.id,c.name,value)).join('')}</select>`;}
function wizard() {
 const w=S.wizard; if(!w)return show('projects');
 const steps=['项目与原文','创作边界','视频与语言','技能与资产','确认创建'];
 let html='';
 if(S.step===0)html=`<h3>项目放在哪里，原文从哪里来</h3><div class="rv-fields">${field('项目名称',input('title',w.title,'text','required maxlength="100"'),'编号自动生成，避免跨项目冲突。')}${field('标签',input('tags',w.tags),'用逗号分隔，用于项目搜索。')}${field('生产父目录',input('base',w.base,'text','required'),'将自动建立“名称_项目编号”子目录；确认后显示实际绝对路径。')}${field('原文类型',select('source_type',['剧本','小说','大纲','已有资产'],w.source_type))}${field('集数策略',select('episode_strategy',['按原文','指定集数'],w.episode_strategy))}${field('指定集数',input('episode_count',w.episode_count,'number','min="1" max="999"'),'仅在选择指定集数时采用。')}<label class="rv-span">原文<textarea name="source" rows="7" placeholder="粘贴原始剧本或选择文件；留空可先创建，后续补齐原文。">${esc(w.source)}</textarea></label><div class="rv-file-field"><span>读取原文文件</span>${fileButton('source-file','选择文件…','.txt,.md,.jsonl,.docx,.pdf')}<small>支持 txt、md、jsonl、docx、pdf，读出的文字会填进上面的原文框。</small></div>${w.batch!==undefined&&w.batch!==false&&w.batch!==''?field('批量项目名（每行一个）',`<textarea name="batch" rows="5">${esc(w.batch)}</textarea>`):''}</div>`;
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
 `<div class="rv-handoff-grid"><div class="rv-panel"><h3>交接内容已经明确</h3><dl><dt>项目编号</dt><dd>${esc(d.meta.project_code)}</dd><dt>生产路径</dt><dd><code>${esc(d.root)}</code></dd><dt>当前发布</dt><dd>${esc(p.revision||'等待 Agent')}</dd><dt>结构状态</dt><dd>${p.revision?'已接收，视觉验收仍需检查':'等待完整材料'}</dd></dl>${p.errors.map(e=>`<p class="rv-notice is-error">${esc(e.revision)}：${esc(e.message)}</p>`).join('')}${btn('补充 / 更新原文','source')}${d.template?fileLink('00_项目说明/production-contract.md','查看生产契约'):''}<p class="rv-caption">${p.legacy?'旧项目可继续查看和生产现有任务，不调用内部文字分析。':'Agent 先读取完整技能与锁定选项，最后写 READY.json；新版本不会修改已排队批次。'}</p></div><div class="rv-panel"><div class="rv-row"><h3>AGENT_TASK.md</h3>${d.template?btn('复制完整模板','copy-template','',true):''}</div><textarea id="agent-template" readonly rows="16">${esc(d.template||'这是旧项目。原来的任务和成品已保留；可在生产页查看、勾选和继续生成。')}</textarea>${d.template?fileLink('00_项目说明/AGENT_TASK.md','打开 / 下载模板'):''}</div></div>${d.template?remotePanel():''}`;
}
function remotePanel() {
 const r=S.remote?.root===S.root?S.remote:null;
 return `<div class="rv-panel"><div class="rv-row"><h3>Codex 不在这台机器上</h3><div class="rv-actions">${btn('导出交接包','handoff-export')}${fileButton('release-file','导入材料或发布包…','.jsonl,.zip','',false)}</div></div><p class="rv-caption">导出的压缩包里已经有剧本全文、生产材料契约、已有分析和完整技能。把这一个文件交给 Codex 即可，不用再另附剧本。交回的 .jsonl 按旧契约导入成任务；交回的发布 zip 仍按校验接收。</p>${r?`<div class="rv-notice ${r.error?'is-error':''}">${r.html}</div>`:''}</div>`;
}
async function loadAssemble(keep=false) {
 const a=S.asm;
 const d=a.chosen.length?await get('assemble-plan',{root:a.root,episodes:a.chosen.join(',')}):{rows:[],extras:a.extras};
 if(S.asm!==a||!$('#dialog').open)return;
 a.extras=d.extras;
 if(!keep)a.rows=d.rows.map(r=>({...r,include:r.ok}));
 renderAssemble();
}
function asmOwners(rows) {
 const marks=rows.map(r=>r.task?r.episode:null);
 return marks.map((m,i)=>m??[...marks.slice(0,i)].reverse().find(x=>x!=null)??marks.slice(i+1).find(x=>x!=null)??S.asm.chosen[0]);
}
function renderAssemble() {
 const a=S.asm,many=a.chosen.length>1,owners=asmOwners(a.rows);
 $('#dialog-body').innerHTML=`<div class="rv-asm-eps"><strong>选择集</strong>${a.episodes.map(e=>`<label class="rv-inline"><input type="checkbox" data-asm-ep="${esc(e)}" ${a.chosen.includes(e)?'checked':''}> ${esc(e||'未指定集')}</label>`).join('')}<span class="rv-actions">${btn('全集','asm-all')}${btn('清空','asm-none')}</span></div><p class="rv-caption">勾几集就每集各出一个成片，「全集」就是每一集各出一个。清单按集、集内按剧本顺序列出。取消勾选就不拼这一段，↑ ↓ 调整顺序，也可以插入抽卡备份或外部视频 —— 插入的视频归到它上面最近那一段所在的集。每一集的画面按分段原片拼接，不重新压缩；每段的声音裁或补到这一段画面的长度，保证音画对齐。和本集分段分辨率或编码不同的外部视频只单独适配那一段，分段本身不重编码。改选集会重新列清单。</p><ol class="rv-asm">${a.rows.map((r,i)=>`<li class="${r.include&&r.ok?'':'rv-off'}"><label class="rv-inline"><input type="checkbox" data-asm-include="${i}" ${r.include&&r.ok?'checked':''} ${r.ok?'':'disabled'}>${many?`<span class="rv-badge">${esc(owners[i]||'')}</span>`:''}<strong>${esc(r.key)}</strong></label><small>${esc(r.rel)}${r.ok?'':' · 还没有有效视频'}${r.task?'':' · 外部视频'}</small><span class="rv-actions">${btn('↑','asm-up',`data-i="${i}" aria-label="上移"`)}${btn('↓','asm-down',`data-i="${i}" aria-label="下移"`)}${r.task?'':btn('移除','asm-remove',`data-i="${i}"`)}</span></li>`).join('')||`<li>${a.chosen.length?'所选集没有分段视频':'先勾选要合成的集'}</li>`}</ol><div class="rv-row"><select id="asm-extra" aria-label="可插入的视频">${a.extras.map(x=>option(x.rel,`${x.from} · ${x.name}`,'')).join('')||option('','没有可插入的视频','')}</select><div class="rv-actions">${btn('插入到末尾','asm-insert',a.extras.length?'':'disabled')}${fileButton('asm-upload','上传外部视频…','.mp4,.mov,.webm','',false)}${btn('刷新清单','asm-refresh')}</div></div><small>超过 40MB 的视频直接复制到项目的 06_成片/插入素材/ 文件夹，再点「刷新清单」。</small><div class="rv-actions wizard-footer">${btn(many?`合成 ${a.chosen.length} 集（每集一个成片）`:'开始合成','assemble-run',a.chosen.length?'':'disabled',true)}</div>`;
}
async function loadDelete() {
 const d=S.del;if(!d)return;
 const request=++d.request;d.loading=true;renderDelete();
 try{
  const r=await get('delete-plan',{root:d.root,key:d.key,up:d.up?1:0,down:d.down?1:0,siblings:d.siblings?1:0});
  if(S.del!==d||d.request!==request||!$('#dialog').open)return;
  d.items=r.items;
  for(const x of d.items)if(!(x.key in d.choices))d.choices[x.key]=x.exists&&!S.data?.running?.[x.key];
 }catch(e){if(S.del===d&&d.request===request)d.items=[];throw e;}
 finally{if(S.del===d&&d.request===request){d.loading=false;renderDelete();}}
}
function deleteFiles() {return S.del?.items.filter(x=>x.exists&&S.del.choices[x.key])||[];}
function renderDelete() {
 const d=S.del,files=deleteFiles(),live=files.filter(x=>S.data?.running?.[x.key]);
 $('#dialog-body').innerHTML=`<p><strong>${esc(d.key)}</strong></p><p class="rv-caption">只把勾选的已生成文件移到 07_检查与记录/回收站/，保留原目录结构。任务保留在生产树上；未勾选的产物不动。</p><div class="rv-del-opts">${[['down','列出下游（子链路）：拿它当参考做出来的，一直往下'],['up','列出上游（父链路）：它用到的参考图，一直往上'],['siblings','列出兄弟链路：和它共用同一个上游的其他任务']].map(([k,l])=>`<label class="rv-inline"><input type="checkbox" data-del="${k}" ${d[k]?'checked':''}> ${esc(l)}</label>`).join('')}<small>链路选项只决定候选范围，下面逐项决定删除哪些文件。保留下游成品时，它仍是按旧参考图生成的。</small></div><div class="rv-actions">${btn('全选可删除文件','delete-all',d.loading?'disabled':'')}${btn('清空选择','delete-none',d.loading?'disabled':'')}</div><h4>${d.loading?'正在更新候选文件…':`已选 ${files.length} / ${d.items.filter(x=>x.exists).length} 个文件`}</h4><ol class="rv-del-list">${d.items.map(x=>`<li class="${x.exists?'':'rv-off'}"><label class="rv-inline"><input type="checkbox" data-delete-key="${esc(x.key)}" ${d.choices[x.key]&&x.exists?'checked':''} ${!x.exists||d.loading?'disabled':''}><span class="rv-badge">${esc(x.relation)}</span> <strong>${esc(x.name)}</strong></label><small>${esc(S.boot.kinds[x.kind]||x.kind)} · ${esc(x.output)}${x.exists?'':' · 还没有文件，跳过'}${S.data?.running?.[x.key]?' · 正在生成，请取消勾选':''}</small></li>`).join('')}</ol>${live.length?`<p class="rv-notice is-error">${live.map(x=>esc(x.key)).join('、')} 正在生成，请取消勾选，或先停止对应批次。</p>`:''}<div class="rv-actions wizard-footer">${btn(`移到回收站（${files.length} 个文件）`,'delete-run',files.length&&!live.length&&!d.loading?'':'disabled',true)}</div>`;
}
function audioCards(t) {
 const refs=t.reference_audios||[];
 return `<h4>参考音频（提交顺序）</h4>${refs.map((r,i)=>`<div class="rv-audio-ref"><span>${i+1}. ${esc(r.name||r.file_ref)}</span><audio controls preload="none" src="${esc(/^https?:/.test(r.file_ref)?r.file_ref:fileURL(r.file_ref))}"></audio>${btn('移除','audio-remove',`data-key="${esc(t.key)}" data-ref="${esc(r.file_ref)}"`)}</div>`).join('')||'<p class="rv-caption">未添加参考音频</p>'}<div class="rv-file-field">${fileButton('audio-file','添加参考音频…','.mp3,.wav,.m4a,.aac,.ogg,.flac',`multiple data-key="${esc(t.key)}"`)}<small>每个音频最多 25MB；使用设置中的对象存储上传。服务商须支持参考音频。仅影响下一次新生成，已提交任务仍取回原 ID。</small></div>`;
}
function videoInputCards(t) {
 const refs=t.reference_videos||[];
 return `<h4>参考视频链接（提交顺序）</h4>${refs.map((r,i)=>`<div class="rv-audio-ref"><span>${i+1}. ${esc(r.name||'参考视频')}</span><a href="${esc(/^https:\/\//i.test(r.url)?r.url:'#')}" target="_blank" rel="noopener noreferrer">查看视频</a>${btn('移除','video-reference-remove',`data-key="${esc(t.key)}" data-ref="${esc(r.url)}"`)}</div>`).join('')||'<p class="rv-caption">未添加参考视频</p>'}<div class="rv-file-field"><input id="video-reference-url" type="url" placeholder="https://…/reference.mp4" aria-label="参考视频 HTTPS 链接">${btn('添加视频链接','video-reference-add',`data-key="${esc(t.key)}"`)}<small>填写可公开访问的视频文件链接；沧元支持此功能的型号可用。只影响下一次新生成，已提交任务仍取回原 ID。</small></div>`;
}
const BULK_EXT=/\.(png|jpe?g|webp)$/i;
function bulkIntro() {
 return `<p class="rv-caption">从别处拿来的图，按文件名对应到出图任务：文件名和任务的输出文件名、任务 ID 或资源名称一样（或包含）就算对上。全角半角、大小写、空格和常见标点不算差别。先列出对应结果，你确认或改选后再放；放图和详情里的「手动放图」一样，会按输出路径的格式转换（JPG 转成 PNG 等）并校验。已经有文件的任务不会被覆盖。</p><div class="rv-actions">${fileButton('bulk-folder','选文件夹…','image/png,image/jpeg,image/webp','webkitdirectory multiple',false)}${fileButton('bulk-files','选图片…','image/png,image/jpeg,image/webp','multiple',false)}</div><div id="bulk-body"></div>`;
}
function renderBulk() {
 const b=S.bulk,body=$('#bulk-body');if(!b||!body)return;
 const chosen=b.rows.filter(r=>r.choice&&!r.done).length;
 const names=new Map(S.data.resources.filter(r=>r.task_key).map(r=>[r.task_key,r.name]));
 const all=S.data.tasks.filter(t=>t.kind!=='video').map(t=>option(t.key,`${names.get(t.key)||t.key}${t.exists?'（已有文件）':''}`,'')).join('');
 updateHTML(body,`<datalist id="bulk-all">${all}</datalist><p><strong>${b.rows.length}</strong> 张图 · 对上 ${b.rows.filter(r=>r.choice).length} 张${b.running?` · 正在放第 ${b.at}/${b.total} 张…`:''}</p><table class="rv-bulk"><thead><tr><th>文件</th><th>放到哪个任务</th><th>说明</th></tr></thead><tbody>${b.rows.map((r,i)=>`<tr class="${r.done?'rv-good':r.error?'rv-bad':''}"><td>${esc(r.name)}</td><td><select data-bulk-choice="${i}" ${b.running||r.done?'disabled':''} aria-label="${esc(r.name)} 放到哪个任务">${option('','不放',r.choice)}${r.candidates.map(c=>option(c.key,`${c.name} · ${c.key}${c.exists?'（已有文件）':''}`,r.choice)).join('')}</select>${r.done?'':`<input data-bulk-key="${i}" list="bulk-all" placeholder="或输入任务 ID" aria-label="${esc(r.name)} 手动输入任务 ID" ${b.running?'disabled':''}>`}</td><td>${r.done?'已放进去':r.error?esc(r.error):esc(r.note||'')}</td></tr>`).join('')}</tbody></table><div class="rv-actions wizard-footer">${btn(`放进去（${chosen} 张）`,'bulk-run',chosen&&!b.running?'':'disabled',true)}</div>`);
}
async function loadBulk(files) {
 files=[...files].filter(f=>BULK_EXT.test(f.name));
 if(!files.length)throw Error('没有 PNG / JPG / WebP 图片');
 const b={root:S.root,files,rows:[],running:false};S.bulk=b;
 $('#bulk-body').innerHTML='<p>正在对应任务…</p>';
 const d=await post('place-plan',{names:files.map(f=>f.name)});
 if(S.bulk!==b)return;
 b.rows=d.rows;renderBulk();
}
async function runBulk() {
 const b=S.bulk;if(!b||b.running)return;
 const todo=b.rows.map((r,i)=>[r,i]).filter(([r])=>r.choice&&!r.done);
 b.running=true;b.total=todo.length;b.at=0;
 for(const [r,i] of todo){
  b.at++;renderBulk();
  const c=r.candidates.find(x=>x.key===r.choice);
  try{await api('manual',{root:b.root,kind:c.kind,key:c.key,content_b64:await readFile(b.files[i])});r.done=true;r.error='';}
  catch(e){r.error=e.message;}
 }
 b.running=false;renderBulk();
 if(S.root===b.root)await loadProject(b.root);
 const ok=b.rows.filter(r=>r.done).length,bad=todo.filter(([r])=>r.error).length;
 notice(`已放进 ${ok} 张${bad?`，${bad} 张没放成，原因写在表里`:''}。`,bad>0);
}
async function openDelete(key) {
 dialog('删除产物','<p>正在列出会被删除的文件…</p>');
 S.del={root:S.root,key,down:true,up:false,siblings:false,items:[],choices:{},request:0,loading:false};
 await loadDelete();
}
async function importRelease(file) {
 const d=await post('handoff/import',{filename:file.name,content_b64:await readFile(file)});
 if(file.name.toLowerCase().endsWith('.jsonl')){
  S.remote={root:S.root,error:d.ok===false,html:esc(d.msg||'')+(d.reproduce?`<pre>${esc(d.reproduce)}</pre>`:'')};
  await loadProject(S.root,false);if(S.page==='handoff')handoff();
  if(d.ok===false)notice(d.msg||'材料没有导入',true);else {notice(d.msg||'已导入生产材料');show('production');}
  return;
 }
 const lines=[...d.imported.map(v=>`${esc(v)} 已导入并通过校验`),...d.unchanged.map(v=>`${esc(v)} 之前已导入过，内容相同，跳过`),...d.rejected.map(x=>`${esc(x.revision)} 没有通过校验，已撤回：${esc(x.message)}`)];
 S.remote={root:S.root,error:d.rejected.length>0,html:lines.join('<br>')+`<br>当前已接收版本：${esc(d.active||'无')}`};
 await loadProject(S.root,false);handoff();
 if(d.rejected.length)notice('发布包里有版本没通过校验，详情见「Codex 不在这台机器上」一栏。',true);
}
function production() {
 const d=S.data;const cats={...S.boot.kinds};if(d.tasks.some(t=>t.kind==='storyboard'))cats.storyboard='旧项目故事板';
 main.innerHTML=`<section class="rv-production-dock" aria-label="生产操作与计划"><div class="rv-panel rv-production-actions"><div><strong id="plan-summary"></strong><small id="production-version"></small></div><div class="rv-actions">${btn('查看生产计划','preview')}${btn('开始生产','start','disabled',true)}</div><small>勾选后先查看生产计划，确认后开始；筛选不清除隐藏勾选。</small></div></section><div id="production-plan" tabindex="0" aria-label="生产计划详情"></div>`+
 heading(d.meta.title,'选择这次要生产什么','资产和分集任务在同一棵树中管理，有效成品自动复用。',btn('批量放图…','bulk-place')+btn('Agent 契约','handoff')+btn('刷新','refresh'))+
 `<div id="publication-errors"></div>
 <div class="rv-panel"><div class="rv-row"><h3>本次服务商与并发</h3>${btn('填写 / 管理密钥','providers')}</div><div class="rv-service-head"><span>生产类型</span><span>服务商</span><span>模型</span><span>比例</span><span>清晰度 / 秒数</span><span>并发</span></div><div id="production-services"></div><p class="rv-caption">各行共享全局总上限与服务商上限；批内并发不能相加当作可用额度。设置变更只影响下次开始的批次。比例和视频的秒数都是直接传给服务商的参数，选「跟随任务」时用每条任务里写的值；下拉里是当前模型声明支持的档位。改了秒数，提示词仍是按原秒数写的。技术失败自动重试最多 ${esc(S.boot.retry)} 次，在 设置 → 并发与重试 里改。</p></div>
 <div id="jobs"></div>
 <div class="rv-toolbar"><select id="tree-mode" aria-label="树形分组">${[['family','按资源分类（父子）'],['type-episode','按资源类型 → 集'],['episode','按集 → 资源类型'],['disk','按磁盘目录']].map(([v,n])=>option(v,n,S.treeMode)).join('')}</select><label class="rv-search"><input id="asset-search" type="search" aria-label="搜索资源" placeholder="搜索人物、场景、任务 ID、文件名" value="${esc(S.filters.asset||'')}"></label><select id="asset-status" aria-label="资源状态">${choices(['全部状态','待生产','已完成','失败','选择有阻塞'],S.filters.assetStatus||'全部状态')}</select><select id="asset-type" aria-label="资源类型">${option('','全部类型',S.filters.assetType||'')}${Object.entries(cats).map(([k,v])=>option(k,v,S.filters.assetType)).join('')}</select></div>
 <div class="rv-row rv-selection"><span id="selection-count"></span><div class="rv-actions">${btn('选中当前结果','select-visible')}${btn('取消当前结果','clear-visible')}${btn('全项目全选','select-all')}${btn('补选依赖','dependencies')}${btn('批量放弃已选待取回','drop-submitted-batch')}</div></div><div id="dependency-alert"></div>
 <div class="rv-production-grid"><div class="rv-panel rv-tree-panel"><div id="resource-tree" role="region" aria-label="生产资源列表" tabindex="0"></div></div><aside class="rv-panel rv-detail" id="resource-detail" aria-label="资源详情" tabindex="0"></aside></div>`;
 renderServices();renderPublication();renderTree();renderDetail();renderJobs();renderPlan();
}
function renderServices() {
 const cats={...S.boot.kinds};if(S.data.tasks.some(t=>t.kind==='storyboard'))cats.storyboard='旧项目故事板';
 updateHTML($('#production-services'),Object.entries(cats).map(([k,n])=>serviceRow(k,n)+`<div class="rv-actions">${btn('首选＋备用排序','provider-chain',`data-kind="${k}"`)}<small>${esc((S.data.production[k]?.fallbacks||[]).map((r,i)=>`备用 ${i+1}：${r.provider} / ${r.model}`).join(' → ')||'尚未配置备用')}</small></div>`).join(''));
}
function renderPublication() {
 $('#production-version').textContent='材料版本 '+(S.data.publication.revision||'等待 Agent 发布');
 updateHTML($('#publication-errors'),S.data.publication.errors.map(e=>`<p class="rv-notice is-error">新发布 ${esc(e.revision)} 未接收：${esc(e.message)}。当前已接收版本保持不变。</p>`).join(''));
}
function renderJobs() {
 updateHTML($('#jobs'),S.data.jobs.slice(0,8).map(j=>`<div class="rv-notice rv-row"><div><strong>${esc(j.project_name||S.data.meta.title)} · ${esc(kindName(j.kind))} · ${esc(j.status)}</strong> ${j.finished}/${j.total} · 已用 ${j.elapsed} 秒</div><div>${btn('任务明细','job',`data-id="${esc(j.id)}"`)}${!['done','error','cancelled','aborted','interrupted'].includes(j.status)?btn('停止','cancel',`data-id="${esc(j.id)}"`):''}</div></div>`).join(''));
}
function serviceRow(kind,label,custom=null) {
 const row=custom||S.data.production[kind],media=kind==='video'?'video':'image';if(!row)return '';
 const cs=S.boot.capabilities.filter(c=>c.supports.includes(media)),cap=cs.find(c=>c.id===row.provider)||{};
 const models=cap[media]?.models||[];
 const resolutions=(cap[media]?.model_options?.[row.model]?.resolutions||cap[media]?.resolutions||[]).map(String);
 const secs=media==='video'?durationsOf(row.provider,row.model).map(String):[],cur=String(row.duration??'');
 const seconds=`<label>秒数<select aria-label="${label}秒数" data-setting="duration">${option('','跟随任务',cur)}${[...new Set([...secs,...(cur?[cur]:[])])].map(v=>option(v,v+' 秒'+(secs.includes(v)?'':'（当前模型未声明）'),cur)).join('')}</select></label>`;
 const opts=cap[media]?.model_options?.[row.model]||{},shapes=((media==='image'?opts.sizes||cap.image?.sizes:opts.ratios||cap.video?.ratios)||[]).map(String);
 const shapeKey=media==='image'?'size':'ratio',shapeNow=String(row[shapeKey]??'');
 const shapeLabel=media==='video'?'比例':(opts.size_mode==='mixed'?'比例 / 像素尺寸':opts.size_mode==='ratio'||shapes.every(v=>v.includes(':'))?'比例':'尺寸');
 const shapeChoices=[...new Set([...shapes,...(shapeNow?[shapeNow]:[])])];
 const shape=opts.allow_custom_size&&media==='image'?`<label>${shapeLabel}<input aria-label="${label}${shapeLabel}" data-setting="size" list="sizes-${kind}" value="${esc(shapeNow)}" placeholder="跟随任务"><datalist id="sizes-${kind}">${shapes.map(v=>option(v,v,'')).join('')}</datalist></label>`:`<label>${shapeLabel}<select aria-label="${label}${shapeLabel}" data-setting="${shapeKey}">${option('','跟随任务',shapeNow)}${shapeChoices.map(v=>option(v,(v==='auto'?'自动比例':v)+(shapes.includes(v)?'':'（当前模型未声明）'),shapeNow)).join('')}</select></label>`;
 const advanced=[];
 const vres=media==='video'?((opts.resolutions||cap.video?.resolutions)||[]).map(String).filter(Boolean):[],vnow=String(row.resolution??'');
 const videoClarity=vres.length||vnow?`<label>清晰度<select aria-label="${label}清晰度" data-setting="resolution">${option('','跟随任务',vnow)}${[...new Set([...vres,...(vnow?[vnow]:[])])].map(v=>option(v,v+(vres.includes(v)?'':'（当前模型未声明）'),vnow)).join('')}</select></label>`:`<label>清晰度<select aria-label="${label}清晰度" disabled title="这个模型没有单独的清晰度参数，清晰度写在模型名里">${option('','由模型决定','')}</select></label>`;
 const imageClarity=resolutions.length?`<label>清晰度<select aria-label="${label}清晰度" data-setting="image_resolution">${option('','跟随任务',row.image_resolution||'')}${resolutions.map(v=>option(v,v,row.image_resolution)).join('')}</select></label>`:`<label>清晰度<select aria-label="${label}清晰度" disabled>${option('',opts.fixed_resolution?`模型固定 ${opts.fixed_resolution}`:(opts.resolution_mode==='pixels'?'由像素尺寸决定':'由模型 / 尺寸决定'),'')}</select></label>`;
 const series=opts.series_options?.length?`<label>系列<select aria-label="${label}系列" data-setting="series">${opts.series_options.map(v=>option(v,v,row.series||opts.default_series)).join('')}</select></label>`:'';
 const clarity=shape+(media==='image'?`<div>${imageClarity}${series}</div>`:`<div class="rv-pair">${videoClarity}${seconds}</div>`);
 return `<div class="rv-service-row" data-kind="${kind}"><strong>${label}</strong><select aria-label="${label}服务商" data-setting="provider">${cs.map(c=>option(c.id,c.name,row.provider)).join('')}</select><input aria-label="${label}模型" data-setting="model" list="models-${kind}" value="${esc(row.model)}"><datalist id="models-${kind}">${models.map(m=>option(m,m,'')).join('')}</datalist>${clarity}<input aria-label="${label}并发" type="number" min="1" data-setting="concurrency" value="${row.concurrency}">${S.boot.features.advanced&&advanced.length?`<div class="row-advanced"><span>参数覆盖（留空沿用任务）</span>${advanced.map(k=>`<label>${esc({ratio:'画幅',duration:'秒数',resolution:'清晰度',size:'尺寸'}[k])}<input data-override="${k}" aria-label="${label}${k}覆盖" value="${esc(row.override?.[k]||'')}"></label>`).join('')}</div>`:''}</div>`;
}
function editChain(kind) {
 const first={...S.data.production[kind]};delete first.fallbacks;
 S.chain={kind,rows:[first,...structuredClone(S.data.production[kind].fallbacks||[])]};renderChain();
}
function renderChain(){
 const c=S.chain;dialog('首选＋备用排序',`<p>从上往下依次使用。可随时修改，保存后用于下一批；已提交任务自动沿用原服务商取回。只有可切换的服务商故障才启用备用。</p>${c.rows.map((r,i)=>`<section data-route-index="${i}">${serviceRow(c.kind,i?'备用 '+i:'首选',r).replaceAll('models-'+c.kind,'models-route-'+i)}<div class="rv-actions">${i?btn('上移','route-up',`data-index="${i}"`):''}${i<c.rows.length-1?btn('下移','route-down',`data-index="${i}"`):''}${c.rows.length>1?btn('移除','route-remove',`data-index="${i}"`):''}</div></section>`).join('')}<div class="rv-actions">${btn('增加备用','route-add')}${btn('保存优先级','route-save','',true)}</div>`);
}
async function choosePath(mode,root=''){
 S.pathEdit={mode,root};const current=mode==='project'?root:mode==='data'?S.boot.paths.data_dir:S.boot.projects_dir;
 dialog(mode==='project'?'修改已有项目目录':mode==='data'?'修改程序数据目录':'选择默认生产目录',`<p>${mode==='project'?'填写新的完整项目目录。保存会移动整个项目并更新契约路径，不合并已有目录；请先结束该项目的生产批次和 Agent 文件写入。':mode==='data'?'填写新的空目录。保存会复制配置、密钥和外挂，并记住新位置；已有项目和旧数据保留。':'保存后新建项目使用此父目录，已有项目不移动。'}</p><input id="path-destination" aria-label="目标目录" value="${esc(current)}"><div class="rv-actions">${btn('浏览此目录','path-browse')}${btn('磁盘列表','path-roots')}${btn('保存目录','path-save','',true)}</div><div id="path-folders"></div>`);
}
async function browsePath(path){const d=await api('paths/browse',{path});$('#path-folders').innerHTML=`<p><code>${esc(d.path||'磁盘 / 网络路径可在上方输入')}</code></p>${d.path?btn('上一级','path-folder',`data-path="${esc(d.parent)}"`):''}${d.dirs.map(p=>`<div>${btn(p,'path-folder',`data-path="${esc(p)}"`)}</div>`).join('')}`;if(d.path)$('#path-destination').value=d.path;}
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
 const leaf = r => {const t=tasks.get(r.task_key);return `<div class="rv-leaf ${S.detail===r.id?'active':''}">${t?`<input type="checkbox" data-key="${esc(t.key)}" aria-label="选择 ${esc(r.name)}" ${t.selected?'checked':''}>`:'<span>♪</span>'}<button class="rv-leaf-name" data-action="detail" data-id="${esc(r.id)}"><strong>${esc(r.name)}</strong><small>${esc(r.id)}</small></button>${(st=>`<span class="rv-badge ${st.cls}">${esc(st.label)}</span>`)(taskState(t))}</div>`;};
 const folder = (name,key,children,resources) => {const id='g'+gi++;const keys=[...new Set(resources.map(r=>r.task_key).filter(Boolean))];S.groupKeys[id]=keys;const selected=keys.filter(k=>tasks.get(k)?.selected).length,done=keys.filter(k=>tasks.get(k)?.done).length,state=JSON.stringify([S.root,S.treeMode,S.filters.asset||'',key]),open=S.folders.get(state)??!!S.filters.asset;return `<details class="rv-folder" data-folder="${esc(key)}" ${open?'open':''} data-folder-state="${esc(state)}"><summary data-folder-toggle><input type="checkbox" data-group="${id}" aria-label="选择 ${esc(name)} 当前结果" ${keys.length&&selected===keys.length?'checked':''} ${!keys.length?'disabled':''} data-partial="${selected>0&&selected<keys.length}"><strong>${esc(name)}</strong><small title="已完成几条 · 勾选了几条（都是这一组共几条里的）">已完成 ${done}/${keys.length} · 选中 ${selected}/${keys.length}</small></summary><div class="rv-folderbody">${children}</div></details>`;};
 let html='';
 if(S.treeMode==='family') {
 const children=new Map();S.data.resources.forEach(r=>{const parent=all.has(r.parent_id)?r.parent_id:null;if(!children.has(parent))children.set(parent,[]);children.get(parent).push(r);});
 const visible=new Set(rs.map(r=>r.id));rs.forEach(r=>{let p=all.get(r.parent_id),seen=new Set();while(p&&!seen.has(p.id)){seen.add(p.id);visible.add(p.id);p=all.get(p.parent_id);}});
 function descendants(r) {return [r,...(children.get(r.id)||[]).flatMap(descendants)].filter(x=>rs.includes(x));}
 function node(r) {const cs=(children.get(r.id)||[]).filter(x=>visible.has(x.id));return cs.length?folder(r.name,'entity:'+r.id,(rs.includes(r)?leaf(r):'')+cs.map(node).join(''),descendants(r)):leaf(r);}
 const roots=(children.get(null)||[]).filter(r=>visible.has(r.id));const groups=new Map();roots.forEach(r=>{const type=r.semantic;if(!groups.has(type))groups.set(type,[]);groups.get(type).push(r);});
 html=[...groups].map(([k,v])=>folder(S.boot.semantics[k]||k,'family:'+k,v.map(node).join(''),v.flatMap(descendants))).join('');
 } else if(S.treeMode==='disk') {
 const groups=new Map();rs.forEach(r=>{const t=tasks.get(r.task_key),l=(t?.output||'声音元数据').split('/').slice(0,-1).join('/')||'声音元数据';if(!groups.has(l))groups.set(l,[]);groups.get(l).push(r);});
 html=[...groups].sort(([a],[b])=>a.localeCompare(b)).map(([k,v])=>folder(k,'disk:'+k,v.map(leaf).join(''),v)).join('');
 } else {
 const eps=r=>r.episodes.length?r.episodes:['全剧共享'],rank=Object.keys(S.boot.semantics),byEp=S.treeMode==='episode';
 const cmpType=(a,b)=>(rank.indexOf(a)+1||99)-(rank.indexOf(b)+1||99),cmpEp=(a,b)=>a==='全剧共享'?-1:b==='全剧共享'?1:a.localeCompare(b,'zh',{numeric:true});
 const outerOf=byEp?eps:r=>[r.semantic],innerOf=byEp?r=>[r.semantic]:eps,typeName=k=>S.boot.semantics[k]||k;
 const groups=new Map();
 rs.forEach(r=>outerOf(r).forEach(o=>{if(!groups.has(o))groups.set(o,new Map());const g=groups.get(o);innerOf(r).forEach(i=>{if(!g.has(i))g.set(i,[]);g.get(i).push(r);});}));
 html=[...groups].sort(([a],[b])=>byEp?cmpEp(a,b):cmpType(a,b)).map(([o,g])=>{const inner=[...g].sort(([a],[b])=>byEp?cmpType(a,b):cmpEp(a,b));
  return folder(byEp?o:typeName(o),S.treeMode+':'+o,inner.map(([i,v])=>folder(byEp?typeName(i):i,S.treeMode+':'+o+'/'+i,v.map(leaf).join(''),v)).join(''),inner.flatMap(([,v])=>v));}).join('');
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
 const detail=$('#resource-detail'),scroll=detail.scrollTop,link=linker(S.data.tasks.map(x=>x.key)),run=t&&S.data.running?.[t.key],st=taskState(t);
 if(updateHTML(detail,`<h3>${esc(r.name)}</h3><p class="rv-caption">${esc(r.id)}</p><dl><dt>分类</dt><dd>${esc(S.boot.semantics[r.semantic]||r.semantic)}</dd><dt>使用范围</dt><dd>${esc(r.episodes.join('、')||'全剧共享')}</dd></dl>${t?`<dl><dt>状态</dt><dd><span class="rv-badge ${st.cls}">${esc(st.label)}</span>${run?.msg?` ${link(run.msg)}`:''}</dd><dt>输出</dt><dd><code>${esc(t.output)}</code></dd><dt>参数</dt><dd>${esc(JSON.stringify(t.params))}</dd><dt>依赖</dt><dd>${t.dependencies.length?link(t.dependencies.join('、')):'无'}</dd></dl>${t.done?(t.kind==='video'?`<video controls preload="metadata" src="${esc(fileURL(t.output))}"></video>`:`<img loading="lazy" alt="${esc(r.name)}" src="${esc(fileURL(t.output))}">`)+fileLink(t.output,'查看原文件'):''}<div class="rv-actions">${btn('查看 / 编辑提示词','prompt',`data-key="${esc(t.key)}"`)}${btn('交给 Agent 修订','revision',`data-key="${esc(t.key)}"`)}${t.kind==='video'&&t.done?btn('重新抽卡','reroll',`data-key="${esc(t.key)}"`):''}${t.done||t.exists?btn('删除产物…','delete',`data-key="${esc(t.key)}"`):''}</div><h4>参考图（上传顺序）</h4>${refCards(t)}${t.kind==='video'?videoInputCards(t)+audioCards(t):''}${t.kind!=='video'?`<div class="rv-file-field"><span>手动放图</span>${fileButton('manual-file','选择图片…','image/png,image/jpeg,image/webp',`data-key="${esc(t.key)}" data-kind="${esc(t.kind)}"`)}<small>空位置可补图；已有产物的修改请发起新版本修订。</small></div>`:''}${t.submitted?`<div class="rv-notice">已在「${esc(t.submitted.provider)}」提交任务 <code>${esc(t.submitted.task_id)}</code>（${esc(t.submitted.submitted_at)}），结果还没取回。下次生产只按此任务 ID 取回；修改参数不会重新生成。<div class="rv-actions">${btn('放弃这个任务，下次重新提交','drop-submitted',`data-key="${esc(t.key)}"`)}</div><small>查询或下载异常仍保留此 ID；服务商明确返回失败时自动解除，显示失败，下次生产提交新任务。需要提前重新生成时可手动放弃；新任务将重新计费。</small></div>`:''}${S.data.failures.filter(f=>f.target===t.key).map(f=>`<p class="rv-notice is-error">${esc(f.raw||f.title)}</p>`).join('')}`:'<p>声音身份与连续性元数据，无独立收费任务。</p>'}`))detail.scrollTop=scroll;
}
function refCards(t) {
 const refs=[...(t.handoff_refs||t.storyboard_refs||[]),...(t.reference_images||[])];
 if(!refs.length)return '<p>无参考图</p>';
 const byOut=new Map(S.data.tasks.map(x=>[x.output,x]));
 const rows=refs.map(x=>{const rel=x.file_ref||x.url||'',src=byOut.get(rel),web=/^https?:/.test(rel);return {x,rel,src,web,st:src?taskState(src):{label:web?'外部链接':'外部文件',cls:''}};});
 const waiting=rows.filter(r=>r.src&&!r.src.done);
 const head=waiting.length?`<p class="rv-notice rv-alert">${waiting.length} 张参考图还没好：${waiting.map(r=>`<button type="button" class="rv-link" data-action="jump" data-key="${esc(r.src.key)}">${esc(r.src.key)}</button>（${esc(r.st.label)}）`).join('、')}。它们做好之前这一条不会提交。</p>`:`<p class="rv-caption">${rows.length>1?`${rows.length} 张参考图都已就绪`:'参考图已就绪'}，点击图片放大预览；查看任务请点下方入口。</p>`;
 return head+`<div class="rv-refs">${rows.map(({x,rel,src,web,st})=>{const title=x.asset_id||x.sheet_id||src?.key||rel,url=web?rel:fileURL(rel);return `<div class="rv-ref" title="${esc(rel)}">${!src||src.done?`<button type="button" class="rv-ref-preview" data-action="preview-ref" data-src="${esc(url)}" data-title="${esc(title)}" aria-label="放大预览 ${esc(title)}"><img loading="lazy" alt="${esc(title)}" src="${esc(url)}"></button>`:`<span class="rv-ref-empty">${esc(st.label)}</span>`}<small>${esc(x.image_n||x.order||'')} · ${esc(title)}</small><span class="rv-badge ${st.cls}">${esc(st.label)}</span>${src?`<button type="button" class="rv-link" data-action="jump" data-key="${esc(src.key)}">查看任务</button>`:''}</div>`;}).join('')}</div>`;
}
function previewReference(e) {
 const src=e.dataset.src;if(!src)return;
 dialog('参考图预览',`<div class="rv-image-preview"><img src="${esc(src)}" alt="${esc(e.dataset.title||'参考图')}"></div><p class="rv-caption">${esc(e.dataset.title||'参考图')}</p><a href="${esc(src)}" target="_blank" rel="noopener">打开原图</a>`);
 $('#dialog').classList.add('rv-image-dialog');
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
  $('#dialog-body').innerHTML=`<p><strong>${esc(key)}</strong> · 当前版本 ${esc(data.revision)}</p><label>任务提示词<textarea id="prompt-edit" rows="16" spellcheck="false">${esc(data.text)}</textarea></label><div id="prompt-edit-error" class="rv-notice is-error" role="alert" hidden></div><p class="rv-notice">保存后，本任务${data.affected.length>1?`及依赖它的 ${data.affected.length-1} 项下游`:''}使用新产物版本。旧产物保留，正在运行的批次不受影响；保存不会自动开始生产。缺少生产参数不影响保存，可在生产前补选。</p><details><summary>查看受影响的 ${data.affected.length} 项任务</summary>${data.affected.map(t=>`<p>${esc(S.boot.kinds[t.kind]||t.kind)} · ${esc(t.key)}</p>`).join('')}</details><div class="rv-actions">${btn('保存新版本','save-prompt','',true)}</div>`;
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
async function dropSubmittedBatch() {
 const root=S.root;
 await selectionQueue;
 if(S.root!==root)return;
 const pending=S.data.tasks.filter(t=>t.selected&&t.submitted&&!t.done);
 if(!pending.length)throw Error('请先勾选需要放弃的“已提交待取回”任务');
 const items=pending.map(t=>({key:t.key,task_id:t.submitted.task_id}));
 const visible=new Set(S.visibleKeys||[]),hidden=pending.filter(t=>!visible.has(t.key)).length;
 const names=pending.slice(0,20).map(t=>t.key).join('、')+(pending.length>20?' 等':'');
 if(!confirm(`确定放弃已勾选的 ${items.length} 个待取回任务？${hidden?`其中 ${hidden} 个被当前筛选隐藏。`:''}\n${names}\n\n不会删除已有成品，也不会取消服务商后台的任务。放弃后不立即生成；下次开始生产将按最新计划重新提交、重新计费。`))return;
 const d=await post('submitted/clear-batch',{root,items});
 if(S.root!==root)return;
 invalidatePlan();
 await loadProject(root);
 notice(`已放弃 ${d.count} 个任务${d.skipped.length?`，跳过 ${d.skipped.length} 个已完成或记录已清除的任务`:''}。请核对参数并重新查看生产计划，再开始生产。`);
}
async function preview() {
 const root=S.root,request=++S.planRequest;await selectionQueue;
 if(root!==S.root||request!==S.planRequest)return;
 const data=await api('preview',{root});if(root!==S.root||S.page!=='production'||request!==S.planRequest)return;S.plan=data;
 renderPlan();
 $('#production-plan')?.scrollIntoView({block:'nearest',behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth'});
}
function renderPlan() {
 const data=S.plan,start=$('[data-action="start"]',main);
 if(!start)return;
 start.disabled=!data||!!data.blocked.length||!data.todo;
 start.textContent=data&&!data.todo?'全部复用，无需生成':'开始生产';
 start.title=!data?'请先查看生产计划':data.blocked.length?'请先处理生产计划中的阻塞':'';
 updateHTML($('#production-plan'),data?`<div class="rv-panel rv-plan"><h3>生产计划</h3><p>已选 ${data.selected} · 复用 ${data.reuse} · 新生成 ${data.todo} · 材料 ${esc(data.revision||'未发布')}</p>${data.resuming?`<p>其中 ${data.resuming} 项上次已提交、结果还没取回，这次先续查原任务，不重新付费。</p>`:''}${data.elsewhere?.length?`<p>另有 ${data.elsewhere.length} 项正在另一批里生成，这次不重复提交；依赖它们的会等它们做好再开跑。</p>`:''}${data.rows.map(r=>`<p>${esc(S.boot.kinds[r.kind]||r.kind)}：${esc(r.provider)} / ${esc(r.model)} · ${r.count} 项 · 批内 ${r.concurrency}，服务商 ${r.provider_limit??'未设'}，全局 ${r.global_limit}${r.kind!=='video'&&r.size?` · 比例按 ${esc(r.size)} 传给服务商`:''}${r.kind==='video'&&r.ratio?` · 画幅按 ${esc(r.ratio)} 传给服务商`:''}${r.kind==='video'&&r.resolution?` · 清晰度按 ${esc(r.resolution)} 传给服务商`:''}${r.kind==='video'&&r.duration?` · 按 ${esc(r.duration)} 秒传给服务商（提示词仍是按原秒数写的）`:''}</p>`).join('')}<p>技术失败（网络抖动、服务商忙）自动重试最多 ${esc(data.max_retry)} 次；参数错误不重试。${!data.soften_rounds?'被审核拒绝后不自动改写（改写轮数设成了 0）。':data.llm_ready?`被审核拒绝时用 LLM 改写提示词后重发，最多 ${esc(data.soften_rounds)} 轮。`:'被审核拒绝后的自动改写没开：改写用的 LLM 还没配（设置 → 提示词改写 LLM）。'}</p>${data.blocked.map(b=>`<p class="rv-notice is-error"><strong>${esc(b.key)}</strong>：${esc(b.reason)}</p>`).join('')}<p class="rv-caption">开始后保存本批任务、参数和提示词快照。未生成的必要上游会先生产；上游失败时下游不会提交。</p></div>`:'');
}
const OUT_CATS=[['fixed','固定资产'],['state','连续状态资产'],['scstate','场景状态图'],['board','交接板'],['region','交接板区域图'],['video','分段视频'],['storyboard','旧项目故事板']];
const outCat=(t,r)=>t.kind==='video'?'video':['scstate','board','region','storyboard'].includes(t.kind)?t.kind:(r?.semantic==='CT'||/连续状态/.test(t.output)?'state':'fixed');
function outputs() {
 const kind=OUT_CATS.some(([k])=>k===S.filters.outputKind)?S.filters.outputKind:'';
 main.innerHTML=heading(S.data.meta.title,'产物与验收','查看实际文件；文件校验通过后仍需检查画面、连续性与声音。',btn('回到生产树','production'))+
 `<div class="rv-toolbar"><input id="output-search" type="search" aria-label="搜索产物" placeholder="搜索名称、文件名或任务编号" value="${esc(S.filters.output||'')}"><select id="output-kind" aria-label="产物分类">${option('','全部分类',kind)}${OUT_CATS.map(([k,n])=>option(k,n,kind)).join('')}</select>${S.boot.features.post?btn('合成成片（单集 / 多集 / 全集）','assemble')+btn('字幕识别与烧录','subtitle'):''}${S.boot.features.legacy_import&&S.data.publication.legacy?btn('旧材料导入','import'):''}</div><div id="output-list"></div><div id="master-list"></div>`;
 outputList();loadMasters();
}
async function loadMasters() {
 const root=S.root,target=$('#master-list');
 try {const d=await get('files',{root,sub:'06_成片'});if(root!==S.root||target!==$('#master-list'))return;
 updateHTML(target,'<h3>成片目录</h3>'+d.items.filter(x=>!x.dir).map(x=>`<p>${fileLink('06_成片/'+x.name,x.name)}</p>`).join(''));
 }catch(e){if(root===S.root&&target===$('#master-list'))notice(e.message,true);}
}
function outputList() {
 const q=(S.filters.output||'').toLowerCase(),type=OUT_CATS.some(([k])=>k===S.filters.outputKind)?S.filters.outputKind:'';
 const res=new Map(S.data.resources.filter(r=>r.task_key).map(r=>[r.task_key,r])),rank=Object.keys(S.boot.semantics);
 const rows=S.data.tasks.filter(t=>t.done&&`${t.key} ${t.output} ${res.get(t.key)?.name||''}`.toLowerCase().includes(q))
  .map(t=>({t,r:res.get(t.key),cat:outCat(t,res.get(t.key))})).filter(x=>!type||x.cat===type);
 const card=({t,r})=>`<article class="rv-panel rv-outcard">${t.kind==='video'?`<video controls preload="metadata" src="${esc(fileURL(t.output))}"></video>`:`<a href="${esc(fileURL(t.output))}" target="_blank" rel="noopener"><img loading="lazy" src="${esc(fileURL(t.output))}" alt="${esc(r?.name||t.key)}"></a>`}<strong>${esc(r?.name||t.key)}</strong><small>${esc(t.key)} · ${esc(t.output)}</small><div class="rv-actions">${fileLink(t.output,'原文件')}${btn('在生产树中查看','jump',`data-key="${esc(t.key)}"`)}${t.kind==='video'?btn('重新抽卡','reroll',`data-key="${esc(t.key)}"`):''}${btn('删除…','delete',`data-key="${esc(t.key)}"`)}</div></article>`;
 const subOf=x=>x.cat==='video'?(x.t.episode||'未指定集'):['fixed','state'].includes(x.cat)?(x.r?.semantic||''):'';
 const subName=(cat,s)=>cat==='video'?s:(S.boot.semantics[s]||s||'未分类');
 const cmp=cat=>cat==='video'?(a,b)=>a.localeCompare(b,'zh',{numeric:true}):(a,b)=>(rank.indexOf(a)+1||99)-(rank.indexOf(b)+1||99);
 const html=OUT_CATS.map(([k,n])=>{const items=rows.filter(x=>x.cat===k);if(!items.length)return '';
  const groups=new Map();items.forEach(x=>{const s=subOf(x);if(!groups.has(s))groups.set(s,[]);groups.get(s).push(x);});
  const many=groups.size>1||[...groups.keys()][0]!=='';
  return `<section class="rv-outgroup"><h3>${esc(n)} <small>${items.length} 项</small></h3>${[...groups].sort(([a],[b])=>cmp(k)(a,b)).map(([s,v])=>`${many?`<h4>${esc(subName(k,s))} <small>${v.length} 项</small></h4>`:''}<div class="output-grid">${v.map(card).join('')}</div>`).join('')}</section>`;}).join('');
 updateHTML($('#output-list'),html||'<p>尚无匹配的有效产物。生成成功后会自动出现。</p>');
}
function settings() {
 main.innerHTML=heading('工作台设置','生产必需配置，始终可见','所有已接入服务商继续可用。设置中只保留图片、视频与本机生产能力。')+
 `<div class="rv-toolbar">${[['providers','服务商与密钥'],['limits','并发与重试'],['llm','提示词改写 LLM'],['upload','参考图上传'],['features','功能包与路径']].map(([v,l])=>btn(l,'settings-tab',`data-tab="${v}"`,v===S.tab)).join('')}</div><div id="settings-content"></div>`;
 if(S.tab==='providers')providerForm();else if(S.tab==='limits')limitForm();else if(S.tab==='llm')llmForm();else if(S.tab==='upload')uploadForm();else featureForm();
}
function llmForm() {
 const l=S.boot.llm||{};
 $('#settings-content').innerHTML=`<form id="llm-form" class="rv-panel"><h3>被审核拒绝后改写提示词的 LLM</h3><p>工作台里 LLM 只做这一件事：图片或视频被服务商的内容审核拒绝时，把服务商的原话和提示词交给它改写一版再重发；改写前后全文记在项目的 <code>03_提示词/自动改写/</code>。它不做剧本分析，也不改没被拒的提示词。</p><div class="rv-fields">${field('Base URL',input('base_url',l.base_url||'','url','placeholder="例如 https://api.paisio.online"'))}${field('模型',input('model',l.model||'','text','placeholder="例如 claude-sonnet-5"'))}${field(`API Key · ${l.api_key_set?'已配置':'未配置'}`,`<div class="secret-field"><input type="password" name="api_key" autocomplete="new-password" placeholder="${l.api_key_set?'留空保持已存密钥':'填写密钥'}"><button type="button" class="rv-link" data-action="reveal">显示</button></div>`)}${field('被审核拒绝后改写几轮',input('soften_rounds',l.rounds??12,'number','min="0" required'),'默认 12 轮（四级改写，每级 3 轮），填 0 关掉。每一轮都会先检查改写结果，删了内容或动了身份映射的不会发出去。')}</div><p class="rv-caption">当前状态：${l.ready?'LLM 凭据齐全，被拒时会自动改写':'LLM 还没配好，被拒时不会改写，照常报失败'}。Key 留空不改；保存不等于服务商鉴权成功。</p>${btn('保存','save-llm','',true)}</form>`;
}
function providerForm() {
 const cap=S.boot.capabilities.find(c=>c.id===S.provider)||S.boot.capabilities[0];if(!cap){$('#settings-content').textContent='没有可用服务商';return;}
 S.provider=cap.id;const saved=S.boot.providers_public[cap.id]||{},status=S.boot.provider_keys_configured[cap.id]||{};
 let keys=cap.key_fields||[];if(cap.id==='chaomo')keys=[['image_1k_api_key','image_1k','TH 分组 Key（原图片 1K）','对应 -th 模型；支持的分辨率由模型和清晰度选项决定。'],['image_4k_api_key','image_4k','Native 原生 Key（原图片 4K）','对应 Native 模型；1K、2K、4K 均使用这把 Key。'],['video_api_key','video','视频 Key']];
 if(!keys.length)keys=[['api_key','api_key','API Key / 多账号凭据']];
 const keyField=(name,label,isSet,help='')=>field(`${esc(label)} · ${isSet?'已配置':'未配置'}`,`<div class="secret-field"><input type="password" name="${esc(name)}" autocomplete="new-password" placeholder="${isSet?'留空保持已存密钥':'填写密钥'}"><button type="button" class="rv-link" data-action="reveal">显示</button></div>`,esc(help));
 let credential=keys.map(([k,slot,label,help])=>keyField(k,label,status[slot]||status[k]||(k==='api_key'&&(status.image||status.video)),help)).join('');
 const af=cap.account_form||{},ac=S.boot.provider_accounts[cap.id]||{shared:{},accounts:[]};
 if(af.per?.length){
 const fields=(rows,values,prefix)=>rows.map(([k,l,secret,note])=>secret?keyField(prefix+'.'+k,l,values[k+'__set'],note):field(esc(l),input(prefix+'.'+k,values[k]||''),esc(note))).join('');
 credential=`<h4 class="rv-span">${esc(af.shared_label||'共用设置')}</h4>${fields(af.shared||[],ac.shared||{},'shared')}<div class="rv-span" id="accounts">${(ac.accounts.length?ac.accounts:[{}]).map((r,i)=>`<fieldset data-account="${i}"><legend>账号 ${i+1}</legend><div class="rv-fields">${fields(af.per,r,'account.'+i)}</div></fieldset>`).join('')}</div>${btn('增加账号','add-account')}`;
 }
 $('#settings-content').innerHTML=`<div class="rv-panel"><div class="rv-fields rv-provider-choice"><label class="rv-span">服务商<select id="provider-picker">${S.boot.capabilities.map(c=>option(c.id,c.name,cap.id)).join('')}</select></label></div><form id="provider-form"><p class="rv-caption">${status.source==='managed'?'当前调用使用后台分配的 Key 和地址；下方填写的是个人备用配置，不会覆盖后台分配。':'当前调用使用个人配置；后台分配后自动优先使用后台 Key。'}</p><div class="rv-fields">${field('Base URL',input('base_url',saved.base_url||cap.default_base_url||'','url'))}${field('等待结果上限（秒）',input('poll_timeout',saved.poll_timeout||2400,'number','min="60" max="7200"'))}${field('轮询间隔（秒）',input('poll_interval',saved.poll_interval||5,'number','min="2" max="60"'))}${credential}<label class="rv-span">已实测的模型时长档位<textarea name="durations" rows="2" placeholder="模型ID=4,5,10,15,30；只填写已经确认支持的档位">${esc(saved.durations||'')}</textarea></label></div><p class="rv-caption">密钥留空不改；保存状态不等于服务商鉴权成功。多账号可按客服提供的完整凭据格式填写。已有密钥不会回显给浏览器。</p><div class="rv-actions">${btn('保存服务商设置','save-provider','',true)}${btn('检查连接 / 刷新模型','refresh-models')}${btn('刷新后台配置','refresh-cloud-config')}${btn('重新扫描服务商','reload-providers')}${fileButton('provider-file','导入服务商文件…','.py','',false)}</div></form><details class="rv-disclosure"><summary>当前模型能力与来源</summary><p>${esc(cap.model_catalog?.source||'服务商适配器声明')}</p>${['image','video'].filter(m=>cap[m]).map(m=>`<h4>${m==='image'?'图片':'视频'}</h4><p>${esc((cap[m].models||[]).join(' · '))}</p>`).join('')}</details></div>`;
}
function limitForm() {$('#settings-content').innerHTML=`<form id="limits-form" class="rv-panel"><h3>所有项目共享的并发额度与重试</h3><div class="rv-fields">${field('失败自动重试次数',input('max_retry',S.boot.retry??2,'number','min="0" required'),'网络抖动、服务商忙这类技术失败自动再试几次，不设上限。审核拦截、参数错误不重试；已经提交出去的任务重试时续查原任务，不重新付费。')}${field('全局在途总上限',input('global',S.boot.limits.global,'number','min="1" required'))}${S.boot.capabilities.map(c=>field(esc(c.name),input('provider.'+c.id,S.boot.limits.per_provider[c.id]||4,'number','min="1" required'))).join('')}</div><p class="rv-caption">运行中降低上限不会撤回在途任务。新任务等额度释放后再提交；按账号串行的服务商还受账号数约束。</p>${btn('保存并发与重试','save-limits','',true)}</form>`;}
function uploadForm() {const u=S.boot.upload;$('#settings-content').innerHTML=`<form id="upload-form" class="rv-panel"><h3>参考图对象存储</h3><p>仅收公网图片地址的服务商使用这里的配置；沿用原程序的上传缓存与图片检查。</p><div class="rv-fields">${[['endpoint','S3 Endpoint'],['bucket','Bucket'],['public_base_url','公开访问域名'],['region','Region'],['prefix','对象路径前缀']].map(([k,l])=>field(l,input(k,u[k]||''))).join('')}${field('Access Key · '+(u.access_key_set?'已配置':'未配置'),input('access_key','','password','autocomplete="new-password" placeholder="留空不改"'))}${field('Secret Key · '+(u.secret_key_set?'已配置':'未配置'),input('secret_key','','password','autocomplete="new-password" placeholder="留空不改"'))}</div>${btn('保存上传设置','save-upload','',true)}</form>`;}
function featureForm() {$('#settings-content').innerHTML=`<form id="projects-path-form" class="rv-panel"><h3>默认生产目录</h3>${field('新项目的生产父目录',input('projects_dir',S.boot.projects_dir||'','text','required'),'填写完整路径，例如 D:\\项目。保存后新建项目自动填入，也可在创建向导中临时修改。')}<p class="rv-caption">只改变新项目的默认位置。已有项目保留在原目录，并继续显示在项目列表中。</p>${btn('选择文件夹…','default-path')} ${btn('保存默认目录','save-projects-path','',true)}</form><div class="rv-panel"><h3>程序数据目录</h3><code>${esc(S.boot.paths.data_dir)}</code><p>配置、密钥和外挂可以迁到新目录；已有项目保持原位置，旧数据保留。运行批次结束后可修改。</p>${btn('修改数据目录…','data-path')}</div><div class="rv-panel"><h3>功能包</h3><p>密钥、基础并发、当前线程、建议并发、开始 / 停止、核心报错和产物查看固定保留。</p>${Object.entries(S.boot.feature_labels).map(([k,l])=>`<div class="rv-feature"><strong>${esc(l)}</strong><label><input type="checkbox" data-feature="${k}" ${S.boot.features[k]?'checked':''}> 显示页面入口</label></div>`).join('')}<h3>数据与路径</h3><p class="rv-caption">program_dir 是 EXE 所在位置，移动 EXE 后改变；bundle_dir 是每次启动的临时解压位置。config_path 随数据目录改变。已有项目的位置可在项目列表点击“修改目录”。</p><dl>${Object.entries(S.boot.paths).filter(([k,v])=>typeof v==='string'&&/dir|path/.test(k)).map(([k,v])=>`<dt>${esc(k)}</dt><dd><code>${esc(v)}</code></dd>`).join('')}</dl><p class="rv-caption">新项目可在向导指定生产父目录。更新程序时保留配置和项目目录；功能包只控制入口，不删除已有项目文件。</p></div>`;}
async function importProvider(file) {
 const d=await api('providers/import',{filename:file.name,content_b64:await readFile(file)});
 if(!d.ok)throw Error(`「${file.name}」加载失败，${d.restored?'已恢复原来的同名文件':'没有留在服务商目录里'}：\n${d.errors.map(x=>x.error).join('\n')}`);
 const names=d.providers.map(p=>p.name).join('、')||'（没有识别到服务商）';
 await reloadBoot();S.provider=d.providers[0]?.id||S.provider;settings();
 notice(`已导入 ${names}，保存在 ${d.file}${d.backup?`；原来的同名文件备份为 ${d.backup}`:''}${d.warnings.length?`\n注意：${d.warnings.flatMap(w=>w.problems).join('；')}`:''}`,false);
}
async function saveProvider() {const f=$('#provider-form');if(!f.reportValidity())return;const data=Object.fromEntries(new FormData(f)),cap=S.boot.capabilities.find(c=>c.id===S.provider);if(cap.account_form?.per?.length){data.account_form={shared:{},accounts:[]};Object.keys(data).forEach(k=>{if(k.startsWith('shared.')){data.account_form.shared[k.slice(7)]=data[k];delete data[k];}else if(k.startsWith('account.')){const [,i,field]=k.split('.');data.account_form.accounts[i]||={};data.account_form.accounts[i][field]=data[k];delete data[k];}});}await api('settings',{providers:{[S.provider]:data}});f.reset();await reloadBoot();settings();notice('已保存服务商设置，密钥未回显。');}
const activeJob = j => !['done','error','cancelled','aborted','interrupted'].includes(j.status);
const jobProject = j => j.project_name||S.boot.projects.find(p=>p.root===j.project_root)?.title||j.project_root?.split(/[\\/]/).pop()||'未命名项目';
function renderGlobalJobs() {
 const jobs=S.runtime.jobs||[],active=jobs.filter(activeJob),names=[...new Set(active.map(jobProject))];
 const label=$('#live-projects');label.textContent=names.length?names.slice(0,2).join('、')+(names.length>2?` 等 ${names.length} 个项目`:''):'暂无运行项目';label.title=names.join('\n');
 $('#global-job-count').textContent=`运行 ${active.length} 批 · 本次启动记录 ${jobs.length} 批`;
 updateHTML($('#global-jobs'),jobs.map(j=>`<div class="rv-global-job" data-job-id="${esc(j.id)}"><div><strong>${esc(jobProject(j))}</strong><small title="${esc(j.project_root)}">${esc(j.project_root||'项目路径未记录')}</small><span>${esc(kindName(j.kind))} · ${esc(j.status)} · ${j.finished}/${j.total} · 已用 ${j.elapsed} 秒</span></div><div class="rv-actions">${j.project_root?btn('任务明细','global-job',`data-id="${esc(j.id)}" data-root="${esc(j.project_root)}"`)+btn('打开项目','job-project',`data-root="${esc(j.project_root)}"`):''}</div></div>`).join('')||'<p class="rv-caption">本次启动暂无任务。各项目的历史记录仍在生产页查看。</p>');
}
async function refreshRuntime() {S.runtime=await api('runtime');const d=S.runtime,u=d.usage,g=d.gates;$('#live-inflight').textContent=`${g.global_inflight} / ${g.global_limit}`;$('#live-jobs').textContent=d.active_jobs+' 批';$('#live-threads').textContent=u.threads??'未知';$('#live-advice').textContent=d.advice.limit==null?'待采样':'≤ '+d.advice.limit;const gb=v=>v==null?'未知':(v/1073741824).toFixed(1)+' GB';$('#live-usage').textContent=`CPU ${u.cpu_percent??'未知'}% · 系统内存 ${gb(u.mem_used)} / ${gb(u.mem_total)} · 本程序 ${u.proc_rss==null?'未知':(u.proc_rss/1048576).toFixed(0)+' MB'}`;$('#live-basis').textContent=d.advice.basis.join('；');renderGlobalJobs();
 for(const id of [...S.watchJobs]){const j=(d.jobs||[]).find(x=>x.id===id);if(!j||activeJob(j))continue;S.watchJobs.delete(id);const c=j.counts||{};notice(`${jobProject(j)} 的成片合成${j.status==='cancelled'?'已停止':'结束'}：${c.ok||0} 集合好${c.failed?`，${c.failed} 集失败`:''}${c.cancelled?`，${c.cancelled} 集没合`:''}。成片在 06_成片/，点「任务明细」可以逐集打开。`,!!c.failed);}}
async function jobDetail(id,root=S.root,refresh=false) {
 const request=++S.jobRequest,d=await get('job',{root,id});
 if(request!==S.jobRequest||(refresh&&(!$('#dialog').open||S.job!==id||S.jobRoot!==root)))return;
 const name=jobProject({...d,project_root:root}),title=`任务明细 · ${name} · ${d.status}`;
 const link=linker(root===S.root&&S.data?S.data.tasks.map(t=>t.key):[]);
 const table=`<p class="rv-caption">${esc(root)} · ${esc(id)}<br>收起仅关闭明细，不会停止任务；点窗口外面也能收起。${d.max_retry!=null?`<br>技术失败自动重试最多 ${esc(d.max_retry)} 次；审核拦截、参数错误不重试。`:''}${link!==esc&&root===S.root?'<br>点任务名或说明里的任务名，跳到生产树里那一项。':''}</p><table class="rv-job-table"><thead><tr><th>任务</th><th>状态</th><th>重试</th><th>说明</th></tr></thead><tbody>${Object.entries(d.items).map(([k,v])=>`<tr><td>${link(k)}</td><td>${esc(STATE_NAMES[v.state]||v.state)}</td><td>${v.attempts?`${esc(v.attempts)} / ${esc(d.max_retry??'?')}`:'—'}</td><td>${link(v.msg)}${v.output&&v.state==='ok'&&root===S.root?` ${fileLink(v.output,'打开')}`:''}</td></tr>`).join('')}</tbody></table>${S.boot.features.logs?`<pre class="job-log">${esc(d.logs.join('\n'))}</pre>`:''}`;
 if(refresh){$('#dialog-title').textContent=title;const body=$('#dialog-body'),top=body.scrollTop,left=body.scrollLeft,logTop=$('.job-log')?.scrollTop||0;body.innerHTML=table;body.scrollTop=top;body.scrollLeft=left;if($('.job-log'))$('.job-log').scrollTop=logTop;}
 else{dialog(title,table);$('#dialog').classList.add('rv-job-dialog');$('#dialog-close').textContent='收起明细';$('#dialog-close').setAttribute('aria-label','收起任务明细');$('#dialog-body').scrollTop=0;$('#dialog-body').scrollLeft=0;S.job=id;S.jobRoot=root;}
}
async function readFile(file) {if(file.size>40*1024*1024)throw Error('文件超过 40MB');const data=await new Promise((resolve,reject)=>{const r=new FileReader();r.onload=()=>resolve(r.result);r.onerror=()=>reject(Error('读取文件失败'));r.readAsDataURL(file);});return data.split(',')[1];}
const actions={
 'preview-ref':previewReference,
 'back-top':backToTop,
 'new':()=>beginWizard(), 'batch':()=>{beginWizard();S.wizard.batch='项目一\n项目二';wizard();},
 'back-projects':()=>show('projects'), 'production':()=>show('production'),'handoff':()=>show('handoff'),
 'open':async e=>{S.root=e.dataset.root;S.data=null;S.detail='';picker();await loadProject(S.root,false);show(S.data.tasks.length?'production':'handoff');},
 'archive':async e=>{const on=e.dataset.archived==='true';await api('archive',{root:e.dataset.root,archived:on});await reloadBoot();projects();notice(on?'已归档：项目文件都还在，只是不在列表里显示。要找回，在「项目状态」里选「已归档」，点「恢复」。':'已恢复，项目回到列表里。');},
 'wizard-step':e=>{wizardSave();S.step=+e.dataset.step;wizard();},
 'wizard-prev':()=>{wizardSave();S.step=Math.max(0,S.step-1);wizard();},
 'wizard-next':()=>{if(!$('#wizard-form').reportValidity())return;wizardSave();S.step++;wizard();},
 'wizard-create':wizardCreate, 'refresh':()=>loadProject(),
 'handoff-export':async()=>{const d=await post('handoff/export');S.remote={root:S.root,html:`交接包已生成：${d.files} 个文件，${Math.max(1,Math.round(d.bytes/1024))} KB，本次新版本号从 ${esc(d.next_revision)} 开始。${fileLink(d.rel,'下载交接包')}<br><code>${esc(d.file)}</code>`};handoff();},
 'copy-template':async()=>{await navigator.clipboard.writeText(S.data.template);notice('已复制完整 Agent 模板。');},
 'source':async()=>{const root=S.root;const d=await get('prompt',{root,rel:'01_剧本与分段/原始素材.txt'});if(root!==S.root)return;dialog('项目原文',`<textarea id="source-edit" rows="16">${esc(d.text)}</textarea>${btn('保存原文','save-source','',true)}`);},
 'save-source':async()=>{await post('source',{source:$('#source-edit').value});closeDialog();notice('已保存原文，Agent 可直接读取。');},
 'providers':()=>{S.tab='providers';show('settings');},
 'settings-tab':e=>{S.tab=e.dataset.tab;settings();},
 'reveal':e=>{const i=e.parentElement.querySelector('input');i.type=i.type==='password'?'text':'password';e.textContent=i.type==='password'?'显示':'隐藏';},
 'add-account':()=>{const cap=S.boot.capabilities.find(c=>c.id===S.provider),i=$$('[data-account]').length,fs=document.createElement('fieldset');fs.dataset.account=i;fs.innerHTML=`<legend>账号 ${i+1}</legend><div class="rv-fields">${cap.account_form.per.map(([k,l,secret,note])=>field(esc(l),input('account.'+i+'.'+k,'',secret?'password':'text'),esc(note))).join('')}</div>`;$('#accounts').append(fs);},
 'save-provider':saveProvider,
 'refresh-cloud-config':async()=>{await refreshCloudConfiguration();},
 'refresh-models':async()=>{const d=await api('models/refresh',{provider:S.provider});await reloadBoot();settings();notice(d.rows?.map(r=>`${r.provider}：${r.ok?'已刷新 '+r.count+' 个模型':r.msg||'未能刷新'}`).join('；')||d.msg, d.rows?.some(r=>!r.ok));},
 'reload-providers':async()=>{await api('providers/reload',{});await reloadBoot();settings();notice('服务商已重新扫描。');},
 'default-path':()=>choosePath('default'),
 'data-path':()=>choosePath('data'),
 'project-path':e=>choosePath('project',e.dataset.root),
 'path-browse':()=>browsePath($('#path-destination').value),
 'path-roots':()=>browsePath(''),
 'path-folder':e=>browsePath(e.dataset.path),
 'path-save':async()=>{const p=S.pathEdit,value=$('#path-destination').value;const d=await api(p.mode==='project'?'paths/project':p.mode==='data'?'paths/data':'paths/projects',p.mode==='project'?{root:p.root,destination:value}:p.mode==='data'?{data_dir:value}:{projects_dir:value});if(p.mode==='project'&&S.root===p.root){S.root=d.root;await loadProject(d.root,false);}await reloadBoot();render();closeDialog();notice('目录已保存；项目和配置按新位置使用。');},
 'provider-chain':e=>editChain(e.dataset.kind),
 'route-add':()=>{const r={...S.chain.rows.at(-1)};delete r.fallbacks;S.chain.rows.push(r);renderChain();},
 'route-up':e=>{const i=+e.dataset.index,a=S.chain.rows;[a[i-1],a[i]]=[a[i],a[i-1]];renderChain();},
 'route-down':e=>{const i=+e.dataset.index,a=S.chain.rows;[a[i+1],a[i]]=[a[i],a[i+1]];renderChain();},
 'route-remove':e=>{S.chain.rows.splice(+e.dataset.index,1);renderChain();},
 'route-save':async()=>{const c=S.chain,setting={...c.rows[0],fallbacks:c.rows.slice(1)};await post('production-settings',{settings:{[c.kind]:setting}});invalidatePlan();await loadProject();closeDialog();notice('首选和备用顺序已保存；正在运行的批次及原任务 ID 不受影响。');},
 'save-projects-path':async()=>{const f=$('#projects-path-form');if(!f.reportValidity())return;await api('paths/projects',Object.fromEntries(new FormData(f)));await reloadBoot();settings();notice('默认生产目录已保存，新建项目自动使用；已有项目仍在原位置。');},
 'save-limits':async()=>{const f=$('#limits-form');if(!f.reportValidity())return;const d=Object.fromEntries(new FormData(f)),per={};Object.entries(d).filter(([k])=>k.startsWith('provider.')).forEach(([k,v])=>per[k.slice(9)]=+v);await api('settings',{limits:{global:+d.global,per_provider:per},defaults:{max_retry:+d.max_retry}});await reloadBoot();notice('已保存共享并发上限和失败自动重试次数，下次开始的批次生效。');await refreshRuntime();},
 'save-llm':async()=>{const f=$('#llm-form');if(!f.reportValidity())return;const d=Object.fromEntries(new FormData(f));await api('settings',{llm:{base_url:d.base_url,model:d.model,api_key:d.api_key},defaults:{soften_rounds:+d.soften_rounds}});await reloadBoot();settings();notice(S.boot.llm?.ready?'已保存。被审核拒绝时会用这个 LLM 改写提示词后重发。':'已保存，但 LLM 的地址或密钥还不齐，被拒时不会改写。',!S.boot.llm?.ready);},
 'save-upload':async()=>{await api('settings',{upload:Object.fromEntries(new FormData($('#upload-form')))});$('#upload-form').reset();await reloadBoot();settings();notice('已保存参考图上传设置。');},
 'jump':e=>jumpTo(e.dataset.key),
 'bulk-place':()=>{dialog('批量放外部图片',bulkIntro());S.bulk=null;},
 'bulk-run':runBulk,
 'delete':e=>openDelete(e.dataset.key),
 'delete-all':()=>{if(S.del&&!S.del.loading){for(const x of S.del.items)S.del.choices[x.key]=x.exists&&!S.data?.running?.[x.key];renderDelete();}},
 'delete-none':()=>{if(S.del&&!S.del.loading){for(const x of S.del.items)S.del.choices[x.key]=false;renderDelete();}},
 'audio-remove':async(el)=>{await post('audio/remove',{key:el.dataset.key,file_ref:el.dataset.ref});invalidatePlan();await loadProject();notice('已移除音频引用，正在运行的批次不受影响。');},
 'video-reference-add':async(el)=>{const input=$('#video-reference-url');if(!input.reportValidity())return;const url=input.value.trim();if(!url)throw Error('请先填写参考视频 HTTPS 链接');await post('video-reference/add',{key:el.dataset.key,url});invalidatePlan();await loadProject();notice('参考视频已添加，仅影响下一次新生成。');},
 'video-reference-remove':async(el)=>{await post('video-reference/remove',{key:el.dataset.key,url:el.dataset.ref});invalidatePlan();await loadProject();notice('参考视频引用已移除，正在运行的批次不受影响。');},
 'delete-run':async()=>{const d=S.del;if(!d||d.loading)return;const keys=deleteFiles().map(x=>x.key);if(!keys.length)return;const r=await api('delete',{root:d.root,keys});closeDialog();invalidatePlan();if(S.root===d.root)await loadProject();notice(`已把 ${r.moved.length} 个文件移到 ${r.folder}。这些任务变回待生产，要重做就勾上再生产。`);},
 'asm-all':async()=>{S.asm.chosen=[...S.asm.episodes];await loadAssemble();},
 'asm-none':()=>{S.asm.chosen=[];S.asm.rows=[];renderAssemble();},
 'detail':e=>{S.detail=e.dataset.id;$$('[data-action="detail"]',main).forEach(b=>b.closest('.rv-leaf')?.classList.toggle('active',b.dataset.id===S.detail));renderDetail();},
 'select-visible':()=>setSelection(S.visibleKeys,true),'clear-visible':()=>setSelection(S.visibleKeys,false),'select-all':()=>setSelection(S.data.tasks.map(t=>t.key),true),
 'dependencies':async()=>{await post('dependencies');await loadProject();notice('已补选必要上游；有效成品继续复用。');},
 'drop-submitted-batch':dropSubmittedBatch,
 'drop-submitted':async e=>{if(!confirm('放弃后，下次生产会重新提交这一段并重新计费。确定服务商那边已查不到、或你已手动取回？'))return;await post('submitted/clear',{key:e.dataset.key});await loadProject();notice('已放弃这个任务，下次生产会重新提交。');},
 'prompt':e=>editPrompt(e.dataset.key),
 'save-prompt':savePrompt,
 'revision':e=>dialog('交给 Agent 定向修订',`<p>${esc(e.dataset.key)}</p><textarea id="revision-note" rows="5" placeholder="说明实际问题、服务商反馈和希望修改的地方"></textarea>${btn('生成修订请求','save-revision',`data-key="${esc(e.dataset.key)}"`,true)}`),
 'save-revision':async e=>{const d=await post('revision',{keys:[e.dataset.key],note:$('#revision-note').value});dialog('Agent 修订请求',`<textarea id="revision-result" readonly rows="14">${esc(d.text)}</textarea>${fileLink(d.rel,'打开修订请求')}`);},
 'preview':preview,
 'start':async()=>{if(!S.plan)throw Error('先查看生产计划');const d=await post('start',{revision:S.plan.revision,only:S.plan.keys});if(!d.ok)throw Error(d.blocked.map(x=>x.key+'：'+x.reason).join('\n'));await loadProject();notice(d.message||'生产批次已启动。这一批的勾选已取消，再点「开始生产」不会重复提交；失败的要重做，用状态筛选「失败」再勾上。');},
 'cancel':async e=>{await post('cancel',{id:e.dataset.id});await loadProject();notice('已请求停止，等待在途调用退出。');},
 'job':e=>jobDetail(e.dataset.id),
 'global-job':e=>jobDetail(e.dataset.id,e.dataset.root),
 'job-project':async e=>{$('#runtime').open=false;await actions.open(e);show('production');},
 'assemble':async()=>{const eps=[...new Set(S.data.tasks.filter(t=>t.kind==='video').map(t=>t.episode||''))].sort((a,b)=>a.localeCompare(b,'zh',{numeric:true}));S.asm={root:S.root,chosen:eps.length?[eps[0]]:[],rows:[],extras:[],episodes:eps};dialog('合成成片','<p>正在读取清单…</p>');await loadAssemble();},
 'asm-refresh':()=>loadAssemble(true),
 'asm-up':e=>{const i=+e.dataset.i,r=S.asm.rows;if(i>0)[r[i-1],r[i]]=[r[i],r[i-1]];renderAssemble();},
 'asm-down':e=>{const i=+e.dataset.i,r=S.asm.rows;if(i<r.length-1)[r[i+1],r[i]]=[r[i],r[i+1]];renderAssemble();},
 'asm-remove':e=>{S.asm.rows.splice(+e.dataset.i,1);renderAssemble();},
 'asm-insert':()=>{const x=S.asm.extras.find(x=>x.rel===$('#asm-extra')?.value);if(!x)throw Error('先选一个要插入的视频');S.asm.rows.push({rel:x.rel,key:x.name,task:false,ok:true,include:true});renderAssemble();},
 'subtitle':async()=>{const d=await get('post-options',{root:S.root});const o=d.options;dialog('字幕识别与烧录',`<form id="subtitle-form"><div class="rv-fields">${field('成片',`<select name="file">${d.files.map(f=>option(f,f,'')).join('')}</select>`)}${field('识别引擎',select('asr',['bijian','jianying','faster-whisper','whisper-cpp'],o.asr))}${field('识别语言',input('language',o.language))}${field('字幕模式',select('subtitle_mode',['hard','soft'],o.subtitle_mode))}${field('字幕样式',`<select name="style">${option('','默认样式',o.style)}${d.styles.map(s=>option(s.name,s.name,o.style)).join('')}</select>`)}</div><p>已有同名 SRT 时直接复用，可先交给 Agent 修订；否则先识别再合成。hard 为烧录字幕，soft 为软字幕轨。保留原成片，生成带字幕的新文件。</p><p class="rv-caption">必剪 / 剪映使用相应识别服务；faster-whisper / whisper-cpp 使用本机模型。文字优化和翻译交给 Agent。</p>${btn('开始字幕处理','subtitle-run',d.files.length?'':'disabled',true)}</form>`);},
 'subtitle-run':async()=>{const d=await post('subtitle',Object.fromEntries(new FormData($('#subtitle-form'))));closeDialog();await loadProject(S.root,false);show('production');notice('字幕后期已启动，可在任务明细查看或停止。');},
 'assemble-run':async()=>{const items=S.asm.rows.filter(r=>r.include&&r.ok).map(r=>r.rel);if(!items.length)throw Error('清单里至少勾选一段有效视频');const d=await post('assemble',{episodes:S.asm.chosen,items});S.watchJobs.add(d.job_id);closeDialog();await loadProject(S.root,false);if(S.page==='production')renderJobs();notice(`已在后台开始合成 ${d.episodes.length} 集、${d.count} 段，可以接着做别的事。进度和成片链接在生产页的批次列表或右上角「任务总览」里看，每合完一集就能打开那一集；中途可以停止。`);},
 'reroll':async e=>{if(!confirm('重新抽卡会把现在这条视频移到备份（05_分段视频/_抽卡备份/），然后马上用同样的提示词和参考图重新生成、重新计费。别的批次还在跑也不用等，只等这一段自己的参考图。继续吗？'))return;const d=await post('reroll',{key:e.dataset.key});await loadProject();notice(`已把原视频移到 ${d.backup}，这一段已经开始重新生成，进度看生产批次的「任务明细」。`);},
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
 if(el.type==='file'){const shown=$(`[data-file-for="${el.id}"]`);if(shown)shown.textContent=el.files[0]?.name||'未选择文件';}
 if(el.id==='provider-file'&&el.files[0]){const file=el.files[0];el.value='';await importProvider(file);return;}
 if(el.id==='release-file'&&el.files[0]){const file=el.files[0];el.value='';await importRelease(file);return;}
 if(el.dataset.asmEp!==undefined){const a=S.asm,e=el.dataset.asmEp,on=new Set(a.chosen);el.checked?on.add(e):on.delete(e);a.chosen=a.episodes.filter(x=>on.has(x));await loadAssemble();return;}
 if(el.dataset.deleteKey!==undefined){if(S.del&&!S.del.loading){S.del.choices[el.dataset.deleteKey]=el.checked;renderDelete();}return;}
 if(el.dataset.del){if(!S.del)return;S.del[el.dataset.del]=el.checked;await loadDelete();return;}
 if(['bulk-folder','bulk-files'].includes(el.id)&&el.files.length){const files=[...el.files];el.value='';await loadBulk(files);return;}
 if(el.dataset.bulkKey!==undefined){const r=S.bulk?.rows[+el.dataset.bulkKey],t=S.data.tasks.find(x=>x.key===el.value.trim()&&x.kind!=='video');if(!r)return;if(!t)throw Error(`没有出图任务「${el.value.trim()}」，从下拉提示里选一个任务 ID`);if(!r.candidates.some(c=>c.key===t.key))r.candidates.unshift({key:t.key,kind:t.kind,name:S.data.resources.find(x=>x.task_key===t.key)?.name||t.key,output:t.output,exists:!!t.exists});r.choice=t.key;r.note='';renderBulk();return;}
 if(el.dataset.bulkChoice!==undefined){const r=S.bulk?.rows[+el.dataset.bulkChoice];if(r){r.choice=el.value;r.note=el.value?'':r.note;renderBulk();}return;}
 if(el.dataset.asmInclude!==undefined){S.asm.rows[+el.dataset.asmInclude].include=el.checked;renderAssemble();return;}
 if(el.id==='asm-upload'&&el.files[0]){const file=el.files[0];el.value='';const d=await post('assemble/upload',{filename:file.name,content_b64:await readFile(file)});S.asm.rows.push({rel:d.rel,key:d.name,task:false,ok:true,include:true});await loadAssemble(true);notice(`已上传 ${d.name}，插在清单末尾。`);return;}
 if(el.id==='project-picker'){S.root=el.value;S.data=null;S.detail='';if(S.root){await loadProject(S.root,false);show('production');}else show('projects');return;}
 const filters={'project-status':'status','project-type':'type','project-sort':'sort','asset-status':'assetStatus','asset-type':'assetType'};
 if(filters[el.id]){S.filters[filters[el.id]]=el.value;el.id.startsWith('project')?projectList():renderTree();return;}
 if(el.id==='tree-mode'){S.treeMode=el.value;renderTree();return;}
 if(el.dataset.key&&el.type==='checkbox'){await setSelection([el.dataset.key],el.checked);return;}
 if(el.dataset.group){await setSelection(S.groupKeys[el.dataset.group],el.checked);return;}
 if(el.id==='provider-picker'){S.provider=el.value;providerForm();return;}
 if(el.dataset.feature){await api('settings',{agent_features:{[el.dataset.feature]:el.checked}});await reloadBoot();notice('功能入口已更新，基础生产功能始终保留。');return;}
 if(el.closest('[data-route-index]')&&(el.dataset.setting||el.dataset.override)){
 const i=+el.closest('[data-route-index]').dataset.routeIndex,r=S.chain.rows[i],k=el.dataset.setting;
 if(el.dataset.override){r.override={...r.override,[el.dataset.override]:el.value};return;}
 r[k]=k==='concurrency'?+el.value:el.value;
 if(k==='provider'){const media=S.chain.kind==='video'?'video':'image';r.model=S.boot.capabilities.find(c=>c.id===r.provider)?.[media]?.default_model||'';}
 if(['provider','model'].includes(k)){for(const key of ['image_resolution','series','size','ratio','duration','resolution'])r[key]='';renderChain();}
 return;
 }
 if(el.dataset.setting||el.dataset.override){const currentRoot=S.root;invalidatePlan();const row=el.closest('[data-kind]'),kind=row.dataset.kind,setting={...S.data.production[kind],override:{...S.data.production[kind].override}};if(el.dataset.override)setting.override[el.dataset.override]=el.dataset.override==='duration'?(el.value?+el.value:''):el.value;else setting[el.dataset.setting]=el.dataset.setting==='concurrency'?+el.value:el.value;
 if(el.dataset.setting==='provider'){const cap=S.boot.capabilities.find(c=>c.id===el.value),media=kind==='video'?'video':'image';setting.model=cap?.[media]?.default_model||'';setting.image_resolution='';setting.series='';setting.size='';}
 if(el.dataset.setting==='model'){setting.image_resolution='';setting.series='';setting.size='';}
 if(['provider','model'].includes(el.dataset.setting)){setting[kind==='video'?'ratio':'size']='';if(kind==='video'){setting.duration='';setting.resolution='';}}
 const controls=[...row.querySelectorAll('input,select')];controls.forEach(control=>control.disabled=true);
 try{await post('production-settings',{settings:{[kind]:setting}});if(currentRoot!==S.root||S.page!=='production')return;S.data.production[kind]=setting;invalidatePlan();if(['provider','model'].includes(el.dataset.setting))renderServices();}
 finally{controls.forEach(control=>control.disabled=false);}return;}
 if(el.id==='source-file'&&el.files[0]){const d=await api('parse',{filename:el.files[0].name,content_b64:await readFile(el.files[0])});$('[name=source]').value=d.text;return;}
 if(el.id==='audio-file'&&el.files.length){const root=S.root,key=el.dataset.key,files=[...el.files];el.value='';el.disabled=true;try{for(const f of files){if(f.size>25*1024*1024)throw Error(`${f.name} 超过 25MB`);await api('audio/upload',{root,key,filename:f.name,content_b64:await readFile(f)});}notice('参考音频已添加，按列表顺序提交。');}finally{el.disabled=false;if(S.root===root){invalidatePlan();await loadProject();}}return;}
 if(el.id==='manual-file'&&el.files[0]){await post('manual',{kind:el.dataset.kind,key:el.dataset.key,content_b64:await readFile(el.files[0])});await loadProject();notice('参考图已保存。');return;}
 if(el.id==='output-kind'){S.filters.outputKind=el.value;outputList();return;}
 if(S.page==='wizard'&&['image_provider','video_provider'].includes(el.name)){const media=el.name.split('_')[0],cap=S.boot.capabilities.find(c=>c.id===el.value);$(`[name=${media}_model]`).value=cap?.[media]?.default_model||'';}
}));
$('#dialog-close').addEventListener('click',closeDialog);$('#dialog').addEventListener('close',()=>{S.job='';S.jobRequest++;S.editor=null;S.del=null;});
$('#dialog').addEventListener('click',e=>{const d=$('#dialog');if(e.target!==d||!d.classList.contains('rv-job-dialog'))return;const r=d.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)closeDialog();});
$('#apply-advice').addEventListener('click',safe(()=>{const n=S.runtime?.advice?.limit;if(n==null||n<1)throw Error('当前样本不足，暂不能给出可用建议');S.tab='limits';show('settings');$('[name=global]').value=n;notice('建议已填入，检查各服务商额度后点击保存。');}));
let cloudRefresh=null;
async function refreshCloudConfiguration(){
 if(cloudRefresh)return cloudRefresh;
 cloudRefresh=(async()=>{notice('正在获取后台最新授权并拉取各把 Key 的模型…');const d=await api('config/refresh',{});await reloadBoot();if(S.root)await loadProject(S.root,false,true);render();notice(d.rows?.map(r=>`${S.boot.capabilities.find(c=>c.id===r.provider)?.name||r.provider}：${r.ok?'已更新 '+r.count+' 个模型':''}${r.partial||!r.ok?'；'+r.msg:''}`).join('；')||'后台配置已刷新，当前未分配后台 Key',d.rows?.some(r=>!r.ok||r.partial));})().finally(()=>{cloudRefresh=null;});return cloudRefresh;
}
window.addEventListener('studio-config-refresh',safe(refreshCloudConfiguration));
async function tick(){try{if(!window.StudioLogin.isAuthenticated())return;await refreshRuntime();if(S.job&&$('#dialog').open)await jobDetail(S.job,S.jobRoot,true);if(S.root&&['production','handoff','outputs'].includes(S.page)&&!$('#dialog').open&&!document.activeElement?.matches('input,select,textarea')){const page=S.page,root=S.root;const previous=await loadProject(root,false,true);if(page===S.page&&root===S.root&&previous!==undefined)refreshProjectView(previous);}}catch(e){notice('更新失败：'+e.message,true);}finally{setTimeout(tick,4000);}}
document.addEventListener('click',event=>{if(!$('#runtime').contains(event.target))$('#runtime').open=false;});
document.addEventListener('keydown',event=>{if(event.key==='Escape')$('#runtime').open=false;});
stickyObserver.observe($('#sticky-header'));
safe(async()=>{await window.StudioLogin.ready;await reloadBoot();render();tick();await refreshCloudConfiguration();})();
})();
