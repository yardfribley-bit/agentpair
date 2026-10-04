import sys,json,shlex
sys.path.insert(0,__import__('os').path.dirname(__file__))
from deploy_insights import ssh
fix="from pathlib import Path;p=Path('/etc/nginx/sites-enabled/agentpair-domain');s=p.read_text();s=s.replace('    location / {\\n        proxy_pass', '    set $agentpair_origin $http_origin;\\n    if ($http_origin = https://www.chuhaijian.com) { set $agentpair_origin https://50.118.187.180; }\\n    location / {\\n        proxy_set_header Origin $agentpair_origin;\\n        proxy_pass');p.write_text(s)"
ssh('python3 -c '+shlex.quote(fix))
ssh('nginx -t && systemctl reload nginx')
code='''import json,urllib.request,http.cookiejar
from pathlib import Path
config=json.loads(Path('/etc/agentpair/private.json').read_text())
jar=http.cookiejar.CookieJar();opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
request=urllib.request.Request('https://www.chuhaijian.com/api/login',data=json.dumps({'username':config.get('username','admin'),'password':config['password']}).encode(),headers={'Content-Type':'application/json','Origin':'https://www.chuhaijian.com'})
response=opener.open(request);print('LOGIN',response.status)
for path in ['/session-insights/','/session-insights/sessions.html','/session-insights/01a0f6e4-d305-7cb2-acb9-49ff46c94a09/report.html']:
 response=opener.open('https://www.chuhaijian.com'+path);body=response.read().decode();print(path,response.status,'AgentPair' in body,'/session-insights/' in body)
'''
print(ssh('python3 -c '+shlex.quote(code)).decode())
print(ssh("systemctl is-enabled agentpair-certbot.timer; curl -s -o /dev/null -w 'HTTP_REDIRECT=%{redirect_url}\\n' http://www.chuhaijian.com/session-insights/; curl -s -o /dev/null -w 'RAW_JSON_STATUS=%{http_code}\\n' https://www.chuhaijian.com/session-insights/01a0f6e4-d305-7cb2-acb9-49ff46c94a09/analysis.json").decode())
