"""Pin official current Windows release and import it on the persistent Driver."""
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys
from urllib.request import urlopen
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from deploy_persistent_navigator import KEY,KNOWN,HOST,ssh
from ops.deploy_packages import DRIVER


def main():
    with urlopen('https://www.workbuddy.cn/v2/update?platform=workbuddy-win32-x64-user',timeout=30) as response:
        release=json.load(response)
    version=release['productVersion'];url=release['url']
    if not re.fullmatch(r'\d+\.\d+\.\d+\.\d+',version):raise ValueError('Unrecognized version')
    if not url.startswith('https://download.codebuddy.cn/workbuddy/saas/win32-x64-user/WorkBuddy-') or not url.endswith('.exe'):
        raise ValueError('Official Windows source changed; review required')
    code=r"""
import hashlib,json,os,secrets,time
from pathlib import Path
from urllib.request import urlopen
from agentpair.package_cache import PackageCache
release=RELEASE
cache=PackageCache('/home/pair/package-cache')
part=cache.directory/('official-import-'+secrets.token_hex(8)+'.part')
h=hashlib.sha256();size=0;started=time.time()
try:
    with urlopen(release['url'],timeout=30) as response,part.open('wb') as output:
        if not response.geturl().startswith('https://download.codebuddy.cn/'):
            raise ValueError('Unexpected download redirect')
        while True:
            block=response.read(1024*1024)
            if not block:break
            size+=len(block)
            if size>cache.max_bytes or time.time()-started>900:raise ValueError('Import limit reached')
            h.update(block);output.write(block)
    key=h.hexdigest()
    publisher=release.get('sha256hash')
    if publisher and publisher.lower()!=key:raise ValueError('Official digest mismatch')
    if cache.hash_file(part)!=key:raise ValueError('Local verification mismatch')
    part.replace(cache.path(key))
    cache.save({'sha256':key,'state':'cached','bytes':size,'softwareId':'workbuddy-windows',
                'verifiedAt':time.time(),'startedAt':started,'digestSource':'publisher' if publisher else 'official_https_download'})
    print(json.dumps({'sha256':key,'bytes':size,'version':release['productVersion']}))
finally:
    part.unlink(missing_ok=True)
""".replace('RELEASE',repr(release))
    command=DRIVER+shlex.quote('cd /home/pair/package-node && python3 -c '+shlex.quote(code))
    result=subprocess.run(['ssh','-i',KEY,'-o','IdentitiesOnly=yes','-o','BatchMode=yes',
        '-o','StrictHostKeyChecking=yes','-o','UserKnownHostsFile='+KNOWN,'-o','ConnectTimeout=8',
        'root@'+HOST,command],capture_output=True,timeout=1100)
    if result.returncode:raise RuntimeError('Official package import failed: '+result.stderr.decode(errors='replace')[-400:])
    receipt=json.loads(result.stdout)
    installed_version='.'.join(version.split('.')[:3])
    recipe={'id':'workbuddy-windows','name':'WorkBuddy','displayName':'WorkBuddy '+installed_version,'platform':'Windows',
            'installer':'inno','version':installed_version,'url':url,'sha256':receipt['sha256']}
    register="from agentpair.software_install import SoftwareCatalog; from pathlib import Path; import os,pwd; r="+repr(recipe)+"; p=Path('/var/lib/agentpair/software-catalog.json'); SoftwareCatalog(p).register(r); u=pwd.getpwnam('agentpair'); os.chown(p,u.pw_uid,u.pw_gid); print('WorkBuddy registered')"
    print(ssh('cd /opt/agentpair && python3 -c '+shlex.quote(register)).decode().strip())
    data=json.loads(ssh('curl -fsS http://127.0.0.1:9090/api/packages'))
    row=next(r for r in data['items'] if r['id']=='workbuddy-windows')
    if row['cacheState']!='cached':raise RuntimeError('Platform did not confirm verified cache')
    print(json.dumps(row,ensure_ascii=False))


if __name__=='__main__':main()
