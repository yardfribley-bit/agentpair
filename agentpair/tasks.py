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
from .methods import method
from .collaboration import message as collaboration_message


class Conflict(ValueError): pass
class Limit(ValueError): pass


_PAUSE_STATUSES = {
    'missing_user_input': 'needs_information',
    'needs_information': 'needs_information',
    'unsupported_capability': 'unsupported_capability',
    'awaiting_confirmation': 'awaiting_confirmation',
}


def _blocking_reason(answer):
    """Only explicit external blockers stop rework; unknown checks can be repaired."""
    if not isinstance(answer, dict):
        return None
    decision = answer.get('decision', {})
    sources = [answer]
    if isinstance(decision, dict):
        sources.append(decision)
    for source in sources:
        reason = source.get('blockingReason')
        if (isinstance(reason, dict) and isinstance(reason.get('type'), str)
                and reason['type'] in _PAUSE_STATUSES):
            return copy.deepcopy(reason)
        if source.get('requiresUserInput') is True:
            return {'type': 'missing_user_input'}
    # decide() also emits needs_information for unknown evidence checks. That
    # action alone does not say that another Driver attempt is impossible.
    action = decision.get('action') if isinstance(decision, dict) else None
    if action in ('unsupported_capability', 'awaiting_confirmation'):
        return {'type': action}
    return None


def _plan_blocking_reason(plan, task):
    reason = _blocking_reason(plan)
    if reason:
        return reason
    tool = plan.get('tool', {}) if isinstance(plan, dict) else {}
    if (isinstance(tool, dict) and tool.get('name') == 'none'
            and plan.get('executionMode') == 'cloud_driver'
            and task.get('engineeringMethod', 'local') == 'local'):
        return {'type': 'unsupported_capability',
                'requiredExecutionMode': 'cloud_driver',
                'engineeringMethod': 'local',
                'executionProfile': task.get('executionProfile', 'none'),
                'message': '本轮计划需要独立云端 Driver，当前任务采用本地协作，无法执行该计划。'}
    return None


def _review_has_unresolved_gap(review):
    """A passing prose review cannot override explicit missing-evidence claims."""
    if not isinstance(review, dict):
        return True
    # Evidence boundaries are not automatically missing task requirements.
    # A structured, complete acceptance decision takes precedence over words
    # such as "missing DNS" appearing in a correctly scoped snapshot report.
    if review.get('blockingGaps'):
        return True
    decision=review.get('decision',{})
    checks=decision.get('checks',[]) if isinstance(decision,dict) else []
    required={'goal_met','grounded','consistent','delivery','readable_answer'}
    accepted={c.get('id') for c in checks if isinstance(c,dict) and c.get('value')=='yes'}
    if (isinstance(decision,dict) and decision.get('action')=='deliver'
            and required.issubset(accepted)
            and all(isinstance(c,dict) and c.get('value')=='yes' for c in checks)):
        return False
    text = json.dumps(review, ensure_ascii=False)
    gap_markers = ('缺少', '缺失', '证据不足', '无法判断', '不能判断', '尚未', '未采集',
                   '未提供', '需要进一步', '待补', 'not enough evidence', 'cannot determine')
    return any(marker in text for marker in gap_markers)


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


