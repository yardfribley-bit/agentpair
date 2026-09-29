"""Deploy sanitized read-only dashboard to this run's Navigator only."""
import getpass
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile
import urllib.request
from agentpair.web import project_run

ROOT=Path(__file__).resolve().parent
RUN=ROOT/'runs'/'20260929T063028Z-f384fc'
HOST='106.75.9.169'
KEY='/private/tmp/agentpair-run.vQ4wLt/id_ed25519'

def ssh(command,data=b''):
    return subprocess.run(['ssh','-i',KEY,'-o','IdentitiesOnly=yes','-o','BatchMode=yes',
        '-o','StrictHostKeyChecking=yes','-o','UserKnownHostsFile=/private/tmp/agentpair-run.vQ4wLt/known_hosts',
        '-o','ConnectTimeout=8','pair@'+HOST,command],input=data,check=True,
        stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=40).stdout

pub=getpass.getpass('UCloud API ID (hidden): ')
priv=getpass.getpass('UCloud API secret (hidden): ')
def api(action,extra):
    p={'Action':action,'PublicKey':pub,'ProjectId':'org-2qgt5t','Region':'cn-bj2',**extra}
    p['Signature']=hashlib.sha1((''.join(k+str(p[k]) for k in sorted(p))+priv).encode()).hexdigest()
    request=urllib.request.Request('https://api.ucloud.cn/',data=json.dumps(p).encode(),headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(request,timeout=20) as response: result=json.load(response)
    if result.get('RetCode')!=0:
        print('API_REJECTED',action,result.get('RetCode'),flush=True)
        raise RuntimeError('API rejected')
    return result

item=api('DescribeUHostInstance',{'UHostIds.0':'uhost-1vv7pkau0lpu'})['UHostSet'][0]
if item.get('Name')!='agentpair-navigator-20260929T063028Z-f384fc':
    raise RuntimeError('Wrong resource')
deployment_path=RUN/'web_deployment.json'
if deployment_path.exists():
    deployment=json.loads(deployment_path.read_text())
else:
    created=api('CreateFirewall',{'Name':'agentpair-web-20260929T063028Z-f384fc',
                'Rule.0':'TCP|22|0.0.0.0/0|ACCEPT|HIGH|SSH',
                'Rule.1':'TCP|8080|0.0.0.0/0|ACCEPT|HIGH|PublicReadonlyDashboard'})
    deployment={'firewallID':created['FWId'],'hostID':'uhost-1vv7pkau0lpu',
                'url':'http://106.75.9.169:8080/','status':'firewall_created'}
    deployment_path.write_text(json.dumps(deployment,indent=2))
api('GrantFirewall',{'FWId':deployment['firewallID'],'ResourceType':'uhost','ResourceId':deployment['hostID']})
deployment['status']='firewall_bound'
deployment_path.write_text(json.dumps(deployment,indent=2))
bundle=io.BytesIO()
with tarfile.open(fileobj=bundle,mode='w') as archive:
    for name in ('__init__.py','web.py','web_assets/index.html','web_assets/app.js','web_assets/style.css'):
        archive.add(ROOT/'agentpair'/name,arcname='agentpair/'+name)
    data=json.dumps(project_run(RUN),ensure_ascii=False).encode()
    info=tarfile.TarInfo('web_snapshot.json'); info.size=len(data); info.mode=0o600
    archive.addfile(info,io.BytesIO(data))
ssh('tar -xf - -C /home/pair/AgentPair',bundle.getvalue())
ssh('cd /home/pair/AgentPair; if ! curl -fsS http://127.0.0.1:8080/api/run >/dev/null; then nohup python3 -m agentpair.web --snapshot web_snapshot.json --bind 0.0.0.0 --port 8080 > web.log 2>&1 < /dev/null & fi')
print('WEB_DEPLOYED',flush=True)
