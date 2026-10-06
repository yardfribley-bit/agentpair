"""Cloud proposals never spend money before an explicit, bound confirmation."""
import copy
import datetime
import json
import tempfile
import threading
import unittest
from pathlib import Path

from agentpair.cloud_workflow import CloudWorkflow
from agentpair.software_install import SoftwareCatalog
from agentpair.tasks import Conflict, TaskEngine


WINDOWS_RECIPE = {
    'id': 'workbuddy-windows', 'platform': 'Windows', 'installer': 'inno',
    'name': 'WorkBuddy', 'displayName': 'WorkBuddy', 'version': '5.6.1',
    'url': 'https://packages.example.test/setup.exe?token=private-download-token',
    'sha256': 'a' * 64,
}
PRIVATE_MARKERS = ('private-download-token', 'private-provider-token',
                   'private-password', 'private-stdout', 'private-stderr')


class CloudPlanBackend:
    def __init__(self):
        self.calls = []
        self.tool = {'name': 'cloud_management', 'action': 'create',
                     'system': 'Windows', 'softwareIds': [WINDOWS_RECIPE['id']]}

    def estimate(self, envelope):
        return .10

    def call(self, role, envelope, timeout):
        self.calls.append((role, copy.deepcopy(envelope)))
        return {'answer': {'summary': '准备 Windows 云机器安装方案',
                           'tool': copy.deepcopy(self.tool), 'executionMode': 'local'}}


class FakeConsole:
    """No networking or subprocesses; intentionally includes private receipt fields."""
    def __init__(self):
        self.quote_calls = []
        self.create_calls = []
        self.login_calls = []
        self.install_calls = []
        self.operation_calls = []
        self.login_state = 'ssh_authenticated'
        self.operation_state = 'completed'
        self.receipt_patch = {}
        self.operation_summary = '安装与版本验证通过'
        self.quote_error = None
        self.create_error = None
        self.login_error = None
        self.operation_error = None

    def quote(self, system, sizing=None):
        self.quote_calls.append((system, copy.deepcopy(sizing)))
        if self.quote_error:
            raise self.quote_error
        return {'system': system, 'hourlyCNY': .51, 'hostCNY': .40, 'eipCNY': .11,
                'cpu': 2, 'memoryMB': 4096, 'zone': 'cn-bj2-03', 'durationMinutes': 60,
                'quotedAt': '2026-10-07T00:00:00+00:00',
                'sizing': sizing or {'cpu': 2, 'memoryGB': 4, 'systemDiskGB': 40, 'dataDiskGB': 20}}

    def create(self, system, cap, request_id, sizing=None):
        self.create_calls.append((system, cap, request_id, copy.deepcopy(sizing)))
        if self.create_error:
            raise self.create_error
        return {'id': 'b' * 24, 'platform': 'windows', 'state': 'active',
                'expiresAt': '2099-10-07T01:00:00+00:00',
                'password': 'private-password', 'token': 'private-provider-token'}

    def login(self, lease_id):
        self.login_calls.append(lease_id)
        if self.login_error:
            raise self.login_error
        return {'loginState': self.login_state, 'cloudState': 'Running',
                'address': '203.0.113.9', 'password': 'private-password',
                'stdout': 'private-stdout'}

    def install(self, lease_id, software_id):
        self.install_calls.append((lease_id, software_id))
        return {'id': 'c' * 24, 'state': 'queued'}

    def operation(self, operation_id):
        self.operation_calls.append(operation_id)
        if self.operation_error:
            raise self.operation_error
        return {'id': operation_id, 'state': self.operation_state,
                'summary': self.operation_summary,
                'token': 'private-provider-token', 'stdout': 'private-stdout',
                'stderr': 'private-stderr', 'password': 'private-password',
                'url': WINDOWS_RECIPE['url'],
                'evidence': {'softwareId': WINDOWS_RECIPE['id'], 'verified': True,
                             'installedVersion': WINDOWS_RECIPE['version'],
                             'sha256': WINDOWS_RECIPE['sha256'],
                             'password': 'private-password', 'stdout': 'private-stdout',
                             'url': WINDOWS_RECIPE['url'], **self.receipt_patch}}


class CloudWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'tasks.db'
        self.backend = CloudPlanBackend()
        self.console = FakeConsole()
        self.engine = TaskEngine(self.path, self.backend, start=False)
        self.addCleanup(self.engine.close)
        self.workflow = CloudWorkflow(self.engine, self.console,
                                      sleep=lambda seconds: None, start_threads=False)
        self.engine.cloud_workflow = self.workflow
        self.workflow.catalog.register(WINDOWS_RECIPE)

    def prepare(self, **task_options):
        task = self.engine.create('Windows 安装', '创建 Windows 并安装 WorkBuddy', **task_options)
        self.engine.process(task['id'])
        return self.engine.get(task['id'])

    def confirmation(self, task):
        action = task['cloudAction']
        return {'requestId': action['requestId'], 'confirmed': True,
                'maxHourlyCNY': action['quote']['hourlyCNY']}

    def run_confirmed(self, task):
        self.workflow.confirm(task['id'], self.confirmation(task))
        self.workflow._run(task['id'], create=True)
        return self.engine.get(task['id'])

    def assert_private_free(self, value):
        body = json.dumps(value)
        for marker in PRIVATE_MARKERS:
            self.assertNotIn(marker, body)
        self.assertNotIn(WINDOWS_RECIPE['url'], body)

    def test_local_plan_pauses_without_driver_or_creation(self):
        task = self.prepare()
        self.assertEqual(task['engineeringMethod'], 'local')
        self.assertEqual(task['status'], 'awaiting_confirmation')
        self.assertEqual([e['mode'] for _, e in self.backend.calls], ['plan'])
        self.assertEqual(len(self.console.quote_calls), 1)
        self.assertEqual(self.console.create_calls, [])
        self.assertEqual(self.console.install_calls, [])
        self.assertEqual(task['results'][0]['outputs']['driver'], {})
        capabilities = self.backend.calls[0][1]['task']['cloudCapabilities']
        self.assertTrue(capabilities['requiresPriceConfirmation'])
        self.assert_private_free(task)

    def test_non_admin_owner_or_missing_catalog_never_quotes_or_creates(self):
        for options, ids in [({'owner': 'alice'}, [WINDOWS_RECIPE['id']]),
                             ({}, ['sessionlens-unregistered']), ({}, ['git-linux'])]:
            with self.subTest(options=options, ids=ids):
                self.backend.tool['softwareIds'] = ids
                task = self.prepare(**options)
                self.assertEqual(task['status'], 'unsupported_capability')
        self.assertEqual(self.console.quote_calls, [])
        self.assertEqual(self.console.create_calls, [])

    def test_confirm_requires_boolean_nonce_exact_finite_price_and_current_round(self):
        task = self.prepare()
        valid = self.confirmation(task)
        invalid = [{**valid, 'confirmed': x} for x in (None, False, 1, 'true')]
        invalid += [{**valid, 'requestId': 'stale-request'}]
        invalid += [{**valid, 'maxHourlyCNY': x} for x in
                    (.52, 0, -1, True, '.51', float('inf'), float('nan'))]
        for payload in invalid:
            with self.subTest(payload=payload), self.assertRaises((ValueError, Conflict)):
                self.workflow.confirm(task['id'], payload)
        with self.engine.lock:
            saved = self.engine._load(task['id'])
            saved['round'] += 1
            self.engine._save(saved)
        with self.assertRaises(Conflict):
            self.workflow.confirm(task['id'], valid)
        self.assertEqual(self.console.create_calls, [])

    def test_expired_quote_and_changed_recipe_require_new_confirmation(self):
        task = self.prepare()
        self.workflow.catalog.register({**WINDOWS_RECIPE, 'version': '5.6.2'})
        with self.assertRaises(Conflict):
            self.workflow.confirm(task['id'], self.confirmation(task))
        self.workflow.catalog.register(WINDOWS_RECIPE)
        with self.engine.lock:
            saved = self.engine._load(task['id'])
            saved['cloudAction']['quoteExpiresAt'] = (
                datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=1)).isoformat()
            self.engine._save(saved)
        with self.assertRaises(Conflict):
            self.workflow.confirm(task['id'], self.confirmation(task))
        self.assertEqual(self.console.create_calls, [])

    def test_concurrent_duplicate_confirmation_only_claims_one_operation(self):
        task = self.prepare()
        payload = self.confirmation(task)
        errors = []
        answers = []
        def confirm():
            try:
                answers.append(self.workflow.confirm(task['id'], payload))
            except Exception as error:
                errors.append(error)
        threads = [threading.Thread(target=confirm) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(len(answers), 8)
        self.assertEqual(self.workflow.active, {task['id']})
        self.workflow._run(task['id'], create=True)
        self.workflow.confirm(task['id'], payload)
        self.assertEqual(len(self.console.create_calls), 1)
        self.assertEqual(len(self.console.install_calls), 1)
        self.assertEqual(self.console.create_calls[0][2], payload['requestId'])
        self.assertEqual(self.workflow.active, set())

    def test_cancelled_before_worker_dispatch_never_creates_a_machine(self):
        task = self.prepare()
        self.workflow.confirm(task['id'], self.confirmation(task))
        self.engine.cancel(task['id'])
        self.workflow._run(task['id'], create=True)
        self.assertEqual(self.engine.get(task['id'])['status'], 'cancelled')
        self.assertEqual(self.console.create_calls, [])
        self.assertEqual(self.console.login_calls, [])
        self.assertEqual(self.console.install_calls, [])

    def test_unready_ssh_never_installs_and_resume_reuses_same_lease(self):
        self.console.login_state = 'ssh_pending'
        task = self.prepare()
        waiting = self.run_confirmed(task)
        self.assertEqual(waiting['status'], 'waiting_for_machine')
        self.assertEqual(waiting['cloudAction']['state'], 'waiting_machine')
        self.assertEqual(len(self.console.login_calls), 20)
        self.assertEqual(self.console.install_calls, [])
        self.console.login_state = 'ssh_authenticated'
        self.workflow.resume(task['id'], {'requestId': task['cloudAction']['requestId']})
        self.workflow._run(task['id'], create=False)
        self.assertEqual(self.engine.get(task['id'])['status'], 'completed')
        self.assertEqual(len(self.console.create_calls), 1)
        self.assertEqual(self.console.install_calls, [('b' * 24, WINDOWS_RECIPE['id'])])

    def test_failed_or_invalid_receipts_cannot_complete(self):
        for state, patch in [('failed', {}), ('interrupted', {}),
                             ('completed', {'sha256': 'd' * 64}),
                             ('completed', {'installedVersion': 'wrong-version'}),
                             ('completed', {'verified': False})]:
            with self.subTest(state=state, patch=patch):
                self.console.operation_state = state
                self.console.receipt_patch = patch
                task = self.prepare()
                done = self.run_confirmed(task)
                self.assertNotEqual(done['status'], 'completed')
                self.assertIn(done['cloudAction']['state'], ('failed', 'interrupted'))

    def test_restart_does_not_replay_creation_or_installation(self):
        task = self.prepare()
        self.workflow.confirm(task['id'], self.confirmation(task))
        restarted = TaskEngine(self.path, CloudPlanBackend(), start=False)
        self.addCleanup(restarted.close)
        workflow = CloudWorkflow(restarted, self.console, sleep=lambda seconds: None, start_threads=False)
        restarted.cloud_workflow = workflow
        self.assertEqual(restarted.get(task['id'])['status'], 'interrupted')
        workflow.confirm(task['id'], self.confirmation(task))
        restarted.process(task['id'])
        self.assertEqual(workflow.active, set())
        self.assertEqual(self.console.create_calls, [])
        self.assertEqual(self.console.install_calls, [])

    def test_restart_during_install_preserves_reference_without_replay(self):
        task = self.prepare()
        self.workflow.confirm(task['id'], self.confirmation(task))
        self.console.operation_error = SystemExit('simulated process death')
        with self.assertRaises(SystemExit):
            self.workflow._run(task['id'], create=True)
        before = self.engine.get(task['id'])
        self.assertEqual(before['status'], 'running')
        self.assertEqual(before['cloudAction']['operations'][0]['id'], 'c' * 24)
        restarted = TaskEngine(self.path, CloudPlanBackend(), start=False)
        self.addCleanup(restarted.close)
        workflow = CloudWorkflow(restarted, self.console, sleep=lambda seconds: None, start_threads=False)
        restarted.cloud_workflow = workflow
        workflow.confirm(task['id'], self.confirmation(task))
        restarted.process(task['id'])
        self.assertEqual(restarted.get(task['id'])['status'], 'interrupted')
        self.assertEqual(len(self.console.create_calls), 1)
        self.assertEqual(len(self.console.install_calls), 1)
        self.assertEqual(workflow.active, set())

    def test_success_and_provider_exception_records_do_not_leak_private_fields(self):
        task = self.prepare()
        self.assert_private_free(self.run_confirmed(task))
        self.console.create_error = RuntimeError('token=private-provider-token password=private-password')
        failed = self.run_confirmed(self.prepare())
        self.assertEqual(failed['status'], 'interrupted')
        self.assert_private_free(failed)

    def test_quote_failure_does_not_publish_provider_exception_body(self):
        self.console.quote_error = RuntimeError('token=private-provider-token url=' + WINDOWS_RECIPE['url'])
        task = self.prepare()
        self.assertEqual(task['status'], 'unsupported_capability')
        self.assert_private_free(task)

    def test_operation_summary_is_not_copied_into_public_task(self):
        self.console.operation_summary = 'private-stdout token=private-provider-token password=private-password'
        task = self.prepare()
        self.assert_private_free(self.run_confirmed(task))

    def test_recipe_changed_after_confirmation_is_rejected_before_creation(self):
        task = self.prepare()
        self.workflow.confirm(task['id'], self.confirmation(task))
        self.workflow.catalog.register({**WINDOWS_RECIPE, 'sha256': 'e' * 64})
        self.workflow._run(task['id'], create=True)
        self.assertEqual(self.console.create_calls, [])
        self.assertEqual(self.console.install_calls, [])
        self.assertNotEqual(self.engine.get(task['id'])['status'], 'completed')


if __name__ == '__main__':
    unittest.main()
