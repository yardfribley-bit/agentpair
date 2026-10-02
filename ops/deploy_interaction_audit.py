"""Publish the scoped interaction audit with baseline check and file rollback."""
import hashlib,io,json,shlex,sys,tarfile
from pathlib import Path
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import deploy_persistent_navigator as remote
remote.KNOWN='/private/tmp/vmess-50.118.187.180-known_hosts'
FILES=['agentpair/web_assets/analysis_view.js','agentpair/credential_threats.py','agentpair/security_investigation.py','agentpair/tasks.py','agentpair/pair_worker.py','agentpair/accounts.py','agentpair/web_assets/workspace.html','agentpair/web_assets/devices.html','agentpair/web_assets/devices.js','agentpair/web_assets/AppLens-macOS.zip','agentpair/platform.py','agentpair/devices.py','agentpair/model_security.py','agentpair/interaction_audit.py',
       'agentpair/web_assets/model_security.html','agentpair/web_assets/model_security.css','agentpair/web_assets/model_security.js',
       'agentpair/web_assets/model_data.html','agentpair/web_assets/model_data.js']
BASELINE=Path('/private/tmp/applens-audit-baseline')

def main():
    ssh=remote.ssh
    active="import json,sqlite3;db=sqlite3.connect('/var/lib/agentpair/tasks.db');print(sum(json.loads(r[0])['status'] in ('queued','running','cancelling') for r in db.execute('select data from tasks')))"
    if int(ssh('python3 -c '+shlex.quote(active))):raise RuntimeError('Active tasks; do not restart platform')
    check="import hashlib,json,pathlib;files="+repr(FILES)+";print(json.dumps({f:hashlib.sha256((pathlib.Path('/opt/agentpair')/f).read_bytes()).hexdigest() for f in files if (pathlib.Path('/opt/agentpair')/f).exists()}))"
    hashes=json.loads(ssh('python3 -c '+shlex.quote(check)))
    for f in FILES:
        if (BASELINE/f).exists() and hashes.get(f)!=hashlib.sha256((BASELINE/f).read_bytes()).hexdigest():raise RuntimeError('Production source changed: '+f)
        if not (BASELINE/f).exists() and f in hashes:raise RuntimeError('Unexpected existing audit file: '+f)
    backup='/opt/agentpair-backups/interaction-audit-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'.tar.gz'
    # SQLite backup is consistent even if endpoint ingestion is still active.
    ssh('install -d -m 0700 /opt/agentpair-backups')
    db_backup=backup.removesuffix('.tar.gz')+'-devices.db'
    db_code="import sqlite3,os;os.umask(0o077);src=sqlite3.connect('/var/lib/agentpair/devices.db');dst=sqlite3.connect("+repr(db_backup)+");src.backup(dst);dst.close();src.close()"
    ssh('python3 -c '+shlex.quote(db_code))
    existing=[f for f in FILES if f in hashes];added=[f for f in FILES if f not in hashes]
    ssh('mkdir -p /opt/agentpair-backups && tar -czf '+shlex.quote(backup)+' -C /opt/agentpair '+' '.join(map(shlex.quote,existing)))
    bundle=io.BytesIO()
    with tarfile.open(fileobj=bundle,mode='w') as archive:
        for f in FILES:archive.add(ROOT/f,arcname=f)
    try:
        ssh('tar -xf - -C /opt/agentpair',bundle.getvalue())
        ssh("cd /opt/agentpair && python3 -c 'import agentpair.platform,agentpair.interaction_audit' && systemctl restart agentpair-navigator.service")
        ssh('curl --retry 5 --retry-connrefused --retry-delay 1 -fsS http://127.0.0.1:9090/model-security >/dev/null')
        ssh('curl -fsS http://127.0.0.1:9090/api/audit/devices >/dev/null')
        for path in ['/api/devices/interaction-audit/unknown','/api/devices/model-data/unknown']:
            result=ssh("curl -s -o /dev/null -w '%{http_code}' "+shlex.quote('http://127.0.0.1:9090'+path)).decode()
            if result!='401':raise RuntimeError('Unauthenticated route not denied')
    except Exception:
        ssh('tar -xzf '+shlex.quote(backup)+' -C /opt/agentpair && rm -f '+' '.join(shlex.quote('/opt/agentpair/'+f) for f in added)+' && systemctl restart agentpair-navigator.service')
        raise
    print(json.dumps({'published':'https://50.118.187.180/model-security','backup':backup,'deviceDatabaseBackup':db_backup,'privateAPI':'401 verified'},ensure_ascii=False))

if __name__=='__main__':main()
