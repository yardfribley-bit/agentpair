import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from agentpair.tasks import TaskEngine
from agentpair.transport import CloudDriverBackend,NodeBackend
from test_tasks import FakeBackend

class MethodTests(unittest.TestCase):
    def backend(self):
        return CloudDriverBackend('test',None,'key','known','public','fw','.')
    def test_default_never_provisions_despite_model_request(self):
        b=self.backend();env={'task':{},'outputs':{'plan':{'answer':{'executionMode':'cloud_driver'}}}}
        with patch('agentpair.transport.run',return_value={'answer':{}}),patch.object(b,'_provision') as create:
            b.call('driver',env,60);create.assert_not_called()
    def test_parallel_distinct_nodes_and_concurrent_execution(self):
        b=self.backend();seen=[];barrier=threading.Barrier(2)
        def provision(excluded):
            seen.append(list(excluded));b.lease_id='lease'+str(len(seen));return 'node'+str(len(seen)),True
        def call(worker,role,env,timeout):
            barrier.wait(timeout=2)
            return {'answer':{'summary':env['task']['approach']},'usage':{'prompt_tokens':3,'completion_tokens':2,'total_tokens':5}}
        env={'task':{'id':'t','engineeringMethod':'parallel'},'outputs':{'plan':{'answer':{'approaches':['方案1','方案2']}}}}
        with patch.object(b,'_provision',side_effect=provision),patch.object(b,'_deploy_worker'),patch.object(NodeBackend,'call',call):
            result=b.call('driver',env,60)
        self.assertEqual(seen,[[],['lease1']]);self.assertEqual(len(result['answer']['branches']),2)
        self.assertEqual(result['usage']['total_tokens'],10)
    def test_method_persists_and_unknown_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            e=TaskEngine(Path(d)/'tasks.db',FakeBackend(),start=False)
            try:
                with self.assertRaises(ValueError):e.create('x','x',engineering_method='invented')
                task=e.create('x','x',engineering_method='parallel');e.process(task['id'])
                self.assertEqual(e.backend.calls[0][1]['task']['engineeringMethod'],'parallel')
                self.assertEqual(e.get(task['id'])['engineeringMethod'],'parallel')
            finally:e.close()
