/* Agent handoffs, not simulated model reasoning or network packet telemetry. */
window.PairFlow=class PairFlow{
 constructor(root){this.root=root;this.timer=null;this.step=0;this.replaying=false;
  root.innerHTML='<div class="flow-top"><div><span class="flow-eyebrow">PAIR PROGRAMMING · COLLABORATION</span><h2>两个角色，一场持续的协作。</h2></div><button class="flow-replay" type="button">▶ 回放本轮交接</button></div><div class="flow-canvas"><svg class="flow-wires" viewBox="0 0 800 260" aria-hidden="true"><defs><marker id="arrow-n" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8" fill="#83dfc1"/></marker><marker id="arrow-d" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8" fill="#a6a0fa"/></marker></defs><path class="wire wire-plan" d="M238 86 C340 30 460 30 562 86" marker-end="url(#arrow-n)"/><path class="wire wire-feedback" d="M562 172 C460 234 340 234 238 172" marker-end="url(#arrow-d)"/><circle class="packet packet-plan" r="5" fill="#a6f1d5"><animateMotion dur="1.8s" repeatCount="indefinite" path="M238 86 C340 30 460 30 562 86"/></circle><circle class="packet packet-feedback" r="5" fill="#c0bcff"><animateMotion dur="1.8s" repeatCount="indefinite" path="M562 172 C460 234 340 234 238 172"/></circle></svg><button class="flow-agent flow-nav" type="button"><span class="flow-avatar">N</span><strong>Navigator</strong><span class="agent-job">规划 · 检查 · 调整方向</span><span class="agent-state">等待任务</span><span class="agent-preview">点击查看规划和复核消息</span></button><div class="handoff handoff-plan"><span>计划与约束</span><b>Navigator → Driver</b></div><div class="handoff handoff-feedback"><span>实现 / 分析与证据</span><b>Driver → Navigator</b></div><button class="flow-agent flow-driver" type="button"><span class="flow-avatar">D</span><strong>Driver</strong><span class="agent-job">实现 · 取证 · 返回结果</span><span class="agent-state">等待任务</span><span class="agent-preview">点击查看执行和分析消息</span></button></div><div class="flow-bottom"><span class="flow-round">第 1 轮</span><span class="flow-phase">等待任务</span><span class="flow-mode">运行事件驱动</span></div><div class="flow-message"><span class="flow-message-label">最新交接</span><p>发布任务后，消息会沿着这两条方向传递。</p></div><div class="flow-steps"><span data-stage="plan">1 Navigator 规划</span><span data-stage="driver">2 Driver 实施</span><span data-stage="review">3 Navigator 复核</span></div><dialog class="flow-dialog"><button class="flow-close" type="button">关闭 ×</button><h3></h3><pre></pre></dialog>';
  this.q=s=>root.querySelector(s);this.q('.flow-replay').onclick=()=>this.replay();this.q('.flow-close').onclick=()=>this.q('dialog').close();this.q('.flow-nav').onclick=()=>this.inspect('navigator');this.q('.flow-driver').onclick=()=>this.inspect('driver');
 }
 update(data){this.data=data;this.round=data.round||1;this.records=data.messages||[];this.events=data.events||[];this.snapshot=!!data.snapshot;this.status=data.status;this.q('.flow-round').textContent='第 '+this.round+' 轮';this.q('.flow-mode').textContent=this.snapshot?'历史回放 · 不发起模型调用':'真实阶段事件 · 非逐 token 流';if(!this.replaying)this.live();}
 live(){const started=this.events.filter(e=>e.round===this.round&&e.kind==='stage_started').at(-1);const active=['running','cancelling'].includes(this.status)&&started?started.stage:null;this.draw(active,false);this.progress(active);}
 progress(active){
  if(!this.q('.task-progress')){const box=document.createElement('section');box.className='task-progress';box.setAttribute('aria-live','polite');this.root.prepend(box);}
  const box=this.q('.task-progress');box.replaceChildren();
  const add=(tag,text,cls)=>{const n=document.createElement(tag);n.textContent=text;if(cls)n.className=cls;box.append(n);return n;};
  const events=this.events.filter(e=>e.round===this.round),done=events.filter(e=>e.kind==='stage_completed');
  const names={plan:'Navigator 正在拆解任务',driver:'Driver 正在生成实现或分析结果',review:'Navigator 正在检查结果'};
  const states={queued:'任务已提交，等待开始',completed:'本轮完成',failed:'任务中断',cancelled:'任务已停止',cancelling:'正在停止，等待当前调用返回',interrupted:'服务重启，本轮已中断',idle:'等待你发布任务'};
  add('small',this.data.title||'实时协作');
  add('h2',states[this.status]||names[active]||'正在交接任务');
  if(this.status==='blocked')box.querySelector('h2').textContent='未完成 · Navigator 未通过验收';
  const user=this.records.filter(m=>m.role==='user'&&m.round===this.round).at(-1);
  add('p',user?.text||'提交任务后，这里会显示当前步骤和角色交接。','task-goal');
  const track=add('div','','progress-track');
  const labels=['收到任务','规划','Driver 处理','复核','本轮完成'];
  const complete=this.status==='completed';
  labels.forEach((label,i)=>{const n=document.createElement('span');n.textContent=label;const stage=['plan','driver','review'][i-1];n.className=(i===0&&this.data.id||i===4&&complete||done.some(e=>e.stage===stage))?'finished':'';if(stage===active)n.classList.add('active');track.append(n);});
  const failure=events.filter(e=>e.errorType).at(-1);
  const result=this.records.filter(m=>m.round===this.round&&m.stage==='review').at(-1);
  if(this.status==='blocked')add('p',result?.answer?.summary||'缺少完成任务所需的证据或能力。','progress-outcome');
  add('p',this.status==='failed'?'停止原因：'+(failure?.errorType||'请展开运行记录查看'):
    complete?(result?.answer?.summary||'结果已返回，可以继续补充要求。'):
    this.status==='queued'?'已进入队列，尚未开始模型调用。':active?'已完成 '+done.length+' / 3 个处理阶段；等待当前角色返回真实结果。':'','progress-outcome');
  this.q('.flow-replay').hidden=['queued','running','cancelling'].includes(this.status);
  this.q('.flow-top h2').textContent=this.data.title?'角色协作与交接':'发布任务，开始协作';
 }
 draw(stage,replay){this.root.dataset.phase=stage||'idle';this.root.classList.toggle('flow-animating',!!stage);this.root.classList.toggle('flow-replaying',replay);
  const state={plan:['正在规划','等待计划'],driver:['等待 Driver 返回','正在实施'],review:['正在复核','结果已交接']}[stage]||[this.status==='completed'||this.status==='analysis_completed'?'本轮复核完成':'等待任务','等待下一轮'];this.q('.flow-nav .agent-state').textContent=state[0];this.q('.flow-driver .agent-state').textContent=state[1];this.q('.flow-phase').textContent=(replay?'记录回放 · ':'')+({plan:'用户要求 → Navigator 规划',driver:'Navigator 将计划与历史交给 Driver',review:'Driver 返回结果 → Navigator 复核'})[stage]||'本轮结束 / 等待下一条要求';
  if(!stage)this.q('.flow-phase').textContent=this.status==='failed'?'本轮失败 · 查看停止原因':this.status==='cancelled'?'已取消 · 无后续调用':this.status==='completed'||this.status==='analysis_completed'?'本轮结束 · 可继续对话':'等待发布任务';
  for(const role of ['navigator','driver']){const rec=this.records.filter(m=>m.role===role&&m.round===this.round).at(-1);this.q(role==='navigator'?'.flow-nav .agent-preview':'.flow-driver .agent-preview').textContent=rec?.answer?.summary?.slice(0,72)||'点击查看已记录的消息';}
  this.root.querySelectorAll('[data-stage]').forEach(n=>{const done=this.events.some(e=>e.round===this.round&&e.kind==='stage_completed'&&e.stage===n.dataset.stage);n.classList.toggle('done',done);n.classList.toggle('current',n.dataset.stage===stage);});
  const sourceStage=stage==='review'?'driver':stage==='driver'?'plan':'review';const m=this.records.filter(m=>m.round===this.round&&m.stage===sourceStage).at(-1);this.q('.flow-message p').textContent=m?.answer?.summary||this.events.filter(e=>e.round===this.round&&e.kind==='handoff_requested').at(-1)?.summary||'点击角色面板可查看真实消息；回放仅重现已记录的阶段，不代表现在正在执行。';
 }
 replay(){if(this.replaying){clearInterval(this.timer);this.replaying=false;this.q('.flow-replay').textContent='▶ 回放本轮交接';this.live();return;}const stages=['plan','driver','review'].filter(s=>this.events.some(e=>e.round===this.round&&e.stage===s&&e.kind==='stage_completed'));if(!stages.length)return;this.replaying=true;this.step=0;this.q('.flow-replay').textContent='■ 停止回放';this.draw(stages[0],true);this.timer=setInterval(()=>{this.step++;if(this.step>=stages.length){clearInterval(this.timer);this.replaying=false;this.q('.flow-replay').textContent='▶ 回放本轮交接';this.live();}else this.draw(stages[this.step],true);},2400);}
 inspect(role){const records=this.records.filter(m=>m.role===role&&m.round===this.round);const dialog=this.q('dialog');dialog.querySelector('h3').textContent=role==='navigator'?'Navigator · 本轮规划与复核':'Driver · 本轮执行与分析';dialog.querySelector('pre').textContent=records.length?JSON.stringify(records,null,2):'该角色本轮尚未返回消息。';dialog.showModal();}
};
