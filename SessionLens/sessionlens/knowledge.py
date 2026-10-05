"""Question-driven local retrieval, with model query understanding and cited answers."""
import json,sqlite3
from pathlib import Path
from .assistant import packet_for_task,request_json,run

def candidates(db,terms,limit=6,source=None,since=None):
    generic={'workbuddy','codex','问题','解决','任务','之前','查找','工具','怎么','如何','查询','失败','解决方法'}
    terms=[x.strip().lower() for x in terms if isinstance(x,str) and 1<len(x.strip())<=60 and x.strip().lower() not in generic][:8]
    if not terms:return []
    expr=' + '.join('(CASE WHEN instr(lower(prompt),?)>0 THEN 5 WHEN instr(lower(search),?)>0 THEN 1 ELSE 0 END)' for _ in terms)
    args=[v for term in terms for v in (term,term)]
    where='score>0 AND prompt NOT LIKE ?'
    args.append('The following is the Codex agent history%')
    if source:where+=' AND source=?';args.append(source)
    if since:where+=' AND updated>=?';args.append(since)
    rows=db.execute('SELECT id,prompt,source,session,updated,('+expr+') AS score FROM tasks WHERE '+where+' ORDER BY score DESC,length(prompt),updated DESC LIMIT 40',args).fetchall()
    seen=set();result=[]
    for row in rows:
        key=(row[2],row[3],row[1])
        if key in seen:continue
        seen.add(key);result.append(row)
        if len(result)>=limit:break
    return result

def ask(root,config,question,history,progress,source=None,days=0):
    token=Path(config['tokenFile']).read_text().strip();endpoint=config['url'].rstrip('/')
    progress('正在理解问题与前文')
    followup=bool(history and question.startswith(('它','这次','刚才','具体','为什么又','那次')) and not any(w in question for w in ('重新查找','换一次','其他任务')))
    plan={'terms':[],'followup':True} if followup else request_json(endpoint+'/retrieve',token,{'question':question,'history':[{'question':r['question'][:1000],'answer':r.get('understanding',{}).get('overview',{}).get('text','')[:500]} for r in history[-4:]]})
    with sqlite3.connect(Path(root)/'collector.db',timeout=10) as db:
        source=source or ('workbuddy' if 'workbuddy' in question.lower() else ('codex' if 'codex' in question.lower() else None))
        import datetime
        since=(datetime.datetime.now(datetime.timezone.utc)-datetime.timedelta(days=days)).isoformat() if days else None
        found=candidates(db,plan['terms'],source=source,since=since);packets=[]
        if plan.get('followup') and history:
            ids=history[-1].get('retrievedTaskIds',[])[:3]
            ids=[tid for tid in ids if db.execute('SELECT 1 FROM tasks WHERE id=? AND (? IS NULL OR source=?) AND (? IS NULL OR updated>=?)',(tid,source,source,since,since)).fetchone()]
        else:ids=[r[0] for r in found[:3]]
        if not ids:raise ValueError('当前已整理的知识库没有找到相关记录。可补充项目、文件或时间；历史整理未完成时，稍后再试。')
        progress('找到相关任务，正在读取原始要求、工具参数和返回')
        for tid in ids:packets.append(packet_for_task(db,tid))
    combined=dict(packets[0]);combined['taskId']='knowledge';combined['prompt']=question;combined['fragments']=[];combined['totalRecords']=sum(p['totalRecords'] for p in packets)
    # Share the input budget across tasks instead of letting the first consume all.
    quota=120//len(packets)
    text_budget=min(2100,65000//max(1,sum(min(quota,len(p['fragments'])) for p in packets))-260)
    for p in packets:
        items=p['fragments'];items=items if len(items)<=quota else items[:quota//2]+items[-quota//2:]
        for f in items:
            f=dict(f);f['taskId']=p['taskId'];f['truncated']=f['truncated'] or len(f['text'])>text_budget;f['text']='任务要求：'+p['prompt'][:250]+'\n'+f['text'][:text_budget];f['evidenceId']=f'E{len(combined["fragments"])+1:03}';combined['fragments'].append(f)
    combined['includedRecords']=len(combined['fragments']);combined['revision']=max(p['revision'] for p in packets)
    contextual=question
    if history:contextual+='\n对话背景（仅用于理解追问，不作事实证据）：'+json.dumps([{'question':r['question'],'answer':r.get('understanding',{}).get('overview',{}).get('text','')[:500]} for r in history[-2:]],ensure_ascii=False)
    result=run(root,config,'knowledge',contextual[:2000],progress,packet=combined)
    result['question']=question;result['retrievedTaskIds']=ids;result['retrieved']=[{'title':p['prompt'][:120],'source':p['source']} for p in packets]
    return result
