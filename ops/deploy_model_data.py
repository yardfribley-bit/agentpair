"""Deploy prototype-aligned model evidence view, with active-task guard and rollback."""
import io,json,shlex,sys,tarfile
from pathlib import Path
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from deploy_persistent_navigator import ssh

def main():
    check="import json,sqlite3;db=sqlite3.connect('/var/lib/agentpair/tasks.db');print(json.dumps([json.loads(r[0])['id'] for r in db.execute('select data from tasks') if json.loads(r[0])['status'] in ('queued','running','cancelling')]))"
    if json.loads(ssh('python3 -c '+shlex.quote(check))):raise RuntimeError('Active tasks; deployment deferred')
    files=['agentpair/devices.py','agentpair/platform.py','agentpair/llm_telemetry.py','agentpair/llm_evidence.py','agentpair/model_context.py','agentpair/applens_protocol.py',
           'agentpair/web_assets/devices.js','agentpair/web_assets/devices.html','agentpair/web_assets/workspace.html','agentpair/web_assets/model_data.html',
           'agentpair/web_assets/model_data.css','agentpair/web_assets/model_data.js','agentpair/web_assets/model_evidence_ui.js']
    backup='/opt/agentpair-backups/model-data-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'.tar.gz'
    ssh('mkdir -p /opt/agentpair-backups && tar -czf '+backup+' -C /opt/agentpair agentpair')
    stream=io.BytesIO()
    with tarfile.open(fileobj=stream,mode='w') as bundle:
        for name in files:bundle.add(ROOT/name,arcname=name)
    ssh('tar -xf - -C /opt/agentpair',stream.getvalue())
    try:
        ssh("cd /opt/agentpair && python3 -c 'import agentpair.platform,agentpair.devices,agentpair.llm_telemetry' && systemctl restart agentpair-navigator.service")
        ssh('curl --retry 5 --retry-connrefused --retry-delay 1 -fsS http://127.0.0.1:9090/model-data >/dev/null')
        result=ssh("curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:9090/api/devices/model-data/unknown").decode()
        if result!='401':raise RuntimeError('Unauthenticated data route not rejected')
    except Exception:
        ssh('tar -xzf '+backup+' -C /opt/agentpair && systemctl restart agentpair-navigator.service');raise
    print('Published /model-data; anonymous API denied. Backup: '+backup)
if __name__=='__main__':main()
