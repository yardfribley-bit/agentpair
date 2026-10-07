import unittest

from agentpair.collection_semantics import group_tasks, turn_packet, validate_lineage


def record(identity, kind, text='', seq=1, role=None, parent=None, call=None,
           source='workbuddy', session='s', device='d', native_turn=None, request=None,
           source_id=None):
    payload = {'id': source_id or identity, 'parentId': parent}
    if native_turn:
        payload['turn_id'] = native_turn
    if request:
        payload['providerData'] = {'conversationRequestId': request}
    if call:
        payload['callId'] = call
    if role:
        payload['role'] = role
    event = {'id': identity, 'source': source, 'sessionId': session, 'kind': kind,
             'role': role, 'callId': call, 'name': 'Read' if kind == 'tool_call' else None,
             'payload': payload, 'timestamp': 1791345600000 + seq}
    return {'id': identity, 'recordId': 'sessionlens:' + identity, 'source': source,
            'sessionId': session, 'deviceId': device, 'kind': kind, 'role': role,
            'text': text, 'seq': seq, 'timestamp': event['timestamp'], 'event': event}


def link(identity, parent=None, relation='request', status='supported'):
    return {'turnId': identity, 'parentTurnId': parent, 'relation': relation,
            'status': status, 'reason': '依据这些用户原话的目标和方案变化。',
            'evidenceTurnIds': [identity] + ([parent] if parent else [])}


