"""AgentReins-derived complete request capture, restricted to model request bodies."""
import base64,datetime,hashlib,json,os
from pathlib import Path
HOST='copilot.tencent.com'
PATHS={'/v1/chat/completions','/v2/chat/completions','/v3/chat/completions'}
OUTPUT=Path(os.environ['APPLENS_WORKBUDDY_NETWORK_JSONL'])
def request(flow):
    gate=os.environ.get('APPLENS_CAPTURE_ENABLED_FILE')
    if gate and not Path(gate).exists():return
    path=flow.request.path.split('?',1)[0]
    if flow.request.pretty_host.lower()!=HOST or path not in PATHS or flow.request.method!='POST':return
    wire=flow.request.raw_content or b'';body=flow.request.get_content(strict=False) or b''
    declared=flow.request.headers.get('content-length')
    record={'flowID':flow.id,'captureStage':'request','observedAt':datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'host':HOST,'path':path,'declaredContentLength':int(declared) if declared and declared.isdigit() else None,
            'capturedWireBodyBytes':len(wire),'requestBodyBase64':base64.b64encode(body).decode(),
            'requestSHA256':hashlib.sha256(body).hexdigest()}
    OUTPUT.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    fd=os.open(OUTPUT,os.O_WRONLY|os.O_CREAT|os.O_APPEND,0o600)
    try:
        data=(json.dumps(record,ensure_ascii=False)+'\n').encode()
        while data:
            written=os.write(fd,data);data=data[written:]
    finally:os.close(fd)
