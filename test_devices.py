import tempfile
import unittest
from pathlib import Path
from agentpair.devices import DeviceStore, digest


class DeviceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = DeviceStore(Path(self.tmp.name) / 'devices.db')
        self.code = self.store.pairing()['code']
        self.identity = self.store.enroll(self.code, 'Windows-test')
        self.app = {'name': 'WorkBuddy', 'version': '1', 'publisher': 'Example', 'processNames': ['workbuddy.exe']}
        self.data = {'processes': [{'name': 'workbuddy.exe', 'pid': 1, 'parentPid': 0, 'commandLine': 'SECRET'}],
                     'applications': [self.app], 'os': 'Windows', 'architecture': 'AMD64'}

    def test_single_use_and_hashed_identity(self):
        with self.assertRaises(PermissionError): self.store.enroll(self.code, 'other')
        self.assertNotIn(self.identity['token'].encode(), Path(self.store.path).read_bytes())
        self.assertNotIn(self.code.encode(), Path(self.store.path).read_bytes())

    def test_expired_code(self):
        code = self.store.pairing()['code']
        with self.store.connect() as db: db.execute('UPDATE pairing SET expires=0 WHERE code=?', (digest(code),))
        with self.assertRaises(PermissionError): self.store.enroll(code, 'expired')

    def test_allowlist_and_revoke(self):
        self.store.report(self.identity['token'], self.data)
        device = self.store.list()[0]
        self.assertTrue(device['online'])
        self.assertNotIn('commandLine', device['snapshot']['processes'][0])
        self.store.revoke(device['id'])
        self.assertEqual(self.store.list(), [])
        with self.assertRaises(PermissionError): self.store.report(self.identity['token'], self.data)

    def test_invalid_token_and_payload(self):
        with self.assertRaises(PermissionError): self.store.report('wrong', self.data)
        for invalid in ([], {'processes': 'oops', 'applications': []}, {**self.data, 'errors': 1}):
            with self.assertRaises(ValueError): self.store.report(self.identity['token'], invalid)

    def test_analysis_uses_only_selected_snapshot(self):
        self.store.report(self.identity['token'], self.data)
        name, context = self.store.analysis_context(self.identity['deviceId'], 'applications', 0, '分析进程', self.app)
        self.assertEqual(name, 'WorkBuddy')
        self.assertIn('workbuddy.exe', context)
        self.assertNotIn('SECRET', context)
        self.assertIn('没有网络', context)
        with self.assertRaises(ValueError): self.store.analysis_context(self.identity['deviceId'], 'applications', 0, '分析', {'name': 'stale'})
        with self.assertRaises(ValueError): self.store.analysis_context(self.identity['deviceId'], 'applications', -1, '分析', self.app)
        with self.store.connect() as db: db.execute('UPDATE devices SET seen=0')
        with self.assertRaises(ValueError): self.store.analysis_context(self.identity['deviceId'], 'applications', 0, '分析', self.app)

    def test_device_task_lease_and_idempotent_result(self):
        task=self.store.dispatch('admin',self.identity['deviceId'],{'goal':'采集网络连接','requiredEvidence':['network']})
        pulled=self.store.pull(self.identity['token'])
        self.assertEqual(pulled['taskId'],task['taskId'])
        self.assertEqual(self.store.complete(self.identity['token'],task['taskId'],pulled['lease'],{'state':'waiting_for_evidence','missingEvidence':['network']})['accepted'],True)
        with self.assertRaises(PermissionError): self.store.complete(self.identity['token'],task['taskId'],pulled['lease'],{'state':'completed'})
        self.assertEqual(self.store.task('admin',task['taskId'])['state'],'waiting_for_evidence')

    def test_tasks_are_device_scoped(self):
        other=self.store.enroll(self.store.pairing()['code'],'other')
        self.store.dispatch('admin',self.identity['deviceId'],{'goal':'only device one'})
        self.assertIsNone(self.store.pull(other['token']))

    def test_poll_does_not_invalidate_client_action_lease(self):
        task=self.store.dispatch('admin',self.identity['deviceId'],{'goal':'手机交互'})
        first=self.store.pull(self.identity['token'])
        self.store.complete(self.identity['token'],task['taskId'],first['lease'],{'state':'received'})
        for _ in range(3):
            self.assertEqual(self.store.pull(self.identity['token'])['lease'],first['lease'])
        self.store.complete(self.identity['token'],task['taskId'],first['lease'],{'state':'running'})
        self.assertEqual(self.store.pull(self.identity['token'])['lease'],first['lease'])
        self.store.complete(self.identity['token'],task['taskId'],first['lease'],{'state':'completed','summary':'回传成功'})


if __name__ == '__main__': unittest.main()
