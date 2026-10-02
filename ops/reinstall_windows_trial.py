"""Reinstall only the explicitly approved disposable Windows test VM."""
import ast
import base64
import datetime
import hashlib
import json
from pathlib import Path
import sys
sys.path.insert(0, '/opt/agentpair')
from agentpair.ucloud import UCloudClient
from agentpair.devices import DeviceStore

c=json.loads(Path('/etc/agentpair/private.json').read_text())['cloud']
api=UCloudClient(c['publicKey'],c['privateKey'],c['projectId'],c['region'])
p=Path('/var/lib/agentpair/leases/958a2ccba140702ce9f12b91.json')
lease=json.loads(p.read_text())
assert lease['hostId']=='uhost-1vx0bfyivbum'
assert datetime.datetime.fromisoformat(lease['expiresAt'])-datetime.datetime.now(datetime.timezone.utc)>datetime.timedelta(minutes=5)
hosts=api.call('DescribeUHostInstance',**{'UHostIds.0':lease['hostId']}).get('UHostSet',[])
assert len(hosts)==1 and hosts[0]['Name']=='agentpair-driver-windows-41ae7a94'
h=hosts[0]
if sys.argv[-1]=='stop':
    if h['State']=='Running':api.call('StopUHostInstance',UHostId=lease['hostId'],Zone=c['zone'])
    print(json.dumps({'previousState':h['State'],'operation':'stop'}));sys.exit()
if h['State']!='Stopped':
    print(json.dumps({'state':h['State'],'reinstall':'not_requested'}));sys.exit()
if lease.get('reinstallRequestedAt'):raise RuntimeError('Reinstall already requested; inspect status, do not repeat')
images=api.call('DescribeImage',ImageType='Base',OsType='Windows',Limit=100).get('ImageSet',[])
image=next(i for i in images if i['ImageId']=='uimage-1is1syh4tgxx' and i['State']=='Available')
assert image['ImageType']=='Base' and image['OsType']=='Windows'
password=json.loads(Path('/var/lib/agentpair/windows-trial-'+lease['id']+'.private.json').read_text())['password']
pairing=DeviceStore('/var/lib/agentpair/devices.db').pairing()['code']
sha=hashlib.sha256(Path('/opt/agentpair/agentpair/web_assets/AgentPair-Windows-Setup-0.1.0.exe').read_bytes()).hexdigest()
tree=ast.parse(Path('/opt/agentpair/ops/windows_trial.py').read_text())
template=next(n.value for n in ast.walk(tree) if isinstance(n,ast.Constant) and isinstance(n.value,str) and n.value.startswith('#ps1_sysnative'))
userdata=template.replace('__SHA__',sha).replace('__PAIR__',pairing)
lease['reinstallRequestedAt']=datetime.datetime.now(datetime.timezone.utc).isoformat()
lease['reinstallImageId']=image['ImageId'];p.write_text(json.dumps(lease,indent=2))
result=api.call('ReinstallUHostInstance',UHostId=lease['hostId'],Zone=c['zone'],ImageId=image['ImageId'],
                LoginMode='Password',Password=base64.b64encode(password.encode()).decode(),ReserveDisk='Yes',
                UserData=base64.b64encode(userdata.encode()).decode())
lease['reinstallAccepted']=True;p.write_text(json.dumps(lease,indent=2))
print(json.dumps({'result':result['RetCode'],'imageName':image['ImageName'],'expiresAt':lease['expiresAt']}))
