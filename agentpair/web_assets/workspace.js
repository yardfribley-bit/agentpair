const $=id=>document.getElementById(id);let csrf='',current=null,last='',logged=false,admin=false,accountName='',canWrite=false,awaitingVisibleAnswer=false;
window.addEventListener('DOMContentLoaded',()=>{const css=document.createElement('link');css.rel='stylesheet';css.href='/workbench.css';document.head.append(css);const script=document.createElement('script');script.src='/workbench.js';script.onload=()=>{const consoleScript=document.createElement('script');consoleScript.src='/team_console.js';consoleScript.onload=()=>{const operations=document.createElement('script');operations.src='/operations_ui.js';document.body.append(operations);};document.body.append(consoleScript);};document.body.append(script);});
const readonly=document.body.dataset.readonly==='true';
const deliverables=el('section',null,'card hidden');deliverables.id='deliverables';$('pair-board').before(deliverables);deliverables.after($('reply-form'));const dialogue=el('section',null,'assistant-dialogue');dialogue.id='assistant-dialogue';deliverables.before(dialogue);
function renderDialogue(task){dialogue.replaceChildren();for(const m of task.messages){if(m.role==='user'){const a=el('article',null,'dialogue-user');a.append(el('small','你'),el('p',m.text));dialogue.append(a);}else if(m.stage==='review'&&m.round!==task.round&&m.answer?.finalAnswer){const a=el('article',null,'dialogue-assistant');a.append(el('small','AgentPair'),el('p',m.answer.finalAnswer));dialogue.append(a);}}}

