(()=>{'use strict';
const $=id=>document.getElementById(id),set=(id,v)=>$(id).textContent=v,params=new URLSearchParams(location.search);
const globalView=params.get('scope')!=='mine';
let data=null,selected=null,view='categories',tab='original',loading=false,findAt=-1,csrf='',detailGeneration=0,loadGeneration=0;
const option=(v,t)=>{const o=document.createElement('option');o.value=v;o.textContent=t;return o;};
const call=()=>data?.calls.find(c=>c.id===$('request').value);
const items=()=> (data?.items||[]).filter(i=>i.requestId===$('request').value);
const bytes=n=>n>=1024?(n/1024).toFixed(1)+' KB':n+' B';
const dateText=t=>Number.isFinite(t)&&t>0?new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}).format(new Date(t*1000))+'（北京时间）':'时间未采集';
function scopeText(){set('search-scope','搜索范围：'+($('device').selectedOptions[0]?.textContent||'未选择设备')+' / '+$('collector').selectedOptions[0].textContent);}
async function api(p){const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),30000);try{const r=await fetch(p,{credentials:'same-origin',cache:'no-store',signal:controller.signal}),d=await r.json();if(!r.ok)throw Error(d.error||'读取失败');return d;}catch(e){throw Error(e.name==='AbortError'?'读取超时，请点击重试':e.message);}finally{clearTimeout(timeout);}}
function failure(e){set('status',e.message);set('empty','读取失败：'+e.message);$('empty').hidden=false;set('receipt','读取失败');}
const endpoint=()=> (globalView?'/api/audit/model-data/':'/api/devices/model-data/')+encodeURIComponent($('device').value);
async function hydrate(){const n=++detailGeneration,id=$('request').value;if(!id){selected=null;render();return;}set('empty','正在读取选中调用…');$('empty').hidden=false;set('receipt','正在读取选中调用');set('original','正在读取选中调用…');set('detail-title','正在读取选中调用');$('rows').replaceChildren();try{const detail=await api(endpoint()+'?collector='+encodeURIComponent($('collector').value)+'&request='+encodeURIComponent(id));if(n!==detailGeneration)return;data.items=detail.items;const index=data.calls.findIndex(c=>c.id===id);if(index>=0&&detail.calls[0])data.calls[index]=detail.calls[0];selected=items().find(i=>i.id===selected?.id)||items()[0]||null;render();set('status','');}catch(e){if(n===detailGeneration)failure(e);}}
function choose(i){selected=i;tab='original';findAt=-1;render();}
function readerText(){return tab==='full'||view==='full'?call()?.body||'无原文':selected?.rawContent||'请选择分类内容';}
function render(){
 const c=call(),q=$('search').value.toLowerCase(),list=items().filter(i=>[i.name,i.category,i.rawContent].join(' ').toLowerCase().includes(q));
 set('source',c?.collector==='sessionlens'?'来源：SessionLens · '+c.application:c?.source==='workbuddy_network_context'?'来源：AgentReins 完整 HTTP 请求体通道':'来源：WorkBuddy generation');set('capture-layer',c?.collector==='sessionlens'?'源日志会话事件':c?.source==='workbuddy_network_context'?'同一 HTTP 请求体':'应用记录的装配上下文');set('destination',c?.destination?'目标：'+c.destination:'目标 / 网络发送未验证');set('model','模型：'+(c?.model||'未采集'));$('model').title=c?.modelEvidence||'未取得模型证据';set('size',c?bytes(c.bodyBytes)+' · '+dateText(c.timestamp):'暂无调用');
 set('truncate',c?.wireLengthMatched?'捕获请求体长度校验一致':c?.truncated?'源记录已触发截断':c?.recordStatus==='parseable'?'源记录可解析 · 未触发截断':'源记录未验证');set('completeness',c?.wireLengthMatched?'SHA256 及声明/捕获长度一致；不代表远端处理成功':c?.truncated?'源记录已触发截断，缺失内容未知':c?.recordStatus==='parseable'?'JSON 可解析、未触发截断；网络请求完整性未验证':'源记录未验证');
 set('coverage',c?.collector==='sessionlens'?'这是一条源日志事件，可查看提问、回复、已记录思路或工具数据；不代表完整模型请求。':c?.truncated?'这份源记录可能被截断。展示全部已采集内容，不补造缺失内容。':c?.source==='workbuddy_network_context'?'沿用 AgentReins 完整请求体采集，保留原文，不采模型返回或独立工具事件。':'当前采集层是应用装配记录，非网络抓包；分类不改变原文。');
 set('receipt',c?.bodySHA256?'平台已入库 · SHA256 '+c.bodySHA256.slice(0,12)+'…':'暂无回执');
 $('rows').replaceChildren();$('empty').hidden=!!list.length;set('empty',c?'没有匹配的分类内容':'尚无模型输入记录');
 for(const i of list){const tr=document.createElement('tr');tr.tabIndex=0;tr.classList.toggle('selected',selected?.id===i.id);
 for(const value of [i.category,i.source,bytes(i.bodyBytes),i.classificationBasis,'未独立验证']){const td=document.createElement('td');td.textContent=value;tr.append(td);}
 tr.onclick=()=>choose(i);tr.onkeydown=e=>{if(e.key==='Enter'){choose(i);}};$('rows').append(tr);}
 set('detail-title',tab==='full'||view==='full'?'完整已采集模型输入':selected?.name||'选择一类上下文');
 set('original',readerText());$('original').hidden=tab==='basis';$('basis-text').hidden=tab!=='basis';
 set('basis-text',selected?.classificationBasis||'请选择数据项');set('basis',selected?.classificationBasis||'完整原文未分类替换');
 set('position',selected?'消息 '+(selected.messageIndex+1)+' / 内容块 '+(selected.blockIndex+1)+' / 字符 '+selected.charStart+'–'+selected.charEnd:'');
 set('raw',JSON.stringify(selected?{...selected,rawContent:undefined}:c?{...c,body:undefined}:{},null,2));
 for(const b of $('detail-tabs').children)b.classList.toggle('selected',b.dataset.tab===tab);
 for(const b of $('view-tabs').children)b.classList.toggle('selected',b.dataset.view===view);
}
async function requests(){const previous=$('request').value||params.get('request'),session=$('session').value;const calls=(data?.calls||[]).filter(c=>(session==='all'||c.sessionId===session)&&($('application').value==='all'||c.application===$('application').value));
 $('request').replaceChildren(...calls.map(c=>option(c.id,dateText(c.timestamp)+' · '+(c.collectorName||'')+' · '+(c.modelEvidence||c.id.slice(0,8)))));
 if(!calls.length)$('request').append(option('','暂无调用'));if(calls.some(c=>c.id===previous))$('request').value=previous;else if(params.get('request')&&previous===params.get('request')&&calls.length)throw Error('指定请求不在当前调用列表中，请重新选择调用');
 await hydrate();}
