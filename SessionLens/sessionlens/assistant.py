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

VERSION=7

FACET_KINDS={'requirement':('用户提问','Agent 回复'),'reasoning':('解题思路',),
             'tools':('工具调用',),'results':('工具返回','Agent 回复'),
             'changes':('文件修改','工具调用'),'failures':('工具返回','Agent 回复','解题思路'),
             'context':('会话背景','用户提问')}

def question_facets(question,facets=None):
    import re
    supplied=[x for x in (facets or []) if isinstance(x,str) and x in FACET_KINDS]
    if supplied:return list(dict.fromkeys(supplied))[:3]
    patterns={'tools':r'工具|参数|网址|curl|命令|调用','reasoning':r'思路|reasoning|为什么|考虑',
              'results':r'结果|返回|成功|交付|完成','failures':r'失败|中断|超时|重试',
              'changes':r'修改|改动|变更','context':r'上下文|记忆|背景','requirement':r'要求|需求|谁提出'}
    return [facet for facet,pattern in patterns.items() if re.search(pattern,question,re.I)][:3]

def focus_evidence(db,task,question,facets):
    if not question:return []
    from .knowledge import query_anchors,explicit_entities
    steps=step_table(db);kinds=list(dict.fromkeys(k for f in facets for k in FACET_KINDS.get(f,())))
    import re
    specific=re.findall(r'https?://[a-zA-Z0-9_./?=&%:+#~-]+|[a-zA-Z0-9][a-zA-Z0-9_.-]*\.[a-zA-Z]{2,}(?:/[a-zA-Z0-9_./?=&%:+#~-]*)?',question)
    specific.extend(x for x in explicit_entities(question) if '.' in x or '/' in x)
    terms=list(dict.fromkeys(x.lower() for x in specific))[:8] or list(dict.fromkeys(explicit_entities(question)+query_anchors(question)))[:8]
    ids=[]
    if terms:
        where=' OR '.join('instr(lower(excerpt),?)>0' for _ in terms)
        ids=[r[0] for r in db.execute('SELECT event FROM '+steps+' WHERE task=? AND ('+where+') ORDER BY seq DESC LIMIT 16',[task]+terms)]
    if kinds:
        sql='SELECT event FROM '+steps+' WHERE task=? AND kind IN ('+','.join('?' for _ in kinds)+') ORDER BY seq '
        for order in ('ASC','DESC'):ids.extend(r[0] for r in db.execute(sql+order+' LIMIT 6',[task]+kinds))
    ids=list(dict.fromkeys(ids))[:24];paired=[]
    for event in ids:
        row=db.execute('SELECT seq,call_id FROM '+steps+' WHERE task=? AND event=?',(task,event)).fetchone()
        if not row:continue
        if row[1]:paired.extend(r[0] for r in db.execute('SELECT event FROM '+steps+' WHERE task=? AND call_id=? ORDER BY seq LIMIT 3',(task,row[1])))
        previous=db.execute("SELECT event FROM "+steps+" WHERE task=? AND kind IN ('用户提问','解题思路') AND seq<? ORDER BY seq DESC LIMIT 1",(task,row[0])).fetchone()
        if previous:paired.append(previous[0])
    return list(dict.fromkeys(ids+paired))[:48]

