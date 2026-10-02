"""Stable UI records projected from persisted execution events, never model guesses."""
from collections import OrderedDict

STAGES={'plan':'规划与拆解','driver':'执行任务','review':'复核与交付',
        'explore':'独立探索','review_peer':'交叉复核','revise':'修订结果'}

def role(value):
    return {'navigator':'Navigator','driver':'Driver','user':'用户'}.get(value,value or 'Navigator')

def project(task):
    jobs=OrderedDict(); messages=OrderedDict(); active={}; addresses={}; activity=[]
    rounds=task.get('round',1)
    def create(key,node,stage,round_number,at):
        if key not in jobs:
            jobs[key]={'id':key,'label':'Job-'+str(len(jobs)+1),'node':node,'stage':stage,
                'title':STAGES.get(stage,stage or '执行任务'),'round':round_number,'startedAt':at,
                'status':'queued','objective':'','steps':[{'id':x,'title':y,'status':'pending'} for x,y in
                    [('dispatch','下发任务'),('receive','接收上下文'),('execute','执行与取证'),('result','返回结果')]],
                'tools':[],'logs':[],'artifacts':[],'waitingReason':None}
        active[node]=key
        return jobs[key]
    for index,e in enumerate(task.get('events',[])):
        kind=e.get('kind'); at=e.get('at'); node=role(e.get('role') or e.get('to'))
        r=e.get('round',rounds); stage=e.get('phase') or e.get('stage','driver')
        packet=e.get('message') or {}
        key=e.get('jobId')
        if kind in ('stage_started','job_started'):
            key=key or f"{task['id']}:event:{index}"
            job=create(key,node,stage,r,at);job['status']='running'
        elif key and key not in jobs:
            job=create(key,node,stage,r,at)
        else:
            key=key or active.get(node)
            job=jobs.get(key)
        if kind=='node_assigned':
            addresses[node]={'address':e.get('address') or e.get('intelligence',{}).get('address'),
                             'kind':e.get('nodeKind') or e.get('intelligence',{}).get('nodeKind')}
        if kind in ('handoff_requested','branch_handoff') and packet.get('id'):
            recipient=role(packet.get('to')); key=e.get('jobId') or active.get(recipient)
            job=jobs.get(key)
            messages[packet['id']]={**packet,'from':role(packet.get('from')),'to':recipient,
                'jobId':key,'at':at,'status':'sent','receipts':[{'state':'sent','at':at,'text':'任务交接已记录'}]}
            if job:
                job['objective']=str(packet.get('summary',''))
                job['steps'][0].update(status='completed',at=at)
                job['steps'][1].update(status='running')
        mid=e.get('messageId')
        if mid in messages and kind in ('worker_received','stage_completed','branch_handoff_processed','worker_failed'):
            state={'worker_received':'received','worker_failed':'failed'}.get(kind,'processed')
            messages[mid]['status']=state
            messages[mid]['receipts'].append({'state':state,'at':at,'text':e.get('text') or {'received':'执行端已接收','processed':'已返回处理结果','failed':'处理失败'}[state]})
        if job:
            job['logs'].append({'at':at,'kind':kind,'text':e.get('text') or e.get('summary') or STAGES.get(stage,stage),
                                **({'receipt':e['receipt']} if e.get('receipt') else {})})
            if kind=='worker_received':
                job['status']='running';job['steps'][1].update(status='completed',at=at);job['steps'][2]['status']='running'
            if kind in ('model_started','tool_started'):
                job['steps'][2]['status']='running';job['waitingReason']='等待模型返回' if kind=='model_started' else '等待工具返回'
            if kind=='tool_started':
                job['tools'].append({'id':e.get('evidenceId') or str(index),'name':e.get('tool') or '工具',
                    'arguments':e.get('arguments',{}),'status':'running','startedAt':at})
            if kind=='tool_result':
                receipt=e.get('receipt') or e.get('evidence') or {}
                evidence_id=receipt.get('evidenceId') or e.get('evidenceId')
                tool=next((t for t in reversed(job['tools']) if t['id']==evidence_id),None)
                if tool is None:
                    tool={'id':evidence_id or str(index),'name':receipt.get('tool','取证'),'arguments':receipt.get('arguments',{})};job['tools'].append(tool)
                tool.update(status='failed' if receipt.get('ok') is False or receipt.get('error') else 'completed',finishedAt=at,receipt=receipt)
                job['waitingReason']=None
            if kind=='model_completed':job['waitingReason']=None
            if kind=='worker_completed':
                job['steps'][2].update(status='completed',at=at);job['steps'][3]['status']='running';job['waitingReason']='等待结果保存与复核'
            if kind in ('stage_completed','job_completed'):
                job['status']='completed';job['finishedAt']=at;job['waitingReason']=None;job['steps'][3].update(status='completed',at=at)
            if kind in ('worker_failed','job_failed'):
                job['status']='failed';job['waitingReason']=e.get('text','执行失败');job['finishedAt']=at
        if kind in ('stage_started','job_started','handoff_requested','branch_handoff','tool_result','rework','stage_completed','job_completed','job_failed','node_assigned'):
            activity.append({'id':str(index),'at':at,'node':node,'jobId':key,'kind':kind,
                'title':e.get('text') or ({'stage_started':'开始'+STAGES.get(stage,stage),
                    'stage_completed':'返回'+STAGES.get(stage,stage)+'结果','handoff_requested':f"{role(packet.get('from'))} → {role(packet.get('to'))}"}.get(kind,STAGES.get(stage,stage))),
                'summary':packet.get('summary') or e.get('summary','')})
    for m in task.get('messages',[]):
        if not isinstance(m.get('answer'),dict):continue
        candidates=[j for j in jobs.values() if j['node']==role(m.get('role')) and j['round']==m.get('round') and j['stage']==m.get('stage')]
        if candidates:
            job=next((j for j in reversed(candidates) if not j.get('output')),candidates[-1])
            job['output']=m['answer'];job['artifacts']=m['answer'].get('artifacts',[])
    terminal=task.get('status') not in ('running','queued','cancelling')
    for j in jobs.values():
        if terminal and j['status']=='running':
            j['status']='interrupted';j['waitingReason']='本轮已结束，缺少该步骤的完成回执'
        j['progress']={'completed':sum(s['status']=='completed' for s in j['steps']),'total':len(j['steps'])}
    review=next((m.get('answer',{}) for m in reversed(task.get('messages',[])) if m.get('stage')=='review' and m.get('round')==rounds),{})
    checks=review.get('decision',{}).get('checks',[]) if isinstance(review.get('decision'),dict) else []
    if not checks and isinstance(review.get('checks'),dict):
        checks=[{'id':k,'question':k,**v} for k,v in review['checks'].items() if isinstance(v,dict)]
    nodes=[]
    names=list(dict.fromkeys(['Navigator']+[j['node'] for j in jobs.values() if j['node']!='用户']+list(addresses)))
    for name in names:
        js=[j for j in jobs.values() if j['node']==name and j['round']==rounds]
        current=js[-1] if js else None
        nodes.append({'id':name,'name':name,'jobId':current['id'] if current else None,
            'status':current['status'] if current else 'pending',
            'summary':(current['objective'] or current['title']) if current else '等待任务分配',
            'waitingReason':current['waitingReason'] if current else None,**addresses.get(name,{})})
    return {'schemaVersion':1,'jobs':list(jobs.values()),'nodes':nodes,'messages':list(messages.values()),
            'activity':activity,'acceptance':{'items':checks,'passed':sum(c.get('value')=='yes' for c in checks),
            'total':len(checks),'verdict':review.get('verdict'),'finalAnswer':review.get('finalAnswer')},
            'plannedSteps':next((m['answer'].get('steps',[]) for m in reversed(task.get('messages',[])) if m.get('stage')=='plan' and m.get('round')==rounds),[])}
