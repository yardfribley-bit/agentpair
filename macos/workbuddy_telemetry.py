"""Read-only WorkBuddy usage adapter. Never exports conversation content."""
import argparse
import datetime
import hashlib
import html
import json
import os
from pathlib import Path
import sqlite3
import time
import importlib.util
_hook_spec=importlib.util.spec_from_file_location('applens_hook',Path(__file__).with_name('workbuddy_hook.py'))
_hook=importlib.util.module_from_spec(_hook_spec);_hook_spec.loader.exec_module(_hook)
redact=_hook.redact

def number(value):
    return value if isinstance(value,int) and not isinstance(value,bool) and value>=0 else None

def normalize(record):
    provider=record.get('providerData')
    if not isinstance(provider,dict):return None
    usage=provider.get('usage')
    if not isinstance(usage,dict):return None
    request=provider.get('conversationRequestId') or provider.get('messageId')
    if not isinstance(request,str) or not request:return None
    timestamp=record.get('timestamp')
    if not isinstance(timestamp,(int,float)) or isinstance(timestamp,bool):return None
    # WorkBuddy records millisecond epoch timestamps.
    seconds=timestamp/1000 if timestamp>100_000_000_000 else timestamp
    model=provider.get('model') or provider.get('requestModelId')
    result={'requestHash':hashlib.sha256(request.encode()).hexdigest(),
            'timestamp':seconds,'application':'WorkBuddy','model':model if isinstance(model,str) else None,
            'inputTokens':number(usage.get('inputTokens')),'outputTokens':number(usage.get('outputTokens')),
            'totalTokens':number(usage.get('totalTokens')),
            'source':'workbuddy_transcript','latency':None,'cost':None,
            'limitations':['completion timestamp only; latency and provider billing unavailable',
                           'session evidence is not a complete network capture']}
    return result

def otlp(rows):
    spans=[]
    for r in rows:
        attrs={'gen_ai.operation.name':'chat','gen_ai.request.model':r['model'],
               'gen_ai.usage.input_tokens':r['inputTokens'],'gen_ai.usage.output_tokens':r['outputTokens'],
               'applens.source':r['source'],'applens.duration.observed':False}
        values=[]
        for key,value in attrs.items():
            if value is None:continue
            kind='boolValue' if isinstance(value,bool) else 'intValue' if isinstance(value,int) else 'stringValue'
            values.append({'key':key,'value':{kind:str(value) if kind=='intValue' else value}})
        stamp=str(int(r['timestamp']*1_000_000_000))
        spans.append({'traceId':r['requestHash'][:32],'spanId':r['requestHash'][32:48],
                      'name':'WorkBuddy LLM observed completion','kind':1,
                      'startTimeUnixNano':stamp,'endTimeUnixNano':stamp,'attributes':values})
    return {'resourceSpans':[{'resource':{'attributes':[{'key':'service.name','value':{'stringValue':'AppLens.WorkBuddy'}}]},
                              'scopeSpans':[{'scope':{'name':'applens.workbuddy','version':'0.1.0'},'spans':spans}]}]}

