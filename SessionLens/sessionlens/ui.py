"""Evidence-backed session UI matching the approved three-page design."""
from collections import Counter
from datetime import datetime
from zoneinfo import ZoneInfo
from html import escape
from urllib.parse import quote
from pathlib import Path

def e(value): return escape(str(value if value is not None else ''))
def result(task):
    stages={m['stage']:m for m in task.get('messages',[]) if m.get('stage')}
    return stages.get('review',stages.get('driver',{})).get('answer',{}).get('report',{})
def severity(report):
    levels=['critical','high','medium','low','info']
    return next((x for x in levels if any(f.get('severity')==x for f in report.get('findings',[]))), 'unknown')
def stamp(value):
    try:return datetime.fromisoformat(value.replace('Z','+00:00')).astimezone(ZoneInfo('Asia/Shanghai')).strftime('%m-%d %H:%M')
    except (ValueError,AttributeError):return '未采集'
def span(packet):
    try:
        seconds=(datetime.fromisoformat(packet['end'].replace('Z','+00:00'))-datetime.fromisoformat(packet['start'].replace('Z','+00:00'))).total_seconds()
        return f'{int(max(seconds,0)//3600)}h {int(max(seconds,0)%3600//60)}m'
    except (KeyError,ValueError,TypeError):return '未采集'
