import tempfile
import unittest
from pathlib import Path
from agentpair.tasks import TaskEngine, Conflict, Limit


class FakeBackend:
    def __init__(self): self.calls=[]; self.on_call=None
    def estimate(self,envelope): return .10
    def call(self,role,envelope,timeout):
        self.calls.append((role,envelope))
        if self.on_call: self.on_call()
        return {'answer':{'summary':envelope['mode']+' round '+str(envelope['task']['round']),'verdict':'pass'},'usage':{'total_tokens':10}}


class TaskTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.path=Path(self.tmp.name)/'tasks.db'
        self.backend=FakeBackend(); self.engine=TaskEngine(self.path,self.backend,start=False)
    def tearDown(self): self.engine.close(); self.tmp.cleanup()
    def test_two_round_history_and_isolation(self):
        t=self.engine.create('first','initial goal'); other=self.engine.create('other','PRIVATE_OTHER')
        self.engine.process(t['id']); self.engine.followup(t['id'],'please refine'); self.engine.process(t['id'])
        done=self.engine.get(t['id']); self.assertEqual(done['status'],'completed'); self.assertEqual(len(done['results']),2)
        self.assertEqual(len(self.backend.calls),6)
        history=self.backend.calls[3][1]['history']
        self.assertEqual(history[0]['text'],'initial goal'); self.assertEqual(history[-1]['text'],'please refine')
        self.assertNotIn('PRIVATE_OTHER',str(history)); self.assertEqual(self.engine.get(other['id'])['status'],'queued')
    def test_more_than_twenty_tasks_can_be_created(self):
        tasks=[]
        for i in range(21):
            task=self.engine.create('task '+str(i),'goal');self.engine.cancel(task['id']);self.engine.process(task['id']);tasks.append(task)
        self.assertEqual(len(self.engine.list()),21)
        self.assertEqual(len({t['id'] for t in tasks}),21)

    def test_active_queue_is_bounded(self):
        for i in range(20):self.engine.create('task '+str(i),'goal')
        with self.assertRaises(Limit):self.engine.create('overflow','goal')

    def test_active_task_conflict(self):
        t=self.engine.create('task','goal')
        with self.assertRaises(Conflict): self.engine.followup(t['id'],'new goal')
    def test_review_requires_explicit_pass(self):
        original=self.backend.call
        def call(*args):
            result=original(*args);result['answer'].pop('verdict');return result
        self.backend.call=call
        t=self.engine.create('task','goal');self.engine.process(t['id'])
        self.assertEqual(self.engine.get(t['id'])['status'],'blocked')
    def test_review_requests_rework(self):
        original=self.backend.call
        def call(role,envelope,timeout):
            result=original(role,envelope,timeout)
            if envelope['mode']=='review' and len(self.backend.calls)==3: result['answer']['verdict']='retry'
            return result
        self.backend.call=call
        t=self.engine.create('task','goal');self.engine.process(t['id'])
        self.assertEqual(len(self.backend.calls),5)
        self.assertEqual(self.engine.get(t['id'])['status'],'completed')
    def test_blocked_review_with_corrections_gets_bounded_rework(self):
        original=self.backend.call
        def call(role,envelope,timeout):
            result=original(role,envelope,timeout)
            if envelope['mode']=='review':
                result['answer']['verdict']='blocked'
                result['answer']['corrections']=['Add evidence for the missing claim']
            return result
        self.backend.call=call
        t=self.engine.create('task','goal');self.engine.process(t['id'])
        done=self.engine.get(t['id'])
        self.assertEqual(len(self.backend.calls),7)
        self.assertEqual(done['status'],'blocked')
        reworks=[e for e in done['events'] if e['kind']=='rework']
        self.assertEqual([e['attempt'] for e in reworks],[1,2])

    def test_pass_with_explicit_evidence_gap_cannot_complete(self):
        class GapBackend(FakeBackend):
            def call(self, role, envelope, timeout):
                result=super().call(role,envelope,timeout)
                if envelope['mode']=='review':
                    result['answer'].update(verdict='pass',summary='应用存在，但无法判断网络行为',nextSteps=['采集网络连接'])
                return result
        from pathlib import Path
        engine=TaskEngine(Path(self.tmp.name)/'gap.db',GapBackend())
        task=engine.create('gap','analyze')
        import time
        for _ in range(50):
            if engine.get(task['id'])['status'] not in ('queued','running'): break
            time.sleep(.01)
        self.assertEqual(engine.get(task['id'])['status'],'needs_more_evidence')
        self.assertTrue(any(e['kind']=='acceptance_guard' for e in engine.get(task['id'])['events']))
        engine.close()
    def test_accepted_scope_limitations_are_not_blocking_gaps(self):
        from agentpair.tasks import _review_has_unresolved_gap
        review={'summary':'快照未提供 DNS；不证明数据外传',
                'decision':{'action':'deliver','checks':[
                    {'id':key,'value':'yes'} for key in
                    ('goal_met','grounded','consistent','delivery','readable_answer')]}}
        self.assertFalse(_review_has_unresolved_gap(review))
        review['blockingGaps']=['任务要求网络数据，但尚未采集']
        self.assertTrue(_review_has_unresolved_gap(review))
    def test_cancel_queued_no_calls(self):
        t=self.engine.create('task','goal'); self.engine.cancel(t['id']); self.engine.process(t['id'])
        self.assertEqual(self.backend.calls,[])
    def test_cancel_inflight_prevents_next_stage(self):
        t=self.engine.create('task','goal'); self.backend.on_call=lambda:self.engine.cancel(t['id'])
        self.engine.process(t['id']); self.assertEqual(len(self.backend.calls),1)
        self.assertEqual(self.engine.get(t['id'])['status'],'cancelled')
    def test_budget_persists_after_restart(self):
        t=self.engine.create('task','goal'); self.engine.process(t['id'])
        new=TaskEngine(self.path,self.backend,budget=.35,start=False)
        t2=new.create('next','goal'); new.process(t2['id'])
        self.assertEqual(new.get(t2['id'])['status'],'failed'); self.assertEqual(len(self.backend.calls),3); new.close()
    def test_restart_does_not_replay_paid_jobs(self):
        t=self.engine.create('task','goal'); new=TaskEngine(self.path,self.backend,start=False)
        self.assertEqual(new.get(t['id'])['status'],'interrupted'); self.assertTrue(new.jobs.empty()); new.close()
    def test_estimate_settlement_idempotent(self):
        self.engine._reserve(.10)
        self.engine._settle('test',.10,.02)
        self.engine._settle('test',.10,.02)
        self.assertAlmostEqual(self.engine.usage()['estimatedReservedCNY'],.02)
        self.engine._reserve(.10)
        self.assertAlmostEqual(self.engine.usage()['estimatedReservedCNY'],.12)
    def test_round_limit(self):
        t=self.engine.create('task','goal')
        for i in range(3):
            if i:self.engine.followup(t['id'],'more')
            self.engine.process(t['id'])
        with self.assertRaises(Limit): self.engine.followup(t['id'],'fourth')
    def test_ssrf_rejected(self):
        for target in ('http://127.0.0.1/','http://169.254.169.254/','http://10.0.0.1/','http://example.com/','http://102.68.79.149:22/'):
            with self.assertRaises(ValueError): self.engine.create('task','goal','public_site',target)


if __name__=='__main__': unittest.main()
