"""Publish only the Windows endpoint feature and verified installer artifact."""
import io
import json
import shlex
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from deploy_persistent_navigator import ssh

def main():
    installer = Path(sys.argv[1])
    if not installer.is_file() or installer.read_bytes()[:2] != b'MZ':
        raise ValueError('Verified Windows installer artifact required')
    code="import json,sqlite3; c=sqlite3.connect('/var/lib/agentpair/tasks.db'); print(json.dumps([json.loads(r[0])['id'] for r in c.execute('select data from tasks') if json.loads(r[0])['status'] in ('queued','running','cancelling')]))"
    if json.loads(ssh('python3 -c '+shlex.quote(code))):
        raise RuntimeError('Active tasks; defer deployment')
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    backup='/opt/agentpair-backups/devices-'+stamp+'.tar.gz'
    ssh('mkdir -p /opt/agentpair-backups && tar -czf '+backup+' -C /opt/agentpair agentpair')
    files=['agentpair/accounts.py','agentpair/devices.py','agentpair/platform.py','agentpair/tasks.py','agentpair/web_assets/workspace.js',
           'agentpair/web_assets/devices.html','agentpair/web_assets/devices.css','agentpair/web_assets/devices.js',
           'agentpair/web_assets/agentpair-windows.ps1']
    bundle=io.BytesIO()
    with tarfile.open(fileobj=bundle,mode='w') as archive:
        for name in files:archive.add(ROOT/name,arcname=name)
        archive.add(installer,arcname='agentpair/web_assets/AgentPair-Windows-Setup-0.1.0.exe')
    ssh('tar -xf - -C /opt/agentpair',bundle.getvalue())
    try:
        ssh("cd /opt/agentpair && python3 -c 'import agentpair.platform, agentpair.devices' && systemctl restart agentpair-navigator.service")
        ssh("curl --retry 5 --retry-connrefused --retry-delay 1 -fsS http://127.0.0.1:9090/devices >/dev/null")
    except Exception:
        ssh('tar -xzf '+backup+' -C /opt/agentpair && systemctl restart agentpair-navigator.service')
        raise
    print('Device feature deployed. Backup: '+backup)

if __name__=='__main__':main()
