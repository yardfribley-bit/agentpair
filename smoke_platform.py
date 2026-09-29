"""Two real conversation rounds through authenticated API; no generated code execution."""
import http.cookiejar
import json
from pathlib import Path
import time
import sys
import urllib.request

ROOT=Path(__file__).resolve().parent
BASE='http://127.0.0.1:18080'
client=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
csrf=''

def call(path,data=None):
    req=urllib.request.Request(BASE+path,data=json.dumps(data).encode() if data is not None else None,
        headers={'Origin':BASE,'Content-Type':'application/json','X-CSRF-Token':csrf})
    with client.open(req,timeout=12) as response: return json.load(response)

def wait(tid):
    previous=None
    for _ in range(150):
        task=call('/api/tasks/'+tid)
        state=(task['status'],len(task['messages']))
        if state!=previous:
            print('ROUND',task['round'],'STATUS',task['status'],'MESSAGES',len(task['messages']),flush=True); previous=state
        if task['status'] not in ('queued','running','cancelling'): return task
        time.sleep(2)
    raise TimeoutError('API experiment timeout')

csrf=call('/api/login',{'password':(ROOT/'runtime'/'workspace-password.txt').read_text().strip()})['csrf']
if '--inspect' in sys.argv:
    print('TASKS',json.dumps(call('/api/tasks')['items'],ensure_ascii=False))
    print('BUDGET',json.dumps(call('/api/session')['budget']))
    sys.exit(0)
if len(sys.argv)>1:
    task=call('/api/tasks/'+sys.argv[1]+'/messages',{'message':'SSH交接已修复。请重试上一轮的stable_unique任务，先支持可哈希元素，保持顺序，提供代码但不要声称已运行测试。'})
else:
    task=call('/api/tasks',{'title':'结队编程实测：稳定去重函数', 'adapter':'discussion',
        'message':'请结队编写Python函数stable_unique，保持输入顺序，先支持可哈希元素。Navigator先规划，Driver给代码，Navigator复核边界条件。没有执行器，不要声称测试已运行。'})
print('TASK_ID',task['id'],flush=True)
task=wait(task['id'])
if task['status']!='completed':
    print('STOP_EVENTS',json.dumps(task['events'][-2:],ensure_ascii=False),flush=True); raise RuntimeError('First round failed')
task=call('/api/tasks/'+task['id']+'/messages',{'message':'继续修改上一轮stable_unique：现在要支持列表和字典这样的不可哈希元素。保留顺序，按相等性去重，请解释与上一轮实现相比为什么要改，给出新版代码和复杂度。仍不要声称执行过测试。'})
task=wait(task['id'])
(ROOT/'runtime'/'live-task.json').write_text(json.dumps(task,ensure_ascii=False,indent=2))
print('FINAL',task['status'],'ROUNDS',len(task['results']),flush=True)
print('USAGE',json.dumps(call('/api/session')['budget']),flush=True)
for m in task['messages']:
    if m.get('answer'):
        print('ANSWER',m['round'],m['role'],m['stage'],json.dumps(m['answer'],ensure_ascii=False),flush=True)
if task['status']!='completed' or len(task['results'])!=2: raise RuntimeError('Second round failed')
