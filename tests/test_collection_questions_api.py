"""Loopback HTTP acceptance of collection questions and their access boundary.

The model and task backend are fakes. No relay, cloud API or external browser is
contacted; the real HTTP handler, SQLite stores, index and question worker run.
"""
import hashlib
import http.cookiejar
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from agentpair.devices import DeviceStore
from agentpair.platform import handler_for
from agentpair.tasks import TaskEngine

try:
    from .test_collection_assistant import EvidenceModel
except ImportError:
    from test_collection_assistant import EvidenceModel


class UnusedTaskBackend:
    """Estimate model analysis while refusing all task executor work."""
    def __init__(self):
        self.estimate_calls = []

    def estimate(self, envelope):
        self.estimate_calls.append(envelope)
        return .10

    def call(self, *args, **kwargs):
        raise AssertionError('Collection questions cannot invoke executor work')


class CollectionQuestionsAPITests(unittest.TestCase):
    ORIGIN = 'http://127.0.0.1:18080'
    PASSWORD = 'fixture-password-12345'

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.model = EvidenceModel()
        self.backend = UnusedTaskBackend()
        self.engine = TaskEngine(self.root / 'tasks.db', self.backend, budget=None, start=False)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0),
            handler_for(self.engine, self.PASSWORD, self.ORIGIN, collection_model=self.model))
        self.thread = threading.Thread(target=lambda: self.server.serve_forever(poll_interval=.02), daemon=True)
        self.thread.start()
        self.url = 'http://127.0.0.1:' + str(self.server.server_port)
        self.clients = {}
        self.csrf = {}
        self.owners = {}
        for name in ('alice', 'bob', 'admin'):
            self.clients[name] = self.new_client()
            path = '/api/login' if name == 'admin' else '/api/register'
            status, value = self.call(path, {'username': name, 'password': self.PASSWORD}, actor=name)
            self.assertEqual(status, 200 if name == 'admin' else 201)
            self.csrf[name] = value['csrf']
            self.owners[name] = 'admin' if name == 'admin' else self.engine.accounts.login(name, self.PASSWORD)['id']
        self.devices = DeviceStore(self.root / 'devices.db')
        self.assets = {}
        for name in ('alice', 'bob'):
            self.assets[name] = self.devices.enroll(self.devices.pairing(self.owners[name])['code'], 'Same MacBook Name')
        self.alice_events = self.upload('alice', '设计会员管理页面', 'alice-session')
        self.bob_events = self.upload('bob', '生成 SSH 私有视频', 'bob-session')

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        # Let finite workers finish before removing the temporary SQLite files.
        for _ in range(100):
            with sqlite3.connect(self.root / 'collection-assistant.db') as db:
                running = db.execute("SELECT count(*) FROM questions WHERE status='running'").fetchone()[0]
            if not running:
                break
            time.sleep(.01)
        self.engine.close()
        self.tmp.cleanup()

    @staticmethod
    def new_client():
        return urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
            urllib.request.ProxyHandler({}))

    def call(self, path, data=None, actor=None, csrf=None, origin=None, headers=None):
        request_headers = {'Content-Type': 'application/json',
                           'Origin': self.ORIGIN if origin is None else origin}
        if actor:
            request_headers['X-CSRF-Token'] = self.csrf.get(actor, '') if csrf is None else csrf
        if headers:
            request_headers.update(headers)
        request = urllib.request.Request(self.url + path,
            data=json.dumps(data, ensure_ascii=False).encode() if data is not None else None,
            headers=request_headers)
        client = self.clients[actor] if actor else self.new_client()
        with client.open(request, timeout=5) as response:
            return response.status, json.load(response)

    def rejected(self, expected, path, data=None, **kwargs):
        with self.assertRaises(urllib.error.HTTPError) as raised:
            self.call(path, data, **kwargs)
        error = raised.exception
        try:
            body = json.load(error)
            self.assertIn('error', body)
            if isinstance(expected, tuple):
                self.assertIn(error.code, expected)
            else:
                self.assertEqual(error.code, expected)
        finally:
            error.close()

    def upload(self, name, prompt, session):
        events = []
        bodies = [('message', 'user', {'content': prompt}),
                  ('tool_call', None, {'name': 'Write', 'arguments': {'path': name + '-members.py', 'content': 'fixture'}}),
                  ('tool_result', None, {'output': 'written ' + name + '-members.py'}),
                  ('message', 'assistant', {'content': '已完成：' + prompt})]
        for seq, (kind, role, payload) in enumerate(bodies):
            ident = hashlib.sha256((name + str(seq)).encode()).hexdigest()
            payload['id'] = name + '-source-' + str(seq)
            if seq:
                payload['parentId'] = name + '-source-' + str(seq - 1)
            event = {'schemaVersion': 1, 'id': ident, 'source': 'workbuddy', 'sessionId': session,
                     'kind': kind, 'role': role, 'timestamp': 1700000000 + seq,
                     'payload': payload, 'evidence': {'path': session + '.jsonl', 'fileIdentity': session,
                                                   'epoch': 0, 'byteStart': seq * 100}}
            if kind in ('tool_call', 'tool_result'):
                event['callId'] = name + '-call'
            if kind == 'tool_call':
                event['name'] = 'Write'
            events.append(event)
        status, receipt = self.call('/api/sessionlens/events', {'schemaVersion': 1, 'events': events},
            headers={'Authorization': 'Bearer ' + self.assets[name]['token']})
        self.assertEqual(status, 200)
        self.assertEqual(receipt['accepted'], 4)
        return events

    def payload(self, asset='alice', scope='mine', **extra):
        return {'deviceId': self.assets[asset]['deviceId'], 'scope': scope,
                'question': '会员管理怎么做的？', **extra}

    def submit(self, actor='alice', payload=None):
        status, started = self.call('/api/collection/questions', payload or self.payload(), actor=actor)
        self.assertEqual(status, 202)
        self.assertEqual(started['status'], 'running')
        self.assertTrue(started['stage'])
        return started['id']

    def completed(self, identity, actor='alice'):
        value = self.finished(identity, actor)
        self.assertEqual(value['status'], 'completed', value)
        return value

    def finished(self, identity, actor='alice'):
        for _ in range(150):
            status, value = self.call('/api/collection/questions/' + identity, actor=actor)
            self.assertEqual(status, 200)
            if value['status'] != 'running':
                return value
            time.sleep(.01)
        self.fail('Finite fake-model question failed to complete')

    def question_count(self):
        with sqlite3.connect(self.root / 'collection-assistant.db') as db:
            return db.execute('SELECT count(*) FROM questions').fetchone()[0]

    def test_guest_csrf_origin_and_content_type_rejections_start_no_question(self):
        path = '/api/collection/questions'
        payload = self.payload()
        self.rejected(401, path, payload)
        self.rejected(403, path, payload, actor='alice', csrf='')
        self.rejected(403, path, payload, actor='alice', csrf='wrong')
        self.rejected(403, path, payload, actor='alice', origin='https://foreign.example.test')
        self.rejected(403, path, payload, actor='alice', origin='')
        self.rejected(415, path, payload, actor='alice', headers={'Content-Type': 'text/plain'})
        self.assertEqual(self.question_count(), 0)
        self.assertEqual(self.model.calls, [])

    def test_http_submit_and_get_preserve_question_and_real_evidence(self):
        identity = self.submit()
        answer = self.completed(identity)
        self.assertEqual(answer['question'], '会员管理怎么做的？')
        candidate = answer['result']['candidates'][0]
        self.assertEqual(candidate['taskId'], self.alice_events[0]['id'])
        self.assertEqual(candidate['source'], 'workbuddy')
        self.assertEqual(candidate['sessionId'], 'alice-session')
        self.assertEqual(candidate['executions'][0]['recordId'], 'sessionlens:' + self.alice_events[1]['id'])
        self.assertTrue(answer['result']['answer']['evidenceRefs'])
        self.assertNotIn('bob-members.py', json.dumps(answer, ensure_ascii=False))

    def test_global_owner_is_resolved_from_device_not_client_supplied_identity(self):
        identity = self.submit('bob', self.payload(scope='global', ownerAccount=self.owners['bob'],
            owner=self.owners['bob'], requester='admin'))
        answer = self.completed(identity, 'bob')
        with sqlite3.connect(self.root / 'collection-assistant.db') as db:
            row = db.execute('SELECT requester,owner,device FROM questions WHERE id=?', (identity,)).fetchone()
        self.assertEqual(row, (self.owners['bob'], self.owners['alice'], self.assets['alice']['deviceId']))
        self.assertEqual(answer['result']['candidates'][0]['sessionId'], 'alice-session')
        self.assertNotIn('bob-members.py', json.dumps(answer, ensure_ascii=False))
        self.assertEqual(self.engine.accounts.balance(self.owners['alice'])['remainingCNY'], 2)
        self.assertAlmostEqual(self.engine.accounts.balance(self.owners['bob'])['remainingCNY'],
                               2 - .10 * len(self.model.calls))
        self.rejected(404, '/api/collection/questions/' + identity, actor='alice')

    def test_mine_scope_does_not_accept_client_claim_of_another_owner(self):
        self.rejected(403, '/api/collection/questions', self.payload(ownerAccount=self.owners['alice'],
            owner=self.owners['alice']), actor='bob')
        self.assertEqual(self.question_count(), 0)

    def test_results_are_requester_private_but_admin_can_audit(self):
        identity = self.submit()
        answer = self.completed(identity)
        path = '/api/collection/questions/' + identity
        self.rejected(401, path)
        self.rejected(404, path, actor='bob')
        status, audited = self.call(path, actor='admin')
        self.assertEqual(status, 200)
        self.assertEqual(audited['result'], answer['result'])
        self.rejected(404, '/api/collection/questions/' + 'f' * 32, actor='alice')

    def test_missing_revoked_and_invalid_scope_devices_reject_before_model(self):
        path = '/api/collection/questions'
        for scope in ('mine', 'global'):
            self.rejected(403, path, self.payload(scope=scope, deviceId='missing-device'), actor='alice')
        self.devices.revoke(self.assets['alice']['deviceId'], self.owners['alice'])
        for actor, scope in (('alice', 'mine'), ('alice', 'global'), ('bob', 'global'), ('admin', 'global')):
            self.rejected(403, path, self.payload(scope=scope), actor=actor)
        self.rejected(400, path, self.payload(asset='bob', scope='everyone'), actor='bob')
        self.assertEqual(self.question_count(), 0)
        self.assertEqual(self.model.calls, [])

    def test_revoked_device_closes_non_admin_cached_results_but_keeps_admin_audit(self):
        identity = self.submit()
        self.completed(identity)
        self.devices.revoke(self.assets['alice']['deviceId'], self.owners['alice'])
        self.rejected((403, 404), '/api/collection/questions/' + identity, actor='alice')
        status, answer = self.call('/api/collection/questions/' + identity, actor='admin')
        self.assertEqual(status, 200)
        self.assertEqual(answer['status'], 'completed')

    def test_previous_question_cannot_be_borrowed_across_requester_or_device(self):
        identity = self.submit()
        self.completed(identity)
        self.rejected(400, '/api/collection/questions', self.payload(scope='global', previousQuestionId=identity), actor='bob')
        self.rejected(400, '/api/collection/questions', self.payload(scope='global', previousQuestionId=identity), actor='admin')
        other = self.devices.enroll(self.devices.pairing(self.owners['alice'])['code'], 'Same MacBook Name')
        self.rejected(400, '/api/collection/questions', self.payload(deviceId=other['deviceId'], previousQuestionId=identity), actor='alice')
        self.assertEqual(self.question_count(), 1)
        followup = self.submit(payload=self.payload(question='会员管理为什么这样修改？', previousQuestionId=identity))
        self.completed(followup)
        self.assertTrue(any(data.get('history') for _, data, _ in self.model.calls))

    def test_invalid_question_has_no_worker_or_cached_answer(self):
        for patch in ({'question': ''}, {'question': ' '}, {'question': 'x' * 2001},
                      {'question': None}, {'deviceId': ''}):
            self.rejected(400, '/api/collection/questions', self.payload(**patch), actor='alice')
        self.assertEqual(self.question_count(), 0)
        self.assertEqual(self.model.calls, [])

    def test_exhausted_requester_wallet_fails_job_before_any_model_call(self):
        with sqlite3.connect(self.root / 'accounts.db') as db:
            db.execute('UPDATE wallets SET remaining=0 WHERE owner=?', (self.owners['alice'],))
        identity = self.submit()
        answer = self.finished(identity)
        self.assertEqual(answer['status'], 'failed')
        self.assertIn('额度不足', answer['error'])
        self.assertNotIn('result', answer)
        self.assertEqual(self.model.calls, [])
        with sqlite3.connect(self.root / 'tasks.db') as db:
            reserved = db.execute('SELECT reserved FROM ledger WHERE id=1').fetchone()[0]
        self.assertEqual(reserved, 0)
        self.assertEqual(self.engine.accounts.balance(self.owners['alice'])['remainingCNY'], 0)

    def test_identical_cached_question_reserves_no_more_money_even_with_empty_wallet(self):
        first = self.completed(self.submit())
        model_calls = len(self.model.calls)
        estimates = len(self.backend.estimate_calls)
        self.assertGreater(model_calls, 0)
        self.assertEqual(estimates, model_calls)
        with sqlite3.connect(self.root / 'accounts.db') as db:
            charged = db.execute('SELECT count(*) FROM charges WHERE owner=?', (self.owners['alice'],)).fetchone()[0]
            db.execute('UPDATE wallets SET remaining=0 WHERE owner=?', (self.owners['alice'],))
        with sqlite3.connect(self.root / 'tasks.db') as db:
            reserved_before = db.execute('SELECT reserved FROM ledger WHERE id=1').fetchone()[0]
        second = self.completed(self.submit())
        self.assertEqual(second['result']['answer'], first['result']['answer'])
        self.assertEqual(len(self.model.calls), model_calls)
        self.assertEqual(len(self.backend.estimate_calls), estimates)
        with sqlite3.connect(self.root / 'tasks.db') as db:
            self.assertEqual(db.execute('SELECT reserved FROM ledger WHERE id=1').fetchone()[0], reserved_before)
        with sqlite3.connect(self.root / 'accounts.db') as db:
            self.assertEqual(db.execute('SELECT count(*) FROM charges WHERE owner=?', (self.owners['alice'],)).fetchone()[0], charged)
        self.assertEqual(self.engine.accounts.balance(self.owners['alice'])['remainingCNY'], 0)

    def test_existing_device_data_upload_and_task_endpoints_still_work(self):
        status, inventory = self.call('/api/audit/devices')
        self.assertEqual(status, 200)
        self.assertEqual({item['id'] for item in inventory['items']}, {asset['deviceId'] for asset in self.assets.values()})
        status, own = self.call('/api/devices', actor='alice')
        self.assertEqual(status, 200)
        self.assertEqual([item['id'] for item in own['items']], [self.assets['alice']['deviceId']])
        status, detail = self.call('/api/audit/model-data/' + self.assets['alice']['deviceId']
            + '?request=sessionlens:' + self.alice_events[0]['id'])
        self.assertEqual(status, 200)
        self.assertIn('设计会员管理页面', detail['calls'][0]['body'])
        status, sessions = self.call('/api/sessionlens/sessions', actor='alice')
        self.assertEqual(status, 200)
        self.assertEqual([session['session'] for session in sessions['items']], ['alice-session'])
        status, session = self.call('/api/session')
        self.assertEqual((status, session['role']), (200, 'viewer'))
        status, tasks = self.call('/api/tasks', actor='alice')
        self.assertEqual(status, 200)
        self.assertEqual(tasks['items'], [])
        self.rejected(401, '/api/sessionlens/events', {'schemaVersion': 1, 'events': self.alice_events},
            headers={'Authorization': 'Bearer invalid-token'})


if __name__ == '__main__':
    unittest.main()
