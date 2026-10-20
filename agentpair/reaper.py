"""Independent one-shot lease cleanup for a systemd timer."""
import json
from pathlib import Path
import sys
from .resources import ResourceManager
from .ucloud import UCloudClient

def main():
    private=json.loads(Path(sys.argv[1]).read_text())['cloud']
    client=UCloudClient(private['publicKey'],private['privateKey'],
                        private['projectId'],private['region'])
    manager=ResourceManager(client,private['leaseDirectory'],
                            max_hosts=private.get('maxHosts',4),
                            max_seconds=private.get('maxSeconds',3600),
                            max_hourly_cny=private.get('maxHourlyCNY',1.0))
    released=manager.release_expired()
    failures=[row for row in released if row['state']!='released']
    print('Expired leases released:',len(released)-len(failures),'failed:',len(failures))
    if failures:raise SystemExit(1)

if __name__=='__main__': main()
