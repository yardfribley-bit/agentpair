import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from agentpair.tasks import TaskEngine
from agentpair.platform_assistant import PlatformAssistant


class PlatformAssistantTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.engine = TaskEngine(Path(self.tmp.name)/'tasks.db', SimpleNamespace(), start=False)
        self.addCleanup(self.engine.close)
        self.seen = []
        def devices(owner):
            self.seen.append(owner)
            return [{'id':'device-1','name':'My Mac','online':True,'lastSeen':123}]
        self.devices = SimpleNamespace(list=devices, llm_data=lambda *a,**k:{'calls':[{}]},
            credential_threats=SimpleNamespace(inventory=lambda device:{'items':[{'id':'finding','label':'凭据进入输入'}]}))
        self.sessions = SimpleNamespace(sessions=lambda owner:[{'device':'device-1','session':'session-1','events':5}],
            report=lambda *a:{'events':[1,2], 'tools':[1], 'quality':{}})
        self.cloud = SimpleNamespace(list=lambda:[{'id':'lease','state':'released','password':'secret'}])
        self.tool = PlatformAssistant(self.engine,self.devices,self.sessions,self.cloud)

    def query(self, action, owner=None, **args):
        task=self.engine.create('平台查询','查看平台',owner=owner)
        self.assertTrue(self.tool.prepare(task['id'],{'tool':{'name':'platform_management','action':action,**args}},{}))
        return self.engine.get(task['id'])

    def test_five_modules_return_real_evidence_and_navigation(self):
        for action in ('machines','devices','model_data','security','sessions'):
            task=self.query(action)
            self.assertEqual(task['status'],'completed')
            self.assertTrue(task['platformResult']['links'])
            self.assertTrue(task['messages'][-1]['answer']['platformEvidence']['items'])
        self.assertNotIn('secret',str(self.query('machines')['platformResult']))

    def test_identity_not_model_controlled_and_foreign_devices_rejected(self):
        task=self.query('devices',owner='alice',ownerId='admin')
        self.assertEqual(self.seen[-1],'alice')
        self.assertEqual(self.query('machines',owner='alice')['status'],'unsupported_capability')
        self.assertEqual(self.query('model_data',owner='alice',deviceId='foreign')['status'],'unsupported_capability')

    def test_session_selection_and_unsupported_writes_do_not_claim_execution(self):
        self.assertEqual(self.query('session_detail',deviceId='device-1',sessionId='session-1')['status'],'completed')
        self.assertEqual(self.query('session_detail',deviceId='foreign',sessionId='missing')['status'],'needs_information')
        self.assertEqual(self.query('delete_device')['status'],'needs_information')
