"""HTTP authorization and public projection tests, backed only by FakeConsole."""
import http.cookiejar
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from agentpair.platform import handler_for
from agentpair.tasks import TaskEngine
try:
    from .test_cloud_workflow import CloudPlanBackend, FakeConsole, PRIVATE_MARKERS, WINDOWS_RECIPE
except ImportError:
    from test_cloud_workflow import CloudPlanBackend, FakeConsole, PRIVATE_MARKERS, WINDOWS_RECIPE


class CloudWorkflowHTTPTests(unittest.TestCase):
    ORIGIN = 'http://127.0.0.1:18080'

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.backend = CloudPlanBackend()
        self.engine = TaskEngine(Path(self.tmp.name) / 'tasks.db', self.backend, start=False)
        self.console = FakeConsole()
        self.server = ThreadingHTTPServer(('127.0.0.1', 0),
            handler_for(self.engine, 'test-password-long-enough', self.ORIGIN, cloud_console=self.console))
        self.engine.cloud_workflow.start_threads = False
        self.engine.cloud_workflow.sleep = lambda seconds: None
        self.engine.cloud_workflow.catalog.register(WINDOWS_RECIPE)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = 'http://127.0.0.1:' + str(self.server.server_port)
        self.client = self.new_client()
        self.task = self.engine.create('Windows 安装', '创建 Windows 并安装 WorkBuddy')
        self.engine.process(self.task['id'])
        self.task = self.engine.get(self.task['id'])
        self.path = '/api/tasks/' + self.task['id'] + '/cloud/confirm'
        self.payload = {'requestId': self.task['cloudAction']['requestId'],
                        'confirmed': True, 'maxHourlyCNY': .51}

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.engine.close()
        self.tmp.cleanup()

    @staticmethod
    def new_client():
        return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
                                           urllib.request.ProxyHandler({}))

    def call(self, path, data=None, csrf='', origin=None, client=None):
        request = urllib.request.Request(self.url + path,
            data=json.dumps(data).encode() if data is not None else None,
            headers={'Origin': self.ORIGIN if origin is None else origin,
                     'Content-Type': 'application/json', 'X-CSRF-Token': csrf})
        with (client or self.client).open(request, timeout=5) as response:
            return response.status, json.load(response)

    def rejected(self, status, path=None, payload=None, **options):
        with self.assertRaises(urllib.error.HTTPError) as raised:
            self.call(path or self.path, self.payload if payload is None else payload, **options)
        self.assertEqual(raised.exception.code, status)
        raised.exception.close()
        self.assertEqual(self.console.create_calls, [])

    def admin_login(self):
        return self.call('/api/login', {'username': 'admin', 'password': 'test-password-long-enough'})[1]['csrf']

    def test_guest_user_csrf_and_origin_cannot_confirm(self):
        self.rejected(401)
        csrf = self.call('/api/register', {'username': 'cloudviewer', 'password': 'viewer-password-123'})[1]['csrf']
        self.rejected(403, csrf=csrf)
        self.call('/api/logout', {}, csrf)
        csrf = self.admin_login()
        self.rejected(403)
        self.rejected(403, csrf='wrong-token')
        self.rejected(403, csrf=csrf, origin='https://foreign.example.test')
        self.rejected(403, csrf=csrf, origin='')

    def test_confirmation_bound_to_nonce_price_recipe_and_task_round(self):
        csrf = self.admin_login()
        for patch in ({'requestId': 'wrong-nonce'}, {'confirmed': False},
                      {'confirmed': 'true'}, {'maxHourlyCNY': .52}, {'maxHourlyCNY': True}):
            with self.subTest(patch=patch):
                self.rejected(409 if 'requestId' in patch else 400,
                              payload={**self.payload, **patch}, csrf=csrf)
        self.engine.cloud_workflow.catalog.register({**WINDOWS_RECIPE, 'version': 'changed'})
        self.rejected(409, csrf=csrf)
        self.engine.cloud_workflow.catalog.register(WINDOWS_RECIPE)
        with self.engine.lock:
            saved = self.engine._load(self.task['id'])
            saved['round'] += 1
            self.engine._save(saved)
        self.rejected(409, csrf=csrf)

    def test_duplicate_admin_confirmation_uses_saved_config_and_exactly_one_creation(self):
        csrf = self.admin_login()
        injected = {**self.payload, 'system': 'Linux', 'softwareIds': ['git-linux'],
                    'sizing': {'cpu': 16}, 'leaseId': 'unexpected-lease', 'url': 'https://bad.example.test/a'}
        self.rejected(400, payload=injected, csrf=csrf)
        status, first = self.call(self.path, self.payload, csrf)
        self.assertEqual(status, 202)
        status, second = self.call(self.path, self.payload, csrf)
        self.assertEqual(status, 202)
        self.assertEqual(first['action']['requestId'], second['action']['requestId'])
        self.assertEqual(self.console.create_calls, [])
        self.engine.cloud_workflow._run(self.task['id'], create=True)
        self.call(self.path, self.payload, csrf)
        self.assertEqual(len(self.console.create_calls), 1)
        self.assertEqual(self.console.create_calls[0][0], 'Windows')
        self.assertEqual(self.console.create_calls[0][3], self.task['cloudAction']['quote']['sizing'])
        self.assertEqual(self.console.install_calls, [('b' * 24, WINDOWS_RECIPE['id'])])

    def test_resume_is_admin_csrf_protected_and_does_not_create_another_machine(self):
        csrf = self.admin_login()
        self.console.login_state = 'ssh_pending'
        self.call(self.path, self.payload, csrf)
        self.engine.cloud_workflow._run(self.task['id'], create=True)
        path = '/api/tasks/' + self.task['id'] + '/cloud/resume'
        resume = {'requestId': self.payload['requestId']}
        self.call('/api/logout', {}, csrf)
        with self.assertRaises(urllib.error.HTTPError) as raised:
            self.call(path, resume)
        self.assertEqual(raised.exception.code, 401)
        raised.exception.close()
        csrf = self.admin_login()
        self.console.login_state = 'ssh_authenticated'
        status, _ = self.call(path, resume, csrf)
        self.assertEqual(status, 202)
        self.engine.cloud_workflow._run(self.task['id'], create=False)
        self.assertEqual(len(self.console.create_calls), 1)
        self.assertEqual(len(self.console.install_calls), 1)

    def test_public_task_receipts_omit_all_private_provider_and_installer_fields(self):
        csrf = self.admin_login()
        self.call(self.path, self.payload, csrf)
        self.engine.cloud_workflow._run(self.task['id'], create=True)
        with self.engine.lock:
            saved = self.engine._load(self.task['id'])
            saved['owner'] = 'admin'
            saved['visibility'] = 'public'
            self.engine._save(saved)
        status, data = self.call('/api/tasks/' + self.task['id'], client=self.new_client())
        self.assertEqual(status, 200)
        self.assertFalse(data['permissions']['canWrite'])
        self.assertEqual(data['cloudAction']['state'], 'completed')
        serialized = json.dumps(data)
        for marker in PRIVATE_MARKERS:
            self.assertNotIn(marker, serialized)
        self.assertNotIn(WINDOWS_RECIPE['url'], serialized)
        self.assertNotIn('"password"', serialized)
        self.assertNotIn('"stdout"', serialized)
        self.assertNotIn('"url"', serialized)


if __name__ == '__main__':
    unittest.main()
