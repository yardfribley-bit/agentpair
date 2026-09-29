"""Persistent task conversations; adapters and transport are injected."""
import copy
from contextlib import closing, contextmanager
import datetime
import json
import queue
import sqlite3
import threading
import time
import uuid


class Conflict(ValueError): pass
class Limit(ValueError): pass


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


class TaskEngine:
    def __init__(self, database, backend, adapters=('discussion', 'public_site'), budget=0.90,
                 max_rounds=3, deadline=600, start=True):
        self.db = str(database)
        self.backend = backend
        self.adapters = tuple(adapters)
        self.budget = budget
        self.max_rounds = max_rounds
        self.deadline = deadline
        self.lock = threading.RLock()
        self.jobs = queue.Queue()
        self.stop = threading.Event()
        historical=[]
        with self.connection() as db:
            db.execute('CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, data TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS ledger (id INTEGER PRIMARY KEY, reserved REAL NOT NULL)')
            db.execute('INSERT OR IGNORE INTO ledger VALUES (1,0)')
            db.execute('CREATE TABLE IF NOT EXISTS credits (id TEXT PRIMARY KEY, amount REAL NOT NULL)')
            for tid, raw in db.execute('SELECT id,data FROM tasks').fetchall():
                task = json.loads(raw)
                for result in task.get('results',[]):
                    for stage, output in result.get('outputs',{}).items():
                        if output.get('budgetMode')=='historical_estimate_not_hard_cap':
                            historical.append((output.get('settlementKey',f"{tid}:{result['round']}:{stage}"),output.get('estimatedUpperCostCNY')))
                if task['status'] in ('queued', 'running', 'cancelling'):
                    task['status'] = 'interrupted'
                    task['events'].append({'at': now(), 'kind': 'interrupted', 'text': '服务重启，未自动重放付费调用。'})
                    db.execute('UPDATE tasks SET data=? WHERE id=?', (json.dumps(task), tid))
        # Settle known historical-price upper bounds from successful replies.
        # Unknown/failed requests keep their entire upfront reservation.
        for key, cost in historical:
            self._settle(key,0.10,cost)
        self.thread = None
        if start:
            self.thread = threading.Thread(target=self._loop, daemon=True)
            self.thread.start()

    @contextmanager
    def connection(self):
        with closing(sqlite3.connect(self.db)) as db:
            with db:
                yield db

    def _load(self, tid):
        with self.connection() as db:
            row = db.execute('SELECT data FROM tasks WHERE id=?', (tid,)).fetchone()
        if not row: raise KeyError('Task not found')
        return json.loads(row[0])

    def _save(self, task):
        task['updatedAt'] = now()
        with self.connection() as db:
            db.execute('INSERT OR REPLACE INTO tasks VALUES (?,?)', (task['id'], json.dumps(task, ensure_ascii=False)))

    def get(self, tid):
        with self.lock: return self._load(tid)

    def list(self):
        with self.lock, self.connection() as db:
            tasks = [json.loads(row[0]) for row in db.execute('SELECT data FROM tasks')]
        return sorted([{k: t[k] for k in ('id','title','adapter','target','status','round','updatedAt')}
                       for t in tasks], key=lambda t: t['updatedAt'], reverse=True)

    @staticmethod
    def _message(text):
        if not isinstance(text, str) or not text.strip() or len(text)>6000:
            raise ValueError('Message must contain 1–6000 characters')
        return text.strip()

    def create(self, title, message, adapter='discussion', target=''):
        if adapter not in self.adapters: raise ValueError('Unknown task adapter')
        if not isinstance(title, str) or not 1<=len(title.strip())<=120: raise ValueError('Invalid title')
        message = self._message(message)
        if adapter == 'public_site':
            from .site_probe import validate_target
            target = validate_target(target)
        elif target: raise ValueError('Discussion tasks do not accept a target')
        with self.lock:
            if len(self.list())>=20: raise Limit('Experiment task limit reached')
            task = {'id': uuid.uuid4().hex, 'title': title.strip(), 'adapter': adapter, 'target': target,
                    'status': 'queued', 'round': 1, 'maxRounds': self.max_rounds,
                    'messages': [{'role':'user','text':message,'round':1,'at':now()}],
                    'events': [], 'results': [], 'createdAt': now(), 'updatedAt': now(),
                    'budgetMode': 'historical_estimate_not_hard_cap'}
            self._save(task); self.jobs.put(task['id'])
            return copy.deepcopy(task)

    def followup(self, tid, message):
        message = self._message(message)
        with self.lock:
            task = self._load(tid)
            if task['status'] in ('queued','running','cancelling'): raise Conflict('Wait for the current round or cancel it')
            if task['round']>=self.max_rounds: raise Limit('Round limit reached')
            task['round'] += 1; task['status'] = 'queued'
            task['messages'].append({'role':'user','text':message,'round':task['round'],'at':now()})
            self._save(task); self.jobs.put(tid)
            return task

    def cancel(self, tid):
        with self.lock:
            task = self._load(tid)
            if task['status'] == 'running': task['status'] = 'cancelling'
            elif task['status'] == 'queued': task['status'] = 'cancelled'
            else: return task
            task['events'].append({'at':now(),'kind':'cancel','text':'取消请求已记录；在途请求可能仍产生费用。'})
            self._save(task); return task

    def _reserve(self, estimate):
        if not isinstance(estimate, (int,float)) or not 0<estimate<=0.30:
            raise Limit('Invalid or excessive request estimate')
        with self.lock, self.connection() as db:
            used = db.execute('SELECT reserved FROM ledger WHERE id=1').fetchone()[0]
            if used+estimate>self.budget: raise Limit('Experiment model estimate budget exhausted')
            db.execute('UPDATE ledger SET reserved=? WHERE id=1', (used+estimate,))
        return estimate

    def _settle(self, key, reserved, cost):
        if not isinstance(cost,(int,float)) or not 0<cost<=reserved: return
        credit=reserved-cost
        with self.lock, self.connection() as db:
            used=db.execute('SELECT reserved FROM ledger WHERE id=1').fetchone()[0]
            if used+1e-9<credit: return
            added=db.execute('INSERT OR IGNORE INTO credits VALUES (?,?)',(key,credit)).rowcount
            if added: db.execute('UPDATE ledger SET reserved=? WHERE id=1',(max(0,used-credit),))

    def usage(self):
        with self.lock, self.connection() as db:
            used = db.execute('SELECT reserved FROM ledger WHERE id=1').fetchone()[0]
        return {'estimatedReservedCNY': used, 'estimatedLimitCNY': self.budget,
                'budgetMode':'historical_estimate_not_hard_cap'}

    def _guard(self, tid, deadline):
        with self.lock:
            if self._load(tid)['status'] in ('cancelled','cancelling'): raise InterruptedError('Cancelled')
        if time.monotonic()>deadline: raise TimeoutError('Round deadline reached')

    def process(self, tid):
        with self.lock:
            task = self._load(tid)
            if task['status'] != 'queued': return
            task['status']='running'; self._save(task)
        deadline = time.monotonic()+self.deadline
        outputs = {}
        try:
            stages=[('plan','navigator'),('driver','driver'),('review','navigator')]
            attempts=0
            for stage, role in stages:
                self._guard(tid, deadline)
                with self.lock:
                    task = self._load(tid)
                    task['events'].append({'at':now(),'round':task['round'],'kind':'stage_started','role':role,'stage':stage})
                    self._save(task)
                envelope = {'mode':stage, 'task':{k:task[k] for k in ('title','adapter','target','round')},
                            'history':task['messages'], 'outputs':outputs}
                estimate = self.backend.estimate(envelope)
                self._reserve(estimate)
                with self.lock:
                    task = self._load(tid)
                    source = {'plan':'user','driver':'navigator','review':'driver'}[stage]
                    source_text = (task['messages'][-1].get('text','') if stage=='plan'
                                   else outputs['plan' if stage=='driver' else 'driver']['answer'].get('summary',''))
                    task['events'].append({'at':now(),'round':task['round'],'kind':'handoff_requested',
                        'from':source,'to':role,'stage':stage,'summary':source_text[:240],
                        'note':'Context passed to backend; not a network receipt confirmation.'})
                    self._save(task)
                answer = self.backend.call(role, envelope, max(1,deadline-time.monotonic()))
                if not isinstance(answer,dict) or not isinstance(answer.get('answer'),dict):
                    raise ValueError('Invalid worker response')
                if answer.get('budgetMode')=='historical_estimate_not_hard_cap':
                    answer['settlementKey']=f"{tid}:{task['round']}:{stage}"+(f':retry{attempts}' if attempts else '')
                    self._settle(answer['settlementKey'],estimate,answer.get('estimatedUpperCostCNY'))
                self._guard(tid, deadline)
                outputs[stage] = answer
                with self.lock:
                    task = self._load(tid)
                    task['messages'].append({'role':role,'stage':stage,'round':task['round'],
                                             'at':now(),'answer':answer['answer'], 'usage':answer.get('usage')})
                    task['events'].append({'at':now(),'round':task['round'],'kind':'stage_completed','role':role,'stage':stage})
                    if answer.get('evidence'):
                        task['events'].append({'at':now(),'round':task['round'],'kind':'tool_result','stage':stage,
                            'text':'已返回查询证据，可展开检查来源与时间','evidence':answer['evidence']})
                    self._save(task)
                if stage=='review' and answer['answer'].get('verdict')=='retry' and attempts<1:
                    attempts+=1
                    stages.extend([('driver','driver'),('review','navigator')])
                    with self.lock:
                        task=self._load(tid)
                        task['events'].append({'at':now(),'round':task['round'],'kind':'rework',
                            'text':'Navigator 未通过验收，交回 Driver 补查：'+str(answer['answer'].get('corrections',[]))})
                        self._save(task)
            with self.lock:
                task = self._load(tid)
                if task['status']=='cancelling': raise InterruptedError('Cancelled')
                task['results'].append({'round':task['round'],'outputs':outputs})
                task['status']='completed' if outputs['review']['answer'].get('verdict')=='pass' else 'blocked'
                self._save(task)
        except Exception as error:
            with self.lock:
                task = self._load(tid)
                task['status']='cancelled' if isinstance(error,InterruptedError) else 'failed'
                task['events'].append({'at':now(),'round':task['round'],'kind':'stopped',
                                       'errorType':type(error).__name__})
                self._save(task)

    def _loop(self):
        while not self.stop.is_set():
            try: tid = self.jobs.get(timeout=0.2)
            except queue.Empty: continue
            self.process(tid); self.jobs.task_done()

    def close(self):
        self.stop.set()
        if self.thread: self.thread.join(timeout=1)
