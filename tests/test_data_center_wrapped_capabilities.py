"""Real wrapper shapes, static false-positive guards and incremental migration."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agentpair.capability_evidence import javascript_calls
from agentpair.collection_view import CollectionView
from agentpair.data_center import DataCenter, VERSION
from agentpair.devices import DeviceStore
from agentpair.session_lens import SessionStore


class WrappedCapabilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        self.devices = DeviceStore(self.root / 'devices.db')
        self.sessions = SessionStore(self.root / 'session-lens.db')
        enrolled = self.devices.enroll(self.devices.pairing('fixture-owner')['code'], 'Fixture Mac')
        self.identity = self.devices.identity(enrolled['token'])
        self.center = DataCenter(CollectionView(self.devices, self.sessions))

    def tearDown(self):
        self.tmp.cleanup()

    def event(self, label, code, name='exec', **item):
        return {'schemaVersion': 1, 'id': hashlib.sha256(label.encode()).hexdigest(),
                'sessionId': 'fixture-session', 'source': 'codex', 'kind': 'tool_call',
                'timestamp': 1700000000, 'name': name,
                'payload': {'namespace': 'functions', 'call_id': 'outer-' + label, 'arguments': code, **item},
                'evidence': {'path': '/fixture/session.jsonl', 'byteStart': 100, 'byteEnd': 200}}

    def upload(self, events):
        for start in range(0, len(events), 30):
            self.sessions.ingest(self.identity, {'schemaVersion': 1, 'events': events[start:start + 30]})

    def search(self, query):
        return self.center.search({'q': query})

    def test_real_custom_skill_shell_read_inside_freeform_exec(self):
        path = '/Users/alice/product-references/taste-skill/SKILL.md'
        parameters = {'cmd': "sed -n '1,240p' " + path, 'workdir': '/Users/alice/project', 'max_output_tokens': 3000}
        code = 'text(await tools.exec_command(' + json.dumps(parameters) + '));'
        original = self.event('taste', code)
        self.upload([original])
        hit = self.search('skill="taste-skill"')
        self.assertEqual(hit['total'], 1)
        capability = hit['items'][0]['capabilities'][0]
        self.assertEqual((capability['name'], capability['evidence'], capability['path']), ('taste-skill', 'read', path))
        self.assertEqual(capability['arguments'], parameters)
        self.assertEqual(capability['originalName'], 'exec_command')
        self.assertEqual(capability['outerCallId'], 'outer-taste')
        self.assertTrue(code[capability['sourceOffset']:capability['sourceEnd']].startswith('tools.exec_command('))
        detail = self.center.record(hit['items'][0]['id'])['item']
        self.assertEqual(detail['arguments'], code)
        self.assertEqual(detail['raw'], original)
        rank = self.center.capabilities()['skills'][0]
        self.assertEqual((rank['loadCount'], rank['readCount']), (0, 1))

    def test_mcp_wrapper_service_alias_preserves_raw_identity_and_other_services(self):
        parameters = {'url': 'https://fixture.example.test/devices', 'prompt': 'Check the device page', 'options': {'steps': 3}, 'names': [None, True, '设备']}
        method = 'mcp__codex_apps__tinyfish_run_web_automation'
        self.upload([self.event('tinyfish', 'const result = await tools.' + method + '(' + json.dumps(parameters) + '); text(result);'),
                     self.event('mail', 'await tools.mcp__codex_apps__gmail_list_messages({query:"hello"});'),
                     self.event('plain', 'await tools.mcp__codex_apps__get_profile({});')])
        tinyfish = self.search('mcp="TinyFish"')
        self.assertEqual(tinyfish['total'], 1)
        value = tinyfish['items'][0]['capabilities'][0]
        self.assertEqual((value['name'], value['namespace'], value['method'], value['originalName']),
                         ('codex_apps', 'mcp__codex_apps', 'tinyfish_run_web_automation', method))
        self.assertEqual(value['serviceAliases'], ['tinyfish'])
        self.assertEqual(value['aliasBasis'], 'namespace_method_prefix')
        self.assertEqual(value['arguments'], parameters)
        self.assertEqual(value['evidence'], 'wrapped')
        self.assertEqual(self.search('mcp="mcp__tinyfish" && mcp_method=tinyfish_run_web_automation')['total'], 1)
        self.assertEqual(self.search('mcp=codex_apps')['total'], 3)
        self.assertEqual(self.search('mcp=gmail')['total'], 1)
        self.assertEqual(self.search('mcp=get')['total'], 0)
        rank = self.center.capabilities()['mcps'][0]
        self.assertEqual((rank['name'], rank['callCount'], rank['wrappedCount']), ('codex_apps', 3, 3))
        self.assertFalse(self.center.capabilities()['coverage']['successInferred'])

    def test_eager_promise_array_generic_names_literal_arguments_and_offsets(self):
        code = '''// A comment containing tools.mcp__fake__method({})
await Promise.allSettled([
  tools.mcp__service_z__novel_action({a:'x\\n', b:[true,null,-2], c:{nested:'\\u4e2d'}}),
  tools.mcp__different_connector__method_that_never_existed({value:`literal`})
]);
await tools.mcp__service_z__novel_action(dynamicArgs);
'''
        calls = javascript_calls(code)
        self.assertEqual(len(calls), 3)
        self.assertEqual(calls[0]['arguments'], {'a': 'x\n', 'b': [True, None, -2], 'c': {'nested': '中'}})
        self.assertEqual(calls[1]['arguments'], {'value': 'literal'})
        self.assertIsNone(calls[2]['arguments'])
        for value in calls:
            self.assertTrue(code[value['sourceOffset']:value['sourceEnd']].startswith('tools.' + value['originalName'] + '('))
        self.upload([self.event('parallel', {'code': code})])
        self.assertEqual(self.search('mcp=service_z')['total'], 1)
        rank = self.center.capabilities()['mcps']
        self.assertEqual(sum(v['callCount'] for v in rank), 2)

    def test_repeated_method_keeps_each_literal_input_but_ranks_one_source_record(self):
        code = 'await tools.mcp__same__method({value:"first"});\nawait tools.mcp__same__method({value:"second"});'
        self.upload([self.event('repeated', code)])
        result = self.search('mcp=same && mcp_method=method')
        self.assertEqual(result['total'], 1)
        calls = result['items'][0]['capabilities']
        self.assertEqual([v['arguments'] for v in calls], [{'value': 'first'}, {'value': 'second'}])
        self.assertEqual(len({v['sourceOffset'] for v in calls}), 2)
        rank = self.center.capabilities()
        self.assertEqual((rank['mcps'][0]['callCount'], rank['mcps'][0]['methods'][0]['callCount']), (1, 1))
        self.assertEqual(rank['coverage']['countBasis'], 'distinct_source_records_with_structured_activity_evidence')

    def test_js_strings_comments_functions_conditions_loops_and_dynamic_members_are_excluded(self):
        call = 'await tools.mcp__fake__method({})'
        bad = [json.dumps(call) + ';', '// ' + call, '/* ' + call + ' */',
               'const template = `' + call + '`;', 'const re = /fake = tools.mcp__fake__method({})/;',
               'function sample(){' + call + ';}', 'const sample=async()=>{' + call + ';};',
               'const sample=()=>tools.mcp__fake__method({});', 'if(flag){' + call + ';}',
               'for(const item of items){' + call + ';}', 'while(flag){' + call + ';}',
               'const x = flag ? ' + call + ' : null;', 'flag && ' + call + ';',
               'await Promise.allSettled([()=>tools.mcp__fake__method({})]);',
               'await tools["mcp__fake__method"]({});', 'const tools = {}; ' + call + ';',
               'const object = {method(){' + call + ';}};']
        self.upload([self.event('bad-' + str(i), code) for i, code in enumerate(bad)])
        self.assertEqual(self.search('mcp=fake')['total'], 0)
        self.assertEqual(self.center.capabilities()['mcps'], [])
        good = '\nawait tools.mcp__real__method({});'
        for code in bad[:14] + bad[16:]:
            calls = javascript_calls(code + good)
            self.assertEqual([value['originalName'] for value in calls], ['mcp__real__method'], code)

    def test_custom_skill_reads_reject_writes_lists_samples_and_embedded_example_commands(self):
        path = '/product-references/taste-skill/SKILL.md'
        bad = ["sed -i '1,20p' " + path, 'ls ' + path, 'echo cat ' + path,
               'cat > /tmp/example <<\'EOF\'\ncat ' + path + '\nEOF',
               'cat /audit-samples/product-references/taste-skill/SKILL.md',
               'if true; then cat ' + path + '; fi']
        events = [self.event('bad-skill-' + str(i), 'await tools.exec_command(' + json.dumps({'cmd': command}) + ');') for i, command in enumerate(bad)]
        events += [self.event('write-custom', {'path': path, 'content': 'new instructions'}, name='Write'),
                   self.event('arbitrary-js', {'code': 'await tools.exec_command({cmd:"cat ' + path + '"});'}, name='unrelated_tool')]
        self.upload(events)
        self.assertEqual(self.search('skill=taste-skill')['total'], 0)

    def test_unbraced_multiline_control_flow_and_host_namespace_rebinding(self):
        fake = 'await tools.mcp__fake__method({});'
        real = '\nawait tools.mcp__real__method({});'
        branches = ['if(flag)\n' + fake,
                    'for(const item of items)\n' + fake,
                    'while(flag)\n' + fake,
                    'do\n' + fake + '\nwhile(flag);',
                    'if(flag)\n' + fake + '\nelse if(other)\n' + fake + '\nelse\n' + fake]
        for code in branches:
            self.assertEqual([v['originalName'] for v in javascript_calls(code + real)], ['mcp__real__method'])
        for code in ['const {tools} = fake;', 'let [tools] = fake;', 'tools = fake;']:
            self.assertEqual(javascript_calls(code + real), [])

    def test_unsupported_numeric_and_string_values_cannot_break_record_indexing(self):
        self.upload([self.event('infinite', 'await tools.mcp__real__method({n:1e999});'),
                     self.event('surrogate', 'await tools.mcp__real__method({text:"\\ud800"});'),
                     self.event('emoji', 'await tools.mcp__real__method({text:"\\ud83d\\ude00"});')])
        result = self.search('mcp=real')
        self.assertEqual(result['total'], 3)
        values = [v['capabilities'][0]['arguments'] for v in result['items']]
        self.assertIn({'text': '😀'}, values)
        self.assertEqual(values.count(None), 2)

    def test_metadata_only_migration_is_bounded_resumable_and_preserves_source_fields(self):
        self.upload([self.event('old-' + str(i), 'await tools.mcp__codex_apps__tinyfish_run_web_automation({n:' + str(i) + '});') for i in range(25)])
        self.search('mcp=tinyfish')
        with self.center._db() as db:
            before_fields = [tuple(row) for row in db.execute('SELECT * FROM dc_fields ORDER BY record,ordinal')]
            before_state = [tuple(row) for row in db.execute('SELECT * FROM dc_state ORDER BY name')]
            for row in db.execute('SELECT key,metadata FROM dc_records').fetchall():
                metadata = json.loads(row['metadata']); metadata.pop('capabilityEvidenceVersion'); metadata['capabilities'] = []
                db.execute('UPDATE dc_records SET metadata=? WHERE key=?', (json.dumps(metadata), row['key']))
        with patch.object(self.center, '_lookup', side_effect=AssertionError('Migration must not expand detail records')):
            result = self.center.refresh_capabilities(limit=4, max_seconds=1)
            self.assertEqual((result['updated'], result['remaining']), (4, 21))
            while result['remaining']:
                result = self.center.refresh_capabilities(limit=4, max_seconds=1)
            self.assertEqual(self.center.refresh_capabilities()['updated'], 0)
        with self.center._db() as db:
            self.assertEqual([tuple(row) for row in db.execute('SELECT * FROM dc_fields ORDER BY record,ordinal')], before_fields)
            self.assertEqual([tuple(row) for row in db.execute('SELECT * FROM dc_state ORDER BY name')], before_state)
        self.assertEqual(VERSION, '5')
        self.assertEqual(self.search('mcp=tinyfish')['total'], 25)
        self.assertEqual(self.center.capabilities()['coverage']['capabilityRemaining'], 0)


if __name__ == '__main__':
    unittest.main()
