"""Read-only usage from task records, local host metrics and persisted leases."""
import datetime
import math
import os
from pathlib import Path
import shutil


def snapshot(engine):
    now=datetime.datetime.now(datetime.timezone.utc)
    with engine.lock,engine.connection() as db:
        import json
        tasks=[json.loads(row[0]) for row in db.execute('SELECT data FROM tasks')]
    models={}; totals={'input':0,'output':0,'calls':0,'missingUsageCalls':0}
    per_task=[]
    for task in tasks:
        task_tokens=0
        for message in task['messages']:
            if message.get('role')=='user':continue
            answer=message.get('answer',{})
            entries=[(message.get('model') or '生成模型（历史记录未标识）',message.get('usage'),False)]
            decision=answer.get('jev',{})
            if decision.get('status')=='evaluated':entries.append((decision.get('model','决策模型'),decision.get('usage'),True))
            for model,usage,is_decision in entries:
                row=models.setdefault(model,{'model':model,'input':0,'output':0,'calls':0,'missingUsageCalls':0,'kind':'决策' if is_decision else '生成'})
                row['calls']+=1;totals['calls']+=1
                usage=usage or {}
                incoming=usage.get('input_tokens',usage.get('prompt_tokens'))
                outgoing=usage.get('output_tokens',usage.get('completion_tokens'))
                if type(incoming)!=int or type(outgoing)!=int:
                    row['missingUsageCalls']+=1;totals['missingUsageCalls']+=1
                for key,value in [('input',incoming),('output',outgoing)]:
                    if type(value)==int and value>=0:row[key]+=value;totals[key]+=value;task_tokens+=value
        per_task.append({'id':task['id'],'title':task['title'],'status':task['status'],'tokens':task_tokens})
    manager=getattr(engine.backend,'manager',None)
    leases=[];lease_error=None;server_cost=0;lease_alerts=[]
    try:
        for lease in manager.records() if manager else []:
            start=datetime.datetime.fromisoformat(lease['createdAt'])
            released=lease.get('releasedAt')
            end=datetime.datetime.fromisoformat(released) if released else now
            hours=max(1,math.ceil(max(0,(end-start).total_seconds())/3600))
            rate=lease.get('price',{}).get('hourlyCNY')
            cost=hours*rate if isinstance(rate,(int,float)) and lease.get('hostId') else None
            server_cost+=cost or 0
            remaining=max(0,int((datetime.datetime.fromisoformat(lease['expiresAt'])-now).total_seconds())) if not released else 0
            leases.append({'id':lease.get('id'),'name':lease.get('name'),'hostId':lease.get('hostId'),
                'managed':True,'state':lease['state'],'createdAt':lease['createdAt'],'expiresAt':lease['expiresAt'],
                'releasedAt':released,'hourlyQuoteCNY':rate,'estimatedBilledCNY':cost,
                'remainingSeconds':remaining})
            if not released and remaining <= 900:
                lease_alerts.append({'severity':'warning' if remaining else 'critical','leaseId':lease.get('id'),
                    'message':'Driver 租约已到期，等待回收。' if not remaining else 'Driver 租约将在 15 分钟内到期，请确认是否继续保留。'})
    except (OSError,ValueError,KeyError):lease_error='租约记录暂时不可读取'
    persistent=getattr(engine.backend,'persistent_status',lambda:None)()
    nodes=[{'id':'navigator','role':'Navigator','kind':'persistent','state':'online',
            'address':None,'currentTasks':[]}]
    if persistent:
        nodes.append({'id':'linux-driver','role':'Linux Driver','kind':'persistent',
            'state':'ready' if persistent['workerReady'] else 'setup_needed' if persistent['reachable'] else 'unreachable',
            'address':persistent['ip'],'checkedAt':persistent['checkedAt'],
            'currentTasks':persistent['currentTasks']})
    for lease in leases:
        if lease['state']=='active':
            nodes.append({'id':lease.get('id'),'role':'Temporary Driver','kind':'leased',
                'state':'leased','address':None,'currentTasks':[]})
    host={'cpuCores':os.cpu_count(),'load1m':os.getloadavg()[0] if hasattr(os,'getloadavg') else None,'memoryUsedBytes':None,'memoryTotalBytes':None}
    try:
        fields={l.split(':')[0]:int(l.split()[1])*1024 for l in Path('/proc/meminfo').read_text().splitlines() if len(l.split())>=2}
        host.update(memoryTotalBytes=fields['MemTotal'],memoryUsedBytes=fields['MemTotal']-fields['MemAvailable'])
    except (OSError,ValueError,KeyError):pass
    disk=shutil.disk_usage('.');host.update(diskUsedBytes=disk.used,diskTotalBytes=disk.total)
    return {'updatedAt':now.isoformat(),'tokens':totals,'models':list(models.values()),'tasks':per_task,
        'activeTasks':sum(t['status'] in ('running','queued','cancelling') for t in tasks),
        'navigator':host,'nodes':nodes,'leases':leases,'leaseAlerts':lease_alerts,'leaseError':lease_error,
        'activeDrivers':sum(l['state']=='active' for l in leases)+int(bool(persistent and persistent['workerReady'])),
        'serverEstimatedCNY':round(server_cost,4),
        'generationEstimate':engine.usage(),'modelActualCNY':None,'balanceCNY':None,
        'notes':['每 5 秒刷新；token 在模型调用返回后更新，未返回或失败调用可能尚未计入。',
                 '云机费用按记录报价及整小时向上取整估算，不是云厂商账单；不包含常驻 Navigator 和 Linux Driver。',
                 '生成调用费用为历史估算，尚未包含独立决策费用；模型实际费用、余额及 Navigator 固定费用尚未接入。',
                 '常驻 Linux Driver 状态来自最近一次 SSH/Worker 检查；临时 Driver 来自租约记录；CPU 显示系统负载，不是使用率。',
                 '未由 Navigator 创建的云主机不会出现在托管租约中；此类资源需在云厂商控制台确认自动续费并手动停止或释放。']}
