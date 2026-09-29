"""Deploy existing nodes only; no cloud provisioning or extra model calls."""
import getpass
import io
import json
import os
from pathlib import Path
import secrets
import subprocess
import tarfile

ROOT=Path(__file__).resolve().parent
KEYDIR=Path('/private/tmp/agentpair-run.vQ4wLt')
KEY=KEYDIR/'id_ed25519'
DRIVERKEY=KEYDIR/'platform_driver'
KNOWN=KEYDIR/'known_hosts'
NAV='106.75.9.169'; DRIVER='106.75.18.16'


def ssh(host,command,data=b''):
    result=subprocess.run(['ssh','-i',str(KEY),'-o','IdentitiesOnly=yes','-o','BatchMode=yes',
        '-o','StrictHostKeyChecking=yes','-o','UserKnownHostsFile='+str(KNOWN),'-o','ConnectTimeout=8',
        'pair@'+host,command],input=data,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=40)
    if result.returncode: raise RuntimeError('Deployment SSH operation failed')
    return result.stdout


def main():
    os.umask(0o077)
    token=getpass.getpass('Relay token (hidden): ')
    password=secrets.token_urlsafe(24)
    private=ROOT/'runtime'; private.mkdir(mode=0o700,exist_ok=True)
    (private/'workspace-password.txt').write_text(password+'\n')
    copy=subprocess.run(['ssh-copy-id','-f','-i',str(DRIVERKEY.with_suffix('.pub')),
        '-o','IdentityFile='+str(KEY),'-o','BatchMode=yes','-o','StrictHostKeyChecking=yes',
        '-o','UserKnownHostsFile='+str(KNOWN),'pair@'+DRIVER],stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30)
    if copy.returncode: raise RuntimeError('Scoped Driver key installation failed')
    bundle=io.BytesIO()
    with tarfile.open(fileobj=bundle,mode='w') as archive:
        for path in (ROOT/'agentpair').rglob('*'):
            if path.is_file() and '__pycache__' not in path.parts:
                archive.add(path,arcname=str(path.relative_to(ROOT)))
        for path,name in [(DRIVERKEY,'runtime/driver_key'),(KNOWN,'runtime/known_hosts')]:
            data=path.read_bytes(); info=tarfile.TarInfo(name); info.size=len(data); info.mode=0o600
            archive.addfile(info,io.BytesIO(data))
    # Both nodes receive code; the scoped key is only sent to Navigator.
    for host in (DRIVER,NAV):
        if host==NAV: data=bundle.getvalue()
        else:
            code=io.BytesIO()
            with tarfile.open(fileobj=code,mode='w') as archive:
                for path in (ROOT/'agentpair').rglob('*.py'):
                    if '__pycache__' not in path.parts: archive.add(path,arcname=str(path.relative_to(ROOT)))
            data=code.getvalue()
        ssh(host,'mkdir -p /home/pair/AgentPair/runtime; chmod 700 /home/pair/AgentPair/runtime; tar -xf - -C /home/pair/AgentPair',data)
    launched=ssh(NAV,'cd /home/pair/AgentPair; python3 -m agentpair.bootstrap',json.dumps({'relayToken':token,'password':password}).encode())
    print(launched.decode().strip())
    print('Workspace password saved privately in runtime/workspace-password.txt; no token saved.')


if __name__=='__main__': main()
