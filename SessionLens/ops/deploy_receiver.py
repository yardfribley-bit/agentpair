"""Add only SessionLens routes to the existing AgentPair deployment."""
import getpass
import json
import os
from pathlib import Path
import shlex
import subprocess

ROOT=Path(__file__).resolve().parents[2]

def main():
    password=getpass.getpass('SSH password: ')
    def ssh(command,data=b''):
        r,w=os.pipe();os.write(w,(password+'\n').encode());os.close(w)
        try:
            result=subprocess.run(['sshpass','-d',str(r),'ssh','-o','StrictHostKeyChecking=yes','-o','UserKnownHostsFile=/private/tmp/vmess-50.118.187.180-known_hosts','-o','ConnectTimeout=10','root@50.118.187.180',command],pass_fds=(r,),input=data,capture_output=True,timeout=60)
        finally:os.close(r)
        if result.returncode:raise RuntimeError(result.stderr.decode()[-1500:])
        return result.stdout.decode()
    local=(ROOT/'agentpair/platform.py').read_text(encoding='utf-8')
    post=local[local.index("            if self.path=='/api/sessionlens/events':"):local.index("            if self.path=='/api/applens/model-context':")]
    get=local[local.index("                if self.path=='/api/sessionlens/sessions':"):local.index("                if self.path=='/api/session':")]
    analyze=local[local.index("                if self.path=='/api/sessionlens/analyze':"):local.index("                if self.path in ('/api/cloud/quote','/api/cloud/create'):")]
    payload={'receiver':(ROOT/'agentpair/session_lens.py').read_text(encoding='utf-8'),'post':post,'get':get,'analyze':analyze}
    code='''import json,sys,datetime,shutil,subprocess
from pathlib import Path
payload=json.load(sys.stdin);root=Path('/opt/agentpair/agentpair');p=root/'platform.py';s=p.read_text();backup=Path('/opt/agentpair-backups')/('sessionlens-receiver-'+datetime.datetime.now().strftime('%Y%m%dT%H%M%S'));backup.mkdir(parents=True)
shutil.copy2(p,backup/'platform.py');receiver=root/'session_lens.py'
if receiver.exists():shutil.copy2(receiver,backup/'session_lens.py')
if '/api/sessionlens/events' not in s:
    changes=[('from .devices import DeviceStore','from .devices import DeviceStore\\nfrom .session_lens import SessionStore'),("    devices=DeviceStore(Path(engine.db).parent/'devices.db')","    devices=DeviceStore(Path(engine.db).parent/'devices.db')\\n    session_lens=SessionStore(Path(engine.db).parent/'sessionlens.db')"),('        def do_POST(self):\\n','        def do_POST(self):\\n'+payload['post']),("                if self.path=='/api/session':",payload['get']+"                if self.path=='/api/session':"),("                if self.path in ('/api/cloud/quote','/api/cloud/create'):",payload['analyze']+"                if self.path in ('/api/cloud/quote','/api/cloud/create'):")]
    for old,new in changes:
        if s.count(old)!=1:raise RuntimeError('Deployment anchor mismatch; no files changed')
        s=s.replace(old,new,1)
compile(s,str(p),'exec');compile(payload['receiver'],str(receiver),'exec')
try:
    receiver.write_text(payload['receiver']);p.write_text(s)
    subprocess.run(['systemctl','restart','agentpair-navigator.service'],check=True)
    import time;time.sleep(2)
    subprocess.run(['systemctl','is-active','--quiet','agentpair-navigator.service'],check=True)
    import urllib.request,urllib.error
    req=urllib.request.Request('http://127.0.0.1:9090/api/sessionlens/events',b'{}',{'Content-Type':'application/json'},method='POST')
    try:urllib.request.urlopen(req,timeout=5);raise RuntimeError('Unauthenticated ingestion accepted')
    except urllib.error.HTTPError as e:
        if e.code!=401:raise
except Exception:
    shutil.copy2(backup/'platform.py',p)
    if (backup/'session_lens.py').exists():shutil.copy2(backup/'session_lens.py',receiver)
    else:receiver.unlink(missing_ok=True)
    subprocess.run(['systemctl','restart','agentpair-navigator.service'])
    raise
print('RECEIVER_READY; unauthorized POST=401; backup='+str(backup))
'''
    print(ssh('python3 -c '+shlex.quote(code),json.dumps(payload).encode()))
    print(ssh("curl -s -o /dev/null -w 'DOMAIN_POST_STATUS=%{http_code}\\n' -H 'Content-Type: application/json' --data '{}' https://www.chuhaijian.com/api/sessionlens/events; curl -s -o /dev/null -w 'INSIGHTS_STATUS=%{http_code}\\n' https://www.chuhaijian.com/session-insights/"))

if __name__=='__main__':main()
