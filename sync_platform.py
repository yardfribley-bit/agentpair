"""Update this experiment without adding instances, calls or changing password."""
import getpass
import argparse
import datetime
import io
import json
from pathlib import Path
import tarfile
from deploy_platform import ROOT, NAV, DRIVER, ssh


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--public-demo',action='store_true')
    args=parser.parse_args()
    token=getpass.getpass('Relay token (hidden): ')
    for host in (DRIVER,NAV):
        bundle=io.BytesIO()
        with tarfile.open(fileobj=bundle,mode='w') as archive:
            for path in (ROOT/'agentpair').rglob('*'):
                if path.is_file() and '__pycache__' not in path.parts:
                    archive.add(path,arcname=str(path.relative_to(ROOT)))
            if host==NAV:
                # Only this synthetic, non-sensitive coding demo is published,
                # never arbitrary user tasks or the task database.
                demo=json.loads((ROOT/'runtime'/'live-task.json').read_text())
                demo['snapshot']=True
                data=json.dumps(demo,ensure_ascii=False).encode()
                info=tarfile.TarInfo('public_conversation.json'); info.size=len(data); info.mode=0o600
                archive.addfile(info,io.BytesIO(data))
        ssh(host,'tar -xf - -C /home/pair/AgentPair',bundle.getvalue())
    password=(ROOT/'runtime'/'workspace-password.txt').read_text().strip()
    print(ssh(NAV,'cd /home/pair/AgentPair; python3 -m agentpair.bootstrap',
              json.dumps({'relayToken':token,'password':password,'publicDemo':args.public_demo,
                          'expiresAt':datetime.datetime.fromisoformat('2026-09-29T08:30:28+00:00').timestamp()}).encode()).decode().strip())


if __name__=='__main__': main()
