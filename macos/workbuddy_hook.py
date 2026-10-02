"""Non-blocking WorkBuddy hook recorder; local redacted evidence only."""
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
import uuid

EVENTS={'SessionStart','SessionEnd','UserPromptSubmit','PreToolUse','PostToolUse','PostToolUseFailure','Stop'}

def redact(value):
    if isinstance(value,dict):
        return {k:('[REDACTED]' if re.search(r'password|secret|authorization|api.?key|token|cookie',k,re.I) else redact(v)) for k,v in value.items()}
    if isinstance(value,list):return [redact(v) for v in value[:100]]
    if isinstance(value,str):
        value=re.sub(r'(?<!\d)1[3-9]\d{9}(?!\d)','[REDACTED:PHONE]',value)
        value=re.sub(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}','[REDACTED:EMAIL]',value)
        value=re.sub(r'(?i)Bearer\s+\S+','Bearer [REDACTED]',value)
        value=re.sub(r'\bsk-[A-Za-z0-9_-]{12,}','[REDACTED]',value)
        value=re.sub(r'(?i)((?:password|api_key|secret|token)\s*[=:]\s*)[^\s,;]+',r'\1[REDACTED]',value)
        return value[:16000]
    return value

def record(payload,storage,event=None):
    event=payload.get('hook_event_name') or event
    if event not in EVENTS:raise ValueError('Unsupported hook event')
    session=payload.get('session_id')
    if not isinstance(session,str) or not session:raise ValueError('Missing session identity')
    result={'schemaVersion':1,'source':'workbuddy_hook','event':event,'observedAt':time.time(),
            'sessionHash':hashlib.sha256(session.encode()).hexdigest(),
            'toolCallId':payload.get('tool_use_id') or payload.get('call_id'),
            'toolName':payload.get('tool_name'),'evidence':{}}
    for key in ('prompt','tool_input','tool_response','tool_result','error'):
        if key in payload:result['evidence'][key]=redact(payload[key])
    # Not claimed to be the complete encrypted network request or model context.
    result['coverage']='hook payload only; complete LLM request not observed'
    storage.mkdir(parents=True,exist_ok=True,mode=0o700)
    destination=storage/(str(time.time_ns())+'-'+uuid.uuid4().hex+'.json')
    fd=os.open(destination,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(fd,'w') as f:json.dump(result,f,ensure_ascii=False)
    return result

def main():
    try:
        raw=sys.stdin.buffer.read(1048577)
        if len(raw)>1048576:raise ValueError('Payload too large')
        payload=json.loads(raw.decode('utf-8-sig'))
        record(payload,Path.home()/'Library/Application Support/AppLens/telemetry/hooks',sys.argv[1] if len(sys.argv)>1 else None)
    except Exception:
        pass  # Observation must never prevent the user's work.
    print('{}')
if __name__=='__main__':main()
