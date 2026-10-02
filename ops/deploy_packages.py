"""Install cache on the existing pinned Driver, then publish Navigator UI."""
import io
import json
from pathlib import Path
import shlex
import sys
import tarfile
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from deploy_persistent_navigator import ssh

DRIVER='ssh -i /var/lib/agentpair/driver_key -o BatchMode=yes -o ConnectTimeout=8 -o StrictHostKeyChecking=yes -o UserKnownHostsFile=/var/lib/agentpair/known_hosts pair@165.154.254.180 '

def archive(files):
    stream=io.BytesIO()
    with tarfile.open(fileobj=stream,mode='w') as bundle:
        for name in files:bundle.add(ROOT/name,arcname=name)
    return stream.getvalue()


def main():
    check="import json,sqlite3;db=sqlite3.connect('/var/lib/agentpair/tasks.db');print(json.dumps([json.loads(r[0])['id'] for r in db.execute('select data from tasks') if json.loads(r[0])['status'] in ('queued','running','cancelling')]))"
    if json.loads(ssh('python3 -c '+shlex.quote(check))):raise RuntimeError('Active tasks; deployment deferred')
    print(ssh(DRIVER+shlex.quote('python3 --version')).decode().strip())
    ssh(DRIVER+shlex.quote('mkdir -p /home/pair/package-node && tar -xf - -C /home/pair/package-node'),archive(['agentpair/__init__.py','agentpair/package_cache.py']))
    driver_setup=r"""
from pathlib import Path
import secrets,subprocess
env=Path('/home/pair/package-cache.env')
if not env.exists():
    env.write_text('AGENTPAIR_PACKAGE_TOKEN='+secrets.token_hex(32)+'\n');env.chmod(0o600)
Path('/home/pair/package-cache').mkdir(exist_ok=True)
"""
    ssh(DRIVER+shlex.quote('python3 -c '+shlex.quote(driver_setup)))
    # Forwarding is transport protection; cache control/data use their HTTP API.
    nav_setup=r"""
from pathlib import Path
import json,subprocess
command=['ssh','-i','/var/lib/agentpair/driver_key','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','UserKnownHostsFile=/var/lib/agentpair/known_hosts','pair@165.154.254.180','cat /home/pair/package-cache.env']
raw=subprocess.check_output(command,text=True)
token=raw.strip().split('=',1)[1]
unit='''[Unit]
Description=AgentPair package Driver transport
After=network-online.target
[Service]
User=agentpair
ExecStart=/usr/bin/ssh -N -i /var/lib/agentpair/driver_key -o BatchMode=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=/var/lib/agentpair/known_hosts -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -L 127.0.0.1:18092:127.0.0.1:8092 pair@165.154.254.180
Restart=always
RestartSec=5
[Install]
WantedBy=multi-user.target
'''
Path('/etc/systemd/system/agentpair-package-transport.service').write_text(unit)
p=Path('/var/lib/agentpair/package-node.private.json')
existing=json.loads(p.read_text()) if p.exists() else {}
p.write_text(json.dumps({'url':'http://127.0.0.1:18092','publicUrl':'https://165.154.254.180/packages','deliveryMode':'direct',
    'downloadReady':existing.get('deliveryMode')=='direct' and existing.get('downloadReady') is True,
    'name':'常驻 Linux Driver','token':token}));p.chmod(0o600)
subprocess.run(['chown','agentpair:agentpair',str(p)],check=True)
subprocess.run(['systemctl','daemon-reload'],check=True)
subprocess.run(['systemctl','enable','--now','agentpair-package-transport.service'],check=True,stdout=subprocess.DEVNULL)
subprocess.run(['systemctl','restart','agentpair-package-transport.service'],check=True)
"""
    ssh('python3 -c '+shlex.quote(nav_setup))
    files=['agentpair/platform.py','agentpair/software_install.py','agentpair/package_cache.py','agentpair/cloud_console.py','agentpair/windows_access.py',
           'agentpair/web_assets/packages.html','agentpair/web_assets/packages.js','agentpair/web_assets/packages.css','agentpair/web_assets/workspace.js',
           'agentpair/web_assets/software.html','agentpair/web_assets/software.js',
           'agentpair/web_assets/cloud_machines.html','agentpair/web_assets/cloud_machines.js','agentpair/web_assets/cloud_machines.css','windows/software-install.ps1']
    backup='/opt/agentpair-backups/packages-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'.tar.gz'
    ssh('mkdir -p /opt/agentpair-backups && tar -czf '+backup+' -C /opt/agentpair agentpair')
    ssh('tar -xf - -C /opt/agentpair',archive(files))
    try:
        ssh("cd /opt/agentpair && python3 -c 'import agentpair.platform,agentpair.package_cache,agentpair.cloud_console' && systemctl restart agentpair-navigator.service")
        data=ssh('curl --retry 5 --retry-connrefused --retry-delay 1 -fsS http://127.0.0.1:9090/api/packages')
        result=json.loads(data)
        if result['node']['state']!='online':raise RuntimeError('Driver cache not reachable')
        for route in ('software','software.js','packages','packages.js','packages.css'):
            ssh('curl -fsS http://127.0.0.1:9090/'+route+' >/dev/null')
        print('Published /packages; verified cache node online; catalog items:',len(result['items']))
    except Exception:
        ssh('tar -xzf '+backup+' -C /opt/agentpair && systemctl restart agentpair-navigator.service');raise


if __name__=='__main__':main()
