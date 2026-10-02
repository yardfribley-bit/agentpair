"""Pair installed Windows AppLens and verify its inventory on Navigator."""
import shlex
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from deploy_persistent_navigator import ssh
code=r'''
import json,base64,subprocess
from agentpair.cloud_client import CloudClient
p=json.load(open('/etc/agentpair/private.json'))
c=CloudClient('https://50.118.187.180');c.login(p.get('username','admin'),p['password'])
pair=c.request('/api/devices/pairing',{})['code']
s="[Console]::OutputEncoding=New-Object Text.UTF8Encoding($false); & (Join-Path $env:LOCALAPPDATA 'Programs/AgentPair Windows/agentpair-windows.ps1') -Server https://50.118.187.180/ -PairCode "+pair+" -Once"
args=['ssh','-i','/var/lib/agentpair/driver_key','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','UserKnownHostsFile=/var/lib/agentpair/windows_known_hosts','Administrator@106.75.5.214','powershell -NoProfile -ExecutionPolicy Bypass -EncodedCommand '+base64.b64encode(s.encode('utf-16le')).decode()]
r=subprocess.run(args,capture_output=True,timeout=65)
print('Collector exit:',r.returncode)
print((r.stdout+r.stderr).decode('utf-8',errors='replace').replace(pair,'[pairing]')[-2500:])
print(json.dumps([{k:d.get(k) for k in ('id','name','online','lastSeen')} for d in c.request('/api/devices')['items']],ensure_ascii=False))
'''
print(ssh('cd /opt/agentpair; python3 -c '+shlex.quote(code)+' 2>&1; true').decode())
