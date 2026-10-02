"""Reusable access preflight for an existing authorized lease; never creates hosts.

Run on the operator workstation. Windows console fallback is opt-in and uses
the lease's private password without printing or persisting it in diagnostics.
"""
import argparse
import base64
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shlex
import sys
import time

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from deploy_persistent_navigator import ssh
from agentpair.windows_access import bootstrap_script


def resolve(lease_id):
    if not re.fullmatch('[0-9a-f]{24}',lease_id):raise ValueError('Invalid lease id')
    lease=json.loads(ssh('cat /var/lib/agentpair/leases/'+lease_id+'.json'))
    if lease['state']!='active':raise RuntimeError('Lease is not active')
    remaining=(datetime.fromisoformat(lease['expiresAt'])-datetime.now(timezone.utc)).total_seconds()
    if remaining<180:raise RuntimeError('Lease has less than 3 minutes; refuse new deployment')
    code="""import json,sys;from pathlib import Path;sys.path.insert(0,'/opt/agentpair');from agentpair.ucloud import UCloudClient
c=json.loads(Path('/etc/agentpair/private.json').read_text())['cloud']
a=UCloudClient(c['publicKey'],c['privateKey'],c['projectId'],c['region'])
rows=a.call('DescribeUHostInstance',**{'UHostIds.0':__HOST__}).get('UHostSet',[])
print(json.dumps(rows))""".replace('__HOST__',repr(lease['hostId']))
    rows=json.loads(ssh('python3 -c '+shlex.quote(code)))
    if len(rows)!=1 or rows[0]['Name']!=lease['name'] or rows[0]['State']!='Running':
        raise RuntimeError('Cloud identity/running state mismatch')
    addresses=[x['IP'] for x in rows[0].get('IPSet',[]) if x.get('Type') in ('Bgp','BGP')]
    if len(addresses)!=1:raise RuntimeError('Expected one cloud-verified public address')
    return lease,addresses[0],int(remaining)


def remote(ip,user,command):
    import ipaddress
    ipaddress.ip_address(ip)
    if user not in ('Administrator','pair','ubuntu','root'):raise ValueError('Unsupported host user')
    # Only provider-verified destinations; independent known-host history,
    # never disable host-key checking or overwrite an existing fingerprint.
    args=['ssh','-i','/var/lib/agentpair/driver_key','-o','BatchMode=yes',
          '-o','StrictHostKeyChecking=accept-new','-o','UserKnownHostsFile=/var/lib/agentpair/windows_known_hosts',
          '-o','ConnectTimeout=8',user+'@'+ip,command]
    return ssh(shlex.join(args)).decode(errors='replace').strip()


def prepare(lease_id,console_login=False):
    lease,ip,remaining=resolve(lease_id)
    windows=lease.get('platform')=='windows';user='Administrator' if windows else 'pair'
    checkpoint={'leaseId':lease_id,'hostId':lease['hostId'],'address':ip,'remainingSeconds':remaining,'stage':'cloud_verified'}
    def save():
        path='/var/lib/agentpair/access/'+lease_id+'.json'
        ssh('install -d -m 0700 /var/lib/agentpair/access && install -m 0600 /dev/stdin '+path,json.dumps(checkpoint).encode())
    save()
    try:
        try:identity=remote(ip,user,'whoami')
        except RuntimeError:
            if not windows or not console_login:raise RuntimeError('SSH not ready; Windows may require console bootstrap') from None
            from windows_console import Console
            secret=json.loads(ssh('cat /var/lib/agentpair/windows-trial-'+lease_id+'.private.json'))
            public=ssh('cat /var/lib/agentpair/driver_key.pub').decode().strip()
            c=Console(lease['hostId'],lease_id=lease_id)
            try:
                c.press(0xffe3,0xffe9,0xffff);time.sleep(2)
                c.type(secret['password']);c.press(0xff0d);time.sleep(8)
                c.press(0xffeb,ord('r'));time.sleep(1);c.type('powershell');c.press(0xff0d);time.sleep(3)
                c.command(bootstrap_script(public))
            finally:c.sock.close()
            # Console keystrokes are NOT success evidence. Actual SSH
            # authentication is the acceptance gate. No repeated login guesses.
            checkpoint['stage']='ssh_bootstrap_pending';save()
            for _ in range(12):
                resolve(lease_id)
                try:identity=remote(ip,user,'whoami');break
                except RuntimeError:time.sleep(10)
            else:raise RuntimeError('Console bootstrap did not produce authenticated SSH; inspect guest status')
        if windows and not identity.lower().endswith('\\administrator'):raise RuntimeError('Unexpected Windows account')
        if not windows and identity!='pair':raise RuntimeError('Unexpected Linux account')
        checkpoint.update(stage='ssh_authenticated',identity=identity,checkedAt=datetime.now(timezone.utc).isoformat())
        save();return checkpoint
    except Exception as error:
        checkpoint.update(stage='failed',reason=str(error)[:300]);save();raise


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--lease',required=True)
    parser.add_argument('--console-login',action='store_true',help='Windows login screen only, one bounded attempt')
    args=parser.parse_args();print(json.dumps(prepare(args.lease,args.console_login),ensure_ascii=False))

if __name__=='__main__':main()
