"""Structured capability evidence and scoped rankings, without external calls."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agentpair.collection_view import CollectionView
from agentpair.data_center import DataCenter, parse_query
from agentpair.devices import DeviceStore
from agentpair.session_lens import SessionStore


class CapabilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.devices = DeviceStore(self.root / 'devices.db')
        self.sessions = SessionStore(self.root / 'session-lens.db')
        self.identities = {}
        for owner in ('alice', 'bob'):
            enrolled = self.devices.enroll(self.devices.pairing(owner)['code'], 'Same Mac')
            self.identities[owner] = self.devices.identity(enrolled['token'])
        self.center = DataCenter(CollectionView(self.devices, self.sessions))

    def tearDown(self):
        self.tmp.cleanup()

    def event(self, label, name=None, arguments=None, source='workbuddy', kind='tool_call',
              stamp=1700000000, session='same-session', **extra):
        event = {'schemaVersion': 1, 'id': hashlib.sha256(label.encode()).hexdigest(), 'sessionId': session,
                 'source': source, 'kind': kind, 'timestamp': stamp, 'payload': {'arguments': arguments},
                 'evidence': {'path': '/fixture/session.jsonl', 'byteStart': 0, 'byteEnd': 100}}
        if name is not None:
            event['name'] = name
        event.update(extra)
        return event

    def upload(self, events, owner='alice', identity=None):
        for start in range(0, len(events), 30):
            self.sessions.ingest(identity or self.identities[owner], {'schemaVersion': 1, 'events': events[start:start + 30]})

    def search(self, q='', **params):
        return self.center.search({'q': q, **params})

    def test_skill_tool_load_and_instruction_read_have_distinct_evidence(self):
        original = {'skill': 'agent-browser', 'args': 'unchanged extra input'}
        self.upload([self.event('loaded', 'Skill', original),
                     self.event('read', 'Read', {'file_path': '/home/a/.agents/skills/agent-browser/SKILL.md'}),
                     self.event('read-only', 'Read', {'path': '/home/a/.codex/skills/youtube-research-cn/SKILL.md'}),
                     self.event('result', 'Skill', kind='tool_result', payload={'output': 'loaded'})])
        result = self.search('skill=agent-browser')
        self.assertEqual(result['total'], 2)
        self.assertEqual({r['capabilities'][0]['evidence'] for r in result['items']}, {'loaded', 'read'})
        loaded = next(r for r in result['items'] if r['tool'] == 'Skill')
        self.assertEqual(self.center.record(loaded['id'])['item']['arguments'], original)
        rank = self.center.capabilities()
        row = rank['skills'][0]
        self.assertEqual((row['name'], row['loadCount'], row['readCount'], row['activityCount']), ('agent-browser', 1, 1, 2))
        read_only = next(r for r in rank['skills'] if r['name'] == 'youtube-research-cn')
        self.assertEqual((read_only['loadCount'], read_only['readCount']), (0, 1))
        self.assertIn('说明读取', ' '.join(rank['coverage']['limitations']))
        self.assertEqual(next(r for r in rank['tools'] if r['name'] == 'Skill')['callCount'], 1)

    def test_skill_mentions_writes_listing_samples_and_shell_control_are_not_activity(self):
        path = '/home/a/.codex/skills/example/SKILL.md'
        bad = [self.event('mention', kind='user_message', payload={'content': 'Skill example mcp__browser__open'}),
               self.event('write', 'Write', {'file_path': path, 'content': 'instructions'}),
               self.event('list', 'Bash', {'command': 'ls ' + path}),
               self.event('echo', 'Bash', {'command': 'echo cat ' + path}),
               self.event('heredoc', 'Bash', {'command': "cat > /tmp/script <<'PY'\ncat " + path + "\nPY"}),
               self.event('write-sed', 'Bash', {'command': "sed -i '1,20p' " + path}),
               self.event('loop', 'Bash', {'command': 'for x in a; do cat ' + path + '; done'}),
               self.event('shell-string', 'Bash', {'command': "bash -c 'cat " + path + "'"}),
               self.event('sample', 'Read', {'file_path': '/tmp/audit-samples/skills/example/SKILL.md'}),
               self.event('testdir', 'Read', {'file_path': '/tmp/skilltest/example/SKILL.md'})]
        self.upload(bad)
        self.assertEqual(self.search('skill=example')['total'], 0)
        self.assertEqual(self.search(object='skill')['total'], 0)
        self.assertEqual(self.center.capabilities()['skills'], [])
        self.assertGreater(self.search('example')['total'], 0)

    def test_windows_read_and_system_skill_paths(self):
        self.upload([self.event('windows', 'Read', {'file_path': r'C:\Users\Alice\.agents\skills\writer\SKILL.md'}),
                     self.event('system', 'Read', {'file_path': '/Users/alice/.codex/skills/.system/imagegen/SKILL.md'})])
        self.assertEqual(self.search('skill=writer')['total'], 1)
        self.assertEqual(self.search('skill=imagegen')['total'], 1)
        self.assertEqual(self.search('skill=writer')['items'][0]['capabilities'][0]['path'], r'C:\Users\Alice\.agents\skills\writer\SKILL.md')

    def test_literal_shell_reads_deduplicate_same_skill_per_source_record(self):
        path = '/home/a/.agents/skills/reader/SKILL.md'
        self.upload([self.event('cat', 'Bash', {'command': 'cat ' + path + ' && head -n 50 ' + path}),
                     self.event('sed', 'Bash', {'command': "sed -n '1,200p' " + path}),
                     self.event('tail', 'Bash', {'command': 'tail -20 ' + path})])
        row = self.center.capabilities()['skills'][0]
        self.assertEqual((row['loadCount'], row['readCount'], row['activityCount']), (0, 3, 3))

    def test_namespace_and_direct_mcp_identity_method_search_and_original_parameters(self):
        args = {'code': 'unchanged()', 'title': '真实参数'}
        self.upload([self.event('namespace', 'js', source='codex', payload={'namespace': 'mcp__cua_repl', 'name': 'js', 'arguments': json.dumps(args)}),
                     self.event('direct', 'mcp__cua_repl__js', source='codex', arguments={'code': 'second()'}),
                     self.event('other-server', 'js', source='codex', payload={'namespace': 'mcp__other', 'arguments': {'code': 'third()'}}),
                     self.event('other-method', 'mcp__cua_repl__getState', source='codex', arguments={}),
                     self.event('tool-result', 'mcp__cua_repl__js', source='codex', kind='tool_result')])
        self.assertEqual(self.search('mcp=cua_repl && mcp_method=js')['total'], 2)
        self.assertEqual(self.search('mcp="mcp__cua_repl"')['total'], 3)
        self.assertEqual(self.search('tool=js')['total'], 2)
        hit = self.search('mcp=cua_repl && tool=js')['items'][0]
        self.assertEqual(self.center.record(hit['id'])['item']['arguments'], args)
        self.assertEqual(hit['capabilities'][0]['namespace'], 'mcp__cua_repl')
        rank = self.center.capabilities()
        row = rank['mcps'][0]
        self.assertEqual((row['name'], row['callCount'], row['directCount'], row['wrappedCount']), ('cua_repl', 3, 3, 0))
        self.assertEqual(row['methods'][0]['name'], 'js')
        self.assertEqual(row['methods'][0]['callCount'], 2)
        self.assertEqual(self.search(row['methods'][0]['searchQuery'])['total'], 2)
        self.assertEqual(self.search(object='mcp')['total'], 4)

    def test_wrapped_tdoc_calls_with_stderr_merge_but_not_help_or_written_examples(self):
        prefix = 'python3 /home/a/skills/tencent-docs/scripts/tencentdocs.py tdoc_call '
        good = 'cd /home/a/skills/tencent-docs && ' + prefix + "doc-mcp create_with_markdown '{}' 2>&1"
        self.upload([self.event('wrapped', 'Bash', {'command': good}),
                     self.event('wrapped2', 'Bash', {'command': prefix + "tencent-docs get_content '{}' 2>&1"}),
                     self.event('help', 'Bash', {'command': prefix + 'doc-mcp create_with_markdown --help'}),
                     self.event('echo-call', 'Bash', {'command': "echo '" + prefix + "doc-mcp create_with_markdown'"}),
                     self.event('written', 'Bash', {'command': "cat <<'PY' > /tmp/a.py\n" + prefix + "doc-mcp create_with_markdown '{}'\nPY"}),
                     self.event('pycode', 'Bash', {'command': "python3 -c '" + prefix + "doc-mcp create_with_markdown'"}),
                     self.event('control', 'Bash', {'command': 'if true; then ' + prefix + 'doc-mcp create_with_markdown; fi'}),
                     self.event('comment', 'Bash', {'command': '# ' + prefix + 'doc-mcp create_with_markdown'}),
                     self.event('result', 'Bash', kind='tool_result', payload={'output': {'exit_code': 0, 'status': 'completed'}})])
        rank = self.center.capabilities()
        self.assertEqual(len(rank['mcps']), 2)
        self.assertEqual(sum(r['callCount'] for r in rank['mcps']), 2)
        self.assertTrue(all(r['directCount'] == 0 and r['wrappedCount'] == 1 for r in rank['mcps']))
        self.assertFalse(rank['coverage']['successInferred'])
        detail = self.center.record(self.search('mcp=doc-mcp')['items'][0]['id'])['item']
        self.assertEqual(detail['arguments']['command'], good)

    def test_function_only_skill_identity_and_unknown_tool_count(self):
        self.upload([self.event('function-skill', payload={'function': {'name': 'Skill', 'arguments': '{"name":"nested-skill"}'}}),
                     self.event('unknown', arguments={'text': 'no tool name'})])
        self.assertEqual(self.search('skill=nested-skill')['total'], 1)
        rank = self.center.capabilities()
        self.assertEqual(rank['coverage']['unknownToolCount'], 1)
        self.assertEqual(rank['tools'][0]['name'], 'Skill')
        self.assertEqual(self.search(object='tool')['total'], 1)

    def test_filters_scope_time_alias_deduplication_and_source_session_count(self):
        same = self.event('same', 'Skill', {'skill': 'shared'}, stamp=1700000000)
        self.upload([same, self.event('codex', 'Skill', {'skill': 'shared'}, source='codex', stamp=1700000001)])
        self.upload([same], 'bob')
        enrolled = self.devices.enroll(self.devices.pairing('alice')['code'], 'old')
        alias = self.devices.identity(enrolled['token'])
        self.upload([same], identity=alias)
        self.devices.merge_registrations('alice', self.identities['alice']['id'], [alias['id']])
        rank = self.center.capabilities()
        row = rank['skills'][0]
        self.assertEqual((row['loadCount'], row['deviceCount'], row['accountCount'], row['sourceSessionCount']), (3, 2, 2, 3))
        self.assertEqual(row['applications'], ['codex', 'workbuddy'])
        self.assertEqual(rank['coverage']['duplicateAliasRecords'], 1)
        self.assertFalse(rank['coverage']['sourceSessionsAreTasks'])
        self.assertEqual(self.center.capabilities(owner='alice')['skills'][0]['loadCount'], 2)
        selected = self.center.capabilities({'device': alias['id'], 'application': 'codex', 'after': 1700000001, 'before': 1700000001})
        self.assertEqual(selected['skills'][0]['loadCount'], 1)
        self.assertEqual(self.center.capabilities({'collector': 'applens'})['skills'], [])
        self.assertEqual(self.center.capabilities({'kind': 'user'})['tools'], [])
        self.assertEqual(self.center.capabilities({'location': 'result'})['skills'], [])
        self.assertEqual(self.center.capabilities({'q': 'skill=shared && app=codex'})['skills'][0]['loadCount'], 1)

    def test_rank_snapshot_stable_and_bound_to_owner_filters_and_endpoint(self):
        self.upload([self.event('old', 'Skill', {'skill': 'stable'})])
        first = self.center.capabilities()
        self.upload([self.event('new', 'Skill', {'skill': 'stable'}, stamp=1800000000)])
        frozen = self.center.capabilities({'snapshot': first['snapshot']})
        self.assertEqual(frozen['skills'][0]['loadCount'], 1)
        self.assertEqual(self.center.capabilities()['skills'][0]['loadCount'], 2)
        for params, owner in [({'snapshot': first['snapshot'], 'q': 'stable'}, None),
                              ({'snapshot': first['snapshot'], 'object': 'skill'}, None),
                              ({'snapshot': first['snapshot']}, 'alice')]:
            with self.assertRaises(ValueError):
                self.center.capabilities(params, owner)
        with self.assertRaises(ValueError):
            self.search(snapshot=first['snapshot'])
        with self.assertRaises(ValueError):
            self.center.capabilities({'snapshot': self.search()['snapshot']})

    def test_top_limit_exact_query_and_no_raw_detail_expansion(self):
        names = ['a "quoted" skill', 'two'] + ['skill-' + str(i) for i in range(101)]
        self.upload([self.event(str(i), 'Skill', {'skill': name}, stamp=1700000000 + i) for i, name in enumerate(names)])
        with patch.object(self.center, '_lookup', side_effect=AssertionError('Rank loaded source detail')):
            rank = self.center.capabilities()
        self.assertEqual(len(rank['skills']), 100)
        self.assertEqual(rank['totals']['skills'], 103)
        self.assertTrue(rank['hasMore']['skills'])
        limited = self.center.capabilities({'pageSize': 1, 'q': 'skill="a \\"quoted\\" skill"'})
        self.assertEqual(limited['skills'][0]['name'], names[0])
        self.assertEqual(self.search(limited['skills'][0]['searchQuery'])['total'], 1)
        skill_only = self.center.capabilities({'object': 'skill'})
        self.assertEqual(skill_only['tools'], [])
        self.assertEqual(skill_only['mcps'], [])
        next_page = self.center.capabilities({'page': 2, 'snapshot': rank['snapshot']})
        self.assertEqual(len(next_page['skills']), 3)
        self.assertFalse(next_page['hasMore']['skills'])
        self.assertFalse({r['name'] for r in rank['skills']} & {r['name'] for r in next_page['skills']})
        for params in [{'object': 'unknown'}, {'page': 0}, {'pageSize': 101}]:
            with self.assertRaises(ValueError):
                self.center.capabilities(params)
        self.assertEqual(parse_query('skill=x && mcp=y && mcp_method=z')[0][0], ('skill', '=', 'x'))

    def test_captured_context_tool_declarations_are_not_ranked_again(self):
        body = json.dumps({'messages': [{'role': 'assistant', 'tool_calls': [{'function': {'name': 'Skill', 'arguments': '{"skill":"embedded"}'}}]}]})
        ident = hashlib.sha256(body.encode()).hexdigest()
        record = {'id': ident, 'source': 'workbuddy_generation_context', 'body': body,
                  'timestamp': 1700000000, 'sessionId': 'same-session'}
        with self.devices.connect() as db:
            db.execute('INSERT INTO applens_model_context VALUES(?,?,?,?)', (self.identities['alice']['id'], ident, json.dumps(record), 1700000010))
        self.assertEqual(self.search('embedded')['total'], 1)
        self.assertEqual(self.search('skill=embedded')['total'], 0)
        self.assertEqual(self.center.capabilities()['skills'], [])


if __name__ == '__main__':
    unittest.main()
