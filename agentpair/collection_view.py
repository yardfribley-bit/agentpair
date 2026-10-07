"""Read-only projections across independent collectors; keep provenance explicit."""
import hashlib
import json
import time
import math
from datetime import datetime

KINDS={'user_message':'用户提问','assistant_message':'模型回复','message':'对话消息','reasoning':'已记录思路','tool_call':'工具调用','tool_result':'工具返回','turn_completed':'轮次完成','turn_aborted':'轮次中断','parse_error':'未解析记录'}

def event_seconds(value):
    """Normalize display time without rewriting source evidence."""
    if isinstance(value, bool): return 0
    if isinstance(value, str):
        try: value=float(value)
        except ValueError:
            try: return datetime.fromisoformat(value.replace('Z','+00:00')).timestamp()
            except (ValueError, OverflowError): return 0
    if not isinstance(value,(int,float)) or not math.isfinite(value): return 0
    if abs(value)>=100_000_000_000: value/=1000
    return value if 0 < value < 253402300800 else 0

class CollectionView:
    def __init__(self,devices,sessions):self.devices=devices;self.sessions=sessions

    def identities(self,owner,device):
        found=self.devices.get(device,owner)
        if not found:raise PermissionError('Device unavailable')
        with self.devices.connect() as db:
            aliases=[r[0] for r in db.execute('SELECT alias FROM device_aliases WHERE canonical=? AND owner=?',(found['id'],owner))]
        return found,[found['id'],*aliases]

    def inventory(self,items,owner=None):
        result=[];now=time.time()
        for original in items:
            item=dict(original);account=owner or item['ownerAccount'];device,ids=self.identities(account,item['id']);marks=','.join('?' for _ in ids)
            collectors=[]
            with self.devices.connect() as db:
                n=sum(db.execute('SELECT count(*) FROM '+table+' WHERE device_id IN ('+marks+')',ids).fetchone()[0] for table in ('applens_model_context','applens_llm_evidence','applens_llm_spans'))
            heartbeat=item.get('lastSeen') or 0
            if n or heartbeat:collectors.append({'id':'applens','name':'AppLens','records':n,'lastHeartbeat':heartbeat,'active':bool(heartbeat and 0<=now-heartbeat<90),'statusBasis':'heartbeat'})
            with self.sessions.connect() as db:
                n=db.execute('SELECT count(*) FROM session_events WHERE owner=? AND device IN ('+marks+')',[account,*ids]).fetchone()[0]
                upload=db.execute('SELECT max(received) FROM session_uploads WHERE owner=? AND device IN ('+marks+') AND status=200',[account,*ids]).fetchone()[0]
            if n or upload:collectors.append({'id':'sessionlens','name':'SessionLens','records':n,'lastUpload':upload,'active':bool(upload and 0<=now-upload<300),'statusBasis':'successful_upload'})
            item['controlOnline']=item.get('online',False)
            item['lastHeartbeat']=heartbeat
            item['lastActivity']=max(heartbeat,upload or 0)
            item['online']=any(c['active'] for c in collectors)
            item['activityStatus']='recent_activity' if item['online'] else 'no_recent_activity'
            item['collectors']=collectors;result.append(item)
        return result

    def search(self,owner,device,query,collector='all'):
        query=query.strip()
        if not query or len(query)>200:raise ValueError('请输入 1–200 字的关键词')
        if collector not in ('all','applens','sessionlens'):raise ValueError('Unknown collector')
        found,ids=self.identities(owner,device);hits=[];marks=','.join('?' for _ in ids)
        if collector in ('all','sessionlens'):
            with self.sessions.connect() as db:
                rows=db.execute('SELECT event FROM session_events WHERE owner=? AND device IN ('+marks+') AND instr(lower(json_extract(event,"$.payload")),lower(?))>0 ORDER BY rowid DESC LIMIT 50',[owner,*ids,query]).fetchall()
            for row in rows:
                e=json.loads(row[0]);body=json.dumps(e.get('payload',{}),ensure_ascii=False);at=body.lower().find(query.lower());start=max(0,at-70)
                hits.append({'id':'sessionlens:'+e['id'],'collector':'sessionlens','source':e['source'],'sessionId':e['sessionId'],'kind':KINDS.get(e['kind'],'其他会话记录'),'excerpt':body[start:start+280],'timestamp':event_seconds(e.get('timestamp') or e.get('at'))})
        if collector in ('all','applens'):
            data=self.model_data(owner,device,collector='applens')
            for c in data['calls']:
                body=c.get('body','');at=body.lower().find(query.lower())
                if at>=0:hits.append({'id':c['id'],'collector':'applens','source':c.get('application'),'sessionId':c.get('sessionId'),'kind':'模型输入','excerpt':body[max(0,at-70):max(0,at-70)+280],'timestamp':c.get('timestamp')})
        return {'items':hits[:100],'coverage':'SessionLens 搜索平台已接收的历史事件，最多返回 50 条；AppLens 搜索当前调用窗口。关键词匹配，不代表语义检索。'}

    def model_data(self,owner,device,request=None,summary=False,collector='all'):
        if collector not in ('all','applens','sessionlens'):raise ValueError('Unknown collector')
        found,ids=self.identities(owner,device);calls=[];items=[]
        if collector in ('all','applens') and not (request or '').startswith('sessionlens:'):
            source=self.devices.llm_data(owner,found['id'],request,summary)
            calls=[dict(c,collector='applens',collectorName='AppLens',application='WorkBuddy',recordType='model_input') for c in source['calls']];items=source['items']
        if collector in ('all','sessionlens') and (not request or request.startswith('sessionlens:')):
            marks=','.join('?' for _ in ids);query='SELECT event FROM session_events WHERE owner=? AND device IN ('+marks+')';args=[owner,*ids]
            if request:query+=' AND id=?';args.append(request.removeprefix('sessionlens:'))
            query+=' ORDER BY rowid DESC LIMIT 200'
            with self.sessions.connect() as db:events=[json.loads(r[0]) for r in db.execute(query,args)]
            for e in events:
                eid='sessionlens:'+e['id'];body=json.dumps(e.get('payload',{}),ensure_ascii=False,indent=2);stamp=event_seconds(e.get('timestamp') or e.get('at'))
                label=KINDS.get(e['kind'],'其他会话记录')
                c={'id':eid,'collector':'sessionlens','collectorName':'SessionLens','application':{'codex':'Codex','workbuddy':'WorkBuddy'}.get(e['source'],e['source']),'recordType':e['kind'],'source':'sessionlens_log','timestamp':stamp,'modelEvidence':label,'recordStatus':'parseable','bodyBytes':len(body.encode()),'bodySHA256':hashlib.sha256(body.encode()).hexdigest(),'sessionId':e['sessionId'],'sessionName':e['sessionId'],'complete':False,'evidence':e['evidence']}
                if not summary:c['body']=body
                calls.append(c)
                if not summary:items.append({'id':eid+':content','requestId':eid,'name':label,'category':label,'source':'SessionLens · '+c['application'],'rawContent':body,'bodyBytes':len(body.encode()),'classificationBasis':'源日志事件类型：'+e['kind'],'messageIndex':0,'blockIndex':0,'charStart':0,'charEnd':len(body),'sourceVerified':False,'complete':False,'evidence':e['evidence']})
        if request and not calls:raise ValueError('采集记录不存在或不属于所选来源')
        for c in calls:c['timestamp']=event_seconds(c.get('timestamp'))
        calls.sort(key=lambda c:c.get('timestamp') or 0,reverse=True)
        activity=self.inventory([dict(found,ownerAccount=owner)],owner)[0]
        return {'device':{'id':found['id'],'name':found['name'],'online':activity['online'],'activityStatus':activity['activityStatus']},'calls':calls,'items':items,'limitations':['SessionLens 是源日志记录，不代表完整模型请求或网络接收证据。'],'coverage':{'collectorSources':sorted({c['collector'] for c in calls})}}
