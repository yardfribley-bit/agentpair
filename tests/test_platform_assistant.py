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

    def test_release_requires_specific_machine_and_confirmation(self):
        self.cloud.list=lambda:[{'id':'lease','name':'Windows test','state':'active'}]
        self.assertEqual(self.query('release_machine')['status'],'needs_information')
        self.assertEqual(self.query('release_machine',owner='alice',leaseId='lease')['status'],'unsupported_capability')
        task=self.query('release_machine',leaseId='lease')
        self.assertEqual(task['status'],'awaiting_confirmation')
        self.assertFalse(self.tool.handle_message(task['id'],'改成另一台',administrator=True))
        with self.assertRaises(PermissionError):self.tool.handle_message(task['id'],'确认释放')
        released=[]
        self.cloud.manager=SimpleNamespace(release=lambda lease:(released.append(lease) or {'state':'released'}))
        from unittest.mock import patch
        with patch('agentpair.platform_assistant.threading.Thread') as thread:
            self.assertTrue(self.tool.handle_message(task['id'],'确认释放',administrator=True))
            self.assertFalse(self.tool.handle_message(task['id'],'确认释放',administrator=True))
            self.assertEqual(thread.call_count,1)
        self.tool._release(task['id'],task['round'],'lease')
        self.assertEqual(released,['lease'])
        self.assertEqual(self.engine.get(task['id'])['status'],'completed')