def collect(source,storage,since=0):
    storage.mkdir(parents=True,exist_ok=True,mode=0o700)
    db=sqlite3.connect(storage/'workbuddy-telemetry.sqlite3');os.chmod(storage/'workbuddy-telemetry.sqlite3',0o600)
    db.execute('CREATE TABLE IF NOT EXISTS usage(id TEXT PRIMARY KEY,data TEXT NOT NULL)')
    files=sorted(source.glob('*/*.jsonl'),key=lambda p:p.stat().st_mtime,reverse=True)
    imported=0;bad=0;timeline=[]
    for path in files:
        if path.stat().st_mtime<since:continue
        # Transcript contents stay local; only allowlisted usage metadata persists.
        with path.open(encoding='utf-8',errors='replace') as f:
            for line in f:
                try:record=json.loads(line);row=normalize(record)
                except (ValueError,TypeError,AttributeError):bad+=1;continue
                timestamp=record.get('timestamp',0)
                if isinstance(timestamp,(int,float)):
                    seconds=timestamp/1000 if timestamp>100_000_000_000 else timestamp
                    kind=record.get('type');provider=record.get('providerData') or {}
                    if seconds>=since and kind in ('message','function_call','function_call_result') and isinstance(provider,dict):
                        role=record.get('role')
                        if role in ('user','assistant') or kind!='message':
                            evidence={k:redact(provider[k]) for k in ('agent','model','argumentsDisplayText','toolResult') if k in provider}
                            if kind=='message' and role in ('user','assistant'):evidence['content']=redact(record.get('content'))
                            timeline.append({'time':seconds,'event':kind,'role':role,'source':'WorkBuddy transcript · 非完整 HTTP 请求','evidence':evidence})
                if not row or row['timestamp']<since:continue
                previous=db.execute('SELECT data FROM usage WHERE id=?',(row['requestHash'],)).fetchone()
                if previous:
                    old=json.loads(previous[0])
                    for key in ('inputTokens','outputTokens','totalTokens'):
                        values=[v for v in (old.get(key),row.get(key)) if v is not None]
                        row[key]=max(values) if values else None
                    row['timestamp']=max(old['timestamp'],row['timestamp'])
                    row['model']=row['model'] or old.get('model')
                else:imported+=1
                db.execute('INSERT OR REPLACE INTO usage VALUES(?,?)',(row['requestHash'],json.dumps(row)))
    db.commit();rows=[json.loads(r[0]) for r in db.execute('SELECT data FROM usage')];db.close()
    rows=[row for row in rows if row['timestamp']>=since]
    output=storage/'workbuddy-otlp.json'
    fd=os.open(output,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
    with os.fdopen(fd,'w') as f:json.dump(otlp(rows),f)
    template=(Path(__file__).with_name('telemetry.html')).read_text()
    events=[]
    for path in sorted((storage/'hooks').glob('*.json'))[-500:]:
        try:
            event=json.loads(path.read_text())
            if event.get('source')=='workbuddy_hook' and event.get('observedAt',0)>=since:events.append(event)
        except (ValueError,OSError):continue
    hook_rows=''.join('<details><summary>'+html.escape(str(e.get('event')))+' · '+html.escape(str(e.get('toolName') or '会话'))+' · '+html.escape(str(e.get('sessionHash','')[:12]))+'</summary><pre>'+html.escape(json.dumps(e,ensure_ascii=False,indent=2))+'</pre></details>' for e in events)
    hook_panel='<section><h2>会话与工具执行 · '+str(len(events))+' 条钩子事件</h2><p>脱敏后的本地钩子证据，不代表完整 LLM 请求。可通过 sessionHash 和 toolCallId 关联事件。</p>'+(hook_rows or '<p>尚未收到 AppLens 钩子事件；不能据此认定没有工具执行。</p>')+'</section>'
    transcript_rows=''.join('<details><summary>'+datetime.datetime.fromtimestamp(e['time']).strftime('%H:%M:%S')+' · '+html.escape(str(e['role'] or e['event']))+'</summary><pre>'+html.escape(json.dumps(e,ensure_ascii=False,indent=2))+'</pre></details>' for e in sorted(timeline,key=lambda x:x['time'])[-200:])
    evidence_events=[{**e,'source':'workbuddy_transcript'} for e in sorted(timeline,key=lambda x:x['time'])[-100:]]+events[-100:]
    evidence_file=storage/'workbuddy-evidence.json'
    fd=os.open(evidence_file,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
    with os.fdopen(fd,'w') as f:json.dump({'events':evidence_events},f,ensure_ascii=False)
    hook_panel+='<section><h2>任务执行实录 · 最近 200 条</h2><p>会话记录中的用户输入、可见回复和工具记录；仅本机展示，未上传正文。脱敏不是所有敏感内容的保证。</p>'+transcript_rows+'</section>'
    template=template.replace('</main>',hook_panel+'</main>')
    table=''.join('<tr><td>'+datetime.datetime.fromtimestamp(r['timestamp']).strftime('%Y-%m-%d %H:%M:%S')+'</td><td>'+html.escape(r['model'] or '未观测')+'</td><td>'+str(r['inputTokens'] if r['inputTokens'] is not None else '未观测')+'</td><td>'+str(r['outputTokens'] if r['outputTokens'] is not None else '未观测')+'</td><td>未观测</td><td><details><summary>'+r['requestHash'][:12]+'</summary><pre>'+html.escape(json.dumps(r,ensure_ascii=False,indent=2))+'</pre></details></td></tr>' for r in sorted(rows,key=lambda x:x['timestamp'],reverse=True))
    template=template.replace('{{ROWS}}',table or '<tr><td colspan="6">没有符合时间范围的 LLM 用量记录；不是零调用的证明。</td></tr>').replace('{{CALLS}}',str(len(rows))).replace('{{INPUT}}',str(sum(r['inputTokens'] or 0 for r in rows))).replace('{{OUTPUT}}',str(sum(r['outputTokens'] or 0 for r in rows)))
    page=storage/'workbuddy-telemetry.html'
    fd=os.open(page,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
    with os.fdopen(fd,'w') as f:f.write(template)
    return {'requests':len(rows),'newRequests':imported,'inputTokens':sum(r['inputTokens'] or 0 for r in rows),
            'outputTokens':sum(r['outputTokens'] or 0 for r in rows),'malformedLines':bad,
            'contentCaptured':bool(timeline or events),'contentUploaded':False,'transcriptEvents':len(timeline),
            'hookEvents':len(events),'cost':None,'latency':None,'otlpFile':str(output),'evidenceFile':str(evidence_file),'dashboardFile':str(page)}

def main():
    p=argparse.ArgumentParser();p.add_argument('--source',type=Path,default=Path.home()/'.workbuddy/projects')
    p.add_argument('--storage',type=Path,default=Path.home()/'Library/Application Support/AppLens/telemetry')
    p.add_argument('--since',type=float,default=0);p.add_argument('--watch',action='store_true')
    args=p.parse_args()
    while True:
        print(json.dumps(collect(args.source,args.storage,args.since)),flush=True)
        if not args.watch:break
        time.sleep(10)
if __name__=='__main__':main()