class TaskEngine:
    def __init__(self, database, backend, adapters=('discussion', 'public_site'), budget=0.90,
                 max_rounds=3, deadline=600, start=True):
        self.db = str(database)
        self.backend = backend
        self.backend.event_callback=self.record_method_event
        self.adapters = tuple(adapters)
        self.budget = budget
        self.max_rounds = max_rounds
        # Rework is separate from user conversation rounds. Keep a small bound
        # so an inconclusive review cannot silently loop forever.
        self.max_rework_attempts = 2
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
        from .operations import project
        with self.lock: task=self._load(tid)
        task['operations']=project(task)
        return task

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

    def record_method_event(self,tid,event):
        with self.lock:
            task=self._load(tid)
            task['events'].append(dict(event,at=now(),round=task['round']))
            self._save(task)

    def create(self, title, message, adapter='discussion', target='', engineering_method='local', execution_profile='none', billing_owner=None, owner=None, security_evidence=None):
        method(engineering_method)
        if execution_profile not in ('none','python','node','browser','native'): raise ValueError('Unknown execution profile')
        if execution_profile!='none' and engineering_method!='pair':
            raise ValueError('Code execution requires an explicitly selected single cloud Driver')
        if adapter not in self.adapters: raise ValueError('Unknown task adapter')
        if not isinstance(title, str) or not 1<=len(title.strip())<=120: raise ValueError('Invalid title')
        message = self._message(message)
        if adapter == 'public_site':
            from .site_probe import validate_target
            target = validate_target(target)
        elif target: raise ValueError('Discussion tasks do not accept a target')
        with self.lock:
            self._check_active_capacity()
            task = {'id': uuid.uuid4().hex, 'title': title.strip(), 'adapter': adapter, 'target': target,
                    'engineeringMethod':engineering_method, 'billingOwner':billing_owner, 'owner':owner or billing_owner,
                    'executionProfile':execution_profile,
                    'status': 'queued', 'round': 1, 'maxRounds': self.max_rounds,
                    'messages': [{'role':'user','text':message,'round':1,'at':now()}],
                    'events': [], 'results': [], 'createdAt': now(), 'updatedAt': now(),
                    'budgetMode': 'historical_estimate_not_hard_cap'}
            if security_evidence is not None:
                task['securityEvidence']=copy.deepcopy(security_evidence)
            self._save(task); self.jobs.put(task['id'])
            return copy.deepcopy(task)

    def _check_active_capacity(self):
        if sum(t['status'] in ('queued','running','cancelling') for t in self.list())>=20:
            raise Limit('当前已有 20 个任务正在排队或运行，请等待任务完成后再提交。历史任务数量不受限制。')

    def followup(self, tid, message):
        message = self._message(message)
        with self.lock:
            task = self._load(tid)
            if task['status'] in ('queued','running','cancelling'): raise Conflict('Wait for the current round or cancel it')
            if task['round']>=self.max_rounds and not getattr(self, 'platform_assistant', None):
                raise Limit('Round limit reached')
            self._check_active_capacity()
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
            if self.budget is not None and used+estimate>self.budget: raise Limit('Experiment model estimate budget exhausted')
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
            task['status']='running'; task.pop('blockingReason', None); self._save(task)
        deadline = time.monotonic()+(max(self.deadline,1800) if task.get('engineeringMethod')=='parallel' else self.deadline)
        outputs = {}
        try:
            stages=[('plan','navigator'),('driver','driver'),('review','navigator')]
            attempts=0
            blocking_reason=None
            for stage, role in stages:
                self._guard(tid, deadline)
                job_id=uuid.uuid4().hex
                with self.lock:
                    task = self._load(tid)
                    task['events'].append({'at':now(),'round':task['round'],'kind':'stage_started','role':role,'stage':stage,'jobId':job_id})
                    self._save(task)
                envelope = {'mode':stage, 'task':{k:task[k] for k in ('title','adapter','target','round')},
                            'history':task['messages'], 'outputs':outputs}
                envelope['task'].update(id=tid,jobId=job_id,engineeringMethod=task.get('engineeringMethod','local'),executionProfile=task.get('executionProfile','none'))
                cloud_workflow = getattr(self, 'cloud_workflow', None)
                platform_assistant = getattr(self, 'platform_assistant', None)
                if platform_assistant and not task.get('securityEvidence'):
                    envelope['task']['platformCapabilities'] = platform_assistant.capabilities()
                    if task.get('platformResult'):
                        envelope['task']['platformContext'] = task['platformResult']
                    if task.get('cloudAction'):
                        a=task['cloudAction']
                        envelope['task']['machineContext']={k:a.get(k) for k in ('leaseId','state','address','expiresAt','system','finalAnswer')}
                if cloud_workflow and not task.get('securityEvidence'):
                    envelope['task']['cloudCapabilities'] = cloud_workflow.capabilities()
                if task.get('securityEvidence'):
                    envelope['task']['securityEvidence']=task['securityEvidence']
                estimate = self.backend.estimate(envelope)
                if task.get('billingOwner'):
                    account_store=getattr(self,'accounts',None)
                    if account_store is None:raise Limit('Account billing unavailable')
                    try:account_store.reserve(task['billingOwner'],estimate)
                    except ValueError as error:raise Limit(str(error)) from error
                self._reserve(estimate)
                with self.lock:
                    task = self._load(tid)
                    source = {'plan':'user','driver':'navigator','review':'driver'}[stage]
                    source_text = (task['messages'][-1].get('text','') if stage=='plan'
                                   else outputs['plan' if stage=='driver' else 'driver']['answer'].get('summary',''))
                    packet=collaboration_message(source,role,'assignment',source_text,
                        task_id=tid,round_number=task['round'],phase=stage,
                        content=({'request':source_text} if stage=='plan' else
                                 outputs['plan' if stage=='driver' else 'driver']['answer']))
                    task['events'].append({'at':now(),'round':task['round'],'kind':'handoff_requested',
                        'from':source,'to':role,'stage':stage,'summary':source_text[:240],'jobId':job_id,
                        'message':packet,
                        'note':'Context passed to backend; not a network receipt confirmation.'})
                    self._save(task)
                envelope['handoff']=packet
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
                    task['messages'].append({'role':role,'stage':stage,'round':task['round'],'jobId':job_id,
                                             'at':now(),'answer':answer['answer'], 'usage':answer.get('usage'),'model':answer.get('model')})
                    task['events'].append({'at':now(),'round':task['round'],'kind':'stage_completed','role':role,'stage':stage,
                        'jobId':job_id,'messageId':packet['id'],'summary':str(answer['answer'].get('summary',''))[:500]})
                    if stage=='driver' and answer.get('resourceDecision'):
                        task['events'].append({'at':now(),'round':task['round'],'kind':'resource_decision',
                            'text':answer['resourceDecision'],'executionNode':answer.get('executionNode'),
                            'leaseId':answer.get('leaseId')})
                    if answer.get('evidence'):
                        evidence=answer['evidence']
                        evidence_text='已返回查询证据，可展开检查来源与时间'
                        if evidence.get('tool')=='github_repository':
                            evidence_text=('源码读取失败，不能视为已验证' if evidence.get('error') else
                                f"GitHub 源码：提交 {evidence.get('commit','')[:12]}，已读取 {len(evidence.get('files',[]))} 个文件，未读 {len(evidence.get('omitted',[]))} 个指定文件；GitIngest 已整理带行号上下文")
                        task['events'].append({'at':now(),'round':task['round'],'kind':'tool_result','stage':stage,
                            'role':role,'jobId':job_id,'text':evidence_text,'evidence':evidence})
                    self._save(task)
                if stage == 'plan' and platform_assistant and platform_assistant.prepare(tid, answer['answer'], outputs):
                    return
                if stage == 'plan' and cloud_workflow and cloud_workflow.prepare(tid, answer['answer'], outputs):
                    return
                blocking_reason = (_plan_blocking_reason(answer['answer'], task) if stage == 'plan'
                                   else _blocking_reason(answer['answer']))
                if blocking_reason:
                    with self.lock:
                        task = self._load(tid)
                        task['events'].append({'at': now(), 'round': task['round'], 'kind': 'paused',
                            'stage': stage, 'reason': blocking_reason,
                            'text': blocking_reason.get('message') or {
                                'needs_information': '本轮需要补充用户信息，已暂停执行。',
                                'unsupported_capability': '当前执行能力不足，已暂停执行。',
                                'awaiting_confirmation': '本轮等待确认，已暂停执行。',
                            }[_PAUSE_STATUSES[blocking_reason['type']]]})
                        self._save(task)
                    break
                if stage=='review':
                    review=answer['answer']
                    verdict=review.get('verdict')
                    if verdict == 'pass' and _review_has_unresolved_gap(review):
                        verdict = 'retry'
                        review['verdict'] = 'retry'
                        review.setdefault('nextSteps', []).append('补齐复核中明确指出的必需证据后再验收')
                        answer['answer'] = review
                        with self.lock:
                            task=self._load(tid)
                            task['events'].append({'at':now(),'round':task['round'],
                                'kind':'acceptance_guard','text':'Negative 明确发现证据缺口，禁止将任务标记为 completed。'})
                            self._save(task)
                    corrections=review.get('corrections',[])
                    next_steps=review.get('nextSteps',[])
                    if isinstance(corrections,str): corrections=[corrections]
                    if isinstance(next_steps,str): next_steps=[next_steps]
                    if not isinstance(corrections,list): corrections=[]
                    if not isinstance(next_steps,list): next_steps=[]
                    actionable=[item.strip() for item in corrections+next_steps
                                if isinstance(item,str) and item.strip()]
                    if verdict=='retry' and not actionable:
                        actionable=['根据验收意见补足证据并重新交付；避免重复已尝试步骤']
                    # A low-confidence/unknown review is not automatically a
                    # dead end: if Navigator identified concrete gaps, let the
                    # Driver address them, then have Navigator review again.
                    # Hard bounds, cancellation, deadline, and budget still apply.
                    if ((verdict=='retry' or (verdict=='blocked' and actionable))
                            and attempts<self.max_rework_attempts):
                        attempts+=1
                        stages.extend([('driver','driver'),('review','navigator')])
                        with self.lock:
                            task=self._load(tid)
                            task['events'].append({'at':now(),'round':task['round'],'kind':'rework',
                                'attempt':attempts,'maxAttempts':self.max_rework_attempts,
                                'text':f'验收未通过（{verdict}），第 {attempts}/{self.max_rework_attempts} 次交回 Driver 按验收意见补正：'
                                       +json.dumps(actionable,ensure_ascii=False)})
                            self._save(task)
            with self.lock:
                task = self._load(tid)
                if task['status']=='cancelling': raise InterruptedError('Cancelled')
                task['results'].append({'round':task['round'],'outputs':outputs})
                if blocking_reason:
                    task['blockingReason'] = blocking_reason
                    task['status'] = _PAUSE_STATUSES[blocking_reason['type']]
                else:
                    final_review=outputs['review']['answer']
                    if final_review.get('verdict') == 'pass' and _review_has_unresolved_gap(final_review):
                        final_review['verdict']='retry'
                    final_verdict=final_review.get('verdict')
                    task['status']=('completed' if final_verdict=='pass' else
                                    'needs_more_evidence' if final_verdict=='retry' else 'blocked')
                self._save(task)
        except Exception as error:
            with self.lock:
                task = self._load(tid)
                task['status']='cancelled' if isinstance(error,InterruptedError) else 'failed'
                task['events'].append({'at':now(),'round':task['round'],'kind':'stopped',
                                       'errorType':type(error).__name__,'text':str(error)[:160]})
                self._save(task)

    def _loop(self):
        while not self.stop.is_set():
            try: tid = self.jobs.get(timeout=0.2)
            except queue.Empty: continue
            self.process(tid); self.jobs.task_done()

    def close(self):
        self.stop.set()
        if self.thread: self.thread.join(timeout=1)
