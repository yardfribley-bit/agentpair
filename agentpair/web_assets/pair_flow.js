/* Agent handoffs, not simulated model reasoning or network packet telemetry. */
window.PairFlow=class PairFlow{
 constructor(root){this.root=root;this.timer=null;this.step=0;this.replaying=false;
  root.innerHTML='<div class="flow-top"><div><span class="flow-eyebrow">PAIR PROGRAMMING · COLLABORATION</span><h2>两个角色，一场持续的协作。</h2></div><button class="flow-replay" type="button">▶ 回放本轮交接</button></div><div class="flow-canvas"><svg class="flow-wires" viewBox="0 0 800 260" aria-hidden="true"><defs><marker id="arrow-n" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8" fill="#83dfc1"/></marker><marker id="arrow-d" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8" fill="#a6a0fa"/></marker></defs><path class="wire wire-plan" d="M238 86 C340 30 460 30 562 86" marker-end="url(#arrow-n)"/><path class="wire wire-feedback" d="M562 172 C460 234 340 234 238 172" marker-end="url(#arrow-d)"/><circle class="packet packet-plan" r="5" fill="#a6f1d5"><animateMotion dur="1.8s" repeatCount="indefinite" path="M238 86 C340 30 460 30 562 86"/></circle><circle class="packet packet-feedback" r="5" fill="#c0bcff"><animateMotion dur="1.8s" repeatCount="indefinite" path="M562 172 C460 234 340 234 238 172"/></circle></svg><button class="flow-agent flow-nav" type="button"><span class="flow-avatar">N</span><strong>Navigator</strong><span class="agent-job">规划 · 检查 · 调整方向</span><span class="agent-state">等待任务</span><span class="agent-preview">点击查看规划和复核消息</span></button><div class="handoff handoff-plan"><span>计划与约束</span><b>Navigator → Driver</b></div><div class="handoff handoff-feedback"><span>实现 / 分析与证据</span><b>Driver → Navigator</b></div><button class="flow-agent flow-driver" type="button"><span class="flow-avatar">D</span><strong>Driver</strong><span class="agent-job">实现 · 取证 · 返回结果</span><span class="agent-state">等待任务</span><span class="agent-preview">点击查看执行和分析消息</span></button></div><div class="flow-bottom"><span class="flow-round">第 1 轮</span><span class="flow-phase">等待任务</span><span class="flow-mode">运行事件驱动</span></div><div class="flow-message"><span class="flow-message-label">最新交接</span><p>发布任务后，消息会沿着这两条方向传递。</p></div><div class="flow-steps"><span data-stage="plan">1 Navigator 规划</span><span data-stage="driver">2 Driver 实施</span><span data-stage="review">3 Navigator 复核</span></div><dialog class="flow-dialog"><button class="flow-close" type="button">关闭 ×</button><h3></h3><pre></pre></dialog>';
  this.q=s=>root.querySelector(s);this.q('.flow-replay').onclick=()=>this.replay();this.q('.flow-close').onclick=()=>this.q('dialog').close();this.q('.flow-nav').onclick=()=>this.inspect('navigator');this.q('.flow-driver').onclick=()=>this.inspect('driver');
 }
 update(data){this.data=data;this.round=data.round||1;this.records=data.messages||[];this.events=data.events||[];this.snapshot=!!data.snapshot;this.status=data.status;this.root.classList.toggle('has-task',!!data.id);this.q('.flow-round').textContent='第 '+this.round+' 轮';this.q('.flow-mode').textContent=this.snapshot?'历史回放 · 不发起模型调用':'真实阶段事件 · 非逐 token 流';if(!this.replaying)this.live();}
 live(){const started=this.events.filter(e=>e.round===this.round&&e.kind==='stage_started').at(-1);const active=['running','cancelling'].includes(this.status)&&started?started.stage:null;this.draw(active,false);this.progress(active);}
 progress(active){
  if(!this.q('.task-progress')){const box=document.createElement('section');box.className='task-progress';box.setAttribute('aria-live','polite');this.root.prepend(box);}
  const box=this.q('.task-progress');box.replaceChildren();
  const add=(tag,text,cls)=>{const n=document.createElement(tag);n.textContent=text;if(cls)n.className=cls;box.append(n);return n;};
  const events=this.events.filter(e=>e.round===this.round),done=events.filter(e=>e.kind==='stage_completed');
  const names={plan:'Navigator 正在拆解任务',driver:'Driver 正在生成实现或分析结果',review:'Navigator 正在检查结果'};
  const states={queued:'任务已提交，等待开始',completed:'本轮完成',failed:'任务中断',cancelled:'任务已停止',cancelling:'正在停止，等待当前调用返回',interrupted:'服务重启，本轮已中断',idle:'等待你发布任务'};
  add('small',this.data.title||'实时协作');
  add('small',({'local':'默认协作 · 0 台云 Driver','pair':'云端结对 · 1 台 Driver','parallel':'并行探索 · A/B 两台 Driver，Navigator 综合评审'})[this.data.engineeringMethod||'local']);
  if(this.data.engineeringMethod==='parallel'){
   for(const role of ['Driver A','Driver B']){
    const latest=this.events.filter(e=>e.round===this.round&&e.role===role&&e.kind.startsWith('branch_')).at(-1);
    add('p',role+'：'+(latest?.text||'等待规划两条不同路线'),'progress-outcome');
   }
   for(const handoff of this.events.filter(e=>e.round===this.round&&e.kind==='branch_handoff')){
    const intel=handoff.intelligence||{};
    const detail=add('details','','progress-outcome');
    const heading=document.createElement('summary');heading.textContent=handoff.role+' → '+handoff.to+' · '+
      (handoff.phase==='review_peer'?'共享发现':'反馈与修订');detail.append(heading);
    const note=document.createElement('p');note.textContent=intel.summary||handoff.text;detail.append(note);
    for(const finding of (intel.findings||[])){
     const item=document.createElement('p');item.textContent='发现：'+(finding.claim||finding.topic||'')+
       (finding.evidenceRefs?.length?' · 证据 '+finding.evidenceRefs.join(', '):' · 待核查证据');detail.append(item);
    }
    for(const question of (intel.questions||[])){
     const item=document.createElement('p');item.textContent='待核查：'+question;detail.append(item);
    }
   }
  }
  add('h2',states[this.status]||names[active]||'正在交接任务');
  if(this.status==='blocked'){
   const attempts=events.filter(e=>e.kind==='rework').length;
   box.querySelector('h2').textContent=attempts
    ?`验收未通过 · 已自动整改 ${attempts} 轮，仍需补充`
    :'验收未通过 · 正在等待补充或能力支持';
  }
  const user=this.records.filter(m=>m.role==='user'&&m.round===this.round).at(-1);
  add('p',user?.text||'提交任务后，这里会显示当前步骤和角色交接。','task-goal');
  const track=add('div','','progress-track');
  const labels=['收到任务','规划','Driver 处理','复核','本轮完成'];
  const complete=this.status==='completed';
  labels.forEach((label,i)=>{const n=document.createElement('span');n.textContent=label;const stage=['plan','driver','review'][i-1];n.className=(i===0&&this.data.id||i===4&&complete||done.some(e=>e.stage===stage))?'finished':'';if(stage===active)n.classList.add('active');track.append(n);});
  const failure=events.filter(e=>e.errorType).at(-1);
  const result=this.records.filter(m=>m.round===this.round&&m.stage==='review').at(-1);
  if(result?.answer){
   add('h3',complete?'任务结果摘要':'当前答复摘要');
   const final=result.answer.finalAnswer;
   const answer=final||result.answer.summary||(result.answer.findings||[]).map(f=>f.claim).join('；');
   add('p',answer.length>220?answer.slice(0,220)+'…':answer||'尚无可交付结论，请查看验收记录。','final-answer');
   if(answer.length>220)add('small','完整内容在下方「成果与代码」中。');
  }
  this.trace(box,events);
  if(result?.answer?.decision){
   if(result.answer.jev)add('small',result.answer.jev.status==='evaluated'?'独立决策：'+result.answer.jev.model+' · '+result.answer.jev.provider:result.answer.jev.note);
   const decision=result.answer.decision;
   const audit=document.createElement('details');const title=document.createElement('summary');title.textContent='查看 Navigator 验收依据';audit.append(title);box.append(audit);
   const auditAdd=(tag,text)=>{const node=document.createElement(tag);node.textContent=text;audit.append(node);};
   for(const check of decision.checks){auditAdd('p',({yes:'✓',no:'✕',unknown:'?'}[check.value]||'?')+' '+check.question+'：'+check.reason+'（'+(check.source==='deterministic'?'程序核验':check.source==='jev'?'独立决策模型':'模型判断')+'）','progress-outcome');}
   auditAdd('small',decision.confidenceNote);
  }
  if(this.status==='blocked'){
   const corrections=result?.answer?.corrections||result?.answer?.nextSteps||[];
   add('p',corrections.length?'最后一次验收意见：'+corrections.join('；'):'缺少完成任务所需的证据或能力。','progress-outcome');
   add('p','可在下方追加要求继续推进；Navigator 会带上本轮验收意见重新规划，不会把未通过结果标成完成。','progress-outcome');
  }
  add('p',this.status==='failed'?'停止原因：'+(failure?.errorType||'请展开运行记录查看'):
    complete?'':
    this.status==='queued'?'已进入队列，尚未开始模型调用。':active?'已完成 '+done.length+' / 3 个处理阶段；等待当前角色返回真实结果。':'','progress-outcome');
  this.q('.flow-replay').hidden=['queued','running','cancelling'].includes(this.status);
  this.q('.flow-top h2').textContent=this.data.title?'角色协作与交接':'发布任务，开始协作';
 }
 trace(box,events){
  const panel=document.createElement('section');panel.className='collaboration-trace';box.append(panel);
  const heading=document.createElement('div');heading.className='trace-heading';panel.append(heading);
  const title=document.createElement('h3');title.textContent='协作现场';heading.append(title);
  const hint=document.createElement('small');hint.textContent='来自真实阶段与交接事件 · 不展示模型私有推理或虚构逐字直播';heading.append(hint);
  const roles=['navigator','Driver A','Driver B'];
  if(this.data.engineeringMethod!=='parallel')roles.splice(1,2,'driver');
  const lanes=document.createElement('div');lanes.className='trace-lanes';panel.append(lanes);
  for(const role of roles){
   const lane=document.createElement('div');lane.className='trace-lane';lanes.append(lane);
   const name=document.createElement('strong');name.textContent=role==='navigator'?'Navigator':role==='driver'?'Driver':role;lane.append(name);
   const relevant=events.filter(e=>e.role===role||e.to===role);
   const latestOwn=events.filter(e=>e.role===role).at(-1);
   const running=['running','cancelling'].includes(this.status)&&
     ['stage_started','branch_progress'].includes(latestOwn?.kind);
   const state=document.createElement('span');state.className='trace-state'+(running?' is-active':'');
   state.textContent=running?'处理中':relevant.length?'最近活动 '+this.eventLabel(relevant.at(-1)):'等待分配';lane.append(state);
  }
  const details=document.createElement('details');details.className='trace-details';details.open=true;panel.append(details);
  const summary=document.createElement('summary');summary.textContent='查看活动与消息 · '+events.length+' 条记录';details.append(summary);
  const timeline=document.createElement('ol');timeline.className='trace-timeline';details.append(timeline);
  const visible=events.filter(e=>['stage_started','stage_completed','handoff_requested','branch_progress','branch_handoff','branch_handoff_processed','branch_completed','branch_failed','tool_result','rework','resource_decision','stopped','interrupted'].includes(e.kind));
  for(const e of visible.slice(-24)){
   const row=document.createElement('li');row.className='trace-event trace-'+e.kind;timeline.append(row);
   const meta=document.createElement('div');meta.className='trace-meta';row.append(meta);
   const time=document.createElement('time');time.textContent=e.at?new Date(e.at).toLocaleTimeString('zh-CN',{hour12:false}):'—';meta.append(time);
   const actor=document.createElement('strong');actor.textContent=(e.from||e.role||'系统')+(e.to?' → '+e.to:'');meta.append(actor);
   const kind=document.createElement('span');kind.textContent=this.eventLabel(e);meta.append(kind);
   const content=document.createElement('p');content.textContent=e.message?.summary||e.summary||e.text||this.stageText(e);row.append(content);
   if(e.message?.id){const id=document.createElement('small');id.textContent='消息 #'+e.message.id.slice(0,8)+(e.kind==='branch_handoff'?' · 已交给执行阶段，尚未确认处理':'');row.append(id);}
   if(e.messageId){const id=document.createElement('small');id.textContent='关联消息 #'+e.messageId.slice(0,8)+' · 阶段已返回';row.append(id);}
   if(e.kind==='branch_handoff_processed'&&e.message?.id){const receipt=document.createElement('small');receipt.textContent='消息 #'+e.message.id.slice(0,8)+' · 接收分支已返回结果';row.append(receipt);}
   const findings=e.intelligence?.findings||[];
   for(const finding of findings.slice(0,3)){const note=document.createElement('small');note.textContent='发现：'+(finding.claim||finding.topic||'')+(finding.evidenceRefs?.length?' · 证据 '+finding.evidenceRefs.join(', '):' · 无证据编号');row.append(note);}
   if(e.receipt){const receipt=document.createElement('small');receipt.textContent='工具：'+(e.receipt.tool||'未知')+' · '+(e.receipt.ok?'成功':'失败');row.append(receipt);}
  }
  if(!visible.length){const empty=document.createElement('li');empty.textContent='任务开始后，实际交接和阶段结果会出现在这里。';timeline.append(empty);}
 }
 eventLabel(e){return ({stage_started:'开始',stage_completed:'返回结果',handoff_requested:'任务交接',branch_progress:'执行中',branch_handoff:'共享情报',branch_handoff_processed:'处理完成',branch_completed:'分支完成',branch_failed:'分支失败',tool_result:'工具记录',rework:'返工',resource_decision:'资源决策',stopped:'停止',interrupted:'中断'})[e.kind]||e.kind;}
 stageText(e){return ({plan:'拆解任务与约束',driver:'执行分配的任务',review:'检查证据和验收标准'})[e.stage]||'状态已更新';}
 draw(stage,replay){this.root.dataset.phase=stage||'idle';this.root.classList.toggle('flow-animating',!!stage);this.root.classList.toggle('flow-replaying',replay);
  const state={plan:['正在规划','等待计划'],driver:['等待 Driver 返回','正在实施'],review:['正在复核','结果已交接']}[stage]||[this.status==='completed'||this.status==='analysis_completed'?'本轮复核完成':'等待任务','等待下一轮'];this.q('.flow-nav .agent-state').textContent=state[0];this.q('.flow-driver .agent-state').textContent=state[1];this.q('.flow-phase').textContent=(replay?'记录回放 · ':'')+({plan:'用户要求 → Navigator 规划',driver:'Navigator 将计划与历史交给 Driver',review:'Driver 返回结果 → Navigator 复核'})[stage]||'本轮结束 / 等待下一条要求';
  if(!stage)this.q('.flow-phase').textContent=this.status==='failed'?'本轮失败 · 查看停止原因':this.status==='cancelled'?'已取消 · 无后续调用':this.status==='completed'||this.status==='analysis_completed'?'本轮结束 · 可继续对话':'等待发布任务';
  for(const role of ['navigator','driver']){const rec=this.records.filter(m=>m.role===role&&m.round===this.round).at(-1);this.q(role==='navigator'?'.flow-nav .agent-preview':'.flow-driver .agent-preview').textContent=rec?.answer?.summary?.slice(0,72)||'点击查看已记录的消息';}
  this.root.querySelectorAll('[data-stage]').forEach(n=>{const done=this.events.some(e=>e.round===this.round&&e.kind==='stage_completed'&&e.stage===n.dataset.stage);n.classList.toggle('done',done);n.classList.toggle('current',n.dataset.stage===stage);});
  const sourceStage=stage==='review'?'driver':stage==='driver'?'plan':'review';const m=this.records.filter(m=>m.round===this.round&&m.stage===sourceStage).at(-1);this.q('.flow-message p').textContent=m?.answer?.summary||this.events.filter(e=>e.round===this.round&&e.kind==='handoff_requested').at(-1)?.summary||'点击角色面板可查看真实消息；回放仅重现已记录的阶段，不代表现在正在执行。';
 }
 replay(){if(this.replaying){clearInterval(this.timer);this.replaying=false;this.q('.flow-replay').textContent='▶ 回放本轮交接';this.live();return;}const stages=['plan','driver','review'].filter(s=>this.events.some(e=>e.round===this.round&&e.stage===s&&e.kind==='stage_completed'));if(!stages.length)return;this.replaying=true;this.step=0;this.q('.flow-replay').textContent='■ 停止回放';this.draw(stages[0],true);this.timer=setInterval(()=>{this.step++;if(this.step>=stages.length){clearInterval(this.timer);this.replaying=false;this.q('.flow-replay').textContent='▶ 回放本轮交接';this.live();}else this.draw(stages[this.step],true);},2400);}
 inspect(role){const records=this.records.filter(m=>m.role===role&&m.round===this.round);const dialog=this.q('dialog');dialog.querySelector('h3').textContent=role==='navigator'?'Navigator · 本轮规划与复核':'Driver · 本轮执行与分析';dialog.querySelector('pre').textContent=records.length?records.map(m=>{const a=m.answer||{};return [m.stage==='plan'?'规划':m.stage==='review'?'复核与交付':'执行结果',a.finalAnswer||a.summary||m.text,...(a.steps||[]),...(a.findings||[]).map(f=>f.claim)].filter(Boolean).join('\n\n');}).join('\n\n────────\n\n'):'该角色本轮尚未返回消息。';dialog.showModal();}
};
