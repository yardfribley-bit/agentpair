const $=id=>document.getElementById(id);let csrf='',current=null,last='',logged=false,admin=false;
const readonly=document.body.dataset.readonly==='true';
const deliverables=el('section',null,'card hidden');deliverables.id='deliverables';$('pair-board').before(deliverables);
function renderDeliverables(task){
 deliverables.classList.remove('hidden');deliverables.replaceChildren(el('h2','成果与代码'));
 const review=task.messages.filter(m=>m.stage==='review'&&m.round===task.round).at(-1);
 deliverables.append(el('p',review?.answer?.finalAnswer||'本轮结果尚未交付，可在下方查看协作进度。','final-answer'));
 const download=(text,name)=>{const url=URL.createObjectURL(new Blob([text],{type:'text/plain;charset=utf-8'})),a=el('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
 const addCode=(answer,label)=>{if(typeof answer?.code!=='string'||!answer.code.trim())return;
  const block=el('details'),heading=el('summary',label+' · 代码建议（未运行）'),pre=el('pre',answer.code),button=el('button','下载代码','secondary');button.type='button';
  const extension=({python:'py',javascript:'js',typescript:'ts',go:'go',rust:'rs',java:'java'})[String(answer.language||'').toLowerCase()]||'txt';
  button.onclick=()=>download(answer.code,task.id+'-'+label.replace(/[^a-zA-Z0-9]/g,'_')+'.'+extension);block.append(heading,button,pre);deliverables.append(block);
 };
 for(const m of task.messages.filter(m=>m.stage==='driver'&&m.round===task.round)){addCode(m.answer,'Driver');for(const [branch,data] of Object.entries(m.answer?.branches||{}))addCode(data.result?.answer,branch);}
 for(const m of task.messages.filter(m=>m.stage==='driver'&&m.round===task.round&&m.answer?.execution)){const x=m.answer.execution,box=el('details'),button=el('button','下载代码补丁');button.onclick=()=>download(x.patch||'',task.id+'.patch');box.append(el('summary','隔离构建/测试：'+x.status),button,el('pre',JSON.stringify(x.steps,null,2)));deliverables.append(box);}
 deliverables.append(el('small','交付位置：本任务记录。只有隔离执行日志能证明对应命令已运行；未推送 GitHub，未执行的验证不算通过。'));
 if(review?.answer?.finalAnswer){const button=el('button','下载结果说明','secondary');button.onclick=()=>download(review.answer.finalAnswer,'result-'+task.id+'.txt');deliverables.append(button);}
}
function applyAccess(){
  $('access-role').textContent=admin?'admin · 管理员':'访客 · 只读';
  $('open-login').classList.toggle('hidden',admin);
  $('logout').classList.toggle('hidden',!admin);
  $('new-task').classList.toggle('hidden',!admin);
  $('reply-form').classList.toggle('hidden',!admin);
  if(!admin)$('create').classList.add('hidden');
  document.querySelector('.hero h1').textContent=admin?'把需求变成可验收的成果':'查看成果，了解协作过程';
  document.querySelector('.hero p').textContent=admin?'写清目标、约束和通过标准，再选择工程方法。':'从左侧选择任务，先看结果和交付物，再展开协作与证据。';
}
$('open-login').onclick=()=>{$('login').classList.remove('hidden');$('username').focus();};
$('close-login').onclick=()=>{$('login').classList.add('hidden');};
$('logout').onclick=async()=>{try{await api('/api/logout',{});admin=false;csrf='';applyAccess();await refresh();}catch(e){error(e.message);}};
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
 parallel:['并行方案探索与评审 · 2 台 Driver','适合技术路线不确定、希望比较两种方案的任务。优势：A/B 分别探索，Navigator（C）统一比较并综合。最多占用 2 台云 Driver，优先复用；合计约 ¥0.30/小时（参考报价）。通常 4 次生成调用、2 次决策调用，返工更多；不保证优于单路线。当前代码产物为建议，未执行测试。']};
for(const [value,[label]] of Object.entries(methodOptions)){const option=el('option',label);option.value=value;methodSelect.append(option);}
methodLabel.firstChild.textContent='3 · 工程方法与资源';methodLabel.append(methodSelect);acceptanceLabel.after(methodLabel,methodInfo);
const delivery=el('div',null,'delivery-note');delivery.append(el('h3','4 · 交付位置'),el('p','保存在本任务「成果与代码」中。云端执行任务提供代码补丁、构建/测试日志和退出码；未选择执行环境时仅提供建议。不会自动提交 GitHub 或导出安装包。'));$('create-form').append(delivery);
const submit=$('create-form').querySelector('button.primary');$('create-form').append(submit);submit.textContent='发布任务并开始协作';
const executionLabel=el('label','执行环境（必须同时选择云端结对）'),executionSelect=el('select');executionSelect.id='execution-profile';for(const [value,label] of [['none','只分析，不运行代码'],['python','隔离 Python：编译检查 + unittest'],['node','隔离 Node：npm build + test（依赖须已具备）']]){const option=el('option',label);option.value=value;executionSelect.append(option);}executionLabel.append(executionSelect);submit.before(executionLabel);
methodSelect.onchange=()=>{methodInfo.textContent=methodOptions[methodSelect.value][1]+' 云机按整小时估算，任务结束保留到租约结束供复用。最终以创建前报价为准。';};methodSelect.onchange();
const resourcePanel=el('section',null,'card resource-panel');
const resourceDrawer=el('details',null,'resource-drawer'),resourceSummary=el('summary','资源概况 · 点击展开');resourceDrawer.append(resourceSummary,resourcePanel);document.body.append(resourceDrawer);
let resourcesBusy=false;
async function refreshResources(){
 if(readonly||resourcesBusy)return;resourcesBusy=true;
 try{
  const d=await api('/api/resources');resourcePanel.replaceChildren(el('h2','实时资源消耗'),el('small','更新于 '+new Date(d.updatedAt).toLocaleTimeString('zh-CN')));
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
  for(const [i,l] of d.leases.entries())detail.append(el('p','Driver '+(i+1)+' · '+({active:'租约有效',released:'已释放',creating:'创建中',reconcile_required:'待核对'}[l.state]||l.state)+' · 剩余 '+Math.floor(l.remainingSeconds/60)+' 分 '+l.remainingSeconds%60+' 秒 · 报价 ¥'+l.hourlyQuoteCNY+'/小时 · 累计估算 '+(l.estimatedBilledCNY==null?'未知':'¥'+l.estimatedBilledCNY.toFixed(2))));
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
$('adapter').options[0].textContent='协作任务 / 天气查询 / 编程建议';
$('adapter').closest('label').hidden=true;
function error(text){$('error').textContent=text;$('error').classList.toggle('hidden',!text);}
async function api(path,data){const options={credentials:'same-origin',cache:'no-store'};if(data!==undefined){options.method='POST';options.headers={'Content-Type':'application/json','X-CSRF-Token':csrf};options.body=JSON.stringify(data);}const r=await fetch(path,options),d=await r.json();if(!r.ok){if(r.status===401){admin=false;applyAccess();$('login').classList.remove('hidden');}throw Error(d.error||'请求失败');}return d;}
function details(parent,value){const n=el('details');n.append(el('summary','查看结构化输出与证据'),el('pre',JSON.stringify(value,null,2)));parent.append(n);}
function render(t){current=t.id;$('create').classList.add('hidden');$('conversation').classList.remove('hidden');$('task-title').textContent=t.title;$('status').textContent=labels[t.status]||t.status;$('round').textContent='第 '+t.round+' / '+t.maxRounds+' 轮';$('messages').replaceChildren();for(const m of t.messages){const c=el('article',null,'card '+(m.role==='user'?'user-message':'')),h=el('div',null,'card-head');h.append(el('h3',({user:'你',navigator:'Navigator',driver:'Driver'})[m.role]+' · 第 '+m.round+' 轮'),el('small',new Date(m.at).toLocaleTimeString('zh-CN',{hour12:false})));c.append(h);if(m.text)c.append(el('p',m.text));if(m.answer){c.append(el('p',m.answer.summary||JSON.stringify(m.answer),'summary'));if(m.answer.steps)for(const s of m.answer.steps)c.append(el('p','→ '+s));if(m.answer.corrections)for(const s of m.answer.corrections)c.append(el('p','复核：'+s));details(c,m.answer);}if(m.usage)c.append(el('small',(m.usage.total_tokens||0)+' tokens'));$('messages').append(c);} $('events').replaceChildren(el('h3','运行事件'));for(const e of t.events){$('events').append(el('div',[new Date(e.at).toLocaleTimeString('zh-CN',{hour12:false}),e.role,e.stage,e.kind,e.errorType,e.text].filter(Boolean).join(' · '),'event'));}for(const result of t.results){if(result.outputs.driver.evidence){const c=el('div');c.append(el('h3','第 '+result.round+' 轮证据'));details(c,result.outputs.driver.evidence);$('events').append(c);}}const busy=['queued','running','cancelling'].includes(t.status);$('send').disabled=busy||t.round>=t.maxRounds;$('cancel').disabled=!busy;$('reply-note').textContent=busy?'本轮尚未结束，请等待或停止。取消不会保证即时中断在途模型请求。':t.round>=t.maxRounds?'本任务已达实验轮数上限；可导出记录。':t.status==='blocked'?'本轮验收未通过。追加消息会带上历史与验收意见，重新规划并继续处理。':'追加消息会承接该任务完整历史，再运行规划、执行与复核。';}
async function refresh(){if(!logged)return;try{const list=await api('/api/tasks');$('budget').textContent='模型预留 ¥'+list.budget.estimatedReservedCNY.toFixed(2)+(list.budget.estimatedLimitCNY==null?' · 不设费用上限（估算）':' / ¥'+list.budget.estimatedLimitCNY.toFixed(2)+'（估算）');$('task-list').replaceChildren();for(const t of list.items){const b=el('button',t.title,'task-item '+(t.id===current?'selected':''));b.append(el('small',(labels[t.status]||t.status)+' · 第 '+t.round+' 轮'));b.onclick=async()=>{try{last='';render(await api('/api/tasks/'+t.id));}catch(e){error(e.message);}};$('task-list').append(b);}if(current){const t=await api('/api/tasks/'+current),s=JSON.stringify(t);if(s!==last){render(t);last=s;}}$('connection').textContent='● 已同步 · '+new Date().toLocaleTimeString('zh-CN',{hour12:false});}catch(e){$('connection').textContent='● 同步失败';error(e.message);}}
$('login-form').onsubmit=async e=>{e.preventDefault();try{const d=await api('/api/login',{username:$('username').value,password:$('password').value});csrf=d.csrf;$('password').value='';logged=true;admin=true;applyAccess();$('login').classList.add('hidden');$('new-task').click();error('');await refresh();}catch(e){error(e.message);}};
$('new-task').onclick=()=>{if(!admin)return;current=null;last='';$('pair-board').classList.add('hidden');$('deliverables').classList.add('hidden');window.scrollTo({top:0,behavior:'smooth'});$('conversation').classList.add('hidden');$('create').classList.remove('hidden');$('status').textContent='新任务';};$('adapter').onchange=()=>{$('target-label').classList.toggle('hidden',$('adapter').value!=='public_site');};
$('create-form').onsubmit=async e=>{e.preventDefault();try{const t=await api('/api/tasks',{executionProfile:$('execution-profile').value,engineeringMethod:$('engineering-method').value,title:$('title').value,message:[$('goal').value,$('acceptance').value?'验收标准：\n'+$('acceptance').value:''].filter(Boolean).join('\n\n'),adapter:$('adapter').value,target:$('adapter').value==='public_site'?$('target').value:''});error('');render(t);await refresh();}catch(e){error(e.message);}};
$('reply-form').onsubmit=async e=>{e.preventDefault();if(!current)return;$('send').disabled=true;try{const t=await api('/api/tasks/'+current+'/messages',{message:$('reply').value});$('reply').value='';error('');render(t);await refresh();}catch(e){error(e.message);await refresh();}};
$('cancel').onclick=async()=>{try{render(await api('/api/tasks/'+current+'/cancel',{}));}catch(e){error(e.message);}};
$('export').onclick=async()=>{try{const t=await api('/api/tasks/'+current),url=URL.createObjectURL(new Blob([JSON.stringify(t,null,2)],{type:'application/json'})),a=el('a');a.href=url;a.download='agentpair-'+t.id+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}catch(e){error(e.message);}};
if(!readonly)(async()=>{try{const session=await api('/api/session');csrf=session.csrf||'';logged=true;admin=session.role==='admin';applyAccess();await refresh();const items=await api('/api/tasks');if(admin)$('new-task').click();else if(items.items.length)render(await api('/api/tasks/'+items.items[0].id));}catch(_){$('connection').textContent='等待登录';}})();setInterval(refresh,2000);
const renderConversation=render;
render=t=>{$('pair-board').classList.remove('hidden');renderDeliverables(t);renderConversation(t);applyAccess();pairFlow.update(t);const cards=document.querySelectorAll('#messages .card');t.messages.forEach((m,i)=>{if(m.answer?.code){cards[i].append(el('h4','Driver 实现建议 · 未执行测试'),el('pre',m.answer.code));}if(m.answer?.citationWarning){cards[i].append(el('div','模型给出了本任务未提供的证据编号；这些引用无效，不作为验收依据。','notice caution'));}});if(readonly){$('reply-form').classList.add('hidden');$('login').classList.add('hidden');$('create').classList.add('hidden');$('new-task').textContent='公开协作回放';$('new-task').disabled=true;$('budget').textContent='编程示例 · 未执行生成代码';}};
if(readonly){logged=false;$('login').classList.add('hidden');$('new-task').disabled=true;fetch('/api/demo',{cache:'no-store'}).then(r=>{if(!r.ok)throw Error('Demo unavailable');return r.json();}).then(t=>{render(t);$('connection').textContent='● 历史编程协作 · 无实时调用';const item=el('div',t.title,'task-item selected');$('task-list').append(item);}).catch(e=>error(e.message));}