function renderDeliverables(task){
 deliverables.classList.remove('hidden');deliverables.replaceChildren(el('h2','AgentPair'));renderDialogue(task);
 const review=task.messages.filter(m=>m.stage==='review'&&m.round===task.round).at(-1);
 const cloud=t=>t.cloudAction?.round===t.round?t.cloudAction:null;
 deliverables.append(el('p',cloud(task)?.finalAnswer||review?.answer?.finalAnswer||task.blockingReason?.message||(['queued','running'].includes(task.status)?'已收到，正在处理。你暂时不用操作。':'本次处理尚未完成，请查看当前状态。'),'final-answer'));
 renderCloudAction(task,deliverables);if(awaitingVisibleAnswer&&!['queued','running','cancelling'].includes(task.status)){awaitingVisibleAnswer=false;requestAnimationFrame(()=>deliverables.scrollIntoView({block:'start',behavior:'smooth'}));}
 const platform=task.platformResult;if(platform?.round===task.round){deliverables.append(el('h3','涉及对象'));const list=el('div',null,'platform-results');for(const item of platform.items||[]){const row=el('div',null,'cloud-task-receipt');row.append(el('strong',item.name||item.deviceName||item.label||item.session||item.id||'记录'));for(const [key,label] of Object.entries({ip:'访问来源',requests:'请求次数',lastAccess:'最近访问',visitType:'来源说明',account:'账号',lastAttempt:'最近上报尝试',lastSuccess:'最近成功接收',uploadStatus:'上报状态',failureReason:'原因',receiptSource:'依据',state:'状态',online:'最近在线',lastSeen:'最近心跳',expiresAt:'到期',recentCalls:'最近模型调用',events:'事件',tools:'工具调用',device:'设备',session:'会话',label:'发现',model:'模型',source:'来源',sessionName:'会话名称',bodyBytes:'正文大小（字节）',networkRecords:'请求正文证据',contextRecords:'上下文记录',evidenceStrength:'证据强度',verificationState:'复查状态',handlingState:'处理状态',requestIds:'相关请求'})){if(item[key]!=null)row.append(el('span',label+'：'+(key==='lastSeen'?new Date(item[key]*1000).toLocaleString():(key==='online'?(item[key]?'在线':'离线'):({active:'已创建',released:'已释放',failed:'失败',reconcile_required:'等待核对',waiting_action:'等待处理',waiting_capture:'等待新采集',unassigned:'待分派',in_progress:'处理中',pending_verification:'等待复查',closed:'已关闭'})[item[key]]||String(item[key])))));}const selectText=({devices:`查看设备 ${item.id} 的详情`,device_detail:`查看设备 ${item.id} 的模型数据`,model_data:`展开设备 ${item.id} 最近的模型调用`,security:`查看安全事件 ${item.id} 的证据和处理状态`,sessions:`查看设备 ${item.device} 的会话 ${item.session} 的执行过程`,machines:item.state==='active'?`检查机器 ${item.id} 是否可以使用`:null})[platform.action];if(selectText){const choose=el('button','继续查看','secondary');choose.type='button';choose.onclick=()=>{$('reply').value=selectText;$('reply').focus();};row.append(choose);}list.append(row);}for(const link of platform.links||[]){const a=el('a',link.label);a.href=link.url;list.append(a);}deliverables.append(list);deliverables.append(el('h3','查询依据'),el('p',platform.basis||'平台真实查询结果','muted'));if(platform.items?.length)deliverables.append(el('h3','下一步'));const steps=el('div',null,'platform-next-steps');for(const prompt of platform.nextSteps||[]){const button=el('button',prompt,'secondary');button.type='button';button.onclick=()=>{const input=$('reply-form').querySelector('textarea');input.value=prompt;input.focus();};steps.append(button);}deliverables.append(steps);}
 const download=(text,name)=>{const url=URL.createObjectURL(new Blob([text],{type:'text/plain;charset=utf-8'})),a=el('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
 const addCode=(answer,label)=>{if(typeof answer?.code!=='string'||!answer.code.trim())return;
  const block=el('details'),heading=el('summary',label+' · 代码建议（未运行）'),pre=el('pre',answer.code),button=el('button','下载代码','secondary');button.type='button';
  const extension=({python:'py',javascript:'js',typescript:'ts',go:'go',rust:'rs',java:'java'})[String(answer.language||'').toLowerCase()]||'txt';
  button.onclick=()=>download(answer.code,task.id+'-'+label.replace(/[^a-zA-Z0-9]/g,'_')+'.'+extension);block.append(heading,button,pre);deliverables.append(block);
 };
 for(const m of task.messages.filter(m=>m.stage==='driver'&&m.round===task.round)){addCode(m.answer,'Driver');for(const [branch,data] of Object.entries(m.answer?.branches||{}))addCode(data.result?.answer,branch);}
 for(const m of task.messages.filter(m=>m.stage==='driver'&&m.round===task.round&&m.answer?.execution)){const x=m.answer.execution,box=el('details'),button=el('button','下载代码补丁');button.onclick=()=>download(x.patch||'',task.id+'.patch');box.append(el('summary','隔离构建/测试：'+x.status),button,el('pre',JSON.stringify(x.steps,null,2)));deliverables.append(box);}
 for(const m of task.messages.filter(m=>m.stage==='driver'&&m.round===task.round&&m.answer?.toolSteps)){const box=el('details');box.append(el('summary','网页操作记录 · '+m.answer.browserStatus));for(const step of m.answer.toolSteps){box.append(el('p',(step.evidenceId||'')+' · '+(step.action?.op||'操作')+' · '+(step.ok?'成功':'失败')),el('pre',step.result?.text||step.reason||step.error||''));}deliverables.append(box);}
 if(!cloud(task))deliverables.append(el('small','交付位置：本任务记录。只有真实执行日志能证明对应命令已运行；未执行的验证不算通过。'));
 if(review?.answer?.finalAnswer){const button=el('button','下载结果说明','secondary');button.onclick=()=>download(review.answer.finalAnswer,'result-'+task.id+'.txt');deliverables.append(button);}
}
function renderCloudAction(task,parent){
 const a=task.cloudAction;if(!a||a.round!==task.round)return;
 const box=el('section',null,'cloud-task-action');box.append(el('h3','云机器与软件安装'));
 const states={awaiting_confirmation:'等待费用确认',creating:'创建机器',checking_login:'验证登录',waiting_machine:'等待登录就绪',installing:'安装软件',completed:'安装验证完成',unavailable:'暂时无法执行',interrupted:'回执待核对',failed:'安装失败',cancelled:'后续调度已停止',released:'机器已释放'};
 box.append(el('p',states[a.state]||a.state,'cloud-task-state'));if(a.state==='awaiting_confirmation')box.append(el('p','请在下方对话中回复“确认”或“继续”，我将按这个报价开机并安装。'));
 if(a.quote){const q=a.quote;box.append(el('p',`${q.system} · ${q.cpu} 核 / ${Math.round(q.memoryMB/1024)} GB · ${q.zone} · ¥${q.hourlyCNY.toFixed(2)}/小时 · 租约 60 分钟`));}
 for(const s of a.software||[])box.append(el('p',s.name+(s.version?' · '+s.version:'')));
 if(a.missingSoftware?.length){box.append(el('p','未登记安装包：'+a.missingSoftware.join('、')));const link=el('a','查看软件包');link.href='/software';box.append(link);}
 if(a.leaseId)box.append(el('small','机器编号 '+a.leaseId+(a.address?' · '+a.address:'')+(a.expiresAt?' · 到期 '+new Date(a.expiresAt).toLocaleString('zh-CN'):'')));
 const run=async(kind,button)=>{button.disabled=true;try{const data={requestId:a.requestId};if(kind==='confirm'){data.confirmed=true;data.maxHourlyCNY=a.quote.hourlyCNY;}const result=await api('/api/tasks/'+task.id+'/cloud/'+kind,data);render(result.task);await refresh();}catch(e){error(e.message);}finally{button.disabled=false;}};
 if(admin&&a.state==='awaiting_confirmation'){const button=el('button','确认报价，开机并安装','primary');button.type='button';button.onclick=()=>{if(confirm(`确认创建 UCloud ${a.system} 机器并安装列出的软件？价格上限 ¥${a.quote.hourlyCNY.toFixed(2)}/小时，租约 60 分钟，确认后开始计费。`))run('confirm',button);};box.append(button);}
 if(admin&&a.state==='waiting_machine'){const button=el('button','继续检查同一台机器','secondary');button.type='button';button.onclick=()=>run('resume',button);box.append(button);}
 for(const op of a.operations||[]){const row=el('div',null,'cloud-task-receipt');row.append(el('strong',op.softwareId+' · '+({queued:'待执行',running:'安装中',completed:'验证通过',failed:'失败',interrupted:'回执缺失'}[op.state]||op.state)));if(op.evidence?.installedVersion)row.append(el('span','版本 '+op.evidence.installedVersion));const more=el('details');more.append(el('summary','查看安装验证证据'),el('pre',JSON.stringify(op.evidence||{},null,2)));row.append(more);box.append(row);}
 const machines=el('a','查看云机器');machines.href='/cloud-machines';box.append(machines);parent.append(box);
}
function applyAccess(){
  let cloudLink=document.getElementById('cloud-machines-link');
  if(!document.getElementById('packages-link')){const packageLink=el('a','▧ 软件包');packageLink.id='packages-link';packageLink.href='/packages';document.querySelector('.primary-nav').append(packageLink);}
  if(admin&&!cloudLink){cloudLink=el('a','▣ 云机器');cloudLink.id='cloud-machines-link';cloudLink.href='/cloud-machines';document.querySelector('.primary-nav').append(cloudLink);}
  if(cloudLink)cloudLink.classList.toggle('hidden',!admin);
  const methodField=$('engineering-method'),profileField=$('execution-profile');
  if(methodField){for(const option of methodField.options)option.disabled=!admin&&option.value!=='local';if(!admin)methodField.value='local';}
  if(profileField){for(const option of profileField.options)option.disabled=!admin&&option.value!=='none';if(!admin)profileField.value='none';}
  $('access-role').textContent=admin?'admin · 管理员':csrf?(accountName+' · 用户'):'游客 · 可浏览';
  $('open-login').classList.toggle('hidden',!!csrf);
  $('logout').classList.toggle('hidden',!csrf);
  $('new-task').classList.toggle('hidden',!csrf);
  $('reply-form').classList.toggle('hidden',!canWrite);
  if(!csrf)$('create').classList.add('hidden');
  document.querySelector('.hero h1').textContent=csrf?'你的 AgentPair 平台助手':'查看成果，了解协作过程';
  document.querySelector('.hero p').textContent=csrf?'用对话管理云机器、设备、模型数据、安全审计和会话，也可以提出编程与其他任务。':'从左侧选择任务，先看结果和交付物，再展开协作与证据。';
}
$('open-login').onclick=()=>{$('login').classList.remove('hidden');$('username').focus();};
$('close-login').onclick=()=>{$('login').classList.add('hidden');};
$('logout').onclick=async()=>{try{await api('/api/logout',{});admin=false;canWrite=false;current=null;last='';csrf='';$('conversation').classList.add('hidden');if($('team-console'))$('team-console').hidden=true;applyAccess();await refresh();}catch(e){error(e.message);}};
// Keep the live task state prominent; detailed records remain available below.
const recordPanel=document.createElement('details');recordPanel.className='execution-records';
const recordTitle=document.createElement('summary');recordTitle.textContent='展开对话与运行记录';recordPanel.append(recordTitle);
$('messages').before(recordPanel);recordPanel.append($('messages'),$('events'));
const pairFlow=new PairFlow($('pair-board'));
$('pair-board').before($('create'));
const guide=el('div',null,'brief-guide');guide.append(el('h3','1 · 定义任务'),el('p','说明使用者、输入输出、技术与环境约束，以及哪些测试通过才算完成。'));
const sample=el('button','填入代码任务示例','secondary');sample.type='button';guide.append(sample);$('create-form').prepend(guide);
sample.onclick=()=>{$('title').value='实现稳定去重函数';$('goal').value='为 Python 3.11 项目实现 stable_unique(items)，保留元素首次出现的顺序。输入整数列表，返回新列表，不修改输入。请提供实现与测试代码。';$('acceptance').value='[3,1,3,2] 返回 [3,1,2]；空列表返回 []；验证输入未被修改；提供 pytest 测试及运行命令。未执行测试须明确说明。';};
const acceptanceLabel=el('label','2 · 验收标准'),acceptance=el('textarea');acceptance.id='acceptance';acceptance.rows=3;acceptance.maxLength=1800;acceptance.placeholder='输入示例、边界条件、性能要求、测试命令与预期输出。';acceptanceLabel.append(acceptance);$('goal').closest('label').after(acceptanceLabel);
const methodLabel=el('label','工程方法'),methodSelect=el('select');methodSelect.id='engineering-method';
const methodInfo=el('p',null,'notice'),methodOptions={
 local:['默认协作 · 不开云机器','适合查询、讨论和轻量建议。优势：启动快、无新增云机费。使用常驻 Navigator，0 台云 Driver；每轮通常 3 次生成调用、2 次决策调用，返工时增加。'],
 pair:['云端结对 · 1 台 Driver','Navigator 规划复核、Driver 独立处理。最多占用 1 台云 Driver，优先复用现有租约；约 ¥0.15/小时（参考报价），模型费用另计。可额外选择实验性隔离构建/测试；尚待真实云机验收，不在线安装项目依赖。'],
 parallel:['并行方案探索与评审 · 2 台 Driver','适合技术路线不确定、希望比较两种方案的任务。A/B 独立探索后交换发现，交叉复核并分别修订，Navigator（C）统一验收。最多占用 2 台云 Driver，优先复用；合计约 ¥0.30/小时（参考报价）。基础流程约 8 次生成调用（规划、A/B 各探索/复核/修订、最终验收），另有决策调用与可能的返工，模型费用随之增加。当前代码产物为建议，未执行测试。']};
for(const [value,[label]] of Object.entries(methodOptions)){const option=el('option',label);option.value=value;methodSelect.append(option);}
methodLabel.firstChild.textContent='高级 · 通用任务执行方式';methodLabel.append(methodSelect);acceptanceLabel.after(methodLabel,methodInfo);
const delivery=el('div',null,'delivery-note');delivery.append(el('h3','4 · 交付位置'),el('p','保存在本任务「成果与代码」中。云端执行任务提供代码补丁、构建/测试日志和退出码；未选择执行环境时仅提供建议。不会自动提交 GitHub 或导出安装包。'));$('create-form').append(delivery);
const submit=$('create-form').querySelector('button.primary');$('create-form').append(submit);submit.textContent='发布任务并开始协作';
const executionLabel=el('label','执行环境（必须同时选择云端结对）'),executionSelect=el('select');executionSelect.id='execution-profile';for(const [value,label] of [['none','只分析，不运行代码'],['python','隔离 Python：编译检查 + unittest'],['node','隔离 Node：npm build + test（依赖须已具备）']]){const option=el('option',label);option.value=value;executionSelect.append(option);}executionLabel.append(executionSelect);submit.before(executionLabel);
methodSelect.onchange=()=>{methodInfo.textContent=methodOptions[methodSelect.value][1]+' 云机按整小时估算，任务结束保留到租约结束供复用。最终以创建前报价为准。';};methodSelect.onchange();
const resourcePanel=el('section',null,'card resource-panel');
const nodePanel=el('section',null,'card team-nodes');nodePanel.append(el('h2','团队节点'),el('p','展示已登记的真实执行节点及最近一次连接检查。','node-note'));
$('pair-board').before(nodePanel);
const resourceDrawer=el('details',null,'resource-drawer'),resourceSummary=el('summary','资源概况 · 点击展开');resourceDrawer.append(resourceSummary,resourcePanel);document.body.append(resourceDrawer);
let resourcesBusy=false;
async function refreshResources(){
 if(readonly||resourcesBusy)return;resourcesBusy=true;
 try{
  const d=await api('/api/resources');resourcePanel.replaceChildren(el('h2','实时资源消耗'),el('small','更新于 '+new Date(d.updatedAt).toLocaleTimeString('zh-CN')));
  window.agentpairNodes=d.nodes||[];window.dispatchEvent(new Event('agentpair:nodes'));
  const cards=el('div',null,'team-node-grid');
  for(const node of d.nodes||[]){
   const state=({online:'在线',ready:'可执行任务',setup_needed:'已连接 · 等待部署 Worker',unreachable:'连接失败',leased:'租约有效'})[node.state]||node.state;
   const card=el('article',null,'team-node');card.append(el('strong',node.role),el('span',state,'node-state '+node.state));
   card.append(el('small',node.address||location.hostname));
   if(node.currentTasks?.length)for(const task of node.currentTasks)card.append(el('p','正在执行：'+task.title));
   else card.append(el('p',node.state==='ready'?'待命 · 可接收下一项任务':'当前没有执行任务'));
   if(node.checkedAt)card.append(el('small','连接核验 '+new Date(node.checkedAt).toLocaleTimeString('zh-CN')));
   cards.append(card);
  }
  nodePanel.replaceChildren(el('h2','团队节点'),el('p','Navigator 协调任务；Linux Driver 仅在成功连接并部署 Worker 后显示为可执行。','node-note'),cards);
  if(d.leaseAlerts?.length){const alert=el('div',null,'notice caution');alert.append(el('strong','租约提醒'));for(const item of d.leaseAlerts)alert.append(el('p',item.message));resourcePanel.append(alert);}
  resourceSummary.textContent='资源 · '+d.activeDrivers+' 台 Driver · '+(d.tokens.input+d.tokens.output).toLocaleString()+' tokens';
  const grid=el('div',null,'resource-grid');resourcePanel.append(grid);
  const metric=(name,value,note)=>{const c=el('div',null,'resource-metric');c.append(el('small',name),el('strong',value),el('small',note));grid.append(c);};
  const fmt=n=>Number(n).toLocaleString('zh-CN'),gb=n=>n==null?'未知':(n/1073741824).toFixed(2)+' GB';
  metric('累计模型 Token',fmt(d.tokens.input+d.tokens.output),'输入 '+fmt(d.tokens.input)+' / 输出 '+fmt(d.tokens.output));
  metric('模型调用',fmt(d.tokens.calls),'缺少用量记录 '+d.tokens.missingUsageCalls+' 次');
  metric('运行 / 排队任务',d.activeTasks,'当前记录中的活动任务');
  metric('云端 Driver',d.activeDrivers,'租约记录 · Navigator 常驻 1 台');
  metric('云机累计估算','¥'+d.serverEstimatedCNY.toFixed(2),'按整小时计费估算');
  metric('生成调用历史估算','¥'+d.generationEstimate.estimatedReservedCNY.toFixed(4),'不含独立决策费 · 余额未接入');
  metric('Navigator 内存',gb(d.navigator.memoryUsedBytes),'/ '+gb(d.navigator.memoryTotalBytes));
  metric('系统负载 / CPU 核数',(d.navigator.load1m??0).toFixed(2)+' / '+d.navigator.cpuCores,'1 分钟负载，非 CPU 百分比');
  metric('Navigator 磁盘',gb(d.navigator.diskUsedBytes),'/ '+gb(d.navigator.diskTotalBytes));
  const detail=el('details'),title=el('summary','展开模型、服务器与任务明细');detail.append(title);resourcePanel.append(detail);
  for(const m of d.models)detail.append(el('p',m.model+' · '+m.kind+' · '+m.calls+' 次 · 输入 '+fmt(m.input)+' / 输出 '+fmt(m.output)+' token'));
  for(const [i,l] of d.leases.entries())detail.append(el('p','Driver '+(i+1)+' · '+(l.managed?'Navigator 托管':'外部资源')+' · '+({active:'租约有效',released:'已释放',creating:'创建中',reconcile_required:'待核对'}[l.state]||l.state)+' · 剩余 '+Math.floor(l.remainingSeconds/60)+' 分 '+l.remainingSeconds%60+' 秒 · 报价 ¥'+l.hourlyQuoteCNY+'/小时 · 累计估算 '+(l.estimatedBilledCNY==null?'未知':'¥'+l.estimatedBilledCNY.toFixed(2))));
  for(const task of d.tasks)detail.append(el('p',task.title+' · '+(labels[task.status]||task.status)+' · '+fmt(task.tokens)+' token'));
  if(d.leaseError)detail.append(el('p',d.leaseError));
  for(const note of d.notes)detail.append(el('small',note,'resource-note'));
 }catch(e){resourcePanel.replaceChildren(el('h2','实时资源消耗'),el('p','数据暂时不可用：'+e.message));}
 finally{resourcesBusy=false;}
}
setTimeout(refreshResources,0);setInterval(refreshResources,5000);
pairFlow.update({round:1,status:'idle',messages:[],events:[]});
const labels={queued:'等待执行',running:'协作中',cancelling:'正在取消',cancelled:'已取消',completed:'本轮已完成',failed:'本轮失败',interrupted:'服务重启中断'};
function el(tag,text,cls){const n=document.createElement(tag);if(text!=null)n.textContent=String(text);if(cls)n.className=cls;return n;}
labels.blocked='验收未通过 · 可继续推进';
Object.assign(labels,{needs_information:'等待补充信息',unsupported_capability:'缺少执行能力',awaiting_confirmation:'等待费用确认',waiting_for_machine:'等待机器登录',needs_more_evidence:'证据尚未齐全'});
$('adapter').options[0].textContent='协作任务 / 天气查询 / 编程建议';
$('adapter').closest('label').hidden=true;
function error(text){$('error').textContent=text;$('error').classList.toggle('hidden',!text);}
async function api(path,data){const options={credentials:'same-origin',cache:'no-store'};if(data!==undefined){options.method='POST';options.headers={'Content-Type':'application/json','X-CSRF-Token':csrf};options.body=JSON.stringify(data);}const r=await fetch(path,options);const body=await r.text();let d;try{d=JSON.parse(body);}catch(_){throw Error([502,503,504].includes(r.status)?'服务暂时不可用，正在重新同步。若刚提交操作，请先查看任务状态，避免重复提交。':'服务器返回了非预期内容（HTTP '+r.status+'），请稍后重试。');}if(!r.ok){if(r.status===401){csrf='';canWrite=false;admin=false;applyAccess();$('login').classList.remove('hidden');}throw Error(d.error||'请求失败');}return d;}
function details(parent,value){const n=el('details');n.append(el('summary','查看结构化输出与证据'),el('pre',JSON.stringify(value,null,2)));parent.append(n);}
function render(t){canWrite=admin||t.permissions?.canWrite===true;current=t.id;$('create').classList.add('hidden');$('conversation').classList.remove('hidden');$('task-title').textContent=t.title;$('status').textContent=labels[t.status]||t.status;$('round').textContent='第 '+t.round+(t.conversationUnlimited?'':' / '+t.maxRounds)+' 轮';$('messages').replaceChildren();for(const m of t.messages){const c=el('article',null,'card '+(m.role==='user'?'user-message':'')),h=el('div',null,'card-head');h.append(el('h3',({user:'你',navigator:'Navigator',driver:'Driver'})[m.role]+' · 第 '+m.round+' 轮'),el('small',new Date(m.at).toLocaleTimeString('zh-CN',{hour12:false})));c.append(h);if(m.text)c.append(el('p',m.text));if(m.answer){c.append(el('p',m.answer.summary||JSON.stringify(m.answer),'summary'));if(m.answer.steps)for(const s of m.answer.steps)c.append(el('p','→ '+s));if(m.answer.corrections)for(const s of m.answer.corrections)c.append(el('p','复核：'+s));details(c,m.answer);}if(m.usage)c.append(el('small',(m.usage.total_tokens||0)+' tokens'));$('messages').append(c);} $('events').replaceChildren(el('h3','运行事件'));for(const e of t.events){$('events').append(el('div',[new Date(e.at).toLocaleTimeString('zh-CN',{hour12:false}),e.role,e.stage,e.kind,e.errorType,e.text].filter(Boolean).join(' · '),'event'));}for(const result of t.results){if(result.outputs?.driver?.evidence){const c=el('div');c.append(el('h3','第 '+result.round+' 轮证据'));details(c,result.outputs.driver.evidence);$('events').append(c);}}const busy=['queued','running','cancelling'].includes(t.status);const cloudReply=t.cloudAction?.round===t.round&&['awaiting_confirmation','waiting_machine'].includes(t.cloudAction.state);$('send').disabled=busy||(!t.conversationUnlimited&&t.round>=t.maxRounds&&!cloudReply);$('send').textContent=cloudReply?'发送 · 继续执行':'发送';$('reply').placeholder=cloudReply?'回复“确认”继续，或直接说明要修改的配置':'询问平台状态、继续任务，或提出新的要求';$('cancel').disabled=!busy;$('reply-note').textContent=cloudReply?(t.cloudAction.state==='awaiting_confirmation'?'回复“确认”或“继续”，按以上报价开机并安装；修改配置请直接说明。':'回复“继续”，检查同一台机器并继续安装。'):busy?'本轮尚未结束，请等待或停止。取消不会保证即时中断在途模型请求。':!t.conversationUnlimited&&t.round>=t.maxRounds?'本任务已达实验轮数上限；可导出记录。':t.status==='blocked'?'本轮验收未通过。追加消息会带上历史与验收意见，重新规划并继续处理。':'追加消息会承接该任务完整历史，再运行规划、执行与复核。';}
async function refresh(){if(!logged)return;try{const list=await api('/api/tasks');$('budget').textContent='模型预留 ¥'+list.budget.estimatedReservedCNY.toFixed(2)+(list.budget.estimatedLimitCNY==null?' · 不设费用上限（估算）':' / ¥'+list.budget.estimatedLimitCNY.toFixed(2)+'（估算）');$('task-list').replaceChildren();for(const t of list.items){const b=el('button',t.title,'task-item '+(t.id===current?'selected':''));b.append(el('small',(labels[t.status]||t.status)+' · 第 '+t.round+' 轮'));b.onclick=async()=>{try{last='';render(await api('/api/tasks/'+t.id));history.replaceState(null,'','/?task='+encodeURIComponent(t.id));}catch(e){error(e.message);}};$('task-list').append(b);}if(current){const t=await api('/api/tasks/'+current),s=JSON.stringify(t);if(s!==last){render(t);last=s;}}if(csrf&&!admin){const balance=await api('/api/balance');$('budget').textContent='我的模型额度 ¥'+balance.remainingCNY.toFixed(4)+' · 预估预扣';}$('connection').textContent='● 已同步 · '+new Date().toLocaleTimeString('zh-CN',{hour12:false});}catch(e){$('connection').textContent='● 同步失败';error(e.message);}}
$('login-form').onsubmit=async e=>{e.preventDefault();try{const d=await api('/api/login',{username:$('username').value,password:$('password').value});csrf=d.csrf;$('password').value='';logged=true;admin=d.role==='admin';accountName=$('username').value;applyAccess();$('login').classList.add('hidden');error('');await refresh();const items=await api('/api/tasks');if(items.items.length)render(await api('/api/tasks/'+items.items[0].id));else if(csrf)$('new-task').click();}catch(e){error(e.message);}};
$('new-task').onclick=()=>{if(!csrf)return;current=null;canWrite=false;last='';$('reply-form').classList.add('hidden');dialogue.replaceChildren();$('pair-board').classList.add('hidden');$('deliverables').classList.add('hidden');window.scrollTo({top:0,behavior:'smooth'});$('conversation').classList.add('hidden');$('create').classList.remove('hidden');$('status').textContent='新任务';};$('adapter').onchange=()=>{$('target-label').classList.toggle('hidden',$('adapter').value!=='public_site');};
$('create-form').onsubmit=async e=>{e.preventDefault();try{const t=await api('/api/tasks',{executionProfile:$('execution-profile').value,engineeringMethod:$('engineering-method').value,title:$('title').value.trim()||$('goal').value.trim().slice(0,60),message:[$('goal').value,$('acceptance').value?'验收标准：\n'+$('acceptance').value:''].filter(Boolean).join('\n\n'),adapter:$('adapter').value,target:$('adapter').value==='public_site'?$('target').value:''});error('');render(t);await refresh();}catch(e){error(e.message);}};
$('reply-form').onsubmit=async e=>{e.preventDefault();if(!current)return;$('send').disabled=true;try{awaitingVisibleAnswer=true;const t=await api('/api/tasks/'+current+'/messages',{message:$('reply').value});$('reply').value='';error('');render(t);await refresh();}catch(e){error(e.message);await refresh();}};
$('cancel').onclick=async()=>{try{render(await api('/api/tasks/'+current+'/cancel',{}));}catch(e){error(e.message);}};
$('export').onclick=async()=>{try{const t=await api('/api/tasks/'+current),url=URL.createObjectURL(new Blob([JSON.stringify(t,null,2)],{type:'application/json'})),a=el('a');a.href=url;a.download='agentpair-'+t.id+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}catch(e){error(e.message);}};
if(!readonly)(async()=>{try{const session=await api('/api/session');csrf=session.csrf||'';logged=true;admin=session.role==='admin';accountName=session.username||'';applyAccess();await refresh();const items=await api('/api/tasks');const requested=new URLSearchParams(location.search).get('task');const chosen=items.items.find(t=>t.id===requested)||items.items[0];if(chosen)render(await api('/api/tasks/'+chosen.id));else if(csrf)$('new-task').click();}catch(_){$('connection').textContent='等待登录';}})();setInterval(refresh,2000);
const _renderConversation=render;const renderConversation=t=>{window.agentpairCurrentTask=t;window.dispatchEvent(new CustomEvent('agentpair:task-rendered'));return _renderConversation(t);};
render=t=>{$('pair-board').classList.remove('hidden');renderDeliverables(t);renderConversation(t);applyAccess();pairFlow.update(t);const cards=document.querySelectorAll('#messages .card');t.messages.forEach((m,i)=>{if(m.answer?.code){cards[i].append(el('h4','Driver 实现建议 · 未执行测试'),el('pre',m.answer.code));}if(m.answer?.citationWarning){cards[i].append(el('div','模型给出了本任务未提供的证据编号；这些引用无效，不作为验收依据。','notice caution'));}});if(readonly){$('reply-form').classList.add('hidden');$('login').classList.add('hidden');$('create').classList.add('hidden');$('new-task').textContent='公开协作回放';$('new-task').disabled=true;$('budget').textContent='编程示例 · 未执行生成代码';}};
if(readonly){logged=false;$('login').classList.add('hidden');$('new-task').disabled=true;fetch('/api/demo',{cache:'no-store'}).then(r=>{if(!r.ok)throw Error('Demo unavailable');return r.json();}).then(t=>{render(t);$('connection').textContent='● 历史编程协作 · 无实时调用';const item=el('div',t.title,'task-item selected');$('task-list').append(item);}).catch(e=>error(e.message));}

$('register-form').onsubmit=async e=>{e.preventDefault();const f=new FormData(e.target);if(f.get('password')!==f.get('confirmation')){error('两次密码不一致');return;}const button=e.target.querySelector('button');button.disabled=true;try{const d=await api('/api/register',{username:f.get('username'),password:f.get('password')});csrf=d.csrf;admin=false;logged=true;accountName=f.get('username');e.target.reset();$('login').classList.add('hidden');applyAccess();error('');await refresh();$('new-task').click();}catch(err){error(err.message);}finally{button.disabled=false;}};

window.addEventListener('DOMContentLoaded',()=>{
 document.body.classList.add('assistant-page');
 $('title').required=false;$('title').closest('label').classList.add('hidden');
 $('goal').placeholder='例如：查看我的设备是否在线，或开一台 1核1GB 的 Linux 服务器';
 $('create').querySelector('h2').textContent='有什么需要我帮你处理？';
 const advanced=el('details',null,'assistant-advanced');advanced.append(el('summary','高级选项 · 编程与通用任务'));
 const form=$('create-form');for(const label of [...form.children])if(label!==$('goal').closest('label')&&label.tagName!=='BUTTON'&&label!==$('title').closest('label'))advanced.append(label);
 form.querySelector('button.primary').before(advanced);form.querySelector('button.primary').textContent='发送';
 $('reply-form').querySelector('label').firstChild.textContent='继续对话';
 const warning=document.querySelector('main>.notice.caution');if(warning){warning.textContent='对话由已配置的模型服务处理，请勿输入密码或密钥。平台操作以真实执行回执为准。';warning.classList.add('assistant-privacy');}
});
