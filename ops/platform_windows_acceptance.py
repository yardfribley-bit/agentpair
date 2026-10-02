"""Run the one-hour Windows trial through Navigator's own administrator API."""
import shlex
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from deploy_persistent_navigator import ssh

action=sys.argv[1]
if action not in ('create','status','install'):raise ValueError('Unknown action')
software=sys.argv[2] if len(sys.argv)>2 else ''
if action=='install' and software not in ('workbuddy-windows','applens-windows'):raise ValueError('Unknown package')
code=r'''
import json,uuid
from pathlib import Path
from agentpair.cloud_client import CloudClient
p=json.loads(Path('/etc/agentpair/private.json').read_text())
c=CloudClient('https://50.118.187.180')
c.login(p.get('username','admin'),p['password'])
record=Path('/var/lib/agentpair/windows-acceptance-20261002.json')
action=ACTION;software=SOFTWARE
if action=='create':
    q=c.quote('Windows')
    if q['hourlyCNY']>1.20:raise ValueError('Authorized price exceeded')
    r=json.loads(record.read_text()) if record.exists() else {'requestId':str(uuid.uuid4())}
    record.write_text(json.dumps(r));record.chmod(0o600)
    lease=c.create('Windows',q['hourlyCNY'],r['requestId'])
    r['lease']=lease;record.write_text(json.dumps(r))
    print(json.dumps({'quote':q,'lease':lease}))
elif action=='status':
    r=json.loads(record.read_text());print(json.dumps(c.check_login(r['lease']['id'])))
else:
    r=json.loads(record.read_text());op=c.install(r['lease']['id'],software)
    r.setdefault('operations',{})[software]=op['id'];record.write_text(json.dumps(r))
    print(json.dumps(op))
'''.replace('ACTION',repr(action)).replace('SOFTWARE',repr(software))
print(ssh('cd /opt/agentpair; python3 -c '+shlex.quote(code)+' 2>&1; true').decode())
