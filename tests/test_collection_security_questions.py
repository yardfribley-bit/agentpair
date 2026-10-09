"""Real local evidence/index with fake security models; no external requests."""
import hashlib
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from agentpair.collection_assistant import CollectionAssistant
from agentpair.collection_view import CollectionView
from agentpair.devices import DeviceStore
from agentpair.session_lens import SessionStore


class ImmediateThread:
    def __init__(self, target, args=(), **kwargs):
        self.target, self.args = target, args

    def start(self):
        self.target(*self.args)


class SecurityModel:
    def __init__(self):
        self.calls = []
        self.invalid = False

    def __call__(self, system, packet, max_tokens=4000):
        self.calls.append((system, packet))
        if 'evidence' not in packet:
            return {'terms': ['不存在的调查目标'] if '不存在' in packet['question'] else ['password']}
        refs = ['E999'] if self.invalid else [e['ref'] for e in packet['evidence'][:2]]
        return {'answer': {'text': '已采请求包含 password 字段；上传账号不等于操作员工，远端保存情况未采集。',
                           'basis': 'recorded', 'evidenceRefs': refs}, 'gaps': ['无远端保存证据']}


class CollectionSecurityQuestionsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.devices = DeviceStore(self.root / 'devices.db')
        self.sessions = SessionStore(self.root / 'sessions.db')
        self.enrolled = self.devices.enroll(self.devices.pairing('alice')['code'], '调查测试 Mac')
        self.device = self.enrolled['deviceId']
        self.model = SecurityModel()
        self.assistant = CollectionAssistant(self.root / 'assistant.db', CollectionView(self.devices, self.sessions), self.model)
        self.secret = 'SyntheticPrivateCredentialForEvidence456'
        self.body = json.dumps({'messages': [
            {'role': 'system', 'content': '测试凭据 password=' + self.secret},
            {'role': 'user', 'content': '你好'}]}, ensure_ascii=False)
        self.request = hashlib.sha256(self.body.encode()).hexdigest()
        self.devices.ingest_model_context(self.enrolled['token'], {'requests': [{
            'id': self.request, 'source': 'workbuddy_network_context', 'body': self.body,
            'timestamp': 1791360000, 'sessionId': 'context-only-session',
            'destination': 'https://model.example.test/v1/chat/completions',
            'recordStatus': 'parseable'}]})

    def tearDown(self):
        self.tmp.cleanup()

    def test_applens_only_evidence_does_not_require_a_user_task(self):
        result = self.assistant.answer('alice', self.device, 'password出现在哪里？', perspective='security')
        self.assertEqual(result['perspective'], 'security')
        self.assertEqual(result['candidates'], [])
        self.assertTrue(result['evidence'])
        self.assertTrue(result['observations'])
        self.assertEqual(result['scope']['deviceId'], self.device)
        self.assertEqual(result['scope']['operator'], '未确认')
        self.assertFalse(result['scope']['global'])
        self.assertIn(self.secret, '\n'.join(e['text'] for e in result['evidence']))
        self.assertNotIn(self.secret, json.dumps(self.model.calls, ensure_ascii=False))
        self.assertEqual(len(self.model.calls), 3, 'planning, analysis, review only')
        self.assertTrue(all('data' not in event for event in result['evidence']))
        self.assertTrue(all(e.get('rawUrl') for e in result['evidence']))
        self.assertIn('上传账号', result['answer']['text'])

    def test_search_is_scoped_and_does_not_count_another_device(self):
        other = self.devices.enroll(self.devices.pairing('bob')['code'], '调查测试 Mac')
        self.devices.ingest_model_context(other['token'], {'requests': [{
            'id': hashlib.sha256(b'other').hexdigest(), 'source': 'workbuddy_generation_context',
            'body': 'password=OtherSyntheticSecret', 'timestamp': 1791360001, 'sessionId': 'context-only-session'}]})
        result = self.assistant.answer('alice', self.device, 'password在哪里？', perspective='security')
        self.assertEqual(result['coverage']['matchedRecords'], 1)
        self.assertTrue(all(e['deviceId'] == self.device for e in result['evidence']))
        with self.assertRaises(PermissionError):
            self.assistant.answer('bob', self.device, 'password在哪里？', perspective='security')

    def test_empty_search_never_reuses_previous_answer_or_fabricates_a_risk(self):
        result = self.assistant.answer('alice', self.device, '不存在的调查目标', perspective='security')
        self.assertEqual(result['answer']['basis'], 'unknown')
        self.assertEqual(result['evidence'], [])
        self.assertEqual(result['observations'], [])
        self.assertEqual(len(self.model.calls), 1)

    def test_related_tool_return_resolves_full_record_and_preserves_tail(self):
        def event(label, kind, **extra):
            return {'schemaVersion': 1, 'id': hashlib.sha256(label.encode()).hexdigest(),
                    'sessionId': 'long-tool-return', 'source': 'workbuddy', 'kind': kind,
                    'timestamp': 1791360100, 'payload': {},
                    'evidence': {'path': '/fixture/workbuddy.jsonl', 'byteStart': 0, 'byteEnd': 100}, **extra}
        tool_call = event('long-call', 'tool_call', name='Bash', callId='unique-call',
                          payload={'arguments': {'command': 'curl https://example.test/password-check'}})
        tail = '尾部实际返回：HTTP 403，访问失败'
        tool_return = event('long-result', 'tool_result', callId='unique-call',
                            payload={'output': '普通诊断行\n' * 400 + tail})
        self.sessions.ingest(self.devices.identity(self.enrolled['token']),
                             {'schemaVersion': 1, 'events': [tool_call, tool_return]})
        result = self.assistant.answer('alice', self.device, 'password工具返回是什么？', perspective='security')
        returned = next(e for e in result['evidence'] if e['kind'] == 'tool_result')
        self.assertIn(tail, returned['text'])
        self.assertTrue(returned['truncated'])
        self.assertIn('完整原文', returned['text'])
        self.assertIn(tail, json.dumps(self.model.calls[-1][1], ensure_ascii=False))

    def test_cache_and_requester_perspective_boundary(self):
        with patch('agentpair.collection_assistant.threading.Thread', ImmediateThread):
            job = self.assistant.submit('user-alice', 'alice', self.device, 'password在哪里？', perspective='security')
            complete = self.assistant.get(job['id'], 'user-alice')
            self.assertEqual(complete['status'], 'completed', complete)
            self.assertEqual(complete['perspective'], 'security')
            with self.assertRaises(KeyError):
                self.assistant.get(job['id'], 'other-user')
            with self.assertRaises(ValueError):
                self.assistant.submit('user-alice', 'alice', self.device, '继续', previous=job['id'], perspective='task')
        count = len(self.model.calls)
        self.assistant.answer('alice', self.device, 'password在哪里？', perspective='security')
        self.assertEqual(len(self.model.calls), count)

    def test_invalid_citation_fails_instead_of_becoming_a_security_finding(self):
        self.model.invalid = True
        with patch('agentpair.collection_assistant.threading.Thread', ImmediateThread):
            job = self.assistant.submit('user-alice', 'alice', self.device, 'password在哪里？', perspective='security')
        result = self.assistant.get(job['id'], 'user-alice')
        self.assertEqual(result['status'], 'failed')
        self.assertNotIn('result', result)
        self.assertIn('未提供的证据', result['error'])

    def test_existing_question_database_migrates_without_losing_records(self):
        path = self.root / 'legacy-assistant.db'
        with closing(sqlite3.connect(path)) as db, db:
            db.executescript('CREATE TABLE questions(id TEXT PRIMARY KEY, requester TEXT, owner TEXT, device TEXT, question TEXT, status TEXT, stage TEXT, result TEXT, error TEXT, updated REAL);')
            db.execute('INSERT INTO questions VALUES(?,?,?,?,?,?,?,?,?,?)',
                       ('old', 'user-alice', 'alice', self.device, '历史问题', 'failed', '完成', None, '历史错误', 1))
        migrated = CollectionAssistant(path, self.assistant.collections, self.model)
        old = migrated.get('old', 'user-alice')
        self.assertEqual(old['perspective'], 'task')
        self.assertEqual(old['question'], '历史问题')
        self.assertEqual(old['error'], '历史错误')


if __name__ == '__main__':
    unittest.main()
