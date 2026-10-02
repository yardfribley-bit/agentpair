"""One explicitly authorized browser trial; retains paid lease until expiry."""
import base64
import json
from pathlib import Path
import secrets
import sys
from agentpair.ucloud import UCloudClient
from agentpair.resources import ResourceManager


def main():
    c=json.loads(Path('/etc/agentpair/private.json').read_text())['cloud']
    api=UCloudClient(c['publicKey'],c['privateKey'],c['projectId'],c['region'])
    original=api.transport
    def diagnostic(payload):
        result=original(payload)
        if result.get('RetCode')!=0:
            print(json.dumps({'action':payload['Action'],'code':result.get('RetCode'),
                              'message':result.get('Message','')[:400]}),flush=True)
        return result
    api.transport=diagnostic
    manager=ResourceManager(api,c['leaseDirectory'],max_hosts=4,max_hourly_cny=.30)
    config={'Zone':c['zone'],'ImageId':'uimage-1u8x80ot9h2e','MachineType':'N','CPU':1,'Memory':1024,
            'ChargeType':'Dynamic','Disks.0.Size':20,'Disks.0.IsBoot':'True','Disks.0.Type':'CLOUD_SSD'}
    eip={'Bandwidth':1,'ChargeType':'Dynamic','PayMode':'Bandwidth','OperatorName':'Bgp'}
    if sys.argv[-1]!='create':
        print(json.dumps(manager.quote(config,eip)));return
    for old in manager.records():
        if old['name'].startswith('agentpair-driver-browser-') and old['state']=='reconcile_required':
            manager.release(old['id'])
    config.update(SecurityGroupId=c['firewallId'],LoginMode='Password',
                  Password=base64.b64encode(('Aa9!'+secrets.token_hex(12)).encode()).decode())
    # Leave two minutes for minute-based reaper and cloud termination before hour boundary.
    lease=manager.create_driver(config,eip,'agentpair-driver-browser-'+secrets.token_hex(4),c['sshPublicKey'],seconds=3480)
    print(json.dumps(lease))


if __name__=='__main__':main()
