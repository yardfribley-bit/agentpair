"""Deploy the persistent Navigator to the user-provided pinned SSH host."""
import getpass
import io
import json
import os
from pathlib import Path
import secrets
import subprocess
import tarfile

ROOT=Path(__file__).resolve().parent
HOST='50.118.187.180'
KEY='/private/tmp/agentpair-nav.CLCAWP/id_ed25519'
KNOWN='/private/tmp/agentpair-nav-known-hosts'

def ssh(command,data=b''):
    result=subprocess.run(['ssh','-i',KEY,'-o','IdentitiesOnly=yes',
        '-o','BatchMode=yes','-o','StrictHostKeyChecking=yes',
        '-o','UserKnownHostsFile='+KNOWN,'-o','ConnectTimeout=8',
        'root@'+HOST,command],input=data,stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,timeout=90)
    if result.returncode:
        raise RuntimeError('Navigator SSH deployment step failed: '+command.split()[0])
    return result.stdout

def main():
    os.umask(0o077)
    public=getpass.getpass('UCloud API ID (hidden): ')
    secret=getpass.getpass('UCloud API secret (hidden): ')
    relay=getpass.getpass('Model relay token (hidden): ')
    private_dir=ROOT/'runtime';private_dir.mkdir(mode=0o700,exist_ok=True)
    password_path=private_dir/'persistent-navigator-password.txt'
    if password_path.exists(): password=password_path.read_text().strip()
    else:
        password=secrets.token_urlsafe(28)
        password_path.write_text(password+'\n');os.chmod(password_path,0o600)
    ssh('id agentpair >/dev/null 2>&1 || useradd --system --create-home --home-dir /var/lib/agentpair --shell /usr/sbin/nologin agentpair')
    ssh('install -d -m 0755 /opt/agentpair /etc/agentpair && install -d -o agentpair -g agentpair -m 0700 /var/lib/agentpair /var/lib/agentpair/leases')
    ssh('test -s /var/lib/agentpair/driver_key || runuser -u agentpair -- ssh-keygen -q -t ed25519 -N "" -f /var/lib/agentpair/driver_key')
    pubkey=ssh('cat /var/lib/agentpair/driver_key.pub').decode().strip()
    ssh('touch /var/lib/agentpair/known_hosts && chown agentpair:agentpair /var/lib/agentpair/known_hosts && chmod 600 /var/lib/agentpair/known_hosts')
    bundle=io.BytesIO()
    with tarfile.open(fileobj=bundle,mode='w') as archive:
        for path in (ROOT/'agentpair').rglob('*'):
            if path.is_file() and '__pycache__' not in path.parts:
                archive.add(path,arcname=str(path.relative_to(ROOT)))
    ssh('tar -xf - -C /opt/agentpair',bundle.getvalue())
    for name in ('agentpair-navigator.service','agentpair-reaper.service','agentpair-reaper.timer'):
        ssh('install -m 0644 /dev/stdin /etc/systemd/system/'+name,(ROOT/'ops'/name).read_bytes())
    config={'password':password,'relayToken':relay,'cloud':{
        'publicKey':public,'privateKey':secret,'projectId':'org-2qgt5t','region':'cn-bj2',
        'zone':'cn-bj2-04','firewallId':'firewall-xf421dem',
        'leaseDirectory':'/var/lib/agentpair/leases','workerRoot':'/opt/agentpair',
        'sshPublicKey':pubkey,'maxHosts':4,'maxSeconds':3600,'maxHourlyCNY':1.0}}
    ssh('install -o agentpair -g agentpair -m 0600 /dev/stdin /etc/agentpair/private.json',
        (json.dumps(config)+'\n').encode())
    ssh('systemctl daemon-reload && systemctl enable --now agentpair-navigator.service agentpair-reaper.timer')
    print(ssh('systemctl is-active agentpair-navigator.service agentpair-reaper.timer && curl -fsS http://127.0.0.1:9090/ >/dev/null && echo WORKSPACE_OK').decode().strip())
    print('Navigator service installed. Password saved in runtime/persistent-navigator-password.txt.')

if __name__=='__main__':main()
