"""Deploy the private understanding gateway; relay credentials stay on AgentPair."""
import getpass,hashlib,json,os,secrets,shlex,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]

def main():
    password=getpass.getpass('SSH password: ')
    root=Path.home()/'Library/Application Support/SessionLens';root.mkdir(parents=True,exist_ok=True)
    tokenfile=root/'assistant-token';token=tokenfile.read_text().strip() if tokenfile.exists() else secrets.token_urlsafe(48)
    payload={'source':(ROOT/'agentpair/session_assistant.py').read_text(),'authorizationHash':hashlib.sha256(('Bearer '+token).encode()).hexdigest()}
    remote='''import json,sys,os,pwd,datetime,shutil,subprocess,time,urllib.request,urllib.error
from pathlib import Path
x=json.load(sys.stdin);source=Path('/opt/agentpair/agentpair/session_assistant.py');nginx=Path('/etc/nginx/sites-available/agentpair-domain');config=Path('/etc/agentpair/sessionlens-assistant.json');unit=Path('/etc/systemd/system/sessionlens-assistant.service')
backup=Path('/opt/agentpair-backups')/('sessionlens-assistant-'+datetime.datetime.now().strftime('%Y%m%dT%H%M%S'));backup.mkdir(parents=True)
files=[source,nginx,config,unit];prior={str(p):p.exists() for p in files}
for p in files:
 if p.exists():shutil.copy2(p,backup/p.name)
text=nginx.read_text();anchor='    location / {\\n        proxy_set_header Origin $agentpair_origin;'
block='    location /api/sessionlens/assistant/ {\\n        proxy_pass http://127.0.0.1:18952;\\n        proxy_set_header Host $host;\\n        proxy_set_header X-Forwarded-Proto https;\\n        proxy_read_timeout 35s;\\n    }\\n'
if 'location /api/sessionlens/assistant/' not in text:
 if text.count(anchor)!=1:raise RuntimeError('nginx anchor mismatch')
 text=text.replace(anchor,block+anchor)
compile(x['source'],str(source),'exec');user=pwd.getpwnam('agentpair');state=Path('/var/lib/agentpair/sessionlens-assistant');state.mkdir(exist_ok=True);os.chown(state,user.pw_uid,user.pw_gid);os.chmod(state,0o700)
try:
 source.write_text(x['source']);nginx.write_text(text)
 config.write_text(json.dumps({'authorizationHash':x['authorizationHash'],'state':str(state)}));os.chown(config,user.pw_uid,user.pw_gid);os.chmod(config,0o600)
 unit.write_text('[Unit]\\nDescription=AgentPair SessionLens Understanding\\nAfter=network-online.target\\n[Service]\\nUser=agentpair\\nGroup=agentpair\\nWorkingDirectory=/opt/agentpair\\nExecStart=/opt/agentpair/venv/bin/python -m agentpair.session_assistant\\nRestart=on-failure\\nUMask=0077\\nNoNewPrivileges=true\\nProtectSystem=strict\\nProtectHome=true\\nReadWritePaths=/var/lib/agentpair/sessionlens-assistant\\nPrivateTmp=true\\nMemoryMax=512M\\n[Install]\\nWantedBy=multi-user.target\\n')
 subprocess.run(['nginx','-t'],check=True,capture_output=True)
 subprocess.run(['systemctl','daemon-reload'],check=True)
 subprocess.run(['systemctl','enable','--now','sessionlens-assistant.service'],check=True,capture_output=True)
 subprocess.run(['systemctl','restart','sessionlens-assistant.service'],check=True)
 time.sleep(2);subprocess.run(['systemctl','is-active','--quiet','sessionlens-assistant.service'],check=True)
 try:urllib.request.urlopen('http://127.0.0.1:18952/api/sessionlens/assistant/health',timeout=5);raise RuntimeError('Missing auth')
 except urllib.error.HTTPError as e:
  if e.code!=401:raise
 subprocess.run(['systemctl','reload','nginx'],check=True)
except Exception:
 for p in files:
  if prior[str(p)]:shutil.copy2(backup/p.name,p)
  else:p.unlink(missing_ok=True)
 subprocess.run(['systemctl','daemon-reload'])
 subprocess.run(['systemctl','restart' if prior[str(unit)] else 'stop','sessionlens-assistant.service'])
 subprocess.run(['systemctl','reload','nginx']);raise
print(json.dumps({'status':'ready','backup':str(backup)}))
'''
    r,w=os.pipe();os.write(w,(password+'\n').encode());os.close(w)
    try:
        result=subprocess.run(['sshpass','-d',str(r),'ssh','-o','StrictHostKeyChecking=yes','-o','UserKnownHostsFile=/private/tmp/vmess-50.118.187.180-known_hosts','-o','ConnectTimeout=10','root@50.118.187.180','python3 -c '+shlex.quote(remote)],input=json.dumps(payload).encode(),capture_output=True,pass_fds=(r,),timeout=90)
    finally:os.close(r)
    if result.returncode:raise RuntimeError(result.stderr.decode()[-2000:])
    tokenfile.write_text(token);os.chmod(tokenfile,0o600)
    configfile=root/'settings.json';config=json.loads(configfile.read_text());config['assistant']={'url':'https://www.chuhaijian.com/api/sessionlens/assistant','tokenFile':str(tokenfile)}
    configfile.write_text(json.dumps(config,ensure_ascii=False,indent=2));os.chmod(configfile,0o600)
    print(result.stdout.decode());print('LOCAL_ASSISTANT_CONFIGURED; model key remains on server')
if __name__=='__main__':main()
