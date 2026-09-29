"""One small HTTPS task to verify Navigator does not rent a Driver unnecessarily."""
import http.cookiejar
import json
from pathlib import Path
import time
import urllib.request

BASE='https://50.118.187.180'
ROOT=Path(__file__).resolve().parent
client=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
                                     urllib.request.ProxyHandler({}))

def api(path,data=None,csrf=''):
    request=urllib.request.Request(BASE+path,
        data=json.dumps(data).encode() if data is not None else None,
        headers={'Origin':BASE,'Content-Type':'application/json','X-CSRF-Token':csrf})
    with client.open(request,timeout=20) as response:return json.load(response)

def main():
    password=(ROOT/'runtime'/'persistent-navigator-password.txt').read_text().strip()
    csrf=api('/api/login',{'username':'admin','password':password})['csrf']
    task=api('/api/tasks',{'title':'持久Navigator验证：上海天气',
        'message':'查询中国上海目前温度，说明时间和来源；检查数据是否足够新。普通查询不要租用云端Driver。',
        'adapter':'discussion'},csrf)
    print('TASK_ID',task['id'],flush=True)
    for _ in range(75):
        task=api('/api/tasks/'+task['id'])
        if task['status'] not in ('queued','running','cancelling'):break
        time.sleep(2)
    print('STATUS',task['status'],flush=True)
    for e in task['events']:
        if e.get('kind') in ('resource_decision','stopped'):
            print('EVENT',json.dumps(e,ensure_ascii=False),flush=True)
    for m in task['messages']:
        if m.get('stage')=='review':
            print('REVIEW',json.dumps(m.get('answer',{}),ensure_ascii=False),flush=True)
    if task['status']!='completed':raise RuntimeError('Task not completed')
    if any(e.get('executionNode')=='ucloud_driver' for e in task['events']):
        raise RuntimeError('Unnecessary cloud Driver was created')

if __name__=='__main__':main()
