import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from agentpair.tasks import TaskEngine
from agentpair.transport import CloudDriverBackend,NodeBackend
from agentpair.collaboration import PROTOCOL, message, validate
from test_tasks import FakeBackend

class MethodTests(unittest.TestCase):
    def test_collaboration_envelope_has_identity_and_recipient_validation(self):
        packet=message('Driver A','Driver B','finding','check this',task_id='t',round_number=1,phase='review_peer',evidence_refs=['G001'])
        self.assertEqual(packet['protocol'],PROTOCOL)
        self.assertEqual(packet['evidenceRefs'],['G001'])
        self.assertIs(validate(packet,task_id='t',recipient='Driver B'),packet)
        with self.assertRaises(ValueError):validate(packet,task_id='other')
        with self.assertRaises(ValueError):validate(packet,recipient='Driver A')
    def backend(self):
        return CloudDriverBackend('test',None,'key','known','public','fw','.')
    def test_default_never_provisions_despite_model_request(self):
        b=self.backend();env={'task':{},'outputs':{'plan':{'answer':{'executionMode':'cloud_driver'}}}}
        with patch('agentpair.transport.run',return_value={'answer':{}}),patch.object(b,'_provision') as create:
            b.call('driver',env,60);create.assert_not_called()
    def test_parallel_distinct_nodes_and_concurrent_execution(self):
        b=self.backend();seen=[];barrier=threading.Barrier(2);phases=[];events=[]
        b.event_callback=lambda tid,event:events.append(event)
        def provision(excluded):
            seen.append(list(excluded));b.lease_id='lease'+str(len(seen));return 'node'+str(len(seen)),True
        def call(worker,role,env,timeout):
            barrier.wait(timeout=2)
            phases.append((env['task']['branch'],env['task']['collaborationPhase'],
                           bool(env['task'].get('peerResult')),bool(env['task'].get('peerFeedback'))))
            return {'answer':{'summary':env['task']['branch']+' '+env['task']['collaborationPhase']},
                    'usage':{'prompt_tokens':3,'completion_tokens':2,'total_tokens':5}}
        env={'task':{'id':'t','engineeringMethod':'parallel'},'outputs':{'plan':{'answer':{'approaches':['方案1','方案2']}}}}
        with patch.object(b,'_provision',side_effect=provision),patch.object(b,'_deploy_worker'),patch.object(NodeBackend,'call',call):
            result=b.call('driver',env,60)
        self.assertEqual(seen,[[],['lease1']]);self.assertEqual(len(result['answer']['branches']),2)
        self.assertEqual(result['usage']['total_tokens'],30)
        self.assertEqual(sorted(phase for _,phase,_,_ in phases),['explore','explore','review_peer','review_peer','revise','revise'])
        self.assertTrue(all(peer for _,phase,peer,_ in phases if phase!='explore'))
        self.assertTrue(all(feedback for _,phase,_,feedback in phases if phase=='revise'))
        branches=result['answer']['branches']
        self.assertEqual(branches['Driver A']['peerReview']['result']['answer']['summary'],'Driver B review_peer')
        handoffs=[event for event in events if event['kind']=='branch_handoff']
        self.assertEqual(len(handoffs),4)
        self.assertEqual({(e['role'],e['to']) for e in handoffs},
                         {('Driver A','Driver B'),('Driver B','Driver A')})
        self.assertEqual(len({e['message']['id'] for e in handoffs}),4)
        processed=[event for event in events if event['kind']=='branch_handoff_processed']
        self.assertEqual({e['message']['id'] for e in processed},
                         {e['message']['id'] for e in handoffs})
    def test_parallel_failed_review_does_not_claim_collaboration_complete(self):
        b=self.backend();count=[0]
        def provision(excluded):
            count[0]+=1;b.lease_id='lease'+str(count[0]);return 'node'+str(count[0]),True
        def call(worker,role,env,timeout):
            if env['task']['collaborationPhase']=='review_peer' and env['task']['branch']=='Driver B':
                raise RuntimeError('review unavailable')
            return {'answer':{'summary':'observed'},'usage':{'prompt_tokens':1,'completion_tokens':1,'total_tokens':2}}
        env={'task':{'id':'t','engineeringMethod':'parallel'},'outputs':{'plan':{'answer':{'approaches':['A','B']}}}}
        with patch.object(b,'_provision',side_effect=provision),patch.object(b,'_deploy_worker'),patch.object(NodeBackend,'call',call):
            result=b.call('driver',env,60)
        self.assertTrue(all(branch['status']=='failed' for branch in result['answer']['branches'].values()))
        self.assertIn('未完成',result['answer']['summary'])
    def test_method_persists_and_unknown_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            e=TaskEngine(Path(d)/'tasks.db',FakeBackend(),start=False)
            try:
                with self.assertRaises(ValueError):e.create('x','x',engineering_method='invented')
                task=e.create('x','x',engineering_method='parallel');e.process(task['id'])
                self.assertEqual(e.backend.calls[0][1]['task']['engineeringMethod'],'parallel')
                self.assertEqual(e.get(task['id'])['engineeringMethod'],'parallel')
            finally:e.close()
