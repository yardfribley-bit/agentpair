"""Independent investigation acceptance using disposable synthetic source data.

No production database, socket, relay, or real model is accessed.
"""
import json
import unittest
from unittest.mock import patch

from tests import test_data_center as data_fixtures
from tests import test_collection_security_questions as security_fixtures


class IndependentDataCenterReviewTests(unittest.TestCase):
    def setUp(self):
        self.fixture = data_fixtures.DataCenterTests('test_incremental_update_and_source_receipt_honesty')
        self.fixture.setUp()

    def tearDown(self):
        self.fixture.tearDown()

    def test_new_duplicate_on_canonical_device_does_not_remove_snapshot_alias(self):
        f = self.fixture
        enrollment = f.devices.enroll(f.devices.pairing('alice')['code'], 'Previous registration')
        alias = f.devices.identity(enrollment['token'])
        event = f.event('snapshot-alias-event', text='snapshot-only-evidence')
        f.upload([event], identity=alias)
        f.devices.merge_registrations('alice', f.identities['alice']['id'], [alias['id']])
        first = f.find('snapshot-only-evidence')
        f.upload([event])
        second = f.find('snapshot-only-evidence', snapshot=first['snapshot'])
        self.assertEqual(first['total'], 1)
        self.assertEqual(second['total'], 1)
        self.assertEqual(second['items'][0]['id'], first['items'][0]['id'])
        self.assertEqual(f.find('snapshot-only-evidence')['total'], 1)

    def test_valid_unusual_applens_json_retains_readable_original_detail(self):
        f = self.fixture
        for index, messages in enumerate((None, 12, 'opaque extension', {'futureSchema': True})):
            with self.subTest(messages=messages):
                marker = 'unusual-message-' + str(index)
                body = json.dumps({'messages': messages, 'note': marker}, ensure_ascii=False)
                f.context(marker, body)
                result = f.find(marker)
                self.assertEqual(result['total'], 1)
                detail = f.center.record(result['items'][0]['id'])['item']
                self.assertEqual(detail['content'], body)
                self.assertEqual(detail['raw']['body'], body)
                self.assertIn(marker, json.dumps(detail['contextItems'], ensure_ascii=False))

    def test_same_call_id_in_another_session_cannot_supply_a_return(self):
        f = self.fixture
        f.upload([
            f.event('call-scope-one', 'tool_call', name='Bash', callId='reused-call',
                    session='first-session', payload={'arguments': {'cmd': 'curl https://example.test/'}}),
            f.event('result-scope-two', 'tool_result', callId='reused-call',
                    session='second-session', payload={'output': 'success 200'}),
        ])
        hit = f.find('tool=Bash')['items'][0]
        detail = f.center.record(hit['id'])['item']
        self.assertIsNone(detail['result'])
        self.assertFalse(any(r['relation'] == 'tool_call_result' for r in detail['relations']))

    def test_nested_function_arguments_readable_on_both_sides(self):
        f = self.fixture
        arguments = {'cmd': 'curl https://example.test/important?q=full', 'password': 'fixture-secret-tail'}
        f.upload([
            f.event('nested-review-call', 'tool_call', callId='nested-review',
                    payload={'function': {'name': 'Bash', 'arguments': json.dumps(arguments)}}),
            f.event('nested-review-return', 'tool_result', callId='nested-review', payload={'output': 'done'}),
        ])
        call = f.find('function=Bash')['items'][0]
        ret = f.find('kind=tool_result')['items'][0]
        self.assertEqual(f.center.record(call['id'])['item']['arguments'], arguments)
        self.assertEqual(f.center.record(ret['id'])['item']['arguments'], arguments)


class IndependentSecurityScopeReviewTests(unittest.TestCase):
    def setUp(self):
        self.fixture = security_fixtures.CollectionSecurityQuestionsTests('test_cache_and_requester_perspective_boundary')
        self.fixture.setUp()

    def tearDown(self):
        self.fixture.tearDown()

    def test_previous_question_cannot_cross_device_owner_or_requester(self):
        f = self.fixture
        alice_other = f.devices.enroll(f.devices.pairing('alice')['code'], 'Second Alice machine')
        bob = f.devices.enroll(f.devices.pairing('bob')['code'], 'Bob machine')
        with patch('agentpair.collection_assistant.threading.Thread', security_fixtures.ImmediateThread):
            job = f.assistant.submit('requester-one', 'alice', f.device, 'password在哪里？', perspective='security')
            self.assertEqual(f.assistant.get(job['id'], 'requester-one')['status'], 'completed')
            count = len(f.model.calls)
            attempts = [
                ('requester-one', 'alice', alice_other['deviceId']),
                ('requester-one', 'bob', bob['deviceId']),
                ('requester-two', 'alice', f.device),
            ]
            for requester, owner, device in attempts:
                with self.subTest(requester=requester, owner=owner, device=device), self.assertRaises(ValueError):
                    f.assistant.submit(requester, owner, device, '继续调查', previous=job['id'], perspective='security')
            self.assertEqual(len(f.model.calls), count, 'Scope rejection must happen before a model request')


if __name__ == '__main__':
    unittest.main()