async function load(){scopeText();if(!$('device').value){set('empty','尚无可查看的采集设备');set('receipt','暂无记录');return;}const n=++loadGeneration;++detailGeneration;loading=true;try{const metadata=await api(endpoint()+'?summary=1&collector='+encodeURIComponent($('collector').value));if(n!==loadGeneration)return;data=metadata;const app=$('application').value;const apps=[...new Set(data.calls.map(c=>c.application).filter(Boolean))];$('application').replaceChildren(option('all','全部应用'),...apps.map(a=>option(a,a)));if(apps.includes(app))$('application').value=app;
 const previous=$('session').value,sessions=new Map(data.calls.map(c=>[c.sessionId,c.sessionName]));
 $('session').replaceChildren(option('all','全部会话'),...[...sessions].map(([id,name])=>option(id,name)));
 if(sessions.has(previous))$('session').value=previous;await requests();
 }catch(e){if(n===loadGeneration)failure(e);}finally{if(n===loadGeneration)loading=false;}}
$('review-session').onclick=async()=>{const current=call();if(!current?.sessionId){set('review-status','当前记录没有会话标识，无法可靠关联。');return;}set('review-status','正在读取会话记录…');$('review-results').replaceChildren();try{const summary=await api(endpoint()+'?summary=1&collector='+encodeURIComponent($('collector').value));const rows=summary.calls.filter(c=>c.sessionId===current.sessionId).sort((a,b)=>a.timestamp-b.timestamp);const counts={};for(const c of rows){const type=c.modelEvidence||c.recordType||'记录';counts[type]=(counts[type]||0)+1;}set('review-status','当前查询窗口中找到 '+rows.length+' 条同会话记录：'+Object.entries(counts).map(([k,v])=>k+' '+v+' 条').join('，')+'。会话可能包含多个任务；不等于完整任务或模型交互次数。');data.calls=summary.calls;for(const c of rows){const b=document.createElement('button');b.type='button';b.className='search-hit';b.textContent=dateText(c.timestamp)+' · '+(c.collectorName||'')+' · '+(c.modelEvidence||c.recordType||'记录')+' → 查看内容';b.onclick=async()=>{$('request').replaceChildren(option(c.id,c.modelEvidence||c.id));await hydrate();};$('review-results').append(b);}}catch(error){set('review-status',error.message);}};
let searchGeneration=0;
$('history-search-form').onsubmit=async e=>{e.preventDefault();const q=$('history-query').value.trim(),n=++searchGeneration,device=$('device').value,collector=$('collector').value;if(!q||!device)return;set('history-status','正在搜索已接收的记录…');$('history-results').replaceChildren();try{const result=await api(endpoint()+'?collector='+encodeURIComponent(collector)+'&q='+encodeURIComponent(q));if(n!==searchGeneration||device!==$('device').value||collector!==$('collector').value)return;set('history-status','找到 '+result.items.length+' 条相关记录。'+result.coverage);for(const hit of result.items){const b=document.createElement('button');b.type='button';b.className='search-hit';const title=document.createElement('strong');title.textContent=(hit.collector==='sessionlens'?'SessionLens':'AppLens')+' · '+hit.source+' · '+hit.kind+' · '+dateText(hit.timestamp);const excerpt=document.createElement('span');const at=hit.excerpt.toLowerCase().indexOf(q.toLowerCase());if(at>=0){const mark=document.createElement('mark');mark.textContent=hit.excerpt.slice(at,at+q.length);excerpt.append(document.createTextNode(hit.excerpt.slice(0,at)),mark,document.createTextNode(hit.excerpt.slice(at+q.length)));}else excerpt.textContent=hit.excerpt;const action=document.createElement('small');action.textContent='查看原文与证据 →';b.append(title,excerpt,action);b.onclick=async()=>{++detailGeneration;data={calls:[{id:hit.id}],items:[]};$('request').replaceChildren(option(hit.id,hit.kind));await hydrate();$('detail-title').scrollIntoView({block:'start',behavior:'smooth'});};$('history-results').append(b);}}catch(error){if(n===searchGeneration)set('history-status',error.message);}};
$('collector').onchange=()=>{selected=null;load();};$('application').onchange=requests;
$('device').onchange=()=>{selected=null;load();};$('session').onchange=requests;$('request').onchange=hydrate;$('search').oninput=render;
$('view-tabs').onclick=e=>{const b=e.target.closest('button');if(b){view=b.dataset.view;render();}};
$('detail-tabs').onclick=e=>{const b=e.target.closest('button');if(b){tab=b.dataset.tab;render();}};
$('copy').onclick=async()=>{try{await navigator.clipboard.writeText(readerText());set('status','已复制当前原文');}catch(e){set('status','复制失败，请选择原文后复制');}};
$('export').onclick=()=>{const c=call();if(!c)return;const url=URL.createObjectURL(new Blob([c.body],{type:'text/plain;charset=utf-8'})),a=document.createElement('a');a.href=url;a.download='model-input-'+c.id.replace(':','-').slice(0,24)+'.txt';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
$('text-search').oninput=()=>{findAt=-1;};
$('find').onclick=()=>{const text=readerText(),q=$('text-search').value;if(!q)return;let at=text.toLowerCase().indexOf(q.toLowerCase(),findAt+1);if(at<0)at=text.toLowerCase().indexOf(q.toLowerCase());set('match-status',at<0?'未找到':'字符 '+at);if(at<0)return;findAt=at;
 const pre=$('original');pre.replaceChildren(document.createTextNode(text.slice(0,at)));const mark=document.createElement('mark');mark.textContent=text.slice(at,at+q.length);pre.append(mark,document.createTextNode(text.slice(at+q.length)));mark.scrollIntoView({block:'nearest'});};
(async()=>{try{if(['applens','sessionlens'].includes(params.get('collector')))$('collector').value=params.get('collector');const session=await api('/api/session');set('identity',session.username?session.username+' · 已登录':'未登录');if(!session.csrf&&!globalView)throw Error('请先登录，或从全局审计打开公开原文');
 const list=await api(globalView?'/api/audit/devices':'/api/devices');$('device').replaceChildren(...list.items.sort((a,b)=>(b.lastActivity||0)-(a.lastActivity||0)).map(d=>option(d.id,d.name+' · '+d.id.slice(0,8)+(d.online?' · 最近有活动':' · 暂无近期活动'))));
 if(list.items.some(d=>d.id===params.get('device')))$('device').value=params.get('device');await load();
 }catch(e){failure(e);}})();
const retry=document.createElement('button');retry.textContent='重试读取';retry.type='button';retry.onclick=load;$('status').after(retry);
const audit=document.createElement('a');audit.textContent='交互审计与安全检测 ↗';audit.href='/model-security';$('identity').parentElement.append(audit);audit.onclick=()=>{audit.href='/model-security?device='+encodeURIComponent($('device').value)+(call()?.collector==='applens'?'&request='+encodeURIComponent(call().id):'');};
setInterval(()=>{if(!document.hidden&&!loading&&!$('text-search').value&&!$('history-query').value)load();},60000);
})();
