"""Request grouping preserves evidence identities, query scope and snapshots."""
import hashlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode

from agentpair.collection_view import CollectionView
from agentpair.data_center import DataCenter
from agentpair.devices import DeviceStore
from agentpair.session_lens import SessionStore


class RequestGroupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.devices = DeviceStore(root/'devices.db')
        self.root = root
        self.sessions = SessionStore(root/'session-lens.db')
        self.identities = {}
        for owner in ('alice','bob'):
            enrolled = self.devices.enroll(self.devices.pairing(owner)['code'],'Same Mac')
            self.identities[owner] = self.devices.identity(enrolled['token'])
        self.center = DataCenter(CollectionView(self.devices,self.sessions))

    def tearDown(self):
        self.tmp.cleanup()

    def event(self, label, kind, stamp, payload, **extra):
        return dict({'id':hashlib.sha256(label.encode()).hexdigest(),'schemaVersion':1,
            'source':'workbuddy','sessionId':'same-session','kind':kind,'timestamp':stamp,
            'payload':payload,'evidence':{'path':'/fixture/session.jsonl','byteStart':0,'byteEnd':100}},**extra)

    def request(self, label='request', stamp=1700000000, text='设计一个首页', **extra):
        payload = {'id':label,'content':text}
        if 'turnId' in extra:payload['turnId'] = extra.pop('turnId')
        return self.event(label,'message',stamp,payload,role='user',**extra)

    def load(self, label, stamp=1700000001, skill='taste-skill', parent=None, **extra):
        payload = {'arguments':{'skill':skill}}
        if parent is not None:payload['parentId'] = parent
        if 'turnId' in extra:payload['turnId'] = extra.pop('turnId')
        return self.event(label,'tool_call',stamp,payload,name='Skill',**extra)

    def execute(self, label, stamp=1700000001, parent=None, turn=None, **extra):
        payload = {'arguments':{'code':'text(await tools.exec_command({cmd:"pwd"}));'}}
        if parent is not None:payload['parentId'] = parent
        if turn is not None:payload['turnId'] = turn
        return self.event(label,'tool_call',stamp,payload,name='exec',callId=extra.pop('callId',label),**extra)

    def result(self, label, call, stamp=1700000002, **extra):
        return self.event(label,'tool_result',stamp,{'output':'/fixture/project'},callId=call,**extra)

    def upload(self, events, owner='alice', identity=None):
        for start in range(0,len(events),30):
            self.sessions.ingest(identity or self.identities[owner],{'schemaVersion':1,'events':events[start:start+30]})

    def grouped(self, **extra):
        return self.center.search({'q':'skill="taste-skill"','group':'request',**extra})

    def test_same_request_is_one_group_before_pagination_and_record_mode_stays_default(self):
        self.upload([self.request()]+[self.load('call-'+str(i),1700000010+i,parent='request') for i in range(6)])
        records = self.center.search({'q':'skill="taste-skill"','pageSize':1})
        self.assertEqual(records['total'],6)
        self.assertEqual(len(records['items']),1)
        grouped = self.grouped(pageSize=1)
        self.assertEqual(grouped['items'],[])
        self.assertEqual((grouped['total'],grouped['groupTotal'],grouped['recordTotal']),(1,1,6))
        self.assertEqual(grouped['summary']['matchedRecords'],6)
        self.assertEqual(grouped['requestGroupTotal'],1)
        self.assertEqual(grouped['unassignedRecordTotal'],0)
        self.assertFalse(grouped['hasMore'])
        group = grouped['groups'][0]
        self.assertEqual(group['recordCount'],6)
        self.assertEqual(group['request']['association'],'recorded')
        self.assertEqual(len(group['items']),6)
        self.assertEqual(self.grouped(page=2,pageSize=1,snapshot=grouped['snapshot'])['groups'],[])

    def test_large_group_preview_and_exact_member_pages_include_eleventh_record(self):
        self.upload([self.request()]+[self.load('large-'+str(i),1700000010+i,parent='request') for i in range(13)])
        calls = []
        original = self.center._item
        def item(*args,**kwargs):
            calls.append(args[1]['key'])
            return original(*args,**kwargs)
        with patch.object(self.center,'_item',side_effect=item):
            grouped = self.grouped()
        self.assertEqual(len(calls),10)
        group = grouped['groups'][0]
        self.assertEqual(len(group['items']),10)
        self.assertEqual(group['recordCount'],13)
        self.assertTrue(group['hasMore'])
        members = self.grouped(groupId=group['id'],page=2,pageSize=10,snapshot=grouped['snapshot'])
        self.assertEqual(members['total'],13)
        self.assertEqual(members['recordTotal'],13)
        self.assertEqual(len(members['items']),3)
        self.assertFalse(members['hasMore'])
        self.assertEqual(members['group']['id'],group['id'])
        ids = [item['id'] for item in group['items']+members['items']]
        self.assertEqual(len(set(ids)),13)

    def test_identical_request_text_with_distinct_source_ids_remains_two_groups(self):
        self.upload([self.request('a'),self.load('a-call',parent='a'),
                     self.request('b',1700000010),self.load('b-call',1700000011,parent='b')])
        grouped = self.grouped(pageSize=1)
        self.assertEqual(grouped['total'],2)
        following = self.grouped(pageSize=1,page=2,snapshot=grouped['snapshot'])
        self.assertNotEqual(grouped['groups'][0]['id'],following['groups'][0]['id'])
        self.assertEqual(grouped['groups'][0]['request']['text'],following['groups'][0]['request']['text'])

    def test_candidate_group_and_unassigned_records_keep_uncertainty(self):
        self.upload([self.load('unknown-a',1699999980),self.load('unknown-b',1699999981),
                     self.request(),self.load('candidate-a'),self.load('candidate-b',1700000002)])
        grouped = self.grouped()
        self.assertEqual((grouped['total'],grouped['recordTotal']),(3,4))
        self.assertEqual((grouped['requestGroupTotal'],grouped['unassignedRecordTotal']),(1,2))
        assigned = next(group for group in grouped['groups'] if group['request'].get('recordId'))
        self.assertEqual(assigned['recordCount'],2)
        self.assertEqual(assigned['request']['association'],'candidate')
        self.assertTrue(all(item['activity']['request']['association']=='candidate' for item in assigned['items']))
        unknown = [group for group in grouped['groups'] if group['request']['association']=='unknown']
        self.assertEqual(len(unknown),2)
        self.assertTrue(all(group['recordCount']==1 for group in unknown))

    def test_mixed_associations_never_promote_candidate_members_to_recorded(self):
        self.upload([self.request(),self.load('explicit',parent='request'),self.load('candidate',1700000002)])
        group = self.grouped()['groups'][0]
        self.assertEqual(group['request']['association'],'candidate')
        self.assertEqual(group['associationCounts'],{'recorded':1,'candidate':1})
        self.assertEqual({item['activity']['request']['association'] for item in group['items']},{'recorded','candidate'})

    def test_only_query_matches_are_grouped_and_filters_apply_before_grouping(self):
        self.upload([self.request(),self.load('taste-a',1700000001,parent='request'),
            self.load('taste-b',1700000002,parent='request'),
            self.load('browser',1700000003,skill='agent-browser',parent='request'),
            self.event('mcp','tool_call',1700000004,{'arguments':{'url':'https://example.test'},'parentId':'request'},
                       name='mcp__tinyfish__run')])
        grouped = self.grouped()
        self.assertEqual(grouped['recordTotal'],2)
        self.assertEqual(grouped['groups'][0]['recordCount'],2)
        filtered = self.grouped(after='1700000002',before='1700000002',location='arguments',object='skill')
        self.assertEqual(filtered['groups'][0]['recordCount'],1)
        excluded = self.grouped(q='skill!=taste-skill',object='skill')
        self.assertEqual(excluded['recordTotal'],1)
        self.assertEqual(excluded['groups'][0]['items'][0]['activity']['name'],'agent-browser')
        combined = self.grouped(q='skill=taste-skill || skill=agent-browser',object='skill')
        self.assertEqual(combined['recordTotal'],3)
        self.assertEqual(combined['groupTotal'],1)
        with self.assertRaises(ValueError):
            self.grouped(q='skill=agent-browser',groupId=grouped['groups'][0]['id'],snapshot=grouped['snapshot'])
        self.assertEqual(self.grouped(groupId=grouped['groups'][0]['id'],snapshot=grouped['snapshot'])['total'],2)

    def test_same_anchor_ids_never_cross_account_application_session_or_device(self):
        base = [self.request(),self.load('load',parent='request')]
        self.upload(base)
        self.upload(base,owner='bob')
        self.upload([dict(event,id=hashlib.sha256(('codex'+event['id']).encode()).hexdigest(),source='codex') for event in base])
        self.upload([dict(event,id=hashlib.sha256(('other-session'+event['id']).encode()).hexdigest(),sessionId='other-session') for event in base])
        another = self.devices.enroll(self.devices.pairing('alice')['code'],'Same Mac')
        other = self.devices.identity(another['token'])
        self.upload(base,identity=other)
        grouped = self.grouped()
        self.assertEqual(grouped['requestGroupTotal'],5)
        self.assertEqual(grouped['recordTotal'],5)
        alice = self.center.search({'q':'skill=taste-skill','group':'request'},owner='alice')
        self.assertEqual(alice['requestGroupTotal'],4)
        bob = self.center.search({'q':'skill=taste-skill','group':'request'},owner='bob')
        with self.assertRaises(ValueError):
            self.center.search({'q':'skill=taste-skill','group':'request','groupId':bob['groups'][0]['id']},owner='alice')

    def test_alias_anchor_replay_merges_only_matching_digest(self):
        alias = self.devices.enroll(self.devices.pairing('alice')['code'],'Alias Mac')
        identity = self.devices.identity(alias['token'])
        anchor = self.request()
        self.upload([anchor,self.load('main-call',parent='request')])
        self.upload([anchor,self.load('alias-call',parent='request')],identity=identity)
        self.devices.merge_registrations('alice',self.identities['alice']['id'],[identity['id']])
        self.assertEqual(self.grouped()['groupTotal'],1)
        other = self.devices.enroll(self.devices.pairing('alice')['code'],'Conflicting Alias')
        conflicting_identity = self.devices.identity(other['token'])
        conflicting = self.request(text='不同的原始需求')
        self.upload([conflicting,self.load('conflicting-call',parent='request')],identity=conflicting_identity)
        self.devices.merge_registrations('alice',self.identities['alice']['id'],[conflicting_identity['id']])
        grouped = self.grouped()
        self.assertEqual(grouped['groupTotal'],2)
        self.assertEqual({group['request']['text'] for group in grouped['groups']},{'设计一个首页','不同的原始需求'})

    def test_snapshot_keeps_request_anchors_and_member_set_during_late_upload(self):
        self.upload([self.load('early-a',1700000010),self.load('early-b',1700000011)])
        before = self.grouped()
        self.assertEqual(before['requestGroupTotal'],0)
        self.upload([self.request(stamp=1700000000),self.load('late-call',1700000012,parent='request')])
        frozen = self.grouped(snapshot=before['snapshot'])
        self.assertEqual(frozen['recordTotal'],2)
        self.assertEqual([group['id'] for group in frozen['groups']],[group['id'] for group in before['groups']])
        frozen_member = self.grouped(groupId=before['groups'][0]['id'],snapshot=before['snapshot'])
        self.assertEqual(frozen_member['total'],1)
        refreshed = self.grouped()
        self.assertEqual((refreshed['groupTotal'],refreshed['recordTotal']),(1,3))

    def test_turn_id_association_groups_explicit_request_without_parent(self):
        self.upload([self.request(turnId='turn-1'),self.load('turn-a',turnId='turn-1'),
                     self.load('turn-b',1700000002,turnId='turn-1')])
        group = self.grouped()['groups'][0]
        self.assertEqual(group['recordCount'],2)
        self.assertEqual(group['request']['association'],'recorded')
        self.assertEqual(group['request']['basis'],'recorded_turn_id')

    def test_generic_exec_calls_have_user_input_without_skill_or_mcp_evidence(self):
        self.upload([self.request(text='检查项目目录'),
            self.execute('exec-parent',parent='request'),self.execute('exec-turn',1700000002,turn='turn-2'),
            self.request('turn-user',1700000001,text='查询项目大小',turnId='turn-2')])
        grouped = self.grouped(q='tool=exec')
        self.assertEqual((grouped['recordTotal'],grouped['requestGroupTotal'],grouped['unassignedRecordTotal']),(2,2,0))
        requests = {group['request']['text']:group['request']['basis'] for group in grouped['groups']}
        self.assertEqual(requests,{'检查项目目录':'recorded_source_parent','查询项目大小':'recorded_turn_id'})
        records = self.center.search({'q':'tool=exec'})['items']
        self.assertTrue(all(not item['capabilities'] and item['activity'] is None for item in records))
        self.assertEqual({item['request']['text'] for item in records},set(requests))

    def test_full_group_tool_counts_exclude_returns_and_preserve_snapshot(self):
        events = [self.request(text='检查全部项目目录')]
        for number in range(13):
            call = 'counted-'+str(number)
            events.extend([self.execute(call,1700000010+number*2,parent='request'),
                           self.result('returned-'+str(number),call,1700000011+number*2)])
        self.upload(events)
        query = 'tool=exec || kind=tool_result'
        grouped = self.grouped(q=query)
        group = grouped['groups'][0]
        self.assertEqual(group['recordCount'],26)
        self.assertEqual(len(group['items']),10)
        self.assertEqual(group['toolCallCounts'],[{'name':'exec','count':13}])
        self.assertEqual(group['toolCallTotal'],13)
        members = self.grouped(q=query,groupId=group['id'],page=3,pageSize=10,snapshot=grouped['snapshot'])
        self.assertEqual(len(members['items']),6)
        self.assertEqual(members['group']['toolCallCounts'],group['toolCallCounts'])
        returns = self.grouped(q='kind=tool_result')['groups'][0]
        self.assertEqual((returns['recordCount'],returns['toolCallTotal'],returns['toolCallCounts']),(13,0,[]))
        self.upload([self.execute('late-counted',1700000050,parent='request'),
                     self.result('late-returned','late-counted',1700000051)])
        frozen = self.grouped(q=query,snapshot=grouped['snapshot'])['groups'][0]
        self.assertEqual((frozen['recordCount'],frozen['toolCallTotal']),(26,13))
        fresh = self.grouped(q=query)['groups'][0]
        self.assertEqual((fresh['recordCount'],fresh['toolCallTotal']),(28,14))

    def test_generic_exec_candidate_skips_short_confirmation_and_keeps_uncertainty(self):
        self.upload([self.request(text='整理项目目录'),self.request('confirmation',1700000001,text='做'),
            self.execute('exec-a',1700000002),self.execute('exec-b',1700000003)])
        group = self.grouped(q='tool=exec')['groups'][0]
        self.assertEqual(group['recordCount'],2)
        self.assertEqual(group['request']['text'],'整理项目目录')
        self.assertEqual(group['request']['association'],'candidate')
        self.assertTrue(all(item['request']['association']=='candidate' for item in group['items']))

    def test_generic_return_inherits_unique_call_request_before_intervening_input(self):
        self.upload([self.request(text='执行目录检查'),self.execute('exec-a',parent='request'),
            self.request('other-input',1700000002,text='接下来查询天气'),self.result('return-a','exec-a',1700000003)])
        grouped = self.grouped(q='tool=exec || kind=tool_result')
        self.assertEqual((grouped['groupTotal'],grouped['recordTotal']),(1,2))
        group = grouped['groups'][0]
        self.assertEqual(group['request']['text'],'执行目录检查')
        returned = next(item for item in group['items'] if item['kind']=='tool_result')
        self.assertEqual(returned['request']['association'],'recorded')
        self.assertEqual(returned['request']['relationBasis'],'recorded_unique_call_id')
        self.assertEqual(returned['request']['viaRecordId'],next(item['id'] for item in group['items'] if item['kind']=='tool_call'))

    def test_generic_return_never_promotes_time_candidate_from_its_unique_call(self):
        self.upload([self.request(text='执行目录检查'),self.execute('exec-a'),
            self.request('other-input',1700000002,text='接下来查询天气'),self.result('return-a','exec-a',1700000003)])
        group = self.grouped(q='tool=exec || kind=tool_result')['groups'][0]
        self.assertEqual(group['recordCount'],2)
        self.assertEqual(group['request']['text'],'执行目录检查')
        self.assertEqual(group['associationCounts'],{'candidate':2})

    def test_generic_return_conflicting_recorded_parent_and_call_input_stays_unassigned(self):
        self.upload([self.request(text='执行目录检查'),self.execute('exec-a',parent='request'),
            self.request('other-input',1700000002,text='接下来查询天气'),
            self.event('return-a','tool_result',1700000003,{'output':'/fixture/project','parentId':'other-input'},callId='exec-a')])
        grouped = self.grouped(q='tool=exec || kind=tool_result')
        self.assertEqual((grouped['requestGroupTotal'],grouped['unassignedRecordTotal']),(1,1))
        returned = next(group for group in grouped['groups'] if group['request']['association']=='unknown')
        self.assertIsNone(returned['request']['text'])
        self.assertEqual(returned['request']['relationBasis'],'conflicting_recorded_links')
        self.assertEqual(len(returned['request']['conflictingRecordIds']),2)
        self.assertEqual(returned['recordCount'],1)

    def test_parent_turn_conflict_from_late_user_upload_respects_old_snapshot(self):
        self.upload([self.request('input-a',text='设计首页',turnId='turn-a'),
                     self.execute('exec-a',parent='input-a',turn='turn-b')])
        before = self.grouped(q='tool=exec')
        self.assertEqual(before['groups'][0]['request']['text'],'设计首页')
        self.upload([self.request('input-b',1700000000,text='删除临时文件',turnId='turn-b')])
        frozen = self.grouped(q='tool=exec',snapshot=before['snapshot'])
        self.assertEqual(frozen['groups'][0]['id'],before['groups'][0]['id'])
        self.assertEqual(frozen['groups'][0]['request']['association'],'recorded')
        fresh = self.grouped(q='tool=exec')
        self.assertEqual((fresh['requestGroupTotal'],fresh['unassignedRecordTotal']),(0,1))
        self.assertEqual(fresh['groups'][0]['request']['relationBasis'],'conflicting_recorded_links')

    def test_conflicted_member_cannot_join_consistent_parent_turn_portrait(self):
        self.upload([self.request('input-a',text='设计首页',turnId='turn-a'),
                     self.request('input-b',1700000001,text='删除临时文件',turnId='turn-b'),
                     self.load('skill-a',1700000002,parent='input-a',turnId='turn-a'),
                     self.execute('conflicted-action',1700000003,parent='input-b',turn='turn-a'),
                     self.execute('valid-action',1700000004,parent='input-a',turn='turn-a')])
        selected = self.grouped()['groups'][0]['items'][0]
        detail = self.center.record(selected['id'])['item']
        ids = {step['recordId'] for step in detail['portrait']['steps']}
        bad = 'sessionlens:'+hashlib.sha256(b'conflicted-action').hexdigest()
        valid = 'sessionlens:'+hashlib.sha256(b'valid-action').hexdigest()
        self.assertNotIn(bad,ids)
        self.assertIn(valid,ids)
        self.assertEqual(detail['taskContext']['userInput'],'设计首页')
        self.assertTrue(any('不一致' in gap for gap in detail['gaps']))

    def test_selected_user_message_remains_its_own_input_despite_a_previous_parent(self):
        previous = self.request('previous',text='先设计首页')
        current = self.request('current',1700000001,text='再整理文件',turnId='new-turn')
        current['payload']['parentId']='previous'
        self.upload([previous,current])
        selected = self.center.search({'q':'再整理文件','kind':'user'})['items'][0]
        detail = self.center.record(selected['id'])['item']
        self.assertEqual(detail['taskContext']['userInput'],'再整理文件')
        self.assertEqual(detail['taskContext']['association'],'recorded')

    def test_generic_ambiguous_call_return_pair_does_not_confirm_request(self):
        self.upload([self.request(text='执行目录检查'),self.execute('exec-a',parent='request'),
            self.execute('duplicate-call',1700000002,parent='request',callId='exec-a'),
            self.request('other-input',1700000003,text='查询上海天气'),self.result('return-a','exec-a',1700000004)])
        group = self.grouped(q='kind=tool_result')['groups'][0]
        self.assertEqual(group['request']['text'],'查询上海天气')
        self.assertEqual(group['request']['association'],'candidate')
        self.assertNotIn('relationBasis',group['request'])

    def test_generic_unknown_rows_remain_reachable_after_known_request_groups(self):
        self.upload([self.request(text='执行目录检查'),self.execute('known',parent='request'),
                     self.execute('unknown',1700010000)])
        first = self.grouped(q='tool=exec',pageSize=1)
        self.assertEqual((first['total'],first['recordTotal'],first['unassignedRecordTotal']),(2,2,1))
        self.assertTrue(first['hasMore'])
        self.assertEqual(first['groups'][0]['request']['text'],'执行目录检查')
        second = self.grouped(q='tool=exec',pageSize=1,page=2,snapshot=first['snapshot'])
        self.assertEqual(second['groups'][0]['request']['association'],'unknown')
        self.assertFalse(second['hasMore'])
        raw = self.center.search({'q':'tool=exec','pageSize':1})
        self.assertEqual(raw['items'][0]['recordId'],'sessionlens:'+hashlib.sha256(b'unknown').hexdigest())

    def test_generic_request_scope_does_not_cross_account_device_source_or_session(self):
        self.upload([self.request(text='这条输入只属于本机本会话'),self.execute('local',parent='request')])
        other = self.devices.enroll(self.devices.pairing('alice')['code'],'Other Mac')
        self.upload([self.execute('other-device',parent='request')],identity=self.devices.identity(other['token']))
        self.upload([self.execute('other-owner',parent='request')],owner='bob')
        self.upload([self.execute('other-app',parent='request',source='codex')])
        self.upload([self.execute('other-session',parent='request',sessionId='other-session')])
        grouped = self.grouped(q='tool=exec')
        self.assertEqual((grouped['requestGroupTotal'],grouped['unassignedRecordTotal']),(1,4))

    def test_generic_snapshot_freezes_late_upload_of_input_and_paired_call(self):
        self.upload([self.result('return-a','exec-a',1700000003)])
        before = self.grouped(q='kind=tool_result')
        self.upload([self.request(text='执行目录检查'),self.execute('exec-a',parent='request')])
        frozen = self.grouped(q='kind=tool_result',snapshot=before['snapshot'])
        self.assertEqual(frozen['groups'][0]['request']['association'],'unknown')
        self.assertEqual(frozen['groups'][0]['id'],before['groups'][0]['id'])
        current = self.grouped(q='kind=tool_result')['groups'][0]
        self.assertEqual(current['request']['text'],'执行目录检查')
        self.assertEqual(current['request']['association'],'recorded')

    def test_non_tool_matches_are_not_grouped_by_a_previous_user_input(self):
        self.upload([self.request(text='执行目录检查'),
            self.event('context','context',1700000001,{'content':'exec 上下文说明'}),
            self.request('other-user',1700000002,text='exec 工具怎么用')])
        grouped = self.grouped(q='exec')
        self.assertEqual((grouped['requestGroupTotal'],grouped['unassignedRecordTotal']),(0,2))
        self.assertTrue(all(group['request']['association']=='unknown' for group in grouped['groups']))

    def test_generic_request_excerpt_links_to_original_input_without_raw_reads(self):
        text = '请检查项目目录并解释每个文件的作用。'*30
        self.upload([self.request(text=text),self.execute('exec-a',parent='request')])
        with patch.object(self.center,'_lookup',side_effect=AssertionError('Grouped search must not read raw source records')):
            request = self.grouped(q='tool=exec')['groups'][0]['request']
        self.assertEqual(request['text'],text[:300])
        self.assertTrue(request['textTruncated'])
        self.assertEqual(request['timestamp'],1700000000)
        self.assertIn(request['recordId'],request['rawUrl'])
        original = self.center.record(request['recordId'])['item']
        self.assertEqual(original['userInput'],text)

    def test_invalid_group_parameters_are_rejected(self):
        for params in ({'group':'session'},{'groupId':'rq_'+'0'*64},
                       {'group':'request','groupId':'invalid'}):
            with self.assertRaises(ValueError):self.center.search(params)

    def test_grouped_http_dispatch_and_members_reuse_existing_search_endpoint(self):
        from agentpair.platform import handler_for
        from agentpair.tasks import TaskEngine
        class Backend:
            def estimate(self,*args,**kwargs):raise AssertionError('Read-only search cannot reserve model usage')
            def call(self,*args,**kwargs):raise AssertionError('Read-only search cannot invoke a model')
        self.upload([self.request()]+[self.load('api-'+str(i),1700000010+i,parent='request') for i in range(12)])
        engine = TaskEngine(self.root/'tasks.db',Backend(),start=False)
        handler = handler_for(engine,'fixture-password-12345','http://127.0.0.1')
        def get(params):
            request = object.__new__(handler)
            request.path = '/api/data-center/search?'+urlencode(params)
            request.headers = {};request.wfile = io.BytesIO()
            responses = []
            request.respond = lambda status,data,cookie=False:responses.append((status,data))
            request.do_GET()
            return responses[0]
        try:
            status,value = get({'q':'skill=taste-skill','group':'request','pageSize':1})
            self.assertEqual(status,200)
            self.assertEqual((value['total'],value['recordTotal'],value['summary']['matchedRecords']),(1,12,12))
            status,members = get({'q':'skill=taste-skill','group':'request','groupId':value['groups'][0]['id'],
                                  'snapshot':value['snapshot'],'page':2,'pageSize':10})
            self.assertEqual(status,200)
            self.assertEqual(len(members['items']),2)
            self.assertEqual(members['total'],12)
            self.assertEqual(get({'q':'skill=agent-browser','group':'request','groupId':value['groups'][0]['id']})[0],400)
        finally:engine.close()


if __name__=='__main__':
    unittest.main()
