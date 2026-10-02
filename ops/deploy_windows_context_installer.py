"""Publish CI-built installer with backup and public SHA256 verification."""
import hashlib,io,shlex,sys,tarfile,time,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from deploy_persistent_navigator import ssh,KEY,KNOWN,HOST
def main():
    artifact=Path(sys.argv[1]).resolve() if len(sys.argv)>1 else ROOT/'runtime/applens-windows-context-build/AgentPair-Windows-Setup-0.1.0.exe'
    raw=artifact.read_bytes();digest=hashlib.sha256(raw).hexdigest()
    if raw[:2]!=b'MZ' or len(raw)<100000:raise RuntimeError('Invalid installer artifact')
    target='/opt/agentpair/agentpair/web_assets/AgentPair-Windows-Setup-0.1.0.exe'
    backup='/opt/agentpair-backups/windows-context-'+str(int(time.time()))+'.exe'
    ssh('if test -f '+target+'; then cp -p '+target+' '+backup+'; fi')
    pending=target+'.pending'
    subprocess.run(['scp','-i',KEY,'-o','IdentitiesOnly=yes','-o','BatchMode=yes',
        '-o','StrictHostKeyChecking=yes','-o','UserKnownHostsFile='+KNOWN,
        str(artifact),'root@'+HOST+':'+pending],check=True,timeout=600)
    uploaded=ssh('sha256sum '+pending).decode().split()[0]
    if uploaded!=digest:raise RuntimeError('Staged installer SHA256 mismatch; live download unchanged')
    ssh('mv '+pending+' '+target)
    result=ssh('curl -fsS https://50.118.187.180/downloads/AgentPair-Windows-Setup-0.1.0.exe | sha256sum').decode().split()[0]
    if result!=digest:raise RuntimeError('Public installer SHA256 mismatch')
    print('Published Windows installer; public SHA256 verified: '+digest)
    # Keep existing non-AppLens locations intact. Only this body-upload route increases to 2 MB.
    path='/etc/nginx/sites-enabled/agentpair'
    code="from pathlib import Path; p=Path("+repr(path)+"); s=p.read_text(); marker='    location / {\\n        proxy_pass http://127.0.0.1:9090;'; insert='    location = /api/applens/model-context {\\n        client_max_body_size 2m;\\n        proxy_pass http://127.0.0.1:9090;\\n        proxy_set_header Host $host;\\n        proxy_set_header X-Forwarded-Proto https;\\n        proxy_read_timeout 180s;\\n    }\\n'; assert marker in s; p.write_text(s if 'location = /api/applens/model-context' in s else s.replace(marker,insert+marker))"
    configBackup='/opt/agentpair-backups/nginx-model-context-'+str(int(time.time()))+'.conf'
    ssh('cp -p '+path+' '+configBackup)
    try:
        ssh('python3 -c '+shlex.quote(code));ssh('nginx -t && systemctl reload nginx')
    except Exception:
        ssh('cp -p '+configBackup+' '+path+' && nginx -t && systemctl reload nginx');raise
    print('Model-body route 2 MB limit applied; other proxy locations preserved.')
if __name__=='__main__':main()
