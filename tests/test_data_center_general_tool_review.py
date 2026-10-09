"""Independent user-facing checks for ordinary tools and input provenance."""
import hashlib
import tempfile
import unittest
from pathlib import Path

from agentpair.collection_view import CollectionView
from agentpair.data_center import DataCenter
from agentpair.devices import DeviceStore
from agentpair.session_lens import SessionStore


class GeneralToolInputReview(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.devices = DeviceStore(root/'devices.db')
        self.sessions = SessionStore(root/'sessions.db')
        self.identities = {}
        for owner in ('alice', 'bob'):
            enrolled = self.devices.enroll(self.devices.pairing(owner)['code'], 'Office Mac')
            self.identities[owner] = self.devices.identity(enrolled['token'])
        self.center = DataCenter(CollectionView(self.devices, self.sessions))

    def tearDown(self):
        self.tmp.cleanup()

    def event(self, label, kind, stamp, payload, **extra):
        return dict({'id':hashlib.sha256(label.encode()).hexdigest(), 'schemaVersion':1,
            'source':'codex', 'sessionId':'ordinary-tools', 'kind':kind, 'timestamp':stamp,
            'payload':payload, 'evidence':{'path':'/fixture/session.jsonl', 'byteStart':0, 'byteEnd':100}}, **extra)

    def user(self, label, text, stamp=1700000000, **extra):
        return self.event(label, 'message', stamp, {'id':label, 'content':text}, role='user', **extra)

    def call(self, label, stamp=1700000010, parent=None, turn=None, **extra):
        payload = {'arguments':{'code':'text(await tools.exec_command({cmd:"pwd"}));'}}
        if parent is not None: payload['parentId'] = parent
        if turn is not None: payload['turnId'] = turn
        return self.event(label, 'tool_call', stamp, payload, name='exec', **extra)

    def upload(self, events, owner='alice'):
        self.sessions.ingest(self.identities[owner], {'schemaVersion':1, 'events':events})

    def search(self, **extra):
        return self.center.search({'q':'tool="exec"', **extra})

    def test_plain_exec_exposes_user_input_without_skill_or_mcp(self):
        self.upload([self.user('input', '修复登录按钮点击没有反应'), self.call('a', parent='input'),
                     self.call('b', 1700000011, parent='input')])
        items = self.search()['items']
        self.assertEqual(len(items), 2)
        self.assertTrue(all(item['capabilities']==[] for item in items))
        self.assertTrue(all(item['request']['text']=='修复登录按钮点击没有反应' for item in items))
        self.assertTrue(all(item['request']['association']=='recorded' for item in items))
        grouped = self.search(group='request')
        self.assertEqual((grouped['groupTotal'], grouped['recordTotal']), (1, 2))
        self.assertEqual(grouped['groups'][0]['request']['text'], '修复登录按钮点击没有反应')
        detail = self.center.record(items[0]['id'])['item']
        self.assertIn('exec_command', detail['arguments']['code'])

    def test_short_authorization_is_visible_as_clue_without_becoming_task_title(self):
        self.upload([self.user('input', '查看哪些服务器访问了天气接口'),
                     self.user('confirm', '做', 1700000001), self.call('a', parent='confirm')])
        request = self.search()['items'][0]['request']
        self.assertEqual(request['text'], '做')
        self.assertEqual(request['association'], 'recorded')
        self.assertEqual(request['inputKind'], 'continuation')
        self.assertEqual(request['contextHint']['text'], '查看哪些服务器访问了天气接口')
        self.assertEqual(request['contextHint']['association'], 'candidate')

    def test_duplicate_parent_source_id_is_not_promoted_to_recorded(self):
        first = self.user('input', '核查上传凭据的任务')
        conflicting = self.user('conflict', '生成五秒 SSH 视频', 1700000001)
        conflicting['payload']['id'] = 'input'
        self.upload([first, conflicting, self.call('a', parent='input')])
        self.assertEqual(self.search()['items'][0]['request']['association'], 'candidate')

    def test_duplicate_turn_users_are_not_promoted_to_recorded(self):
        first = self.user('input', '设计登录页面')
        second = self.user('second', '查上海天气', 1700000001)
        for user in (first, second): user['payload']['turnId'] = 'duplicate-turn'
        self.upload([first, second, self.call('a', turn='duplicate-turn')])
        self.assertEqual(self.search()['items'][0]['request']['association'], 'candidate')

    def test_late_tool_return_inherits_its_call_input_not_the_newer_user(self):
        self.upload([self.user('input-a', '查上海天气'), self.call('a', parent='input-a', callId='call-a'),
                     self.user('input-b', '生成 SSH 视频', 1700000011),
                     self.event('result-a', 'tool_result', 1700000012, {'output':'Shanghai: 23℃'}, callId='call-a')])
        returned = self.center.search({'q':'kind=tool_result'})['items'][0]
        request = returned['request']
        self.assertEqual(request['text'], '查上海天气')
        self.assertEqual(request['association'], 'recorded')
        self.assertTrue(request['viaRecordId'])
        self.assertEqual(self.center.record(returned['id'])['item']['result'], 'Shanghai: 23℃')

    def test_frozen_search_does_not_adopt_late_uploaded_input(self):
        self.upload([self.call('a')])
        before = self.search(group='request')
        self.upload([self.user('late-input', '给天气结果增加中文说明')])
        frozen = self.search(group='request', snapshot=before['snapshot'])
        self.assertEqual(frozen['groups'][0]['request']['association'], 'unknown')
        self.assertEqual(frozen['groups'][0]['id'], before['groups'][0]['id'])
        fresh = self.search(group='request')
        self.assertEqual(fresh['groups'][0]['request']['text'], '给天气结果增加中文说明')
        self.assertEqual(fresh['groups'][0]['request']['association'], 'candidate')

    def test_input_context_never_crosses_upload_account_or_source_session(self):
        self.upload([self.user('input', 'Alice 的服务器修复'), self.call('alice')])
        self.upload([self.call('bob')], owner='bob')
        self.upload([dict(self.call('different-session'), sessionId='different-session')])
        items = self.search()['items']
        self.assertEqual(sum(item['request']['text']=='Alice 的服务器修复' for item in items), 1)
        self.assertEqual(sum(item['request']['association']=='unknown' for item in items), 2)
        scoped = self.center.search({'q':'tool=exec', 'group':'request'}, owner='bob')
        self.assertEqual(scoped['recordTotal'], 1)
        self.assertEqual(scoped['groups'][0]['request']['association'], 'unknown')

    def conflicting_round_events(self, tool):
        first = self.user('input-a', '设计首页')
        first['payload']['turnId'] = 'turn-a'
        second = self.user('input-b', '删除临时文件', 1700000001)
        second['payload']['turnId'] = 'turn-b'
        selected = self.call('selected', 1700000002, parent='input-a', turn='turn-b')
        if tool == 'Skill':
            selected['name'] = 'Skill'
            selected['payload']['arguments'] = {'skill':'taste-skill'}
        return [first, second, selected,
            self.call('other-request-action', 1700000003, parent='input-b', turn='turn-b')]

    def assert_conflicting_round_stays_unassigned(self, query):
        items = self.center.search({'q':query})['items']
        selected = next(item for item in items if item['timestamp'] == 1700000002)
        self.assertEqual(selected['request']['association'], 'unknown')
        self.assertIsNone(selected['request']['text'])
        grouped = self.center.search({'q':query, 'group':'request'})
        group = next(group for group in grouped['groups']
                     if any(item['id'] == selected['id'] for item in group['items']))
        self.assertEqual(group['request']['association'], 'unknown')
        detail = self.center.record(selected['id'])['item']
        self.assertEqual(detail['request']['association'], 'unknown')
        self.assertNotEqual(detail['taskContext']['association'], 'recorded')
        unrelated = self.center.search({'q':'tool=exec'})['items']
        other_id = next(item['id'] for item in unrelated if item['timestamp'] == 1700000003)
        self.assertNotIn(other_id, [step['id'] for step in detail['portrait']['steps']],
                         'The other demand must not become the Skill or tool follow-up')
        self.assertTrue(any('冲突' in str(gap) or '不同' in str(gap)
                            for gap in detail['gaps']), 'Users need an explanation for the missing association')

    def test_skill_parent_and_native_round_pointing_to_different_demands_are_not_merged(self):
        self.upload(self.conflicting_round_events('Skill'))
        self.assert_conflicting_round_stays_unassigned('skill="taste-skill"')

    def test_exec_parent_and_native_round_pointing_to_different_demands_are_not_merged(self):
        self.upload(self.conflicting_round_events('exec'))
        self.assert_conflicting_round_stays_unassigned('tool="exec"')

    def test_consistent_skill_round_keeps_explicitly_linked_later_action(self):
        request = self.user('input', '按界面设计规范生成首页')
        request['payload']['turnId'] = 'same-turn'
        skill = self.event('skill', 'tool_call', 1700000001,
            {'id':'skill','parentId':'input','turnId':'same-turn',
             'arguments':{'skill':'taste-skill'}}, name='Skill')
        action = self.call('implementation', 1700000002, parent='input', turn='same-turn')
        self.upload([request, skill, action])
        selected = self.center.search({'q':'skill="taste-skill"'})['items'][0]
        self.assertEqual(selected['request']['association'], 'recorded')
        detail = self.center.record(selected['id'])['item']
        self.assertEqual(detail['taskContext']['userInput'], '按界面设计规范生成首页')
        action_id = self.search()['items'][0]['id']
        step = next(step for step in detail['portrait']['steps'] if step['id'] == action_id)
        self.assertEqual(step['association'], 'recorded')


if __name__=='__main__':
    unittest.main()
