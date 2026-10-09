"""Loopback acceptance of capability rankings, object search and evidence drilldown.

Synthetic collector events only; model calls and cloud execution are forbidden.
"""
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from urllib.parse import urlencode
from http.server import ThreadingHTTPServer

from agentpair.devices import DeviceStore
from agentpair.platform import handler_for
from agentpair.session_lens import SessionStore
from agentpair.tasks import TaskEngine


class NoExternalBackend:
    def estimate(self, *args, **kwargs):
        raise AssertionError('A read-only ranking cannot reserve model usage')

    def call(self, *args, **kwargs):
        raise AssertionError('A read-only ranking cannot run a task')


class DataCenterCapabilityAPITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.engine = TaskEngine(self.root / 'tasks.db', NoExternalBackend(), start=False)
        def no_model(*args, **kwargs):
            raise AssertionError('Read-only APIs cannot call a model')
        self.server = ThreadingHTTPServer(('127.0.0.1', 0),
            handler_for(self.engine, 'Synthetic-fixture-only-1234', 'http://127.0.0.1', collection_model=no_model))
        self.thread = threading.Thread(target=lambda: self.server.serve_forever(poll_interval=.02), daemon=True)
        self.thread.start()
        self.url = 'http://127.0.0.1:' + str(self.server.server_port)
        self.client = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        devices = DeviceStore(self.root / 'devices.db')
        enrolled = devices.enroll(devices.pairing('fixture-company')['code'], 'Fixture workstation')
        self.device = enrolled['deviceId']
        session_path = self.root / 'session-lens.db'
        sessions = SessionStore(session_path)
        events = []
        def event(label, source='workbuddy', kind='tool_call', **item):
            return {'schemaVersion': 1, 'id': hashlib.sha256(label.encode()).hexdigest(),
                    'sessionId': 'fixture-multi-goal-session', 'source': source, 'kind': kind,
                    'timestamp': 1700000000 + len(events), 'payload': item,
                    'evidence': {'path': '/fixture/session.jsonl', 'byteStart': 0, 'byteEnd': 100}}
        for label in ('first-skill', 'second-skill'):
            events.append(event(label, name='Skill', arguments={'skill': 'agent-browser'}, call_id=label))
        events.append(event('mcp', source='codex', name='navigate', namespace='mcp__browser',
                            arguments={'url': 'https://fixture.example.test/'}, call_id='fixture-browser-call'))
        events.append(event('mention', kind='user_message', content='Mention agent-browser and mcp__browser'))
        sessions.ingest(devices.identity(enrolled['token']), {'schemaVersion': 1, 'events': events})

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=3)
        self.engine.close(); self.tmp.cleanup()

    def get(self, path):
        with self.client.open(self.url + path, timeout=10) as response:
            return json.load(response), response.headers.get('Content-Type', '')

    def rejected(self, path):
        with self.assertRaises(urllib.error.HTTPError) as raised:
            self.get(path)
        try:
            self.assertEqual(raised.exception.code, 400)
            self.assertIn('error', json.load(raised.exception))
        finally:
            raised.exception.close()

    def test_guest_ranking_to_object_search_to_exact_raw_evidence(self):
        ranks, ctype = self.get('/api/data-center/capabilities')
        self.assertIn('application/json', ctype)
        self.assertEqual(len(ranks['skills']), 1)
        skill = ranks['skills'][0]
        self.assertEqual((skill['name'], skill['loadCount'], skill['readCount']), ('agent-browser', 2, 0))
        self.assertEqual((skill['deviceCount'], skill['accountCount'], skill['sourceSessionCount']), (1, 1, 1))
        mcp = ranks['mcps'][0]
        self.assertEqual((mcp['name'], mcp['callCount'], mcp['directCount']), ('browser', 1, 1))
        self.assertEqual(mcp['methods'][0]['name'], 'navigate')
        hits, _ = self.get('/api/data-center/search?' + urlencode({'object': 'mcp', 'q': mcp['searchQuery']}))
        self.assertEqual(hits['total'], 1)
        detail, _ = self.get('/api/data-center/record?' + urlencode({'id': hits['items'][0]['id']}))
        raw, _ = self.get('/api/data-center/raw?' + urlencode({'id': hits['items'][0]['id']}))
        self.assertEqual(raw, detail['item']['raw'])
        self.assertEqual(detail['item']['arguments']['url'], 'https://fixture.example.test/')
        self.assertTrue(detail['item']['capabilities'])
        self.assertEqual(detail['item']['deviceId'], self.device)
        self.assertNotIn('payload', json.dumps(ranks))

    def test_filtered_rankings_and_object_browsing_exclude_mentions(self):
        ranks, _ = self.get('/api/data-center/capabilities?' + urlencode({'application': 'codex'}))
        self.assertEqual(ranks['skills'], [])
        self.assertEqual(len(ranks['mcps']), 1)
        ranks, _ = self.get('/api/data-center/capabilities?' + urlencode({'device': 'no-such-device'}))
        self.assertEqual((ranks['tools'], ranks['skills'], ranks['mcps']), ([], [], []))
        hits, _ = self.get('/api/data-center/search?object=skill')
        self.assertEqual(hits['total'], 2)
        hits, _ = self.get('/api/data-center/search?object=mcp')
        self.assertEqual(hits['total'], 1)
        hits, _ = self.get('/api/data-center/search?object=tool')
        self.assertEqual(hits['total'], 3)

    def test_validation_and_static_rankings_route(self):
        for path in ('/api/data-center/capabilities?device=a&device=b',
                     '/api/data-center/capabilities?object=invalid',
                     '/api/data-center/capabilities?after=bad',
                     '/api/data-center/capabilities?unknown=x'):
            self.rejected(path)
        with self.client.open(self.url + '/model-data/rankings', timeout=10) as response:
            self.assertIn('text/html', response.headers.get('Content-Type', ''))
            self.assertIn('使用排行', response.read().decode())


if __name__ == '__main__':
    unittest.main()
