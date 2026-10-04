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
    css=(Path(__file__).parent/'ui.css').read_text()
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{e(title)} · AgentPair</title><style>{css}</style></head><body><div id="bp-replica"><header><span class="brand">◩ AgentPair</span><span class="muted">SessionLens / 会话洞察</span></header><div class="shell"><nav aria-label="主导航"><a class="{'active' if active=='dashboard' else ''}" href="/" aria-label="总览">总览</a><a class="{'active' if active=='list' else ''}" href="/sessions.html" aria-label="会话">会话</a></nav><main>{body}<div class="foot">SessionLens → AgentPair → DeepSeek · 规划、分析与复核 · 时间：Asia/Shanghai · 当前展示已有分析结果</div></main></div></div><script>
function reveal(){{const el=document.getElementById(decodeURIComponent(location.hash.slice(1)));if(el){{el.closest('[data-panel]')?.removeAttribute('hidden');if(el.tagName==='DETAILS')el.open=true;el.scrollIntoView({{block:'start'}})}}}}
addEventListener('hashchange',reveal);reveal();
document.querySelectorAll('[data-tab]').forEach(b=>b.addEventListener('click',()=>{{document.querySelectorAll('[data-panel]').forEach(p=>p.hidden=p.id!==b.dataset.tab);document.querySelectorAll('[data-tab]').forEach(t=>t.classList.toggle('active',t===b))}}));
const filter=document.getElementById('severity-filter');filter?.addEventListener('change',()=>document.querySelectorAll('[data-severity]').forEach(row=>row.hidden=filter.value==='high'&&!['critical','high'].includes(row.dataset.severity)));
</script></body></html>'''
def link(folder):return '/'+quote(folder.name)+'/report.html'
def load(root):
    import json
    sessions=[]
    for folder in root.iterdir():
        if not folder.is_dir() or not (folder/'analysis.json').exists() or not (folder/'evidence.json').exists():continue
        task=json.loads((folder/'analysis.json').read_text());packet=json.loads((folder/'evidence.json').read_text())
        sessions.append((folder,packet,task,result(task)))
    return sorted(sessions,key=lambda s:s[1].get('end') or '',reverse=True)
def build_pages(root):
    sessions=load(root)
    for folder,packet,task,report in sessions:render(packet,task,folder/'report.html')
    findings=[(folder,f) for folder,p,t,r in sessions for f in r.get('findings',[])]
    urgent=[x for x in findings if x[1].get('severity') in ('critical','high')]
    kinds=Counter()
    for _,p,_,_ in sessions:kinds.update(p.get('recordKinds',{}))
    body='<h1>总览</h1><p>你的 Agent 活动</p><p class="muted">已分析的本机会话 · 每份洞察保留实际采集范围</p><div class="label">整体情况</div><div class="metrics">'
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
    (root/'index.html').write_text(shell('总览',body,'dashboard'))
    body='<h1>会话</h1><div class="row"><label>严重程度 <select id="severity-filter"><option value="all">全部</option><option value="high">高风险及以上</option></select></label></div><div class="panel tablewrap"><table><thead><tr><th>会话 / 采集范围</th><th>严重程度</th><th>洞察</th><th>费用</th><th>记录</th><th>跨度</th><th>时间</th></tr></thead><tbody>'
    for folder,p,t,r in sessions:
        scope='一轮任务' if p.get('recordKinds',{}).get('tool_call') else '仅消息 / 审批记录'
        body+=f'<tr data-severity="{severity(r)}"><td><a href="{link(folder)}">{e(r.get("title","分析未完成"))}</a><p class="muted">{e(r.get("summary"))}</p><span class="pill">{scope}</span><p class="muted">{e(p.get("sessionId"))}</p></td><td>{tag(severity(r).upper(),"high")}<p>{len(r.get("findings",[]))} 个发现</p></td><td>{len(r.get("story",[]))} 个过程节点<br>{len(r.get("goodPractices",[]))} 个好做法</td><td>未采集</td><td>{p.get("recordCount",0)}</td><td>{span(p)}</td><td>{stamp(p.get("end"))}</td></tr>'
    body+='</tbody></table></div>'
    (root/'sessions.html').write_text(shell('会话',body))
def render(packet,task,path):
    report=result(task);findings=report.get('findings',[]);fragments=packet.get('fragments',[]);kinds=packet.get('recordKinds',{})
    body='<div class="label"><a href="/sessions.html">会话</a> › '+e(packet.get('sessionId'))+'</div><h1 style="margin-top:18px">'+e(report.get('goal') or report.get('title','分析未完成'))+'</h1>'
    body+='<div class="panel"><div class="reporthead">'+tag(severity(report).upper(),'high')+'<div><h2>'+e(report.get('title','分析未完成'))+'</h2><p>'+e(report.get('summary'))+'</p></div><span class="mono muted">'+stamp(packet.get('start'))+'<br>'+stamp(packet.get('end'))+'</span></div><div class="reportstats">'
    for label,value in [('跨度',span(packet)),('费用','未采集'),('发现',len(findings)),('记录',packet.get('recordCount',0)),('工具调用',kinds.get('tool_call',0)),('证据片段',len(fragments))]:body+=f'<div><div class="value">{e(value)}</div><span class="label">{label}</span></div>'
    body+='</div><div class="section"><span class="label">实际交付</span><p>'+e(report.get('outcome','尚未形成交付结论'))+'</p></div><div class="section"><span class="label">分析依据</span><p>来源：Codex 日志 · '+('一轮已结束任务' if kinds.get('tool_call') else '仅消息 / 审批记录，无法验证工具执行')+'</p><p>状态：'+e({'completed':'已完成模型复核','failed':'分析未完成'}.get(task.get('status'),task.get('status')))+' · Tokens、费用和完整系统行为尚未采集。</p></div></div>'
    if task.get('status')!='completed':body+='<div class="panel pad high">以下为未通过复核的草稿，不作为已确认结论。</div>'
    body+='<div class="tabs">'+''.join(f'<button data-tab="{key}" class="{ "active" if key=="findings" else ""}">{label}</button>' for key,label in [('findings','发现与处置'),('engineering','执行与交付'),('tools','工具与证据'),('limits','分析范围与盲区')])+'</div><section id="findings" data-panel>'
    for title,items in [('立即处理',[f for f in findings if f.get('severity') in ('critical','high')]),('值得检查',[f for f in findings if f.get('severity') not in ('critical','high')])]:
        body+=f'<h3>{title} {tag(len(items))}</h3>'
        for f in items:
            body+='<details><summary>'+tag(f.get('severity','unknown').upper(),'high')+'<strong>'+e(f.get('title'))+'</strong></summary><p>'+e(f.get('fact'))+'</p><div class="dual"><div><h3>可能的影响</h3><p>'+e(f.get('impact'))+'</p></div><div><h3>怎么处理</h3><p>'+e(f.get('remediation'))+'</p></div></div><div class="evidence">证据依据：'+refs(f)+'<p class="muted">'+e({'observed':'来源中有记录，仍需结合上下文核实','hypothesis':'待验证线索'}.get(f.get('status'),f.get('status')))+'</p></div></details>'
        if not items:body+='<p class="muted">本次分析未列出此类发现。</p>'
    body+='</section><section id="engineering" data-panel hidden><h3>执行过程</h3>'
    for i,s in enumerate(report.get('story',[]),1):body+=f'<details open><summary><span class="mono">{i:02}</span><strong>{e(s.get("title"))}</strong></summary><p>{e(s.get("action"))}</p><p><strong>返回与结果：</strong>{e(s.get("result"))}</p>{refs(s)}</details>'
    body+='<h3 style="margin-top:24px">值得保留的做法</h3>'+''.join('<p>'+e(x)+'</p>' for x in report.get('goodPractices',[]))+'</section><section id="tools" data-panel hidden><h3>工具调用分布</h3>'
    tools=Counter(f.get('tool') for f in fragments if f.get('kind')=='tool_call' and f.get('tool'))
    body+='<div class="panel pad">'+(''.join(f'<p>{e(name)} <span class="mono">{count}</span></p>' for name,count in tools.most_common()) or '该分析包没有具名工具调用记录。')+'</div><p class="muted">按分析包中的证据片段统计，不代表完整会话。文件访问、Skills 与 PR 统计尚未提供，未生成推测数字。</p><h3>来源证据</h3>'
    for f in fragments:
        source=f.get('source',{});body+=f'<details id="{e(f["evidenceId"])}"><summary>{e(f["evidenceId"])} · {e(f.get("kind"))} · {e(f.get("tool") or "消息")}</summary><p>{stamp(f.get("timestamp"))} · 原始事件 {e(f.get("eventId"))}</p><p>字节 {e(source.get("byteStart"))}–{e(source.get("byteEnd"))} · '+('片段已截取' if f.get('truncated') else '该记录片段完整')+'</p><pre>'+e(f.get('excerpt'))+'</pre></details>'
    body+='</section><section id="limits" data-panel hidden><h3>哪些内容还不能确定</h3><div class="panel pad">'+''.join('<p>'+e(x)+'</p>' for x in dict.fromkeys(packet.get('limitations',[])+report.get('limitations',[])))+'</div><h3>采集与分析链路</h3><div class="evidence">SessionLens → AgentPair 规划 → DeepSeek 分析 → AgentPair 复核 → 会话洞察</div><p>模型复核不等于独立验证。未记录的行为不显示为已通过安全检查。</p></section>'
    path.write_text(shell('会话洞察',body))

def publish(root,base='/session-insights/'):
    build_pages(root)
    for path in [root/'index.html',root/'sessions.html',*root.glob('*/report.html')]:
        text=path.read_text().replace('href="/"','href="'+base+'"').replace('href="/sessions.html"','href="'+base+'sessions.html"')
        for folder in root.iterdir():
            if folder.is_dir():text=text.replace('href="/'+quote(folder.name)+'/report.html','href="'+base+quote(folder.name)+'/report.html')
        path.write_text(text)
