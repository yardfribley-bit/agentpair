"""Explicit native turn identities remain bounded, explainable and conflict-safe."""
import copy
import unittest

from agentpair.source_turn_identity import MAX_TURN_ID_LENGTH, extract_turn_identity


def expected_turn(turn_id=None, sources=None, conflict=False):
    return {
        'turnId': turn_id, 'turnIdentityConflict': conflict, 'turnIdentitySources': sources or [],
        'rootTurnId': None, 'rootTurnIdentityConflict': False, 'rootTurnIdentitySources': [],
    }


class SourceTurnIdentityTests(unittest.TestCase):
    def test_codex_function_call_uses_native_payload_passthrough(self):
        event = {
            'kind': 'tool_call', 'source': 'codex', 'name': 'exec',
            'payload': {
                'type': 'function_call', 'name': 'exec', 'call_id': 'call-fixture',
                'arguments': 'text(await tools.exec_command({cmd:"pwd"}));',
                'internal_chat_message_metadata_passthrough': {'turn_id': 'native-turn'},
            },
            'sourceFields': {'type': 'response_item', 'timestamp': '2026-10-08T11:30:00Z'},
        }
        result = extract_turn_identity(event)
        self.assertEqual(result, expected_turn('native-turn', [{
                'path': 'payload.internal_chat_message_metadata_passthrough.turn_id',
                'value': 'native-turn',
            }]))

    def test_codex_input_text_message_uses_same_native_turn(self):
        event = {
            'kind': 'message', 'source': 'codex', 'role': 'user',
            'payload': {
                'type': 'message', 'role': 'user',
                'content': [{'type': 'input_text', 'text': '查询上海天气'}],
                'internal_chat_message_metadata_passthrough': {'turn_id': 'native-turn'},
            },
        }
        self.assertEqual(extract_turn_identity(event)['turnId'], 'native-turn')

    def test_codex_item_completed_uses_inner_item_metadata(self):
        event = {
            'kind': 'command_execution', 'source': 'codex',
            'payload': {'type': 'item_completed', 'item': {
                'type': 'CommandExecution',
                'internal_chat_message_metadata_passthrough': {'turnId': 'item-turn'},
            }},
        }
        result = extract_turn_identity(event)
        self.assertEqual(result['turnId'], 'item-turn')
        self.assertEqual(result['turnIdentitySources'][0]['path'],
                         'payload.item.internal_chat_message_metadata_passthrough.turnId')

    def test_all_known_direct_locations_and_aliases(self):
        fixtures = [
            ({'turn_id': 'turn'}, 'turn_id'),
            ({'turnId': 'turn'}, 'turnId'),
            ({'payload': {'turn_id': 'turn'}}, 'payload.turn_id'),
            ({'payload': {'turnId': 'turn'}}, 'payload.turnId'),
            ({'payload': {'item': {'turn_id': 'turn'}}}, 'payload.item.turn_id'),
            ({'payload': {'item': {'turnId': 'turn'}}}, 'payload.item.turnId'),
            ({'sourceFields': {'turn_id': 'turn'}}, 'sourceFields.turn_id'),
            ({'sourceFields': {'turnId': 'turn'}}, 'sourceFields.turnId'),
        ]
        for event, path in fixtures:
            with self.subTest(path=path):
                self.assertEqual(extract_turn_identity(event),
                                 expected_turn('turn', [{'path': path, 'value': 'turn'}]))

    def test_all_known_passthrough_locations(self):
        passthrough = {'internal_chat_message_metadata_passthrough': {'turn_id': 'turn'}}
        fixtures = [
            (passthrough, 'internal_chat_message_metadata_passthrough.turn_id'),
            ({'payload': passthrough}, 'payload.internal_chat_message_metadata_passthrough.turn_id'),
            ({'payload': {'item': passthrough}}, 'payload.item.internal_chat_message_metadata_passthrough.turn_id'),
            ({'sourceFields': passthrough}, 'sourceFields.internal_chat_message_metadata_passthrough.turn_id'),
        ]
        for event, path in fixtures:
            with self.subTest(path=path):
                result = extract_turn_identity(event)
                self.assertEqual(result['turnId'], 'turn')
                self.assertEqual(result['turnIdentitySources'], [{'path': path, 'value': 'turn'}])

    def test_agreeing_candidates_retain_every_source_in_stable_order(self):
        event = {
            'turn_id': 'turn', 'turnId': 'turn',
            'internal_chat_message_metadata_passthrough': {'turn_id': 'turn'},
            'payload': {'turn_id': 'turn', 'item': {'turnId': 'turn'}},
            'sourceFields': {'turn_id': 'turn'},
        }
        result = extract_turn_identity(event)
        self.assertEqual(result['turnId'], 'turn')
        self.assertFalse(result['turnIdentityConflict'])
        self.assertEqual([source['path'] for source in result['turnIdentitySources']], [
            'turn_id', 'turnId', 'internal_chat_message_metadata_passthrough.turn_id',
            'payload.turn_id', 'payload.item.turnId', 'sourceFields.turn_id',
        ])

    def test_different_direct_and_passthrough_ids_conflict_without_priority(self):
        event = {'payload': {
            'turn_id': 'direct-turn',
            'internal_chat_message_metadata_passthrough': {'turn_id': 'native-turn'},
        }}
        result = extract_turn_identity(event)
        self.assertIsNone(result['turnId'])
        self.assertTrue(result['turnIdentityConflict'])
        self.assertEqual([source['value'] for source in result['turnIdentitySources']],
                         ['direct-turn', 'native-turn'])

    def test_alias_conflict_is_not_hidden_by_first_nonempty_field(self):
        result = extract_turn_identity({'sourceFields': {'turn_id': 'a', 'turnId': 'b'}})
        self.assertIsNone(result['turnId'])
        self.assertTrue(result['turnIdentityConflict'])
        self.assertEqual(len(result['turnIdentitySources']), 2)

    def test_invalid_candidate_types_and_lengths_are_not_coerced(self):
        invalid = [None, False, True, 0, 42, [], {}, ['turn'], b'turn', '', ' \n\t',
                   'x' * (MAX_TURN_ID_LENGTH + 1)]
        for value in invalid:
            with self.subTest(value_type=type(value).__name__):
                self.assertEqual(extract_turn_identity({'payload': {'turn_id': value}}), expected_turn())
        self.assertEqual(extract_turn_identity({'turn_id': 'x' * MAX_TURN_ID_LENGTH})['turnId'],
                         'x' * MAX_TURN_ID_LENGTH)

    def test_invalid_candidate_does_not_override_valid_native_id(self):
        result = extract_turn_identity({'turn_id': 42, 'payload': {
            'turnId': 'turn', 'internal_chat_message_metadata_passthrough': ['bad'],
        }})
        self.assertEqual(result['turnId'], 'turn')
        self.assertFalse(result['turnIdentityConflict'])
        self.assertEqual(result['turnIdentitySources'], [{'path': 'payload.turnId', 'value': 'turn'}])

    def test_malformed_source_containers_are_ignored(self):
        for event in (None, [], 'raw', 17, {'payload': []}, {'sourceFields': 'raw'},
                      {'payload': {'item': 'raw', 'internal_chat_message_metadata_passthrough': None}}):
            with self.subTest(event_type=type(event).__name__):
                self.assertEqual(extract_turn_identity(event), expected_turn())

    def test_arguments_outputs_content_and_unrecognized_nesting_are_not_identity(self):
        event = {
            'id': 'message-id', 'sessionId': 'session-id', 'callId': 'call-id',
            'payload': {
                'arguments': {'turn_id': 'argument-turn'},
                'output': {'turnId': 'returned-turn'},
                'content': [{'type': 'input_text', 'text': '{"turn_id":"text-turn"}'}],
                'nested': {'internal_chat_message_metadata_passthrough': {'turn_id': 'nested-turn'}},
            },
            'metadata': {'turnId': 'derived-turn'},
        }
        self.assertEqual(extract_turn_identity(event), expected_turn())

    def test_opaque_ids_are_not_silently_normalized(self):
        result = extract_turn_identity({'turn_id': 'turn', 'payload': {'turn_id': ' turn '}})
        self.assertTrue(result['turnIdentityConflict'])
        self.assertIsNone(result['turnId'])
        self.assertEqual(result['turnIdentitySources'][-1]['value'], ' turn ')

    def test_source_event_is_unchanged_and_results_do_not_share_state(self):
        event = {'payload': {'internal_chat_message_metadata_passthrough': {'turn_id': 'turn'}}}
        original = copy.deepcopy(event)
        first = extract_turn_identity(event)
        first['turnIdentitySources'][0]['value'] = 'changed'
        self.assertEqual(event, original)
        self.assertEqual(extract_turn_identity(event)['turnIdentitySources'][0]['value'], 'turn')

    def test_source_turn_without_user_remains_only_a_source_turn(self):
        result = extract_turn_identity({
            'source': 'codex', 'sessionId': 'child-session', 'kind': 'tool_call',
            'payload': {'internal_chat_message_metadata_passthrough': {'turn_id': 'new-child-turn'}},
        })
        self.assertEqual(result['turnId'], 'new-child-turn')
        self.assertEqual(set(result), set(expected_turn()))

    def test_native_child_turn_context_preserves_distinct_root_turn(self):
        for kind in ('turn_context', 'turn_started'):
            with self.subTest(kind=kind):
                result = extract_turn_identity({
                    'kind': kind, 'source': 'codex', 'sessionId': 'child-session',
                    'payload': {'turn_id': 'child-turn', 'root_turn_id': 'parent-turn'},
                    'sourceFields': {'type': kind},
                })
                self.assertEqual(result['turnId'], 'child-turn')
                self.assertEqual(result['rootTurnId'], 'parent-turn')
                self.assertFalse(result['turnIdentityConflict'])
                self.assertFalse(result['rootTurnIdentityConflict'])
                self.assertEqual(result['rootTurnIdentitySources'],
                                 [{'path': 'payload.root_turn_id', 'value': 'parent-turn'}])

    def test_root_turn_agreement_does_not_hide_child_turn_conflict(self):
        result = extract_turn_identity({
            'payload': {
                'turn_id': 'child-a', 'root_turn_id': 'root',
                'internal_chat_message_metadata_passthrough': {
                    'turn_id': 'child-b', 'rootTurnId': 'root',
                },
            },
        })
        self.assertTrue(result['turnIdentityConflict'])
        self.assertIsNone(result['turnId'])
        self.assertEqual(result['rootTurnId'], 'root')
        self.assertFalse(result['rootTurnIdentityConflict'])
        self.assertEqual(len(result['rootTurnIdentitySources']), 2)

    def test_root_turn_conflict_does_not_override_child_turn(self):
        result = extract_turn_identity({
            'payload': {'turn_id': 'child', 'root_turn_id': 'root-a'},
            'sourceFields': {'rootTurnId': 'root-b'},
        })
        self.assertEqual(result['turnId'], 'child')
        self.assertFalse(result['turnIdentityConflict'])
        self.assertIsNone(result['rootTurnId'])
        self.assertTrue(result['rootTurnIdentityConflict'])
        self.assertEqual([source['value'] for source in result['rootTurnIdentitySources']],
                         ['root-a', 'root-b'])

    def test_root_identity_never_comes_from_encrypted_or_business_content(self):
        result = extract_turn_identity({
            'payload': {
                'type': 'agent_message', 'encrypted_content': 'opaque',
                'content': {'root_turn_id': 'content-root'},
                'arguments': {'root_turn_id': 'argument-root'},
                'output': {'rootTurnId': 'returned-root'},
            },
            'rootSessionId': 'session-root', 'parentId': 'message-parent',
        })
        self.assertEqual(result, expected_turn())

    def test_root_identity_uses_same_fixed_locations_and_type_bounds(self):
        result = extract_turn_identity({
            'root_turn_id': 'root', 'rootTurnId': 'root',
            'payload': {'item': {'rootTurnId': 'root'}},
            'sourceFields': {'internal_chat_message_metadata_passthrough': {'root_turn_id': 'root'}},
        })
        self.assertEqual(result['rootTurnId'], 'root')
        self.assertEqual([source['path'] for source in result['rootTurnIdentitySources']], [
            'root_turn_id', 'rootTurnId', 'payload.item.rootTurnId',
            'sourceFields.internal_chat_message_metadata_passthrough.root_turn_id',
        ])
        for value in (False, 42, {'turn': 'root'}, ' \n', 'x' * (MAX_TURN_ID_LENGTH + 1)):
            with self.subTest(value_type=type(value).__name__):
                self.assertEqual(extract_turn_identity({'payload': {'root_turn_id': value}}), expected_turn())

    def test_root_identity_never_falls_back_to_current_turn_or_vice_versa(self):
        only_turn = extract_turn_identity({'payload': {'turn_id': 'child'}})
        self.assertIsNone(only_turn['rootTurnId'])
        only_root = extract_turn_identity({'payload': {'root_turn_id': 'root'}})
        self.assertIsNone(only_root['turnId'])
        self.assertEqual(only_root['rootTurnId'], 'root')


if __name__ == '__main__':
    unittest.main()
