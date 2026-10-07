import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from agentpair.collection_knowledge import CollectionKnowledge
from agentpair.devices import DeviceStore
from agentpair.session_lens import SessionStore


class CollectionKnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.devices = DeviceStore(self.root / 'devices.db')
        self.sessions = SessionStore(self.root / 'sessions.db')
        self.enrolled = self.devices.enroll(self.devices.pairing('alice')['code'], 'Same Computer')
        self.identity = self.devices.identity(self.enrolled['token'])
        self.device = self.identity['id']
        self.kb = CollectionKnowledge(self.root / 'knowledge.db', self.sessions, self.devices)
        self.counter = 0

    def tearDown(self):
        self.tmp.cleanup()

    def event(self, text='', kind='user_message', source='workbuddy', session='s', seq=None, timestamp=None, payload=None, **extra):
        self.counter += 1
        seq = seq if seq is not None else self.counter
        ident = hashlib.sha256((source + session + str(self.counter) + text).encode()).hexdigest()
        return {'schemaVersion': 1, 'id': ident, 'source': source, 'sessionId': session, 'kind': kind,
                'timestamp': timestamp if timestamp is not None else 1700000000 + seq,
                'payload': payload if payload is not None else {'content': text},
                'evidence': {'path': session + '.jsonl', 'fileIdentity': session + '-file', 'epoch': 0, 'byteStart': seq * 100}, **extra}

    def ingest(self, events, identity=None):
        for start in range(0, len(events), 30):
            self.sessions.ingest(identity or self.identity, {'schemaVersion': 1, 'events': events[start:start + 30]})

    def sync_all(self, limit=500):
        for _ in range(100):
            status = self.kb.sync('alice', self.device, [self.device], limit)
            if status['indexComplete']:
                return status
        self.fail('Index did not finish finite backfill')

    def test_semantic_fields_not_identity_and_preserves_structural_relations(self):
        user = self.event('<system-reminder>旧背景</system-reminder><user_query>查上海天气</user_query>', kind='message', role='user')
        call = self.event(kind='tool_call', payload={'id': 'm2', 'parentId': 'm1', 'arguments': {'command': 'curl https://weather.example/shanghai', 'timeout': 5}, 'providerData': {'conversationRequestId': 'r1'}}, callId='c1', name='Bash')
        self.ingest([user, call])
        self.sync_all()
        hit = self.kb.search('alice', self.device, ['上海', '天气'])[0]
        self.assertEqual(hit['prompt'], '查上海天气')
        self.assertNotIn('旧背景', hit['text'])
        tool = self.kb.search('alice', self.device, ['weather.example'])[0]
        self.assertIn('"timeout": 5', tool['text'])
        self.assertEqual(tool['event']['payload']['parentId'], 'm1')
        self.assertEqual(tool['event']['payload']['providerData']['conversationRequestId'], 'r1')
        self.assertEqual(tool['event']['callId'], 'c1')
        self.assertEqual(self.kb.search('alice', self.device, ['r1']), [])

    def test_large_fields_are_head_tail_bounded_and_current_query_survives(self):
        text = '<system-reminder>' + '旧历史 ' * 40000 + '</system-reminder><user_query>给上海查天气</user_query>'
        event = self.event(text, kind='message', role='user')
        self.ingest([event])
        self.sync_all()
        hit = self.kb.search('alice', self.device, ['上海'])[0]
        self.assertEqual(hit['prompt'], '给上海查天气')
        self.assertTrue(hit['truncated'])
        self.assertLessEqual(len(hit['text']), 4000)

    def test_history_summaries_and_encrypted_reasoning_are_not_fresh_requests(self):
        events = [self.event('<cb_summary>上海天气 SSH视频</cb_summary>', kind='message', role='user'),
                  self.event('The following is the Codex agent history: SSH视频'),
                  self.event(kind='reasoning', payload={'encrypted_content': 'secretcipher上海天气', 'content': []}),
                  self.event(kind='reasoning', payload={'summary': [{'type': 'summary_text', 'text': '先搜索上海天气，再核对日期'}]})]
        self.ingest(events)
        status = self.sync_all()
        self.assertEqual(status['indexedRecords'], 4)
        self.assertEqual(status['searchableRecords'], 1)
        self.assertEqual(len(self.kb.search('alice', self.device, ['上海天气'])), 1)
        self.assertEqual(self.kb.search('alice', self.device, ['SSH视频']), [])
        self.assertEqual(self.kb.search('alice', self.device, ['secretcipher']), [])

    def test_incremental_backfill_is_bounded_and_new_upload_has_priority(self):
        old = [self.event('历史条目' + str(i)) for i in range(701)]
        self.ingest(old)
        first = self.kb.sync('alice', self.device, [self.device], limit=50)
        self.assertEqual(first['indexedThisBatch'], 50)
        self.assertFalse(first['indexComplete'])
        self.assertTrue(self.kb.search('alice', self.device, ['历史条目700']))
        latest = self.event('实时采集独立标记')
        self.ingest([latest])
        status = self.kb.sync('alice', self.device, [self.device], limit=50)
        self.assertLessEqual(status['indexedThisBatch'], 50)
        self.assertEqual(self.kb.search('alice', self.device, ['实时采集独立标记'])[0]['id'], latest['id'])
        completed = self.sync_all(50)
        self.assertEqual(completed['indexedRecords'], 702)
        self.assertEqual(self.kb.sync('alice', self.device, [self.device], limit=50)['indexedThisBatch'], 0)

    def test_idempotent_index_reopen_does_not_recopy_or_change_evidence(self):
        event = self.event('查上海天气')
        self.ingest([event])
        with self.sessions.connect() as db:
            original = db.execute('SELECT event FROM session_events').fetchone()[0]
        self.sync_all()
        self.kb = CollectionKnowledge(self.root / 'knowledge.db', self.sessions, self.devices)
        self.ingest([event])
        self.assertEqual(self.kb.sync('alice', self.device)['indexedThisBatch'], 0)
        with self.sessions.connect() as db:
            self.assertEqual(original, db.execute('SELECT event FROM session_events').fetchone()[0])

    def test_account_device_and_source_boundaries(self):
        other = self.devices.enroll(self.devices.pairing('alice')['code'], 'Same Computer')
        other_identity = self.devices.identity(other['token'])
        bob = self.devices.enroll(self.devices.pairing('bob')['code'], 'Same Computer')
        bob_identity = self.devices.identity(bob['token'])
        events = [self.event('上海天气'), self.event('Codex天气', source='codex')]
        self.ingest(events)
        self.ingest([self.event('另一设备私有记录')], other_identity)
        self.ingest([self.event('另一账号私有记录')], bob_identity)
        self.sync_all()
        self.assertEqual(self.kb.search('alice', self.device, ['私有']), [])
        for owner, device in [('bob', self.device), ('alice', bob_identity['id'])]:
            with self.assertRaises(PermissionError):
                self.kb.search(owner, device, ['天气'])
        with self.assertRaises(PermissionError):
            self.kb.sync('alice', self.device, [other_identity['id']])
        window = self.kb.window('alice', self.device, 'workbuddy', 's', events[0]['id'])
        self.assertEqual(len(window), 1)
        with self.assertRaises(ValueError):
            self.kb.window('alice', self.device, 'codex', 's', events[0]['id'])

    def test_aliases_share_canonical_index_but_similar_names_do_not(self):
        alias = self.devices.enroll(self.devices.pairing('alice')['code'], 'Alias Machine')
        alias_identity = self.devices.identity(alias['token'])
        self.ingest([self.event('别名端采集天气')], alias_identity)
        with self.devices.connect() as db:
            db.execute('INSERT INTO device_aliases VALUES(?,?,?)', (alias_identity['id'], self.device, 'alice'))
        status = self.kb.sync('alice', self.device, [self.device, alias_identity['id']])
        self.assertEqual(status['indexedRecords'], 1)
        self.assertEqual(self.kb.search('alice', alias_identity['id'], ['天气'])[0]['text'], '别名端采集天气')

    def test_window_follows_source_order_despite_reverse_upload(self):
        records = [self.event('第' + str(i), kind='user_message' if i in (1, 4, 7) else 'assistant_message', seq=i) for i in range(1, 9)]
        self.ingest(list(reversed(records)))
        self.sync_all()
        result = self.kb.window('alice', self.device, 'workbuddy', 's', records[4]['id'], before=1, after=1)
        self.assertEqual([r['id'] for r in result], [r['id'] for r in records])
        self.assertEqual([r['event']['_seq'] for r in result], list(range(8)))
        self.assertTrue(all(r['orderBasis'] == 'source_file_offset' for r in result))

    def test_tool_result_far_from_user_includes_its_boundary(self):
        user = self.event('写一个解析天气的工具', seq=1)
        events = [user] + [self.event('执行中' + str(i), kind='reasoning', seq=i + 2) for i in range(80)]
        tool = self.event(kind='tool_result', payload={'output': 'weather finished'}, seq=90, callId='c1')
        events.append(tool)
        self.ingest(events)
        self.sync_all()
        result = self.kb.window('alice', self.device, 'workbuddy', 's', tool['id'], before=0, after=0)
        self.assertEqual(result[0]['id'], user['id'])
        self.assertEqual(result[-1]['id'], tool['id'])

    def test_large_turns_keep_users_anchor_and_label_finite_coverage(self):
        user = self.event('大任务的原始需求', seq=1)
        rows = [user] + [self.event('思路' + str(i), kind='reasoning', seq=i + 2) for i in range(620)]
        anchor = rows[300]
        self.ingest(rows)
        self.sync_all()
        window = self.kb.window('alice', self.device, 'workbuddy', 's', anchor['id'], before=0, after=0)
        self.assertLessEqual(len(window), 500)
        self.assertIn(user['id'], [r['id'] for r in window])
        self.assertIn(anchor['id'], [r['id'] for r in window])
        self.assertTrue(window[0]['windowCoverage']['truncated'])

    def test_missing_user_not_invented_and_zero_timestamps_use_offsets(self):
        events = [self.event('返回1', kind='tool_result', seq=1, timestamp=0), self.event('返回2', kind='tool_result', seq=2, timestamp=0)]
        self.ingest(list(reversed(events)))
        self.sync_all()
        window = self.kb.window('alice', self.device, 'workbuddy', 's', events[1]['id'])
        self.assertEqual([r['id'] for r in window], [e['id'] for e in events])
        self.assertEqual(window[0]['windowCoverage']['basis'], 'missing_preceding_user')
        self.assertTrue(all('prompt' not in r for r in window))

    def test_milliseconds_and_nested_item_fields(self):
        event = self.event(kind='reasoning', timestamp='1700000000123', payload={'item': {'id': 'i1', 'parentId': 'p1', 'content': [{'text': '查询天气的思路'}]}})
        self.ingest([event])
        self.sync_all()
        hit = self.kb.search('alice', self.device, ['查询天气'])[0]
        self.assertAlmostEqual(hit['timestamp'], 1700000000.123)
        self.assertEqual(hit['event']['payload']['parentId'], 'p1')

    def test_tool_return_preserves_exit_status_beside_output(self):
        event = self.event(kind='tool_result', payload={'output': {'stdout': '天气查询没有响应', 'exitCode': 22, 'stderr': 'HTTP 503'}})
        self.ingest([event])
        self.sync_all()
        hit = self.kb.search('alice', self.device, ['天气'])[0]
        self.assertIn('"exitCode": 22', hit['text'])
        self.assertIn('HTTP 503', hit['text'])

    def test_inputs_are_bounded_and_query_quotes_do_not_inject_sql(self):
        self.ingest([self.event('登录认证 login auth')])
        self.sync_all()
        self.assertTrue(self.kb.search('alice', self.device, ['登录认证', 'login']))
        self.assertEqual(self.kb.search('alice', self.device, ['" OR 1=1 --']), [])
        for limit in (0, 501, True):
            with self.assertRaises(ValueError):
                self.kb.sync('alice', self.device, limit=limit)
        with self.assertRaises(ValueError):
            self.kb.search('alice', self.device, ['x' * 2001])


if __name__ == '__main__':
    unittest.main()
