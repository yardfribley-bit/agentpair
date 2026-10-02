"""Publish the approved operations console and its observation dependencies."""
import io
import json
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from deploy_persistent_navigator import ssh

FILES=['agentpair/collaboration.py','agentpair/tasks.py','agentpair/transport.py',
       'agentpair/pair_worker.py','agentpair/driver_runtime.py','agentpair/platform.py',
       'agentpair/credentials.py','agentpair/assistant_loop.py','agentpair/tool_registry.py',
       'agentpair/browser.py','agentpair/browser_setup.py','agentpair/browserkit_client.py',
       'agentpair/pinchtab_browser.py','agentpair/decisions.py',
       'agentpair/web_assets/workspace.js','agentpair/web_assets/workbench.js',
       'agentpair/web_assets/workbench.css','agentpair/web_assets/credentials.js',
       'agentpair/web_assets/team_console.js','agentpair/web_assets/team_console.css']

def main():
    check="import json,sqlite3; c=sqlite3.connect('/var/lib/agentpair/tasks.db'); print(json.dumps([json.loads(r[0])['id'] for r in c.execute('select data from tasks') if json.loads(r[0])['status'] in ('queued','running','cancelling')]))"
    import shlex
    if json.loads(ssh('python3 -c '+shlex.quote(check))):raise RuntimeError('Active tasks; deployment deferred')
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    backup='/opt/agentpair-backups/team-console-'+stamp+'.tar.gz'
    ssh('mkdir -p /opt/agentpair-backups && tar -czf '+backup+' -C /opt/agentpair agentpair')
    bundle=io.BytesIO()
    with tarfile.open(fileobj=bundle,mode='w') as archive:
        for name in FILES:archive.add(ROOT/name,arcname=name)
    ssh('tar -xf - -C /opt/agentpair',bundle.getvalue())
    try:
        ssh("cd /opt/agentpair && python3 -c 'import agentpair.platform,agentpair.transport,agentpair.driver_runtime' && systemctl restart agentpair-navigator.service")
        ssh("systemctl is-active agentpair-navigator.service && curl --retry 5 --retry-connrefused --retry-delay 1 -fsS http://127.0.0.1:9090/team_console.js >/dev/null")
    except Exception:
        ssh('tar -xzf '+backup+' -C /opt/agentpair && systemctl restart agentpair-navigator.service')
        raise
    print('DEPLOYED. Backup: '+backup)

if __name__=='__main__':main()