def tag(text,style=''):return f'<span class="pill {style}">{e(text)}</span>'
def metric(label,value,note=''):return f'<div class="panel metric"><div class="label">{e(label)}</div><div class="value">{e(value)}</div><span class="muted">{e(note)}</span></div>'
def refs(item):return ' '.join(f'<a class="pill blue" href="#{quote(str(ref))}">{e(ref)}</a>' for ref in item.get('evidenceRefs',[]))
def shell(title,body,active='list'):
    css=(Path(__file__).parent/'ui.css').read_text(encoding='utf-8')
    assets=Path(__file__).resolve().parents[2]/'agentpair'/'web_assets'
    foundation=assets/'product_ui.css';controller=assets/'product_ui.js'
    # Embed the shared shell when available, so exported local HTML remains usable.
    shared_style='<style data-product-ui>'+foundation.read_text(encoding='utf-8')+'</style>' if foundation.exists() else '<link rel="stylesheet" href="/product_ui.css?v=20261007" data-product-ui>'
    shared_script='<script>'+controller.read_text(encoding='utf-8')+'</script>' if controller.exists() else '<script src="/product_ui.js?v=20261007" defer></script>'
    pages=[('/', '任务中心'),('/devices','我的设备'),('/model-data','采集数据'),('/model-security','交互安全审计'),('/session-insights/','会话洞察'),('/cloud-machines','云机器'),('/packages','软件包')]
    nav=''.join(f'<a href="{url}"'+(' class="active" aria-current="page"' if url=='/session-insights/' else '')+f'><span>{label}</span></a>' for url,label in pages)
    subnav='<div class="insight-views"><a data-insights href="/"'+(' class="active" aria-current="page"' if active=='dashboard' else '')+'>总览</a><a data-insights href="/sessions.html"'+(' class="active" aria-current="page"' if active=='list' else '')+'>会话列表</a></div>'
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{e(title)} · AgentPair</title>{shared_style}<style>{css}</style></head><body class="agentpair-web" data-platform-module="insights"><aside><a class="brand" href="/">◢ <span>AgentPair<small>Agent 工作台</small></span></a><nav class="primary-nav" aria-label="主导航">{nav}</nav></aside><main><header><span>平台 / 会话洞察</span><a href="/model-data">查看采集数据</a></header><div id="bp-replica">{subnav}{body}<div class="foot">依据 SessionLens 已上传记录 · AgentPair 规划、分析与复核 · 时间：Asia/Shanghai</div></div></main>{shared_script}<script>''' + INSIGHT_SCRIPT + '</script></body></html>'

INSIGHT_SCRIPT=r'''
(() => {
 const panels=[...document.querySelectorAll('[data-panel]')],tabs=[...document.querySelectorAll('[data-tab]')];
 function selectPanel(id){panels.forEach(p=>p.hidden=p.id!==id);tabs.forEach(t=>{const on=t.dataset.tab===id;t.classList.toggle('active',on);t.setAttribute('aria-selected',String(on));t.tabIndex=on?0:-1;});stop();}
 function reveal(){let id;try{id=decodeURIComponent(location.hash.slice(1));}catch(_){return;}const item=document.getElementById(id);if(!item)return;const panel=item.closest('[data-panel]');if(panel)selectPanel(panel.id);if(item.tagName==='DETAILS')item.open=true;item.scrollIntoView({block:'start'});}
 tabs.forEach((b,i)=>{b.addEventListener('click',()=>selectPanel(b.dataset.tab));b.addEventListener('keydown',event=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;event.preventDefault();const n=event.key==='Home'?0:event.key==='End'?tabs.length-1:(i+(event.key==='ArrowRight'?1:-1)+tabs.length)%tabs.length;selectPanel(tabs[n].dataset.tab);tabs[n].focus();});});
 const story=[...document.querySelectorAll('[data-story-step]')],cards=[...document.querySelectorAll('[data-step]')];let current=0,timer=null;
 function selectStep(index){if(!story.length)return;current=Math.max(0,Math.min(index,story.length-1));story.forEach((b,i)=>{b.classList.toggle('active',i===current);b.setAttribute('aria-pressed',String(i===current));});cards.forEach((card,i)=>card.hidden=i!==current);const count=document.getElementById('story-position');if(count)count.textContent=(current+1)+' / '+story.length;}
 function stop(){if(timer)clearInterval(timer);timer=null;const play=document.getElementById('story-play');if(play){play.textContent='播放过程';play.setAttribute('aria-pressed','false');}}
 story.forEach((b,i)=>b.onclick=()=>{stop();selectStep(i);});
 document.getElementById('story-next')?.addEventListener('click',()=>{stop();selectStep((current+1)%story.length);});
 document.getElementById('story-play')?.addEventListener('click',()=>{if(timer){stop();return;}if(!story.length)return;const button=document.getElementById('story-play');button.textContent='暂停回放';button.setAttribute('aria-pressed','true');if(current===story.length-1)selectStep(0);timer=setInterval(()=>{if(current>=story.length-1){stop();return;}selectStep(current+1);},1800);});
 document.addEventListener('visibilitychange',()=>{if(document.hidden)stop();});
 const filter=document.getElementById('severity-filter');filter?.addEventListener('change',()=>document.querySelectorAll('[data-severity]').forEach(row=>row.hidden=filter.value==='high'&&!['critical','high'].includes(row.dataset.severity)));
 selectStep(0);addEventListener('hashchange',reveal);reveal();
})();
'''
def link(folder):return '/'+quote(folder.name)+'/report.html'
def load(root):
    import json
    sessions=[]
    for folder in root.iterdir():
        if not folder.is_dir() or not (folder/'analysis.json').exists() or not (folder/'evidence.json').exists():continue
        task=json.loads((folder/'analysis.json').read_text(encoding='utf-8'));packet=json.loads((folder/'evidence.json').read_text(encoding='utf-8'))
        sessions.append((folder,packet,task,result(task)))
    return sorted(sessions,key=lambda s:s[1].get('end') or '',reverse=True)
def build_pages(root):
    sessions=load(root)
    for folder,packet,task,report in sessions:render(packet,task,folder/'report.html')
    findings=[(folder,f) for folder,p,t,r in sessions for f in r.get('findings',[])]
    urgent=[x for x in findings if x[1].get('severity') in ('critical','high')]
    kinds=Counter()
    for _,p,_,_ in sessions:kinds.update(p.get('recordKinds',{}))
    body='<h1>会话洞察</h1><p class="muted">先看任务的结果，再沿执行过程核对。</p><div class="label">整体情况</div><div class="metrics">'
    body+=''.join([metric('已分析来源',len(sessions),'部分来源仅含审批消息'),metric('已分析记录',sum(p.get('recordCount',0) for _,p,_,_ in sessions),'分析范围内的记录'),metric('估算费用','未采集','不以未知费用显示为零'),metric('发现',len(findings),f'{len(urgent)} 个高风险及以上'),metric('工具调用',kinds['tool_call'],'来源日志记录'),metric('工具返回',kinds['tool_result'],'来源日志记录'),metric('证据片段',sum(len(p.get('fragments',[])) for _,p,_,_ in sessions),'部分片段经过截取'),metric('复核通过',sum(t.get('status')=='completed' for _,_,t,_ in sessions),'模型复核不等于独立验证')])+'</div>'
    body+='<div class="label">值得关注</div><div class="findgrid">'
    for title,items in [('立即处理',urgent),('值得检查',[x for x in findings if x not in urgent])]:
        body+=f'<div class="panel pad"><div class="row"><h3>{title}</h3>{tag(len(items))}</div>'
        body+=''.join(f'<p><a href="{link(folder)}#findings">{e(f.get("title"))}</a></p>' for folder,f in items[:3]) or '<p class="muted">当前分析未列出相关发现。</p>'
        body+='</div>'
    practices=[(folder,x) for folder,p,t,r in sessions for x in r.get('goodPractices',[])]
    body+='<div class="panel pad"><h3>下次可以更快</h3><p class="muted">当前分析尚未单独生成效率观察。</p></div><div class="panel pad"><h3>值得保留的做法</h3>'+''.join(f'<p>{e(x)}</p>' for _,x in practices[:2])+'</div></div><div class="row" style="justify-content:space-between;margin-top:24px"><h3>最近的会话</h3><a href="/sessions.html">查看全部 →</a></div>'
    for folder,p,t,r in sessions[:3]:body+=f'<div class="panel pad">{tag(severity(r).upper(),"high")}<h3><a href="{link(folder)}">{e(r.get("title","分析未完成"))}</a></h3><p>{e(r.get("summary"))}</p><span class="muted">{stamp(p.get("end"))} · {span(p)} · {e(p.get("coverage"))}</span></div>'
    if not sessions:body+='<div class="panel pad">暂无分析结果。SessionLens 上传后需先完成 AgentPair 分析。</div>'
    (root/'index.html').write_text(shell('总览',body,'dashboard'), encoding='utf-8')
    body='<h1>会话列表</h1><div class="row"><label>严重程度 <select id="severity-filter"><option value="all">全部</option><option value="high">高风险及以上</option></select></label></div><div class="panel tablewrap"><table><thead><tr><th>会话 / 采集范围</th><th>严重程度</th><th>洞察</th><th>费用</th><th>记录</th><th>跨度</th><th>时间</th></tr></thead><tbody>'
    for folder,p,t,r in sessions:
        scope='一轮任务' if p.get('recordKinds',{}).get('tool_call') else '仅消息 / 审批记录'
        body+=f'<tr data-severity="{severity(r)}"><td><a href="{link(folder)}">{e(r.get("title","分析未完成"))}</a><p class="muted">{e(r.get("summary"))}</p><span class="pill">{scope}</span><p class="muted">{e(p.get("sessionId"))}</p></td><td>{tag(severity(r).upper(),"high")}<p>{len(r.get("findings",[]))} 个发现</p></td><td>{len(r.get("story",[]))} 个过程节点<br>{len(r.get("goodPractices",[]))} 个好做法</td><td>未采集</td><td>{p.get("recordCount",0)}</td><td>{span(p)}</td><td>{stamp(p.get("end"))}</td></tr>'
    body+='</tbody></table></div>'
    (root/'sessions.html').write_text(shell('会话',body), encoding='utf-8')
def render(packet,task,path):
    report=result(task);findings=report.get('findings',[]);fragments=packet.get('fragments',[]);kinds=packet.get('recordKinds',{})
    body='<div class="label"><a href="/sessions.html">会话</a> › '+e(packet.get('sessionId'))+'</div><h1 style="margin-top:18px">'+e(report.get('goal') or report.get('title','分析未完成'))+'</h1>'
    completion={'completed':'已交付 · 需核验','partial':'部分交付','unknown':'交付尚未确认'}.get(report.get('completion'),'交付尚未确认')
    body+='<div class="panel"><div class="reporthead">'+tag(completion,'attention')+'<div><h2>'+e(report.get('title','分析未完成'))+'</h2><p>'+e(report.get('summary'))+'</p></div><span class="mono muted">'+stamp(packet.get('start'))+'<br>'+stamp(packet.get('end'))+'</span></div><details class="coverage-details"><summary>采集范围与分析依据</summary><div class="reportstats">'
    for label,value in [('跨度',span(packet)),('费用','未采集'),('发现',len(findings)),('记录',packet.get('recordCount',0)),('工具调用',kinds.get('tool_call',0)),('证据片段',len(fragments))]:body+=f'<div><div class="value">{e(value)}</div><span class="label">{label}</span></div>'
    body+='</div><div class="section"><span class="label">分析依据</span><p>来源：SessionLens 日志 · '+('包含工具记录' if kinds.get('tool_call') else '仅消息 / 审批记录，无法验证工具执行')+'</p><p>状态：'+e({'completed':'已完成模型复核','failed':'分析未完成'}.get(task.get('status'),task.get('status')))+'</p></div></details><div class="section outcome"><span class="label">实际交付</span><p>'+e(report.get('outcome','尚未形成交付结论'))+'</p><p class="muted">依据已采集记录；模型复核不等于独立验证。</p></div></div>'
    if task.get('status')!='completed':body+='<div class="panel pad high">以下为未通过复核的草稿，不作为已确认结论。</div>'
    body+='<div class="tabs" role="tablist" aria-label="会话详情">'+''.join(f'<button role="tab" aria-selected="{str(key=="engineering").lower()}" aria-controls="{key}" data-tab="{key}" class="{ "active" if key=="engineering" else ""}">{label}</button>' for key,label in [('findings','发现与处置'),('engineering','执行与交付'),('tools','工具与证据'),('limits','分析范围与盲区')])+'</div><section id="findings" role="tabpanel" data-panel hidden>'
    for title,items in [('立即处理',[f for f in findings if f.get('severity') in ('critical','high')]),('值得检查',[f for f in findings if f.get('severity') not in ('critical','high')])]:
        body+=f'<h3>{title} {tag(len(items))}</h3>'
        for f in items:
            body+='<details><summary>'+tag(f.get('severity','unknown').upper(),'high')+'<strong>'+e(f.get('title'))+'</strong></summary><p>'+e(f.get('fact'))+'</p><div class="dual"><div><h3>可能的影响</h3><p>'+e(f.get('impact'))+'</p></div><div><h3>怎么处理</h3><p>'+e(f.get('remediation'))+'</p></div></div><div class="evidence">证据依据：'+refs(f)+'<p class="muted">'+e({'observed':'来源中有记录，仍需结合上下文核实','hypothesis':'待验证线索'}.get(f.get('status'),f.get('status')))+'</p></div></details>'
        if not items:body+='<p class="muted">本次分析未列出此类发现。</p>'
    body+='</section><section id="engineering" role="tabpanel" data-panel><div class="story-heading"><h3>任务过程</h3><div class="story-controls"><button id="story-play" type="button" aria-pressed="false">播放过程</button><button id="story-next" type="button">下一步</button><span id="story-position" class="muted"></span></div></div><p class="muted">回放已记录的执行摘要；原始工具参数和返回可通过证据编号核对。</p>'
    story=report.get('story',[])
    body+='<div class="story-steps">'+''.join(f'<button type="button" data-story-step="{i}" aria-pressed="{str(i==0).lower()}"><span>{i+1}</span>{e(step.get("title"))}</button>' for i,step in enumerate(story))+'</div>'
    for i,step in enumerate(story):
        body+=f'<article class="panel pad story-card" data-step="{i}"'+(' hidden' if i else '')+f'><h3>{e(step.get("title"))}</h3><div class="tool-flow"><section><span class="label">Agent 做了什么</span><p>{e(step.get("action"))}</p></section><section><span class="label">返回与结果</span><p>{e(step.get("result"))}</p></section></div><div class="row">{refs(step)}</div></article>'
    if not story:body+='<div class="panel pad"><p class="muted">该分析没有记录可回放的过程。仍可查看已采集证据，不能补写执行步骤。</p></div>'
    body+='<h3 style="margin-top:24px">值得保留的做法</h3>'+''.join('<p>'+e(x)+'</p>' for x in report.get('goodPractices',[]))+'</section><section id="tools" data-panel hidden><h3>工具调用分布</h3>'
    tools=Counter(f.get('tool') for f in fragments if f.get('kind')=='tool_call' and f.get('tool'))
    body+='<div class="panel pad">'+(''.join(f'<p>{e(name)} <span class="mono">{count}</span></p>' for name,count in tools.most_common()) or '该分析包没有具名工具调用记录。')+'</div><p class="muted">按分析包中的证据片段统计，不代表完整会话。文件访问、Skills 与 PR 统计尚未提供，未生成推测数字。</p><h3>来源证据</h3>'
    for f in fragments:
        source=f.get('source',{});body+=f'<details id="{e(f["evidenceId"])}"><summary>{e(f["evidenceId"])} · {e(f.get("kind"))} · {e(f.get("tool") or "消息")}</summary><p>{stamp(f.get("timestamp"))} · 原始事件 {e(f.get("eventId"))}</p><p>字节 {e(source.get("byteStart"))}–{e(source.get("byteEnd"))} · '+('片段已截取' if f.get('truncated') else '该记录片段完整')+'</p><pre>'+e(f.get('excerpt'))+'</pre></details>'
    body+='</section><section id="limits" data-panel hidden><h3>哪些内容还不能确定</h3><div class="panel pad">'+''.join('<p>'+e(x)+'</p>' for x in dict.fromkeys(packet.get('limitations',[])+report.get('limitations',[])))+'</div><h3>采集与分析链路</h3><div class="evidence">SessionLens → AgentPair 规划 → DeepSeek 分析 → AgentPair 复核 → 会话洞察</div><p>模型复核不等于独立验证。未记录的行为不显示为已通过安全检查。</p></section>'
    path.write_text(shell('会话洞察',body,'detail'), encoding='utf-8')

def publish(root,base='/session-insights/'):
    build_pages(root)
    for path in [root/'index.html',root/'sessions.html',*root.glob('*/report.html')]:
        text=path.read_text(encoding='utf-8').replace('data-insights href="/"','data-insights href="'+base+'"').replace('href="/sessions.html"','href="'+base+'sessions.html"')
        for folder in root.iterdir():
            if folder.is_dir():text=text.replace('href="/'+quote(folder.name)+'/report.html','href="'+base+quote(folder.name)+'/report.html')
        path.write_text(text, encoding='utf-8')
