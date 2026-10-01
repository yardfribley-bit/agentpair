import base64
import hashlib
import unittest
from agentpair.endpoint_modules import bundle, prepare
import test_devices


class ModuleTests(unittest.TestCase):
    def test_catalog_integrity(self):
        for name in ('process_details', 'process_tcp'):
            module = bundle(name, {'pid': 42, 'startedAt': '2026-10-01T00:00:00Z'})
            source = base64.b64decode(module['sourceBase64'])
            self.assertEqual(hashlib.sha256(source).hexdigest(), module['sha256'])
            self.assertIn(b'Target process changed', source)
            self.assertEqual(module['timeoutSeconds'], 30)

    def test_reject_unpublished_or_injected_code(self):
        with self.assertRaises(ValueError): bundle('../other', {})
        with self.assertRaises(ValueError): prepare({'module': {'sourceBase64': 'evil'}})
        for parameters in ({}, {'pid': True, 'startedAt': 'now'}, {'pid': 1, 'startedAt': 'now', 'command': 'evil'}):
            with self.assertRaises(ValueError): bundle('process_tcp', parameters)


class ModuleDispatchTests(test_devices.DeviceTests):
    def test_dynamic_module_task_and_evidence_roundtrip(self):
        queued = self.store.dispatch('admin', self.identity['deviceId'], {
            'goal': 'Collect target TCP evidence', 'action': 'run_module',
            'moduleId': 'process_tcp', 'parameters': {'pid': 42, 'startedAt': '2026-10-01T00:00:00Z'}})
        task = self.store.pull(self.identity['token'])
        self.assertEqual(task['payload']['module']['id'], 'process_tcp')
        module=task['payload']['module']
        with self.assertRaises(ValueError):
            self.store.complete(self.identity['token'], queued['taskId'], task['lease'], {'state':'completed','evidence':{'processCount':1}})
        result = {'state': 'completed', 'evidence': {'moduleId': 'process_tcp',
            'moduleVersion':module['version'],'sha256':module['sha256'],
            'output':{'schemaVersion':1,'capability':'process_tcp','target':module['parameters'],'evidence':{'connections':[]}}}}
        self.store.complete(self.identity['token'], queued['taskId'], task['lease'], result)
        self.assertEqual(self.store.task('admin', queued['taskId'])['result'], result)


if __name__ == '__main__': unittest.main()
