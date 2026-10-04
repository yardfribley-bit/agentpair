import sys,shlex
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parent))
from deploy_insights import ssh
code='''from pathlib import Path
import shutil,time
backup=Path('/opt/agentpair-backups/public-insights-'+str(int(time.time())));backup.mkdir()
for name in ['agentpair','agentpair-domain']:
 p=Path('/etc/nginx/sites-enabled')/name;shutil.copy2(p,backup/name)
 s=p.read_text().replace('        auth_request /_session-insights-auth;\\n','').replace('        error_page 401 = @session_insights_login;\\n','')
 p.write_text(s)
print('BACKUP',backup)
'''
print(ssh('python3 -c '+shlex.quote(code)).decode())
print(ssh('nginx -t && systemctl reload nginx').decode())
