"""Register the published AppLens installer and preload it directly on Driver."""
import json
import shlex
import sys
import time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from deploy_persistent_navigator import ssh

def main():
    digest=sys.argv[1]
    recipe={'id':'applens-windows','name':'AppLens','displayName':'AppLens 应用透镜 version 0.1.0',
            'platform':'Windows','installer':'inno','version':'0.1.0',
            'url':'https://50.118.187.180/downloads/AgentPair-Windows-Setup-0.1.0.exe','sha256':digest}
    code="""
import os,pwd
from pathlib import Path
from agentpair.software_install import SoftwareCatalog
from agentpair.package_cache import PackageNode
recipe=RECIPE
p=Path('/var/lib/agentpair/software-catalog.json')
SoftwareCatalog(p).register(recipe)
u=pwd.getpwnam('agentpair');os.chown(p,u.pw_uid,u.pw_gid)
print(PackageNode(p.parent/'package-node.private.json').call('/prepare',recipe))
""".replace('RECIPE',repr(recipe))
    print(ssh('cd /opt/agentpair && python3 -c '+shlex.quote(code)).decode().strip())
    for _ in range(60):
        result=json.loads(ssh('curl -fsS http://127.0.0.1:9090/api/packages'))
        row=next(x for x in result['items'] if x['id']==recipe['id'])
        if row['cacheState']=='cached':
            print(json.dumps(row,ensure_ascii=False));return
        if row['cacheState']=='failed':raise RuntimeError('AppLens cache download failed')
        time.sleep(5)
    raise TimeoutError('AppLens cache still pending; do not create Windows yet')

if __name__=='__main__':main()
