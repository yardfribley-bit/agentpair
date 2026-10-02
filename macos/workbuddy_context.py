"""Read generation input and allowlisted request metadata; never tool spans."""
import datetime,hashlib,json,re
from pathlib import Path

def request_metadata(log_root,since):
    rows=[]
    if not log_root.exists():return rows
    for path in log_root.glob('*/*.log'):
        if path.stat().st_mtime<since:continue
        try:
            with path.open(errors='replace') as stream:
                for line in stream:
                    if '[ModelProvider]' not in line:continue
                    if 'Sending request:' not in line and 'message-to-model-request latency' not in line:continue
                    stamp=re.match(r'\[(\d+/\d+/\d+), (\d+:\d+:\d+) (AM|PM)\.(\d+)\]',line)
                    pid=re.search(r'\[pid=(\d+)\]',line)
                    model=re.search(r'\bmodel(?:Name|Id)?=([A-Za-z0-9_.:/-]+)',line)
                    if not stamp or not pid or not model:continue
                    time=datetime.datetime.strptime(' '.join(stamp.group(i) for i in (1,2,3)),'%m/%d/%Y %I:%M:%S %p').timestamp()+float('0.'+stamp[4])
                    if time<since:continue
                    session=re.search(r'\bsessionId=([A-Za-z0-9_-]+)',line)
                    rows.append({'timestamp':time,'pid':int(pid[1]),'model':model[1],'sessionId':session[1] if session else None,'sending':'Sending request:' in line})
        except (OSError,ValueError):continue
    return rows

def metadata_for(span,trace,rows,stamp):
    direct=span.get('model') or (trace.get('modelInfo') or {}).get('model')
    candidates=[r for r in rows if r['pid']==trace.get('workerPid') and abs(r['timestamp']-stamp)<=1]
    models={r['model'] for r in candidates if r['sending']}
    model=direct or (next(iter(models)) if len(models)==1 else None)
    sessions={r['sessionId'] for r in candidates if r['sessionId'] and r['model']==model}
    return {'model':model,'modelEvidence':'generation metadata' if direct else '发送日志：同一进程、调用时间差不超过 1 秒，候选模型唯一' if model else '缺少唯一可关联的模型证据',
            'sessionId':trace.get('sessionId') or (next(iter(sessions)) if len(sessions)==1 else 'unknown'),
            'sessionName':'已关联会话' if trace.get('sessionId') or len(sessions)==1 else '会话未识别'}

def record_integrity(body):
    units=len(body.encode('utf-16-le'))//2
    truncated=units==100003 and body.endswith('...')
    try:
        value=json.loads(body);valid=isinstance(value,list) or isinstance(value,dict) and isinstance(value.get('messages'),list)
    except ValueError:valid=False
    return {'truncated':truncated,'recordJSONValid':valid,'recordCharactersUTF16':units,
            'recordStatus':'truncated' if truncated else 'parseable' if valid else 'unparseable',
            'integrityEvidence':'WorkBuddy 源记录存在 100000 UTF-16 单元截断上限；JSON 解析校验仅针对已保存记录，非网络请求完整性证明'}

def collect_context(root,since=0,limit=100):
    requests=[];rows=request_metadata(root.parent/'logs',since)
    for path in sorted(root.glob('*/*.json'),key=lambda p:p.stat().st_mtime,reverse=True):
        if path.stat().st_mtime<since:continue
        try:doc=json.loads(path.read_text())
        except (ValueError,OSError):continue
        for span in doc.get('spans',[]):
            body=span.get('toolInput')
            if span.get('type')!='generation' or not isinstance(body,str) or not body:continue
            try:stamp=datetime.datetime.fromisoformat(span['startedAt'].replace('Z','+00:00')).timestamp()
            except (ValueError,KeyError):continue
            if stamp<since:continue
            trace=doc.get('trace',{});identity=hashlib.sha256((str(trace.get('traceId'))+str(span.get('spanId'))).encode()).hexdigest()
            requests.append({'id':identity,'body':body,'timestamp':stamp,**metadata_for(span,trace,rows,stamp),**record_integrity(body),
                             'source':'workbuddy_generation_context','complete':False,'bodySHA256':hashlib.sha256(body.encode()).hexdigest()})
            if len(requests)>=limit:return {'requests':requests}
    requests.sort(key=lambda r:r['timestamp'],reverse=True)
    return {'requests':requests}

if __name__=='__main__':
    import os,time,argparse
    parser=argparse.ArgumentParser();parser.add_argument('--since',type=float,default=time.time()-86400);args=parser.parse_args()
    storage=Path.home()/'Library/Application Support/AppLens/telemetry';storage.mkdir(parents=True,exist_ok=True,mode=0o700)
    payload=collect_context(Path.home()/'.workbuddy/traces',args.since)
    from workbuddy_network_context import collect_network
    network=collect_network([storage/'workbuddy-network.jsonl',Path.home()/'Library/Application Support/AgentReins/workbuddy-network.jsonl'],args.since)
    payload['requests']=sorted(network+payload['requests'],key=lambda r:r['timestamp'],reverse=True)[:100]
    output=storage/'model-context.json';fd=os.open(output,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
    with os.fdopen(fd,'w') as f:json.dump(payload,f,ensure_ascii=False)
    print(json.dumps({'requests':len(payload['requests']),'contextFile':str(output),'transcriptEvents':0}))
