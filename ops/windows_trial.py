"""One Windows acceptance lease. Run on Navigator; secrets never leave it."""
import argparse
import base64
import datetime
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys

sys.path.insert(0, '/opt/agentpair')
from agentpair.devices import DeviceStore
from agentpair.resources import ResourceManager
from agentpair.ucloud import UCloudClient
from agentpair.windows_access import bootstrap_script


def main():
    p=argparse.ArgumentParser();p.add_argument('--cpu',type=int,default=1);p.add_argument('--memory',type=int,default=1024)
    p.add_argument('--max-hourly',type=float,default=.50)
    p.add_argument('--create',action='store_true');args=p.parse_args()
    os.umask(0o077)
    c=json.loads(Path('/etc/agentpair/private.json').read_text())['cloud']
    api=UCloudClient(c['publicKey'],c['privateKey'],c['projectId'],c['region'])
    manager=ResourceManager(api,c['leaseDirectory'],max_hourly_cny=args.max_hourly+1e-9)
    config={'Zone':'cn-bj2-03','ImageId':'uimage-1is1syh4tgxx','MachineType':'O','CPU':args.cpu,'Memory':args.memory,
            'ChargeType':'Dynamic','Disks.0.Size':40,'Disks.0.IsBoot':True,'Disks.0.Type':'CLOUD_RSSD',
            'Disks.1.Size':20,'Disks.1.IsBoot':False,'Disks.1.Type':'CLOUD_RSSD'}
    eip={'Bandwidth':1,'ChargeType':'Dynamic','PayMode':'Bandwidth','OperatorName':'Bgp'}
    quote=manager.quote(config,eip)
    if not args.create:print(json.dumps(quote));return
    if subprocess.run(['systemctl','is-active','--quiet','agentpair-reaper.timer']).returncode:
        raise RuntimeError('Expiry reaper is not active')
    active=[r for r in manager.records() if r['name'].startswith('agentpair-driver-windows-') and r['state'] not in ('released','failed')]
    if active:raise RuntimeError('A Windows lease already exists; reconcile first')
    installer=Path('/opt/agentpair/agentpair/web_assets/AgentPair-Windows-Setup-0.1.0.exe')
    sha=hashlib.sha256(installer.read_bytes()).hexdigest()
    pairing=DeviceStore('/var/lib/agentpair/devices.db').pairing()['code']
    started=datetime.datetime.now(datetime.timezone.utc)
    lease={'id':secrets.token_hex(12),'name':'agentpair-driver-windows-'+secrets.token_hex(4),
           'state':'creating','createdAt':started.isoformat(),
           'expiresAt':(started+datetime.timedelta(seconds=3480)).isoformat(),
           'price':quote,'hostId':None,'region':c['region'],'projectId':c['projectId'],'zone':config['Zone'],
           'purpose':'Windows installer/inventory acceptance','platform':'windows','installerSHA256':sha,'config':config}
    # Durable expiry record BEFORE the billable API call, including ambiguous-result recovery.
    manager._save(lease)
    firewall=api.call('CreateFirewall',Name='agentpair-windows-'+lease['id'][:8],
        **{'Rule.0':'TCP|3389|50.118.187.180/32|ACCEPT|HIGH|NavigatorRDP',
           'Rule.1':'TCP|22|50.118.187.180/32|ACCEPT|HIGH|NavigatorSSH'})['FWId']
    lease['dedicatedFirewallId']=firewall;manager._save(lease)
    password='W9!'+secrets.token_hex(10)
    secretfile=Path('/var/lib/agentpair')/('windows-trial-'+lease['id']+'.private.json')
    secretfile.write_text(json.dumps({'username':'Administrator','password':password}))
    userdata=r'''#ps1_sysnative
$ErrorActionPreference='Stop'
[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12
New-Item -Type Directory -Force C:\AgentPairTrial | Out-Null
Invoke-WebRequest -UseBasicParsing -Uri 'https://50.118.187.180/downloads/AgentPair-Windows-Setup-0.1.0.exe' -OutFile C:\AgentPairTrial\setup.exe
if ((Get-FileHash C:\AgentPairTrial\setup.exe -Algorithm SHA256).Hash -ne '__SHA__') {throw 'Installer hash mismatch'}
$install=Start-Process C:\AgentPairTrial\setup.exe -ArgumentList '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /DIR="C:\AgentPairTrial\App"' -Wait -PassThru
if($install.ExitCode -ne 0) {throw 'Installer failed'}
$test=Start-Process C:\AgentPairTrial\App\AgentPairWindows.exe -ArgumentList '--self-test' -Wait -PassThru
if($test.ExitCode -ne 0) {throw 'Installed app self-test failed'}
$test=Start-Process C:\AgentPairTrial\App\AgentPairWindows.exe -ArgumentList '--once https://50.118.187.180/ __PAIR__' -Wait -PassThru
if($test.ExitCode -ne 0) {throw 'Installed app inventory test failed'}
'INSTALL_AND_INVENTORY_OK' | Set-Content C:\AgentPairTrial\acceptance.txt
Start-Process powershell.exe -ArgumentList '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File C:\AgentPairTrial\App\agentpair-windows.ps1 -Server https://50.118.187.180/' -WindowStyle Hidden
'''.replace('__SHA__',sha).replace('__PAIR__',pairing)
    # Connectivity must be established before downloading or enrolling apps.
    userdata = '#ps1_sysnative\n' + bootstrap_script(c['sshPublicKey']) + userdata.split('\n', 1)[1]
    request={**config,'Name':lease['name'],'MinCount':1,'MaxCount':1,'SecurityGroupId':firewall,
             'LoginMode':'Password','Password':base64.b64encode(password.encode()).decode(),
             'UserData':base64.b64encode(userdata.encode()).decode(),
             'NetworkInterface.0.EIP.Bandwidth':1,'NetworkInterface.0.EIP.PayMode':'Bandwidth','NetworkInterface.0.EIP.OperatorName':'Bgp'}
    # Preserve diagnostic codes without credential payloads; no blind retry on create timeouts.
    original=api.transport
    def diagnostic(payload):
        response=original(payload)
        if response.get('RetCode')!=0:
            message=str(response.get('Message',''))
            for secret in (password,request['Password'],pairing,c['publicKey'],c['privateKey']):message=message.replace(secret,'[redacted]')
            print(json.dumps({'action':payload['Action'],'code':response.get('RetCode'),'message':message[:400]}),flush=True)
        return response
    api.transport=diagnostic
    try:
        result=api.call('CreateUHostInstance',**request)
        if len(result.get('UHostIds',[]))!=1:raise RuntimeError('Unexpected host count; reconcile lease by exact name')
        lease['hostId']=result['UHostIds'][0];lease['state']='active';manager._save(lease)
        print(json.dumps(lease))
    except Exception:
        lease['state']='reconcile_required';manager._save(lease)
        raise

if __name__=='__main__':main()
