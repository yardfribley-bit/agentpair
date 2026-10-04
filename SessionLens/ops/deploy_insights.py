"""Deploy session insight UI through the existing AgentPair reverse proxy."""
import getpass,subprocess,os,json,shlex,io,tarfile
from pathlib import Path
password=getpass.getpass('SSH password: ')
def ssh(command,data=b''):
 r,w=os.pipe();os.write(w,(password+'\n').encode());os.close(w)
 try:
  p=subprocess.run(['sshpass','-d',str(r),'ssh','-o','StrictHostKeyChecking=yes','-o','UserKnownHostsFile=/private/tmp/vmess-50.118.187.180-known_hosts','-o','ConnectTimeout=10','root@50.118.187.180',command],pass_fds=(r,),input=data,capture_output=True,timeout=120)
 finally:os.close(r)
 if p.returncode:raise RuntimeError(p.stderr.decode()[-2500:])
 return p.stdout
if __name__=='__main__':
 print(ssh("systemctl cat agentpair-certbot.service; ls /opt; ls /root/.acme.sh/acme.sh 2>/dev/null; python3 -m pip --version").decode())
if __name__=='__main__':
 root=Path(__file__).resolve().parents[1]
 backup='/opt/agentpair-backups/session-insights-'+__import__('datetime').datetime.now().strftime('%Y%m%dT%H%M%S')
 ssh('mkdir -p '+backup+'; cp -a /etc/nginx/sites-enabled/agentpair /etc/nginx/sites-enabled/agentpair-domain '+backup+'/; cp /opt/agentpair/agentpair/web_assets/workspace.html '+backup+'/workspace.html')
 print('BACKUP',backup,flush=True)
 stream=io.BytesIO()
 with tarfile.open(fileobj=stream,mode='w') as bundle:
  for name in ['sessionlens/__init__.py','sessionlens/ui.py','sessionlens/ui.css']:bundle.add(root/name,arcname=name)
  for path in (root/'runtime/reports').glob('*/*.json'):
   if path.name in ('analysis.json','evidence.json'):bundle.add(path,arcname='runtime/reports/'+path.parent.name+'/'+path.name)
 ssh('install -d -m 0700 /opt/agentpair/sessionlens-ui; tar -xf - -C /opt/agentpair/sessionlens-ui',stream.getvalue())
 print(ssh("cd /opt/agentpair/sessionlens-ui && python3 -c \"from pathlib import Path; from sessionlens.ui import publish; publish(Path('runtime/reports'))\" && find runtime/reports -name '*.json' -exec chmod 600 {} + && install -d -m 0755 /var/www/agentpair-insights && cp runtime/reports/index.html runtime/reports/sessions.html /var/www/agentpair-insights/ && for d in runtime/reports/*/; do install -d -m 0755 /var/www/agentpair-insights/$(basename \"$d\"); install -m 0644 \"$d/report.html\" /var/www/agentpair-insights/$(basename \"$d\")/; done && echo UI_READY").decode(),flush=True)
 print(ssh('/opt/agentpair-certbot/bin/certbot certonly --webroot -w /var/www/html -d www.chuhaijian.com --cert-name agentpair-domain --non-interactive --agree-tos --register-unsafely-without-email').decode(),flush=True)
 location='''    location = /session-insights { return 301 /session-insights/; }
    location = /_session-insights-auth {
        internal;
        proxy_pass http://127.0.0.1:9090/api/devices/tasks;
        proxy_pass_request_body off;
        proxy_set_header Content-Length "";
        proxy_set_header Cookie $http_cookie;
    }
    location /session-insights/ {
        alias /var/www/agentpair-insights/;
        index index.html;
        autoindex off;
        add_header Cache-Control "no-store";
        add_header X-Content-Type-Options "nosniff";
    }
    location @session_insights_login { return 302 /?insights=login; }
'''
 domain='''server {
    listen 80;
    server_name www.chuhaijian.com;
    location /.well-known/acme-challenge/ { root /var/www/html; }
    location / { return 301 https://$host$request_uri; }
}
server {
    listen 443 ssl;
    server_name www.chuhaijian.com;
    ssl_certificate /etc/letsencrypt/live/agentpair-domain/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/agentpair-domain/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    client_max_body_size 2m;
'''+location+'''    location / {
        proxy_pass http://127.0.0.1:9090;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 180s;
    }
}
'''
 ssh('cat > /etc/nginx/sites-enabled/agentpair-domain',domain.encode())
 code="from pathlib import Path;p=Path('/etc/nginx/sites-enabled/agentpair');s=p.read_text();loc="+repr(location)+";s=s.replace('    location / {\\n        proxy_pass http://127.0.0.1:9090;',loc+'    location / {\\n        proxy_pass http://127.0.0.1:9090;') if '/session-insights/' not in s else s;p.write_text(s)"
 ssh('python3 -c '+shlex.quote(code))
 try:ssh('nginx -t && systemctl reload nginx')
 except Exception:
  ssh('cp '+backup+'/agentpair '+backup+'/agentpair-domain /etc/nginx/sites-enabled/; nginx -t && systemctl reload nginx');raise
 code="from pathlib import Path;p=Path('/opt/agentpair/agentpair/web_assets/workspace.html');s=p.read_text();s=s.replace('</nav>','<a href=\"/session-insights/\">▥ <span>会话洞察</span></a></nav>',1) if '/session-insights/' not in s else s;p.write_text(s)"
 ssh('python3 -c '+shlex.quote(code))
 print(ssh("curl -s -o /dev/null -w 'ANONYMOUS_STATUS=%{http_code}\\n' https://www.chuhaijian.com/session-insights/; curl -fsS https://www.chuhaijian.com/ >/dev/null; systemctl is-active agentpair-navigator.service").decode(),flush=True)
