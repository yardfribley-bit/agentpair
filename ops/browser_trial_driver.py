"""SSH only to the exact browser trial lease, via the Navigator."""
import json
from pathlib import Path
import subprocess
import sys
from agentpair.ucloud import UCloudClient

c=json.loads(Path('/etc/agentpair/private.json').read_text())['cloud']
lease=json.loads((Path(c['leaseDirectory'])/'78be192086074d0476f0c46f.json').read_text())
api=UCloudClient(c['publicKey'],c['privateKey'],c['projectId'],c['region'])
hosts=api.call('DescribeUHostInstance',**{'UHostIds.0':lease['hostId']}).get('UHostSet',[])
if len(hosts)!=1 or hosts[0]['Name']!=lease['name']:raise RuntimeError('Trial host identity mismatch')
host=hosts[0]
ips=[x['IP'] for x in host.get('IPSet',[]) if x.get('Type') not in ('Private','private')]
import ipaddress
ips=[ip for ip in ips if ipaddress.ip_address(ip).is_global]
if not ips:raise RuntimeError('Public IP not ready')
print('TRIAL_HOST',lease['hostId'],ips[0],flush=True)
result=subprocess.run(['ssh','-i','/var/lib/agentpair/driver_key','-o','IdentitiesOnly=yes',
    '-o','BatchMode=yes','-o','StrictHostKeyChecking=accept-new',
    '-o','UserKnownHostsFile=/var/lib/agentpair/known_hosts','-o','ConnectTimeout=8',
    'pair@'+ips[0],sys.argv[1]],timeout=65)
sys.exit(result.returncode)
