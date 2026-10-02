"""Reuse AgentReins same-flow body evidence. Never ingest headers/replies/tools."""
import base64,datetime,hashlib,json
from pathlib import Path
PATHS={'/v1/chat/completions','/v2/chat/completions','/v3/chat/completions'}

def project(record,since):
    if record.get('host','').lower()!='copilot.tencent.com' or record.get('path') not in PATHS:return None
    stamp=datetime.datetime.fromisoformat(record['observedAt'].replace('Z','+00:00')).timestamp()
    if stamp<since:return None
    raw=base64.b64decode(record['requestBodyBase64'],validate=True)
    digest=hashlib.sha256(raw).hexdigest()
    if digest!=record.get('requestSHA256') or len(raw)>1000000:return None
    body=raw.decode('utf-8');parsed=json.loads(body)
    if not isinstance(parsed,dict) or not isinstance(parsed.get('messages'),list):return None
    declared=record.get('declaredContentLength');wire=record.get('capturedWireBodyBytes')
    matched=isinstance(declared,int) and not isinstance(declared,bool) and declared>=0 and declared==wire
    flow=str(record.get('flowID') or digest)
    return {'id':hashlib.sha256(('network:'+flow).encode()).hexdigest(),'body':body,'bodySHA256':digest,
            'source':'workbuddy_network_context','timestamp':stamp,'model':parsed.get('model'),
            'modelEvidence':'同一 HTTP 请求体 model 字段','sessionId':'network:'+flow,'sessionName':'HTTP 请求 '+flow[:8],
            'recordStatus':'wire_length_matched' if matched else 'wire_length_unknown','recordJSONValid':True,
            'truncated':False,'complete':matched,'wireLengthMatched':matched,'destination':'copilot.tencent.com'+record['path'],
            'integrityEvidence':'沿用 AgentReins：解码请求体 SHA256 校验通过；声明长度与捕获传输长度'+('一致' if matched else '尚未验证')+'；不以此断言模型服务处理成功'}

def collect_network(paths,since):
    records={}
    for path in paths:
        if not path.exists() or path.stat().st_mtime<since:continue
        try:
            with path.open() as stream:
                for line in stream:
                    try:r=project(json.loads(line),since)
                    except (ValueError,KeyError,UnicodeError):continue
                    if r:records[r['id']]=r
        except OSError:continue
    return sorted(records.values(),key=lambda r:r['timestamp'],reverse=True)[:100]