def packet_for_task(db,task_id,question=None,facets=None):
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
    facets=question_facets(question or '',facets);focus=focus_evidence(db,task_id,question,facets)
    chosen=list(dict.fromkeys(focus+[r[0] for r in selected]))
    required=[r['eventId'] for r in requirements[:1]+requirements[-24:]]
    for event in chosen:
        link=bindings.get(event,{})
        required.extend(x for x in (link.get('requirementEvent'),link.get('approvalEvent'),link.get('planEvent')) if x)
    wanted=list(dict.fromkeys(required+chosen))[:120]
    selected=sorted(((ident,) for ident in wanted),key=lambda r:db.execute('SELECT rowid FROM events WHERE id=?',r).fetchone()[0])
    fragments=[];records=[];per_record=min(5000,90000//max(1,len(selected)))
    from .message_graph import bounded_event
    for (identity,) in selected:
        seq=db.execute('SELECT rowid FROM events WHERE id=?',(identity,)).fetchone()[0]
        e=bounded_event(db,identity,seq,budget=min(262144,4*1024*1024//max(1,len(selected))));records.append(e);text=event_text(e);cut=text[:per_record]
        if not cut:continue
        fragments.append({'evidenceId':'E'+str(len(fragments)+1).zfill(3),'eventId':identity,'kind':e['kind'],'role':e.get('role'),'tool':e.get('name'),'callId':e.get('callId'),'timestamp':e.get('timestamp'),'text':cut,'truncated':bool(e.get('_bodyTruncated')) or len(cut)<len(text)})
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
            'messageRelationCoverage':'selected_records','messageRelationGaps':len(graph['gaps']),
            'questionFacets':facets,'focusedEvidenceRefs':[refs[x] for x in focus if x in refs]}

def combine_packets(packets):
    """Bounded comparison evidence; preserve ownership and globally unique refs."""
    if not 1<=len(packets)<=3:raise ValueError('一次最多解释三个独立任务')
    fragments=[];tasks=[];relations=[];execution=[];requirements=[];reasoning=[]
    per_record=min(5000,90000//max(1,sum(min(40,len(p['fragments'])) for p in packets)))
    # 40 records per task, total <=120. Each packet was already bounded; retain
    # relevant records and the original goal rather than concatenate full logs.
    for packet in packets:
        focus=set(packet.get('focusedEvidenceRefs',[]));all_fragments=packet['fragments']
        origin=next((r['evidenceRef'] for r in packet.get('requirementHistory',[])),None)
        ranked=sorted(enumerate(all_fragments),key=lambda r:(r[1]['evidenceId']!=origin,r[1]['evidenceId'] not in focus,r[0]))[:40]
        selected=[f for _,f in sorted(ranked)];mapping={}
        for f in selected:
            ref='E'+str(len(fragments)+1).zfill(3);mapping[f['evidenceId']]=ref
            cut=f['text'][:per_record]
            fragments.append({**f,'text':cut,'truncated':f.get('truncated',False) or len(cut)<len(f['text']),'evidenceId':ref,'taskId':packet['taskId']})
        tasks.append({k:packet.get(k) for k in ('taskId','source','sessionId','prompt','revision','lineageSignature','totalRecords')})
        tasks[-1]['includedRecords']=len(selected)
        tasks[-1]['prompt']=tasks[-1]['prompt'][:1200]
        for row in packet.get('messageRelations',[]):
            if row['fromRef'] in mapping and row['toRef'] in mapping:relations.append({**row,'fromRef':mapping[row['fromRef']],'toRef':mapping[row['toRef']],'taskId':packet['taskId']})
        for row in packet.get('executionLinks',[]):
            if row['evidenceRef'] in mapping:execution.append({**row,**{k:mapping.get(row.get(k)) for k in ('evidenceRef','requirementRef','approvalRef','planRef')},'taskId':packet['taskId']})
        for row in packet.get('requirementHistory',[]):
            if row['evidenceRef'] in mapping:requirements.append({**row,'evidenceRef':mapping[row['evidenceRef']],'taskId':packet['taskId']})
        for row in packet.get('reasoningLinks',[]):
            if all(x in mapping for x in row['pathRefs']):reasoning.append({**row,'callRef':mapping[row['callRef']],'reasoningRef':mapping[row['reasoningRef']],'pathRefs':[mapping[x] for x in row['pathRefs']],'taskId':packet['taskId']})
    return {'version':VERSION,'scope':'multiple','tasks':tasks,'prompt':'所检索到的相关任务比较',
            'source':'multiple','coverage':'up_to_three_retrieved_tasks','fragments':fragments,
            'includedRecords':len(fragments),'totalRecords':sum(p['totalRecords'] for p in packets),
            'requirementHistory':requirements,'executionLinks':execution,'messageRelations':relations,
            'reasoningLinks':reasoning,'questionFacets':packets[0].get('questionFacets',[])}

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