class CollectionSemanticsTests(unittest.TestCase):
    def test_source_parents_stop_at_nearest_user_not_oldest_goal(self):
        records = [record('login', 'message', '设计邮箱登录', 1, 'user'),
                   record('plan', 'message', '先使用邮箱认证', 2, 'assistant', 'login'),
                   record('weather', 'message', '另外查上海天气', 3, 'user', 'plan'),
                   record('reason', 'reasoning', '搜索天气信息', 4, parent='weather'),
                   record('call', 'tool_call', 'query: 上海天气', 5, parent='reason', call='c')]
        packet = turn_packet(records)
        self.assertEqual([turn['turnId'] for turn in packet['turns']], ['login', 'weather'])
        self.assertIn('sessionlens:call', packet['turns'][1]['recordIds'])
        tasks = group_tasks(packet['turns'], [link('login'), link('weather')])
        self.assertEqual(len(tasks), 2)
        self.assertEqual(tasks[1]['executions'][0]['requirementTurnId'], 'weather')
        self.assertNotIn('sessionlens:call', tasks[0]['recordIds'])

    def test_semantic_resume_links_earlier_goal_without_swallowing_interleaved_task(self):
        records = [record('a', 'message', '设计会员登录', 1, 'user'),
                   record('b', 'message', '查上海天气', 2, 'user'),
                   record('c', 'message', '回到会员页面，采用第二种', 3, 'user'),
                   record('d', 'message', '行，落地', 4, 'user'),
                   record('write', 'tool_call', 'path: members.py', 5, parent='d', call='write')]
        packet = turn_packet(records)
        tasks = group_tasks(packet['turns'], [link('a'), link('b'), link('c', 'a', 'resume'), link('d', 'c', 'execution')])
        self.assertEqual([task['turnsCount'] for task in tasks], [3, 1])
        call = tasks[0]['executions'][0]
        self.assertEqual((call['turnId'], call['requirementTurnId'], call['approvalTurnId']), ('d', 'c', 'd'))
        self.assertEqual(tasks[0]['title'], '设计会员登录')
        self.assertTrue(all(requirement['recordId'].startswith('sessionlens:') for requirement in tasks[0]['requirements']))

    def test_short_confirmation_without_origin_remains_unresolved(self):
        turns = turn_packet([record('u', 'message', '采用第二种', role='user')])['turns']
        tasks = group_tasks(turns, [link('u', relation='unresolved', status='ambiguous')])
        self.assertTrue(tasks[0]['unresolved'])
        self.assertEqual(tasks[0]['status'], 'ambiguous')

    def test_mirrored_users_deduplicate_but_repeated_requests_after_reply_do_not(self):
        records = [record('u', 'user_message', '继续', 1, source='codex', source_id='native-u'),
                   record('mirror', 'message', '继续', 2, 'user', source='codex', source_id='native-u'),
                   record('reply', 'message', '已继续', 3, 'assistant', source='codex'),
                   record('again', 'user_message', '继续', 4, source='codex', source_id='native-u2')]
        turns = turn_packet(records)['turns']
        self.assertEqual([turn['turnId'] for turn in turns], ['u', 'again'])
        self.assertIn('sessionlens:mirror', turns[0]['recordIds'])

    def test_background_result_returns_to_invocation_after_new_goal(self):
        records = [record('video', 'message', '生成 SSH 视频', 1, 'user'),
                   record('call', 'tool_call', 'prompt: SSH', 2, parent='video', call='gen'),
                   record('weather', 'message', '上海天气', 3, 'user'),
                   record('result', 'tool_result', '生成完成', 4, parent='call', call='gen')]
        turns = turn_packet(records)['turns']
        self.assertIn('sessionlens:result', turns[0]['recordIds'])
        self.assertNotIn('sessionlens:result', turns[1]['recordIds'])
        result = next(item for item in turns[0]['records'] if item['kind'] == 'tool_result')
        self.assertEqual(result['callRecordId'], 'sessionlens:call')
        self.assertEqual(result['association'], 'recorded_call_id')
        tasks = group_tasks(turns, [link('video'), link('weather')])
        self.assertEqual(tasks[0]['executions'][0]['resultRecordIds'], ['sessionlens:result'])

    def test_original_turn_id_overrides_sequence_fallback_for_codex(self):
        records = [record('old', 'user_message', '编写登录', 1, source='codex', native_turn='turn1'),
                   record('new', 'user_message', '查天气', 2, source='codex', native_turn='turn2'),
                   record('late', 'command_execution', 'python login.py', 3, source='codex', native_turn='turn1')]
        turns = turn_packet(records)['turns']
        late = next(item for item in turns[0]['records'] if item['kind'] == 'command_execution')
        self.assertEqual(late['association'], 'recorded_turn_id')
        self.assertNotIn('sessionlens:late', turns[1]['recordIds'])

    def test_unique_request_metadata_can_link_unparented_late_records(self):
        records = [record('u1', 'message', '读文件', 1, 'user'),
                   record('call', 'tool_call', 'path: a.md', 2, parent='u1', request='r1'),
                   record('u2', 'message', '查天气', 3, 'user'),
                   record('reply', 'message', '文件读取完毕', 4, 'assistant', request='r1')]
        turns = turn_packet(records)['turns']
        reply = next(item for item in turns[0]['records'] if item['recordId'] == 'sessionlens:reply')
        self.assertEqual(reply['association'], 'recorded_request_id')

    def test_shared_request_id_does_not_merge_distinct_rounds(self):
        records = [record('u1', 'message', '读文件', 1, 'user', request='shared'),
                   record('u2', 'message', '查天气', 2, 'user', request='shared'),
                   record('reply', 'message', '天气结果', 3, 'assistant', request='shared')]
        turns = turn_packet(records)['turns']
        reply = next(item for item in turns[1]['records'] if item['recordId'] == 'sessionlens:reply')
        self.assertEqual(reply['association'], 'inferred')

    def test_reused_call_id_is_a_gap_not_arbitrary_pair(self):
        records = [record('u', 'message', '读取文件', 1, 'user'),
                   record('c1', 'tool_call', 'path: a', 2, call='same'),
                   record('c2', 'tool_call', 'path: b', 3, call='same'),
                   record('r', 'tool_result', '内容', 4, call='same')]
        packet = turn_packet(records)
        self.assertTrue(any(gap['reason'] == 'ambiguous_call' for gap in packet['gaps']))
        result = next(item for item in packet['turns'][0]['records'] if item['kind'] == 'tool_result')
        self.assertIsNone(result['callRecordId'])

    def test_missing_reasoning_is_explicit_and_never_substitutes_reply(self):
        records = [record('u', 'message', '写代码', 1, 'user'),
                   record('r', 'reasoning', '', 2), record('a', 'message', '已完成代码', 3, 'assistant')]
        records[1]['event']['payload']['encrypted_content'] = 'ciphertext'
        packet = turn_packet(records)
        self.assertTrue(any(gap['reason'] == 'no_readable_reasoning' for gap in packet['gaps']))
        self.assertEqual(packet['turns'][0]['agentProposalAfter'], '已完成代码')
        self.assertNotIn('ciphertext', str(packet['turns']))

    def test_device_scope_prevents_cross_machine_source_parent(self):
        records = [record('u', 'message', '读文件', 1, 'user', device='one', source_id='u-source'),
                   record('other', 'message', '查天气', 2, 'user', device='two'),
                   record('call', 'tool_call', 'path: a', 3, parent='u-source', device='two')]
        packet = turn_packet(records)
        self.assertNotIn('sessionlens:call', packet['turns'][0]['recordIds'])
        self.assertTrue(any(gap['reason'] == 'parent_not_in_selected_records' for gap in packet['gaps']))
        with self.assertRaises(ValueError):
            validate_lineage({'links': [link('u'), link('other', 'u', 'revision')]}, packet['turns'])

    def test_validation_rejects_future_unknown_missing_and_uncited_parent(self):
        turns = turn_packet([record('a', 'message', '登录', 1, 'user'), record('b', 'message', '做', 2, 'user')])['turns']
        invalid = [
            [link('a', 'b', 'revision'), link('b')],
            [link('a'), link('b', 'missing', 'execution')],
            [link('a')],
            [link('a'), {**link('b', 'a', 'approval'), 'evidenceTurnIds': ['b']}],
            [link('a'), link('b', 'a', 'execution', 'ambiguous')],
        ]
        for links in invalid:
            with self.assertRaises(ValueError):
                validate_lineage({'links': links}, turns)

    def test_cross_source_and_session_links_rejected_even_when_labels_valid(self):
        for change in ({'source': 'codex'}, {'session': 'other'}):
            turns = turn_packet([record('a', 'message', '登录', 1, 'user'), record('b', 'message', '做', 2, 'user', **change)])['turns']
            with self.assertRaises(ValueError):
                validate_lineage({'links': [link('a'), link('b', 'a', 'execution')]}, turns)

    def test_later_revision_does_not_rewrite_earlier_execution_requirement(self):
        records = [record('a', 'message', '使用邮箱登录', 1, 'user'),
                   record('c1', 'tool_call', 'email login', 2, parent='a', call='one'),
                   record('b', 'message', '改成手机号登录', 3, 'user'),
                   record('c2', 'tool_call', 'phone login', 4, parent='b', call='two')]
        turns = turn_packet(records)['turns']
        task = group_tasks(turns, [link('a'), link('b', 'a', 'revision')])[0]
        self.assertEqual([call['requirementTurnId'] for call in task['executions']], ['a', 'b'])
        self.assertEqual([requirement['text'] for requirement in task['requirements']], ['使用邮箱登录', '改成手机号登录'])

    def test_future_background_reply_is_not_the_approved_plan(self):
        records = [record('a', 'message', '设计会员登录', 1, 'user'),
                   record('proposal', 'message', '建议邮箱认证', 2, 'assistant', 'a'),
                   record('b', 'message', '采用这个，动手吧', 3, 'user'),
                   record('call', 'tool_call', 'write login', 4, parent='b', call='c'),
                   record('future', 'message', '之后才出现的另一个建议', 5, 'assistant', 'a')]
        turns = turn_packet(records)['turns']
        task = group_tasks(turns, [link('a'), link('b', 'a', 'execution')])[0]
        self.assertEqual(task['executions'][0]['planRecordId'], 'sessionlens:proposal')

    def test_large_windows_are_bounded_with_omissions_and_text_budget(self):
        records = []
        for i in range(30):
            records += [record('u' + str(i), 'message', '需求' * 4000, i * 10, 'user'),
                        record('a' + str(i), 'message', '回复' * 4000, i * 10 + 1, 'assistant')]
        packet = turn_packet(records)
        self.assertEqual(len(packet['turns']), 24)
        self.assertLessEqual(packet['coverage']['textCharacters'], 30000)
        self.assertTrue(any(gap['reason'] == 'user_turns_omitted' for gap in packet['gaps']))
        self.assertTrue(any(turn['hasTurnGapBefore'] for turn in packet['turns']))
        self.assertTrue(all(turn['truncated'] for turn in packet['turns']))
        self.assertTrue(all('event' not in item for turn in packet['turns'] for item in turn['records']))

    def test_orphan_records_are_not_assigned_to_a_future_user(self):
        packet = turn_packet([record('c', 'tool_call', 'read old file', 1, call='old'),
                              record('u', 'message', '查天气', 2, 'user')])
        self.assertTrue(any(gap['reason'] == 'no_user_round_in_window' for gap in packet['gaps']))
        self.assertNotIn('sessionlens:c', packet['turns'][0]['recordIds'])

    def test_result_has_frontend_ids_and_preserves_original_timestamp(self):
        packet = turn_packet([record('u', 'message', '读文件', 1, 'user')])
        turn = packet['turns'][0]
        item = turn['records'][0]
        self.assertEqual(item['recordId'], 'sessionlens:u')
        self.assertEqual(item['sourceRecordId'], 'u')
        self.assertEqual(item['timestamp'], 1791345600001)
        task = group_tasks(packet['turns'], [link('u')])[0]
        expected = {'taskId', 'title', 'source', 'sessionId', 'requirements', 'stages', 'contexts', 'gaps', 'turnsCount', 'recordCount'}
        self.assertTrue(expected <= set(task))
        self.assertEqual(task['stages'][0]['collector'], 'sessionlens')

    def test_window_source_order_wins_over_reversed_upload_rowids(self):
        user = record('u', 'message', '读文件', 100, 'user')
        call = record('c', 'tool_call', 'path: a', 3, parent='u', call='read')
        user['windowSeq'] = 0
        call['windowSeq'] = 1
        packet = turn_packet([user, call])
        self.assertEqual(packet['turns'][0]['recordIds'], ['sessionlens:u', 'sessionlens:c'])
        self.assertEqual(packet['turns'][0]['records'][1]['association'], 'recorded_source_parent')


if __name__ == '__main__':
    unittest.main()
