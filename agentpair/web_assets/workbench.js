// Evidence-led workspace: no simulated browser screenshots or invented progress.
const credentialsScript=document.createElement('script');credentialsScript.src='/credentials.js';document.body.append(credentialsScript);
const bench=document.createElement('section');bench.id='execution-workbench';bench.className='card hidden';
document.querySelector('main').append(bench);
const previousRender=render;
render=task=>{
 previousRender(task);bench.classList.remove('hidden');bench.replaceChildren();
 for(const m of task.messages.filter(m=>m.stage==='driver'&&m.round===task.round))for(const artifact of m.answer?.artifacts||[]){
  const button=el('button','下载 '+artifact.path,'secondary');button.type='button';button.onclick=()=>{const u=URL.createObjectURL(new Blob([artifact.content],{type:'text/plain;charset=utf-8'}));const a=el('a');a.href=u;a.download=artifact.path.split('/').at(-1);a.click();setTimeout(()=>URL.revokeObjectURL(u),1000);};deliverables.append(button);
 }
 bench.append(el('div','DRIVER WORKSPACE','eyebrow'),el('h2','执行现场'));
 const busy=['running','queued'].includes(task.status);bench.classList.toggle('is-working',busy);
 const events=task.events.filter(e=>e.round===task.round);
 const latest=events.at(-1);bench.append(el('p',busy?(latest?.text||'Navigator 正在安排下一步'):(labels[task.status]||task.status),'workspace-state'));
 const steps=events.filter(e=>e.receipt).map(e=>({...e.receipt,at:e.at}));
 if(!steps.length)for(const m of task.messages.filter(m=>m.round===task.round))steps.push(...(m.answer?.toolSteps||[]));
 const observation=[...steps].reverse().find(s=>s.ok&&(s.result?.text||s.result?.nodes));
 const preview=el('div',null,'observation');preview.append(el('small','浏览器观察 · DOM / 文本，不是截图'));
 if(observation){
  preview.append(el('h3',observation.result.title||'页面内容'),el('small',observation.result.url||''));
  const content=observation.result.text||observation.result.nodes.map(n=>n.name||n.text||'').filter(Boolean).join('\n');
  preview.append(el('pre',content.slice(0,10000)));
 }else preview.append(el('p','执行浏览器读取后，真实页面内容将出现在这里。'));
 bench.append(preview);
 const timeline=el('ol',null,'tool-timeline');
 for(const step of steps){const li=el('li',null,step.ok?'tool-ok':'tool-failed');
  li.append(el('strong',(step.tool||step.action?.op||'工具调用')+' · '+(step.ok?'已返回':'失败')));
  li.append(el('small',step.evidenceId||''));const details=el('details');details.append(el('summary','查看输入与证据'),el('pre',JSON.stringify({arguments:step.arguments,result:step.result,error:step.reason||step.error},null,2)));li.append(details);timeline.append(li);
 }
 bench.append(el('h3','工具时间线'),timeline);
 if(!steps.length)bench.append(el('p','还没有工具返回。规划与模型请求不等于执行成功。','muted'));
};
const newTask=$('new-task').onclick;$('new-task').onclick=()=>{newTask();bench.classList.add('hidden');};
const demo=el('button','试试：浏览一个网页','secondary');demo.type='button';
demo.onclick=()=>{$('title').value='阅读网页并验证主要内容';$('goal').value='打开 https://example.com/，读取页面标题和正文，告诉我这个页面的用途。请引用真实浏览器证据，不要根据记忆回答。';$('acceptance').value='实际打开页面；返回标题、来源 URL 和正文摘要；不执行登录或提交操作。';methodSelect.value='pair';methodSelect.onchange();executionSelect.value='browser';};
document.querySelector('.brief-guide').append(demo);
methodOptions.pair[1]='Navigator 规划与复核，独立 Driver 执行。优先复用租约；浏览器与原生工具运行在云机，不使用 Docker。参考 ¥0.15/小时，模型费用另计。';methodSelect.onchange();
executionSelect.replaceChildren();for(const [value,label] of [['none','讨论与分析 · 不运行工具'],['browser','网页助手 · 打开 / 读取 / 点击（实验性）'],['native','原生开发 · 文件 / 终端 / 浏览器（实验性）']]){const option=el('option',label);option.value=value;executionSelect.append(option);}
executionSelect.onchange=()=>{if(executionSelect.value!=='none'){methodSelect.value='pair';methodSelect.onchange();}};
document.querySelector('.brand small').textContent='YOUR AGENT WORKSPACE';
document.querySelector('.crumb').textContent='工作台 / 任务与成果';
document.querySelector('.delivery-note p').textContent='结果、浏览器证据、执行记录和限额内的文本文件保存在任务中，可直接下载。最多保存 20 个文本文件、合计 250KB；大型文件与二进制尚不自动回传。不会自动推送 GitHub。';
