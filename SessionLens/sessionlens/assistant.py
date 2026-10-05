"""Explicit task questions, private AgentPair gateway, durable local answers."""
import hashlib
import json
from pathlib import Path
import sqlite3
import time
import urllib.request
from urllib.parse import urlparse
from .supervision import event_text
from .task_lineage import task_table,step_table,resolve,history,execution_map,exists,signature

VERSION=6

def packet_for_task(db,task_id):
    task_id=resolve(db,task_id);steps=step_table(db)
    task=db.execute('SELECT source,session,prompt,last_row FROM '+task_table(db)+' WHERE id=?',(task_id,)).fetchone()
    if not task:raise ValueError('任务不存在')
    total=db.execute('SELECT count(*) FROM '+steps+' WHERE task=?',(task_id,)).fetchone()[0]
    # Fetch only selected identities, then one raw record at a time.
    selected=db.execute('SELECT event FROM '+steps+' WHERE task=? ORDER BY seq LIMIT ?',(task_id,120 if total<=120 else 60)).fetchall()
    if total>120:selected+=db.execute('SELECT event FROM '+steps+' WHERE task=? ORDER BY seq DESC LIMIT 60',(task_id,)).fetchall()[::-1]
    requirements=history(db,task_id);bindings=execution_map(db,task_id)
    # Evidence for the origin and recent revisions gets priority, including
    # the requirement/approval used by sampled executions in long tasks.
    chosen=[r[0] for r in selected]
    required=[r['eventId'] for r in requirements[:1]+requirements[-24:]]
    for event in chosen:
        link=bindings.get(event,{})
        required.extend(x for x in (link.get('requirementEvent'),link.get('approvalEvent'),link.get('planEvent')) if x)
    wanted=list(dict.fromkeys(required+chosen))[:120]
    selected=sorted(((ident,) for ident in wanted),key=lambda r:db.execute('SELECT rowid FROM events WHERE id=?',r).fetchone()[0])
    fragments=[];records=[];per_record=min(5000,90000//max(1,len(selected)))
    for (identity,) in selected:
        raw=db.execute('SELECT event FROM events WHERE id=?',(identity,)).fetchone()[0]
        e=json.loads(raw);e['_seq']=db.execute('SELECT rowid FROM events WHERE id=?',(identity,)).fetchone()[0];records.append(e);text=event_text(e);cut=text[:per_record]
        if not cut:continue
        fragments.append({'evidenceId':'E'+str(len(fragments)+1).zfill(3),'eventId':identity,'kind':e['kind'],'role':e.get('role'),'tool':e.get('name'),'callId':e.get('callId'),'timestamp':e.get('timestamp'),'text':cut,'truncated':len(cut)<len(text)})
    if not fragments:raise ValueError('任务暂时没有可分析的文本证据')
    refs={f['eventId']:f['evidenceId'] for f in fragments}
    requirement_history=[{**{k:v for k,v in r.items() if k!='text'},'evidenceRef':refs[r['eventId']]} for r in requirements if r['eventId'] in refs]
    execution_links=[{'eventId':event,'evidenceRef':refs[event],**link,
                     'requirementRef':refs.get(link['requirementEvent']),
                     'approvalRef':refs.get(link['approvalEvent']),
                     'planRef':refs.get(link['planEvent'])} for event,link in bindings.items() if event in refs]
    from .message_graph import build,reasoning_bindings
    graph=build(records)
    message_relations=[{'fromRef':refs[e['from']],'toRef':refs[e['to']],'relation':e['relation'],'basis':e['basis']}
                       for e in graph['edges'] if e['from'] in refs and e['to'] in refs]
    reasoning_links=[{'callRef':refs[call],'reasoningRef':refs[link['reasoningEvent']],
                      'pathRefs':[refs[x] for x in link['path']], 'basis':link['basis']}
                     for call,link in reasoning_bindings(graph).items() if all(x in refs for x in link['path'])]
    return {'version':VERSION,'lineageVersion':1 if exists(db) else 0,'lineageSignature':signature(db,task_id),'taskId':task_id,'source':task[0],'sessionId':task[1],'prompt':task[2][:16000],
            'revision':task[3],'totalRecords':total,'includedRecords':len(fragments),
            'coverage':'selected_task_log_records','fragments':fragments,
            'requirementHistory':requirement_history,'totalRequirementTurns':len(requirements),
            'executionLinks':execution_links,'messageRelations':message_relations,'reasoningLinks':reasoning_links,
            'messageRelationCoverage':'selected_records','messageRelationGaps':len(graph['gaps'])}

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None

def request_json(url,token,payload=None):
    parsed=urlparse(url)
    if parsed.scheme!='https' or not parsed.netloc or parsed.username or parsed.password:raise ValueError('分析服务必须使用 HTTPS')
    data=None if payload is None else json.dumps(payload,ensure_ascii=False).encode()
    req=urllib.request.Request(url,data,{'Authorization':'Bearer '+token,'Content-Type':'application/json'})
    try:
        with urllib.request.build_opener(NoRedirect()).open(req,timeout=30) as response:return json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code in (401,403):raise ValueError('分析服务授权失效，请重新配置') from None
        raise RuntimeError('分析服务暂不可用（HTTP '+str(exc.code)+'）') from None

def run(root,config,task_id,question,progress=lambda text:None,packet=None):
    endpoint=config.get('url','').rstrip('/');tokenfile=config.get('tokenFile','')
    if not endpoint or not tokenfile:raise ValueError('尚未配置 AgentPair 分析服务，请打开采集状态 / 设置')
    token=Path(tokenfile).read_text(encoding='utf-8').strip()
    if packet is None:
        with sqlite3.connect(Path(root)/'collector.db',timeout=10) as db:packet=packet_for_task(db,task_id)
    body={'question':question,'packet':packet}
    key=hashlib.sha256((endpoint+'\n'+json.dumps(body,ensure_ascii=False,sort_keys=True)).encode()).hexdigest()
    with sqlite3.connect(Path(root)/'assistant.db') as cache:
        cache.execute('CREATE TABLE IF NOT EXISTS answers(id TEXT PRIMARY KEY,task TEXT,question TEXT,result TEXT)')
        row=cache.execute('SELECT result FROM answers WHERE id=?',(key,)).fetchone()
        if row:progress('已读取这份任务记录的分析');return json.loads(row[0])
    progress('正在提交当前任务证据');job=request_json(endpoint+'/jobs',token,body)
    for _ in range(300):
        result=request_json(endpoint+'/jobs/'+job['id'],token)
        if result['status']=='completed':
            result['packet']=packet;result['question']=question;result['taskId']=task_id
            with sqlite3.connect(Path(root)/'assistant.db') as cache:cache.execute('INSERT OR REPLACE INTO answers VALUES(?,?,?,?)',(key,task_id,question,json.dumps(result,ensure_ascii=False)))
            return result
        if result['status'] in ('failed','needs_attention','cancelled','interrupted'):raise RuntimeError(result.get('error','分析未通过复核，请稍后重试'))
        progress(result.get('stage','正在分析').replace('DeepSeek','助手'));time.sleep(1)
    raise TimeoutError('分析等待超时；服务器可能仍在处理，请稍后重试')
