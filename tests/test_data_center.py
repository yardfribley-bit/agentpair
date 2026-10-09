"""Retained evidence search: no cloud, model, real device or network access."""
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

from agentpair.collection_view import CollectionView
from agentpair.data_center import DataCenter, parse_query
from agentpair.devices import DeviceStore
from agentpair.session_lens import SessionStore


class DataCenterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.devices = DeviceStore(self.root / 'devices.db')
        self.sessions = SessionStore(self.root / 'session-lens.db')
        self.identities = {}
        for owner in ('alice', 'bob'):
            enrolled = self.devices.enroll(self.devices.pairing(owner)['code'], 'Same MacBook')
            self.identities[owner] = self.devices.identity(enrolled['token'])
        self.center = DataCenter(CollectionView(self.devices, self.sessions))

    def tearDown(self):
        self.tmp.cleanup()

    def event(self, label, kind='user_message', text='查看天气', source='codex', session='same-session', timestamp=1700000000, **extra):
        return {'schemaVersion': 1, 'id': hashlib.sha256(label.encode()).hexdigest(), 'sessionId': session,
                'source': source, 'kind': kind, 'timestamp': timestamp,
                'payload': {'content': text}, 'evidence': {'path': '/fixture/private.jsonl', 'byteStart': 1, 'byteEnd': 200}, **extra}

    def upload(self, events, owner='alice', identity=None):
        for index in range(0, len(events), 30):
            self.sessions.ingest(identity or self.identities[owner], {'schemaVersion': 1, 'events': events[index:index + 30]})

    def context(self, label, body, owner='alice', source='workbuddy_generation_context', destination='', received=1700000100, timestamp=1700000001):
        ident = hashlib.sha256(label.encode()).hexdigest()
        record = {'id': ident, 'source': source, 'body': body, 'timestamp': timestamp,
                  'sessionId': 'same-session', 'destination': destination, 'truncated': False,
                  'bodySHA256': hashlib.sha256(body.encode()).hexdigest()}
        with self.devices.connect() as db:
            db.execute('INSERT OR REPLACE INTO applens_model_context VALUES(?,?,?,?)',
                       (self.identities[owner]['id'], ident, json.dumps(record, ensure_ascii=False), received))
        return ident

    def find(self, q='', **filters):
        return self.center.search({'q': q, **filters})

    def test_repeated_receipt_does_not_reparse_unchanged_large_context(self):
        ident = self.context('large-repeat', '大段已保留背景\n' * 2000 + 'receipt-check')
        first = self.find('receipt-check')
        self.assertEqual(first['total'], 1)
        with self.devices.connect() as db:
            db.execute('UPDATE applens_model_context SET received=received+1 WHERE request_id=?',(ident,))
        with patch('agentpair.data_center._app_projection', side_effect=AssertionError('Unchanged body reparsed')):
            repeated = self.find('receipt-check')
        self.assertEqual(repeated['total'], 1)
        self.assertEqual(repeated['items'][0]['receivedAt'], first['items'][0]['receivedAt'] + 1)

    def test_shell_control_options_and_url_fragments_are_not_program_names(self):
        from agentpair.data_center import _programs
        parsed = _programs('if true; then echo ok; else echo failed; fi\n$(ls -d /tmp); $YT https://example.test/watch?v=x; curl https://example.test; -d value')
        self.assertEqual(parsed, ['curl'])
        self.assertEqual(_programs("python3 - <<'PY'\nimport json\ndef example():\n    pass\nPY\ncurl https://example.test"),['curl','python3'])

    def test_complete_history_and_long_body_middle_not_latest_200_or_prefix(self):
        events = [self.event(str(i), text='普通记录', timestamp=1700000000 + i) for i in range(260)]
        events[0]['payload']['content'] = '旧的历史独有提问'
        self.upload(events)
        self.context('old-app', 'a' * 18000 + '历史上下文独有凭据suffix-secret' + 'z' * 18000, timestamp=1600000000)
        for i in range(205):
            self.context('app-' + str(i), '最新普通上下文', received=1700000200 + i)
        self.assertEqual(self.find('历史独有提问')['total'], 1)
        hit = self.find('suffix-secret')['items'][0]
        self.assertIn('suffix-secret', hit['excerpt'])
        self.assertEqual(hit['matchBasis'][0]['field'], 'context')
        self.assertEqual(self.find()['total'], 466)
        self.assertTrue(self.find()['coverage']['fullRetainedText'])
        detail = self.center.record(hit['id'])['item']
        self.assertEqual(detail['raw']['body'], 'a' * 18000 + '历史上下文独有凭据suffix-secret' + 'z' * 18000)
        self.assertIn('/api/data-center/raw?', detail['rawUrl'])
        self.assertIn('/model-data/raw?', detail['legacyRawUrl'])

    def test_same_ids_names_sessions_call_ids_never_merge_different_owner_or_device(self):
        shared = self.event('shared', text='相同事件标识')
        self.upload([shared]);self.upload([shared], 'bob')
        hits = self.find('相同事件标识')['items']
        self.assertEqual(len(hits), 2)
        self.assertEqual(len({h['id'] for h in hits}), 2)
        self.assertEqual(len({h['deviceId'] for h in hits}), 2)
        alice = next(h for h in hits if h['ownerAccount'] == 'alice')
        with self.assertRaises(KeyError):
            self.center.record(alice['id'], owner='bob')
        self.assertEqual(self.center.search({'q': '相同事件标识'}, owner='alice')['total'], 1)
        self.assertEqual(alice['operator'], '未确认')

    def test_alias_history_maps_canonical_and_identical_evidence_deduplicates_only_aliases(self):
        alias_enrollment = self.devices.enroll(self.devices.pairing('alice')['code'], '旧注册')
        alias = self.devices.identity(alias_enrollment['token'])
        event = self.event('alias', text='别名历史内容')
        self.upload([event], identity=alias);self.upload([event])
        self.assertEqual(self.find('别名历史内容')['total'], 2)
        self.devices.merge_registrations('alice', self.identities['alice']['id'], [alias['id']])
        result = self.find('别名历史内容')
        self.assertEqual(result['total'], 1)
        self.assertEqual(result['coverage']['duplicateAliasRecords'], 1)
        self.assertEqual(result['items'][0]['deviceId'], self.identities['alice']['id'])
        self.assertEqual(self.find('别名历史内容', device=alias['id'])['total'], 1)

    def test_ip_exact_tokens_text_mention_tool_target_http_target_distinct(self):
        self.upload([self.event('a', text='备注 192.0.2.1，无需连接'),
                     self.event('b', text='备注 192.0.2.10'), self.event('c', text='备注 1192.0.2.1'),
                     self.event('curl', 'tool_call', name='Bash', callId='call-1',
                                payload={'arguments': {'cmd': 'curl -I https://192.0.2.1:443/status'}})])
        self.context('http', '{"messages":[]}', source='workbuddy_network_context', destination='https://192.0.2.1/model')
        result = self.find('ip="192.0.2.1"')
        self.assertEqual(result['total'], 3)
        self.assertEqual({h['addressBasis'] for h in result['items']}, {'not_collected', 'tool_argument_target', 'captured_request_target'})
        self.assertEqual({h['matchBasis'][0]['addressBasis'] for h in result['items']}, {'text_mention', 'tool_argument_target', 'captured_request_target'})
        self.assertEqual(self.find('192.0.2.1')['total'], 3)
        self.assertEqual(self.find('ip="192.0.2.10"')['total'], 1)
        self.assertTrue(all(h['confirmation'] == 'recorded' for h in result['items']))
        self.assertIsNone(result['summary']['observedThreats'])

    def test_domain_exact_token_and_url_in_echo_only_a_mention(self):
        self.upload([self.event('root-domain', 'tool_call', name='Bash', payload={'arguments': {'cmd': 'echo https://example.com/foo'}}),
                     self.event('subdomain', text='https://sub.example.com/foo'),
                     self.event('superdomain', text='https://example.com.evil/foo')])
        result = self.find('domain=example.com')
        self.assertEqual(result['total'], 1)
        self.assertEqual(result['items'][0]['addressBasis'], 'not_collected')
        self.assertEqual(result['items'][0]['matchBasis'][0]['addressBasis'], 'text_mention')
        self.assertIsNone(result['items'][0]['destination'])

    def test_tool_function_program_separate_and_arguments_and_return_readable(self):
        user = self.event('weather', text='查上海天气', payload={'id': 'source-user', 'content': '查上海天气'})
        call = self.event('call', 'tool_call', timestamp=1700000001, name='functions.exec_command', callId='c1', sourceSubtype='function_call',
                          payload={'id': 'source-call', 'parentId': 'source-user', 'arguments': '{"cmd":"curl https://example.com/weather?q=shanghai"}'})
        result = self.event('result', 'tool_result', timestamp=1700000002, callId='c1',
                            payload={'parentId': 'source-call', 'output': {'temperature': 23, 'unit': 'C'}})
        self.upload([user, call, result])
        hit = self.find('tool=functions.exec_command && function=functions.exec_command && command=curl')['items'][0]
        self.assertEqual(hit['function'], 'functions.exec_command')
        self.assertTrue(hit['command'].startswith('curl '))
        self.assertEqual(self.find('tool=curl')['total'], 0)
        detail = self.center.record(hit['id'])['item']
        self.assertEqual(detail['arguments'], {'cmd': 'curl https://example.com/weather?q=shanghai'})
        self.assertEqual(detail['result'], {'temperature': 23, 'unit': 'C'})
        self.assertEqual(detail['taskContext']['userInput'], '查上海天气')
        self.assertEqual(detail['taskContext']['association'], 'recorded')
        self.assertTrue(any(r['relation'] == 'tool_call_result' for r in detail['relations']))
        self.assertIn('多轮', detail['taskContext']['limitation'])

    def test_call_pair_scoped_source_and_ambiguous_call_not_paired(self):
        call = self.event('call', 'tool_call', source='workbuddy', name='Bash', callId='same', payload={'arguments': {'cmd': 'pwd'}})
        other = self.event('other-result', 'tool_result', source='codex', callId='same', payload={'output': '/secret'})
        self.upload([call, other])
        self.upload([self.event('other-owner', 'tool_result', source='workbuddy', callId='same', payload={'output': '/bob'})], 'bob')
        hit = self.find('tool=Bash')['items'][0]
        detail = self.center.record(hit['id'])['item']
        self.assertIsNone(detail['result'])
        self.assertFalse(any(r['relation'] == 'tool_call_result' for r in detail['relations']))
        self.upload([self.event('duplicate', 'tool_call', source='workbuddy', name='Bash', callId='same', payload={'arguments': {'cmd': 'ls'}}),
                     self.event('result', 'tool_result', source='workbuddy', callId='same', payload={'output': '/alice'})])
        detail = self.center.record(hit['id'])['item']
        self.assertIsNone(detail['result'])
        self.assertTrue(any('唯一' in gap for gap in detail['gaps']))

    def test_nested_function_arguments_detail_agrees_with_index_and_paired_return(self):
        call = self.event('nested-call','tool_call',name='Bash',callId='nested',
                          payload={'function':{'name':'Bash','arguments':'{"cmd":"curl https://example.com"}'}})
        result = self.event('nested-result','tool_result',callId='nested',timestamp=1700000001,payload={'output':'returned'})
        self.upload([call,result])
        hit = self.find('function=Bash && command=curl')['items'][0]
        detail = self.center.record(hit['id'])['item']
        self.assertEqual(detail['arguments'],{'cmd':'curl https://example.com'})
        result_hit = self.find(kind='tool_result')['items'][0]
        paired = self.center.record(result_hit['id'])['item']
        self.assertEqual(paired['arguments'],detail['arguments'])
        self.assertEqual(paired['result'],'returned')

    def test_source_session_not_task_background_is_candidate_and_control_not_goal(self):
        user = self.event('real-user', text='写登录页面', timestamp=1700000000)
        notification = self.event('notice', text='<task-notification><task-id>bg1</task-id>已完成</task-notification>', timestamp=1700000001)
        call = self.event('execution', 'tool_call', name='Bash', timestamp=1700000002, payload={'arguments': {'cmd': 'pwd'}})
        self.upload([user, notification, call])
        hit = self.find('tool=Bash')['items'][0]
        detail = self.center.record(hit['id'])['item']
        self.assertEqual(detail['taskContext']['userInput'], '写登录页面')
        self.assertEqual(detail['taskContext']['association'], 'candidate')
        self.assertFalse(any(r['relation'] == 'source_parent' for r in detail['relations']))
        self.assertTrue(any('容器' in gap for gap in detail['gaps']))
        self.assertIn('后台通知', self.find('bg1')['items'][0]['title'])

    def test_filters_boolean_precedence_pagination_and_millisecond_iso_time(self):
        self.upload([self.event('a', text='甲天气', source='codex', timestamp=1700000000123),
                     self.event('b', text='乙天气', source='workbuddy', timestamp='2023-11-14T22:13:21Z'),
                     self.event('c', text='甲视频', source='workbuddy', timestamp=1700000002)])
        result = self.find('甲 && app=codex || 乙 && app=workbuddy', page='1', pageSize='1')
        self.assertEqual(result['total'], 2);self.assertTrue(result['hasMore'])
        next_page = self.find('甲 && app=codex || 乙 && app=workbuddy', page='2', pageSize='1')
        self.assertNotEqual(next_page['items'][0]['id'], result['items'][0]['id'])
        self.assertFalse(next_page['hasMore'])
        self.assertEqual(self.find('app!=codex')['total'], 2)
        self.assertEqual(self.find('天气', application='codex', after='1700000000', before='1700000001')['total'], 1)
        self.assertEqual(self.find('天气', location='arguments')['total'], 0)
        self.assertAlmostEqual(self.find('甲天气')['items'][0]['timestamp'], 1700000000.123)

    def test_paging_snapshot_excludes_new_records_and_scope_count_honest(self):
        self.upload([self.event('old-'+str(i),text='同一查询',timestamp=1700000000+i) for i in range(4)])
        self.upload([self.event('bob',text='另一个账号')],'bob')
        first = self.find('同一查询',device=self.identities['alice']['id'],pageSize='2')
        self.assertEqual(first['coverage']['scopeRecords'],4)
        self.assertEqual(first['coverage']['retainedRecords'],5)
        self.upload([self.event('new',text='同一查询',timestamp=1800000000)])
        second = self.find('同一查询',device=self.identities['alice']['id'],pageSize='2',page='2',snapshot=first['snapshot'])
        self.assertEqual(second['total'],4)
        self.assertEqual(len({h['id'] for h in first['items']+second['items']}),4)
        self.assertEqual(self.find('同一查询')['total'],5)
        with self.assertRaises(ValueError):self.find('不同查询',snapshot=first['snapshot'])
        with self.assertRaises(ValueError):self.find('同一查询',snapshot='broken!')

    def test_snapshot_alias_dedup_never_observes_later_canonical_replay(self):
        enrolled = self.devices.enroll(self.devices.pairing('alice')['code'],'old install')
        alias = self.devices.identity(enrolled['token'])
        event = self.event('late-canonical',text='快照历史')
        self.upload([event],identity=alias)
        self.devices.merge_registrations('alice',self.identities['alice']['id'],[alias['id']])
        first = self.find('快照历史')
        self.upload([event])
        frozen = self.find('快照历史',snapshot=first['snapshot'])
        self.assertEqual(frozen['total'],1)
        self.assertEqual(frozen['items'][0]['id'],first['items'][0]['id'])
        self.assertEqual(self.find('快照历史')['total'],1)

    def test_unique_raw_link_preserves_alias_conflicting_payload_identity(self):
        enrolled = self.devices.enroll(self.devices.pairing('alice')['code'],'old install')
        alias = self.devices.identity(enrolled['token'])
        event = self.event('conflict-id',text='canonical-original-one')
        self.upload([event])
        self.upload([{**event,'payload':{'content':'alias-different-two'}}],identity=alias)
        self.devices.merge_registrations('alice',self.identities['alice']['id'],[alias['id']])
        first = self.find('canonical-original-one')['items'][0]
        second = self.find('alias-different-two')['items'][0]
        self.assertNotEqual(first['rawUrl'],second['rawUrl'])
        self.assertEqual(self.center.raw(first['id'])['payload']['content'],'canonical-original-one')
        self.assertEqual(self.center.raw(second['id'])['payload']['content'],'alias-different-two')

    def test_incremental_update_and_source_receipt_honesty(self):
        self.upload([self.event('old', text='第一版')])
        self.assertEqual(self.find('新增')['total'], 0)
        self.upload([self.event('new', text='新增事件')])
        hit = self.find('新增')['items'][0]
        self.assertIsNone(hit['receivedAt'])
        self.assertEqual(self.center.record(hit['id'])['item']['sourceEvidence']['receivedBasis'], 'not_collected_per_event')
        self.context('upsert', '初始正文', received=1700000100)
        self.assertEqual(self.find('初始正文')['total'], 1)
        self.context('upsert', '更新正文', received=1700000101)
        self.assertEqual(self.find('更新正文')['total'], 1)
        self.assertEqual(self.find('初始正文')['total'], 0)
        self.assertEqual(self.find('更新正文')['items'][0]['receivedAt'], 1700000101)

    def test_physical_index_and_shared_writer_lock_avoid_history_quadratic_scan(self):
        second = DataCenter(CollectionView(self.devices,self.sessions))
        self.assertIs(second.lock,self.center.lock)
        with self.center._db() as db:
            plan = ' '.join(r[3] for r in db.execute('EXPLAIN QUERY PLAN SELECT key FROM dc_records WHERE collector=? AND seq=?',('sessionlens',1)))
            self.assertIn('dc_records_physical',plan)
            self.assertIn('collector=? AND seq=?',plan)

    def test_invalid_query_and_parameters_are_explicit_not_ignored(self):
        for q in ('tool=', '&& hello', 'hello ||', 'unknown=foo', '(hello)', 'ip=192.0.2', 'domain=https://example.com', 'tool="unterminated', 'a & b'):
            with self.subTest(q=q), self.assertRaises(ValueError):
                self.find(q)
        for values in ({'page': '0'}, {'pageSize': '101'}, {'location': 'memory'}, {'collector': 'agents'}, {'after': 'yesterday'}, {'bogus': 'ignored'}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.center.search(values)
        self.assertEqual(parse_query('"two words" && collector!=applens || tool="Bash"'),
                         [[('keyword', '=', 'two words'), ('collector', '!=', 'applens')], [('tool', '=', 'Bash')]])

    def test_large_detail_valid_json_and_unredacted_original_fields(self):
        secret = 'very-secret-password-123456'
        text = 'x' * 100000 + secret + 'z' * 100000
        self.upload([self.event('large', text=text)])
        hit = self.find(secret)['items'][0]
        detail = self.center.record(hit['id'])
        encoded = json.dumps(detail, ensure_ascii=False, allow_nan=False)
        decoded = json.loads(encoded)
        self.assertEqual(decoded['item']['content']['content'], text)
        self.assertIn(secret, hit['excerpt'])

    def test_malformed_message_container_preserves_full_raw_detail(self):
        for value in (None,1,'not an array'):
            body = json.dumps({'messages':value,'note':'valid retained null payload'})
            self.context('malformed-'+str(value),body)
        result = self.find('valid retained null payload')
        self.assertEqual(result['total'],3)
        for hit in result['items']:
            detail = self.center.record(hit['id'])['item']
            self.assertEqual(detail['content'],detail['raw']['body'])
            self.assertEqual(detail['contextItems'][0]['rawContent'],detail['raw']['body'])
            self.assertTrue(any('无法分类' in gap for gap in detail['gaps']))

    def test_address_match_never_relabels_a_different_actual_destination(self):
        self.context('target','{"messages":[{"role":"user","content":"记录 192.0.2.1"}]}',
                     source='workbuddy_network_context',destination='https://api.example.com/model')
        for query in ('ip=192.0.2.1','192.0.2.1'):
            hit = self.find(query)['items'][0]
            self.assertEqual(hit['destination'],'https://api.example.com/model')
            self.assertEqual(hit['addressBasis'],'captured_request_target')
            self.assertEqual(hit['matchBasis'][0]['addressBasis'],'text_mention')


class DataCenterAPITests(unittest.TestCase):
    """Exercise real GET dispatch in process, avoiding sockets and model calls."""
    def setUp(self):
        from agentpair.platform import handler_for
        from agentpair.tasks import TaskEngine
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        class Backend:
            def estimate(self, *args):
                raise AssertionError('Search cannot reserve a model request')
            def call(self, *args, **kwargs):
                raise AssertionError('Search cannot invoke a model')
        self.engine = TaskEngine(self.root / 'tasks.db', Backend(), start=False)
        self.handler = handler_for(self.engine, 'fixture-password-12345', 'http://127.0.0.1')
        devices = DeviceStore(self.root / 'devices.db')
        enrolled = devices.enroll(devices.pairing('alice')['code'], 'API Mac')
        self.identity = devices.identity(enrolled['token'])
        SessionStore(self.root / 'session-lens.db').ingest(self.identity, {'schemaVersion': 1, 'events': [{
            'schemaVersion': 1, 'id': 'a' * 64, 'source': 'codex', 'sessionId': 'api-session',
            'kind': 'user_message', 'timestamp': 1700000000, 'payload': {'content': '公开原始提问'}, 'evidence': {}}]})

    def tearDown(self):
        self.engine.close();self.tmp.cleanup()

    def get(self, path):
        h = object.__new__(self.handler)
        h.path = path;h.headers = {};h.wfile = io.BytesIO()
        responses = []
        h.respond = lambda status, data, cookie=False: responses.append((status, data))
        h.do_GET()
        return responses[0]

    def test_anonymous_global_search_and_detail_without_model_charge(self):
        status, result = self.get('/api/data-center/search?' + urlencode({'q': '公开', 'pageSize': 20}))
        self.assertEqual(status, 200);self.assertEqual(result['total'], 1)
        status, detail = self.get('/api/data-center/record?' + urlencode({'id': result['items'][0]['id']}))
        self.assertEqual(status, 200)
        self.assertEqual(detail['item']['raw']['payload']['content'], '公开原始提问')
        raw_status, raw = self.get(result['items'][0]['rawUrl'])
        self.assertEqual(raw_status,200);self.assertEqual(raw['payload']['content'],'公开原始提问')
        with self.engine.connection() as db:
            self.assertEqual(db.execute('SELECT reserved FROM ledger').fetchone()[0], 0)

    def test_invalid_get_returns_json_400_and_legacy_api_remains_available(self):
        self.assertEqual(self.get('/api/data-center/search?q=bogus%3Dfoo')[0], 400)
        self.assertEqual(self.get('/api/data-center/search?q=a&q=b')[0], 400)
        self.assertEqual(self.get('/api/data-center/record?id=bad')[0], 400)
        status, legacy = self.get('/api/audit/model-data/' + self.identity['id'])
        self.assertEqual(status, 200);self.assertEqual(len(legacy['calls']), 1)


if __name__ == '__main__':
    unittest.main()
