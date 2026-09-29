"""One experiment's explicitly extended lease; exact resource cleanup only."""
import datetime
import getpass
import hashlib
import json
from pathlib import Path
import time
import urllib.request

ROOT=Path(__file__).resolve().parent
RUN=ROOT/'runs'/'20260929T063028Z-f384fc'
NODES={'uhost-1vv7pkau0lpu':'agentpair-navigator-20260929T063028Z-f384fc',
       'uhost-1vv7pnod2lry':'agentpair-driver-20260929T063028Z-f384fc'}
EXPIRY=datetime.datetime.fromisoformat('2026-09-29T08:30:28+00:00').timestamp()
pub=getpass.getpass('UCloud API ID (hidden): ')
priv=getpass.getpass('UCloud API secret (hidden): ')

def api(action,extra=None):
    p={'Action':action,'PublicKey':pub,'ProjectId':'org-2qgt5t','Region':'cn-bj2',**(extra or {})}
    p['Signature']=hashlib.sha1((''.join(k+(str(p[k]).lower() if isinstance(p[k],bool) else str(p[k])) for k in sorted(p))+priv).encode()).hexdigest()
    with urllib.request.urlopen(urllib.request.Request('https://api.ucloud.cn/',data=json.dumps(p).encode(),headers={'Content-Type':'application/json'}),timeout=25) as response:return json.load(response)

def state(hid):
    data=api('DescribeUHostInstance',{'UHostIds.0':hid})
    if data.get('RetCode')!=0:raise RuntimeError('Resource lookup rejected')
    hosts=data.get('UHostSet',[])
    if hosts and hosts[0].get('Name')!=NODES[hid]:raise RuntimeError('Resource identity mismatch')
    return hosts

for hid in NODES: state(hid)
print('LEASE_CONFIRMED_UNTIL 2026-09-29T08:30:28Z',flush=True)
while time.time()<EXPIRY:time.sleep(min(30,EXPIRY-time.time()))
result={'expiresUTC':'2026-09-29T08:30:28Z','nodes':[]}
for hid in NODES:
    try:
        hosts=state(hid)
        if hosts and hosts[0].get('State')=='Running':api('StopUHostInstance',{'UHostId':hid,'Zone':'cn-bj2-04'})
        for _ in range(20):
            if not state(hid):break
            deleted=api('TerminateUHostInstance',{'UHostId':hid,'Zone':'cn-bj2-04','ReleaseEIP':True,'ReleaseUDisk':True})
            if deleted.get('RetCode')==299:raise RuntimeError('Delete permission denied')
            time.sleep(5)
        result['nodes'].append({'id':hid,'deleted':not state(hid)})
    except Exception as error:result['nodes'].append({'id':hid,'errorType':type(error).__name__})
try:
    fw=api('DescribeFirewall',{'FWId':'firewall-nfhhhmlu'})
    if fw.get('RetCode')==0 and fw.get('DataSet') and fw['DataSet'][0].get('ResourceCount')==0:
        result['firewallDeleteRetCode']=api('DeleteFirewall',{'FWId':'firewall-nfhhhmlu'}).get('RetCode')
except Exception as error:result['firewallErrorType']=type(error).__name__
(RUN/'extended_cleanup.json').write_text(json.dumps(result,indent=2))
print('CLEANUP_RESULT',json.dumps(result),flush=True)
