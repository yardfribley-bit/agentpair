"""Sync code and HTTPS units to the pinned persistent Navigator."""
import io
from pathlib import Path
import tarfile
from deploy_persistent_navigator import ROOT, ssh

def main():
    bundle=io.BytesIO()
    with tarfile.open(fileobj=bundle,mode='w') as archive:
        for path in (ROOT/'agentpair').rglob('*'):
            if path.is_file() and '__pycache__' not in path.parts:
                archive.add(path,arcname=str(path.relative_to(ROOT)))
    ssh('tar -xf - -C /opt/agentpair',bundle.getvalue())
    ssh('/opt/agentpair/venv/bin/pip install -r /dev/stdin',
        (ROOT/'requirements-repository.txt').read_bytes())
    for name in ('agentpair-navigator.service','agentpair-reaper.service',
                 'agentpair-reaper.timer','agentpair-certbot.service','agentpair-certbot.timer'):
        ssh('install -m 0644 /dev/stdin /etc/systemd/system/'+name,(ROOT/'ops'/name).read_bytes())
    ssh('install -m 0644 /dev/stdin /etc/nginx/sites-available/agentpair',
        (ROOT/'ops'/'agentpair-nginx.conf').read_bytes())
    ssh('test -L /etc/nginx/sites-enabled/agentpair || ln -s /etc/nginx/sites-available/agentpair /etc/nginx/sites-enabled/agentpair')
    ssh('nginx -t && systemctl daemon-reload && systemctl reload nginx && systemctl restart agentpair-navigator.service && systemctl enable --now agentpair-certbot.timer')
    print(ssh('systemctl is-active agentpair-navigator.service nginx.service agentpair-reaper.timer agentpair-certbot.timer').decode().strip())

if __name__=='__main__':main()
