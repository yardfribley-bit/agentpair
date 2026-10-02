"""Publish endpoint protocol and executable memory, preserving rollback files."""
import io
import shlex
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from deploy_persistent_navigator import ssh


def main():
    installer=Path(sys.argv[1])
    if not installer.is_file() or installer.read_bytes()[:2]!=b'MZ': raise ValueError('Installer required')
    check="import json,sqlite3;c=sqlite3.connect('/var/lib/agentpair/tasks.db');print(sum(json.loads(r[0])['status'] in ('running','queued','cancelling') for r in c.execute('select data from tasks')))"
    if int(ssh('python3 -c '+shlex.quote(check))): raise RuntimeError('Active Navigator tasks; defer restart')
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    backup='/opt/agentpair-backups/modules-'+stamp+'.tar.gz'
    ssh('mkdir -p /opt/agentpair-backups && tar -czf '+backup+' -C /opt/agentpair agentpair')
    files=['agentpair/devices.py','agentpair/platform.py','agentpair/endpoint_modules.py','agentpair/experience_store.py','agentpair/endpoint_analysis.py',
        'agentpair/endpoint_modules/process_details.ps1','agentpair/endpoint_modules/process_tcp.ps1']
    content=io.BytesIO()
    with tarfile.open(fileobj=content,mode='w') as archive:
        for name in files: archive.add(ROOT/name,arcname=name)
        name='agentpair/web_assets/agentpair-windows.ps1'
        data=(ROOT/name).read_text().encode('utf-8-sig')
        info=tarfile.TarInfo(name);info.size=len(data);info.mode=0o644
        archive.addfile(info,io.BytesIO(data))
        archive.add(installer,arcname='agentpair/web_assets/AgentPair-Windows-Setup-0.1.0.exe')
    ssh('tar -xf - -C /opt/agentpair',content.getvalue())
    try:
        ssh("cd /opt/agentpair && python3 -c 'import agentpair.platform, agentpair.devices, agentpair.experience_store' && systemctl restart agentpair-navigator.service")
        ssh('curl --retry 5 --retry-connrefused --retry-delay 1 -fsS http://127.0.0.1:9090/devices >/dev/null')
    except Exception:
        ssh('tar -xzf '+backup+' -C /opt/agentpair && systemctl restart agentpair-navigator.service')
        raise
    print('Endpoint modules deployed; rollback: '+backup)


if __name__=='__main__':main()
