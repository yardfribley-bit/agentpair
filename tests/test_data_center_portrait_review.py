"""Independent portrait correctness checks using disposable synthetic records.

These tests intentionally separate linked evidence from surrounding records.
No production database, network, or model endpoint is accessed.
"""
import json
import unittest

from tests import test_data_center as data_fixtures


class DataCenterPortraitReviewTests(unittest.TestCase):
    def setUp(self):
        self.fixture = data_fixtures.DataCenterTests('test_incremental_update_and_source_receipt_honesty')
        self.fixture.setUp()

    def tearDown(self):
        self.fixture.tearDown()

    def test_duplicate_parent_message_ids_cannot_confirm_user_request(self):
        f = self.fixture
        f.upload([
            f.event('duplicate-parent-a', text='需求 A', timestamp=1700000000,
                    payload={'id': 'duplicated-parent', 'content': '需求 A'}),
            f.event('duplicate-parent-b', text='需求 B', timestamp=1700000001,
                    payload={'id': 'duplicated-parent', 'content': '需求 B'}),
            f.event('ambiguous-child', 'tool_call', name='Bash', callId='own-call', timestamp=1700000002,
                    payload={'id': 'own-child', 'parentId': 'duplicated-parent', 'arguments': {'cmd': 'pwd'}}),
            f.event('own-return', 'tool_result', callId='own-call', timestamp=1700000003,
                    payload={'output': '/recorded/directory'}),
        ])
        hit = f.find('tool=Bash')['items'][0]
        detail = f.center.record(hit['id'])['item']
        self.assertNotEqual(detail['taskContext']['association'], 'recorded')
        self.assertFalse(any(r['relation'] == 'source_parent' for r in detail['relations']))
        portrait = detail['portrait']
        self.assertFalse(portrait['coverage']['sourceSessionsAreTasks'])
        self.assertFalse(any(step['kind'] == 'user' and step.get('association') == 'recorded'
                             for step in portrait['steps']))
        self.assertEqual(detail['result'], '/recorded/directory')

    def test_surrounding_return_never_becomes_selected_calls_result(self):
        f = self.fixture
        f.upload([
            f.event('selected-no-return', 'tool_call', name='Bash', callId='selected-call',
                    timestamp=1700000010, payload={'arguments': {'cmd': 'curl https://selected.example/'}}),
            f.event('nearby-unrelated-return', 'tool_result', callId='unrelated-call', timestamp=1700000011,
                    payload={'output': {'status': 'completed', 'files': ['/unrelated/video.mp4']}}),
        ])
        hit = f.find('tool=Bash')['items'][0]
        detail = f.center.record(hit['id'])['item']
        self.assertIsNone(detail['result'])
        self.assertEqual(detail['presentation']['result']['status'], 'unknown')
        self.assertFalse(detail['presentation']['result']['outputs'])
        self.assertFalse(any(r['relation'] == 'tool_call_result' for r in detail['relations']))
        for record in detail['portrait']['neighbors']:
            self.assertEqual(record.get('association'), 'same_session_neighbor')

    def test_ambiguous_turn_id_cannot_confirm_other_calls_as_same_round(self):
        f = self.fixture
        f.upload([
            f.event('round-user-a', text='需求 A', timestamp=1700000000,
                    payload={'id': 'round-user-a', 'turn_id': 'reused-round', 'content': '需求 A'}),
            f.event('round-user-b', text='需求 B', timestamp=1700000001,
                    payload={'id': 'round-user-b', 'turn_id': 'reused-round', 'content': '需求 B'}),
            f.event('round-selected', 'tool_call', name='Bash', timestamp=1700000002,
                    payload={'turn_id': 'reused-round', 'arguments': {'cmd': 'pwd'}}),
            f.event('round-other-call', 'tool_call', name='Read', timestamp=1700000003,
                    payload={'turn_id': 'reused-round', 'arguments': {'path': '/unconfirmed/file'}}),
        ])
        selected = f.find('tool=Bash')['items'][0]
        other = f.find('tool=Read')['items'][0]
        detail = f.center.record(selected['id'])['item']
        self.assertNotEqual(detail['taskContext']['association'], 'recorded')
        self.assertFalse(any(step['id'] == other['id'] and step.get('association') == 'recorded'
                             for step in detail['portrait']['steps']))

    def test_target_domains_exclude_headers_and_embedded_query_addresses(self):
        f = self.fixture
        command = (
            "curl -H 'Referer: https://header-only.example/' "
            "'https://first.example/?redirect=https://wttr.in/&note=https://embedded.example/' "
            "&& curl 'https://wttr.in/?format=j1&lang=zh'"
        )
        f.upload([f.event('multi-target', 'tool_call', name='Bash', payload={'arguments': {'cmd': command}})])
        hit = f.find('target_domain="wttr.in"')['items'][0]
        self.assertEqual(hit['destinations'], [
            'https://first.example/?redirect=https://wttr.in/&note=https://embedded.example/',
            'https://wttr.in/?format=j1&lang=zh',
        ])
        target_match = next(m for m in hit['matchBasis'] if m['field'] == 'target_domain')
        self.assertEqual(target_match['targets'], ['https://wttr.in/?format=j1&lang=zh'])
        self.assertEqual(f.find('target_domain="header-only.example"')['total'], 0)
        self.assertEqual(f.find('target_domain="embedded.example"')['total'], 0)
        self.assertEqual(f.find('domain="header-only.example"')['total'], 1)
        self.assertEqual(f.find('domain="embedded.example"')['total'], 1)

    def test_neighbors_remain_scoped_to_physical_device_source_and_owner(self):
        f = self.fixture
        selected = f.event('scope-selected', 'tool_call', name='Bash', source='workbuddy',
                           timestamp=1700000005, payload={'arguments': {'cmd': 'pwd'}})
        f.upload([selected, f.event('scope-local', source='workbuddy', timestamp=1700000006,
                                   text='local surrounding input')])
        f.upload([f.event('scope-codex', source='codex', timestamp=1700000006,
                          text='another Agent hidden content')])
        f.upload([f.event('scope-bob', source='workbuddy', timestamp=1700000006,
                          text='another owner hidden content')], owner='bob')
        enrolled = f.devices.enroll(f.devices.pairing('alice')['code'], 'Another physical machine')
        other = f.devices.identity(enrolled['token'])
        f.upload([f.event('scope-machine', source='workbuddy', timestamp=1700000006,
                          text='another machine hidden content')], identity=other)
        hit = f.find('tool=Bash')['items'][0]
        detail = f.center.record(hit['id'])['item']
        portrait = detail['portrait']
        encoded = json.dumps(portrait, ensure_ascii=False)
        for forbidden in ('another Agent hidden content', 'another owner hidden content',
                          'another machine hidden content'):
            self.assertNotIn(forbidden, encoded)
        for item in portrait['steps'] + portrait['neighbors']:
            self.assertEqual(item['ownerAccount'], 'alice')
            self.assertEqual(item['application'], 'workbuddy')
            self.assertEqual(item['sourceDeviceId'], f.identities['alice']['id'])

    def test_search_large_command_and_error_are_bounded_but_detail_keeps_original(self):
        f = self.fixture
        command = 'echo recorded\n' + ('x' * 90000)
        error = 'a real retained error: ' + ('y' * 90000)
        f.upload([
            f.event('long-call-review', 'tool_call', name='Bash', callId='long-review',
                    payload={'arguments': {'command': command}}),
            f.event('long-return-review', 'tool_result', callId='long-review', timestamp=1700000001,
                    payload={'output': {'status': 'failed', 'error': error}}),
        ])
        hit = f.find('tool=Bash')['items'][0]
        self.assertLess(len(json.dumps(hit, ensure_ascii=False)), 12000,
                        'Search should return a bounded preview, not the retained large command/error')
        detail = f.center.record(hit['id'])['item']
        self.assertEqual(detail['arguments']['command'], command)
        self.assertEqual(detail['result']['error'], error)


if __name__ == '__main__':
    unittest.main()
