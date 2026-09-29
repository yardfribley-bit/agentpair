"""One bounded cloud Driver bootstrap check, with exact-lease cleanup."""
import json
from pathlib import Path
import sys
from .resources import ResourceManager
from .transport import CloudDriverBackend
from .ucloud import UCloudClient


def main():
    private=json.loads(Path(sys.argv[1]).read_text())['cloud']
    client=UCloudClient(private['publicKey'],private['privateKey'],
                        private['projectId'],private['region'])
    manager=ResourceManager(client,private['leaseDirectory'],
                            max_hosts=private.get('maxHosts',4),
                            max_seconds=private.get('maxSeconds',3600),
                            max_hourly_cny=private.get('maxHourlyCNY',1.0))
    existing={r['id'] for r in manager.records()}
    backend=CloudDriverBackend('',manager,'/var/lib/agentpair/driver_key',
                               '/var/lib/agentpair/known_hosts',
                               private['sshPublicKey'],private['firewallId'],
                               private['workerRoot'],private['zone'])
    try:
        ip,_=backend._provision()
        backend._deploy_worker(ip)
        print(json.dumps({'bootstrap':'ok','leaseId':backend.lease_id}))
    finally:
        for lease in manager.records():
            if lease['id'] not in existing and lease['state']!='released':
                result=manager.release(lease['id'])
                print(json.dumps({'leaseId':result['id'],'state':result['state']}))


if __name__=='__main__':main()
