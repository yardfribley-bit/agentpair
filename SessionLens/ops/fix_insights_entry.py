import sys,shlex
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parent))
from deploy_insights import ssh
print(ssh("curl -sI https://www.chuhaijian.com/session-insights/ | head -8; curl -fsS https://www.chuhaijian.com/ | grep -o 'href=\"/session-insights/\"'; nginx -T 2>/dev/null | grep -A1 'location @session_insights_login'").decode())
code='''from pathlib import Path
import shutil,time
backup=Path('/opt/agentpair-backups/insights-entry-'+str(int(time.time())));backup.mkdir()
for name in ['agentpair-domain','agentpair']:
 p=Path('/etc/nginx/sites-enabled')/name;shutil.copy2(p,backup/name)
 s=p.read_text().replace('location @session_insights_login { return 302 /; }','location @session_insights_login { return 302 /?insights=login; }');p.write_text(s)
p=Path('/opt/agentpair/agentpair/web_assets/workspace.html');shutil.copy2(p,backup/'workspace.html');s=p.read_text()
script="""<script id="session-insights-login-return">
(()=>{
if(new URLSearchParams(location.search).get('insights')!=='login')return;
const setup=()=>{
const login=document.getElementById('login');
if(login){login.classList.remove('hidden');login.scrollIntoView({block:'center'});}
const hint=document.createElement('p');hint.textContent='查看会话洞察需要登录。登录成功后将自动进入。';hint.style.color='#3157a5';login?.prepend(hint);
let stopped=false;
const check=async()=>{if(stopped)return;try{const r=await fetch('/api/session',{credentials:'same-origin',cache:'no-store'});if(!r.ok)return;const data=await r.json();if(data.csrf){stopped=true;location.replace('/session-insights/');}}catch(_){}};
check();const timer=setInterval(()=>{if(stopped)clearInterval(timer);else check();},1500);
};
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',setup);else setup();
})();
</script>"""
if 'id="session-insights-login-return"' not in s:s=s.replace('</body>',script+'</body>') if '</body>' in s else s+script
p.write_text(s)
print('BACKUP',backup)
'''
print(ssh('python3 -c '+shlex.quote(code)).decode())
print(ssh("nginx -t && systemctl reload nginx && curl -sI https://www.chuhaijian.com/session-insights/ | head -8; curl -fsS 'https://www.chuhaijian.com/?insights=login' | grep -o 'session-insights-login-return'").decode())
