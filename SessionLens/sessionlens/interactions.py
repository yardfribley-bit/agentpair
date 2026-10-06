"""Count recorded conversations, without pretending messages are API calls."""
from collections import Counter
from .task_lineage import resolve,step_table,task_table,exists

def model_identifiers(db,tasks):
    keys=set();skipped=0
    for start in range(0,len(tasks),100):
        batch=tasks[start:start+100];marks=','.join('?' for _ in batch)
        rows=db.execute('''SELECT coalesce(json_extract(e.event,'$.source'),'codex'),e.session,
          coalesce(json_extract(e.event,'$.payload.providerData.messageId'),json_extract(e.event,'$.payload.item.providerData.messageId'))
          FROM '''+step_table(db)+''' s JOIN events e ON e.id=s.event
          WHERE s.task IN ('''+marks+''') AND s.kind IN ('Agent 回复','解题思路','工具调用') AND length(e.event)<=262144''',batch)
        for source,session,ident in rows:
            if isinstance(ident,str) and ident and len(ident)<=256:keys.add((source,session,ident))
        skipped+=db.execute('SELECT count(*) FROM '+step_table(db)+' s JOIN events e ON e.id=s.event WHERE s.task IN ('+marks+") AND s.kind IN ('Agent 回复','解题思路','工具调用') AND length(e.event)>262144",batch).fetchone()[0]
    return len(keys) or None,skipped

def task_interactions(db,task,metadata=True):
    task=resolve(db,task)
    relations=db.execute('SELECT relation,count(*) FROM task_links WHERE root=? GROUP BY relation',(task,)).fetchall() if exists(db) else []
    roles=Counter(dict(relations));turns=sum(roles.values())
    if not turns:turns=int(db.execute('SELECT 1 FROM '+task_table(db)+' WHERE id=?',(task,)).fetchone() is not None)
    kinds=dict(db.execute('SELECT kind,count(*) FROM '+step_table(db)+' WHERE task=? GROUP BY kind',(task,)))
    identifiers,skipped=model_identifiers(db,[task]) if metadata else (None,0)
    return {'userTurns':turns,'approvalTurns':roles['approval'],'executionTurns':roles['execution'],
            'discussionTurns':roles['discussion'],'revisionTurns':roles['revision'],'unresolvedTurns':roles['unresolved'],
            'agentReplyRecords':kinds.get('Agent 回复',0),'reasoningRecords':kinds.get('解题思路',0),'toolCalls':kinds.get('工具调用',0),
            'modelCalls':None,'modelCallsStatus':'not_recorded',
            'modelMessageIdentifiers':identifiers,'modelMetadataSkipped':skipped,
            'modelIdentifierBasis':'按日志提供的模型 messageId 去重；消息标识、usage.requests 及外层对话请求 ID 不认证实际模型 API 调用次数。',
            'modelCallsReason':'SessionLens 会话日志没有完整的逐次模型请求记录，当前无法确认实际调用次数。',
            'basis':'用户轮次按同一需求关联后的原始用户提问去重；确认与开始执行也计入用户发言，不另算需求。',
            'coverage':'全部已关联任务记录；不是回答中选取的片段。Agent 回复记录可能包含分片，不当作模型调用次数。'}

def project_interactions(db,tasks):
    seen=list(dict.fromkeys(resolve(db,t) for t in tasks));items=[task_interactions(db,t,metadata=False) for t in seen]
    totals={name:sum(x[name] for x in items) for name in ('userTurns','approvalTurns','executionTurns','agentReplyRecords','toolCalls')}
    identifiers,skipped=model_identifiers(db,seen)
    totals.update(modelMessageIdentifiers=identifiers,modelMetadataSkipped=skipped)
    totals.update(modelCalls=None,modelCallsStatus='not_recorded',taskCount=len(seen),modelCallsReason='会话日志未完整记录模型请求，不能确认 Agent 调用大模型的总次数。')
    return totals

def interaction_question(question):
    return any(w in question.lower() for w in ('交互','几轮','多少轮','对话次数','发了几','发了多少','问了几','问了多少','模型调用','模型多少','模型几次','确认几次'))
