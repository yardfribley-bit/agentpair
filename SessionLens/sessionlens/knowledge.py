"""Question-driven local retrieval, with model query understanding and cited answers."""
import json,sqlite3
from pathlib import Path
from .assistant import packet_for_task,request_json,run

def candidates(db,terms,limit=6,source=None,since=None,question=None):
    generic={'workbuddy','codex','问题','解决','任务','之前','查找','工具','怎么','如何','查询','失败','解决方法'}
    terms=[x.strip().lower() for x in terms if isinstance(x,str) and 1<len(x.strip())<=60 and x.strip().lower() not in generic][:8]
    if not terms:return []
    expr=' + '.join('(CASE WHEN instr(lower(prompt),?)>0 THEN 5 WHEN instr(lower(search),?)>0 THEN 1 ELSE 0 END)' for _ in terms)
    args=[v for term in terms for v in (term,term)]
    import re
    anchors=re.findall(r'[a-zA-Z][a-zA-Z0-9_-]{1,}',question or '')
    for phrase in re.findall(r'[\u4e00-\u9fff]+',question or ''):anchors.extend(phrase[i:i+2] for i in range(len(phrase)-1))
    anchors=list(dict.fromkeys(x.lower() for x in anchors if x not in generic and x not in ('那次','当时','最后','成了','了吗','怎么','是怎','的最','然后','什么','哪些')))[:30]
    qexpr=' + '.join('(CASE WHEN instr(lower(prompt),?)>0 THEN 1 ELSE 0 END)' for _ in anchors) or '0'
    args.extend(anchors)
    where='score>0 AND prompt NOT LIKE ?'
    args.append('The following is the Codex agent history%')
    if source:where+=' AND source=?';args.append(source)
    if since:where+=' AND updated>=?';args.append(since)
    rows=db.execute('SELECT id,prompt,source,session,updated,('+expr+') AS score,('+qexpr+') AS query_score FROM tasks WHERE '+where+' ORDER BY query_score DESC,score DESC,length(prompt),updated DESC LIMIT 40',args).fetchall()
    seen=set();result=[]
    for row in rows:
        key=(row[2],row[3],row[1])
        if key in seen:continue
        seen.add(key);result.append(row)
        if len(result)>=limit:break
    return result

def ask(root,config,question,history,progress,source=None,days=0):
    from .relay_model import call,answer
    from .task_presentation import project
    import datetime
    if not config.get('enabled') or not config.get('credentialFile'):raise ValueError('请先在设置中配置智能助手的模型地址和凭据。')
    progress('正在理解问题，检索本机任务库')
    plan=call(config,'把问题转为本机历史任务检索词。输出 JSON {"terms":[最多六个有辨识度的短词],"followup":布尔}。不要回答问题。',{'question':question,'previousQuestion':history[-1]['question'] if history else ''},max_tokens=500)
    with sqlite3.connect(Path(root)/'collector.db',timeout=10) as db:
        since=(datetime.datetime.now(datetime.timezone.utc)-datetime.timedelta(days=days)).isoformat() if days else None
        found=candidates(db,plan.get('terms',[]),source=source,since=since,question=question)
        ids=history[-1].get('retrievedTaskIds',[])[:1] if plan.get('followup') and history else [r[0] for r in found[:1]]
        if ids and not db.execute('SELECT 1 FROM tasks WHERE id=? AND (? IS NULL OR source=?) AND (? IS NULL OR updated>=?)',(ids[0],source,source,since,since)).fetchone():ids=[]
        if not ids:raise ValueError('没有找到相关任务，请补充任务名称、文件或时间。')
        packet=packet_for_task(db,ids[0]);presentation=project(db,ids[0])
    progress('正在核对这一次任务的思路、工具参数与返回')
    understanding=answer(root,config,question,packet,history)
    return {'question':question,'taskId':ids[0],'retrievedTaskIds':ids,'retrieved':[{'title':packet['prompt'],'source':packet['source']}],'packet':packet,'presentation':presentation,'understanding':understanding}
