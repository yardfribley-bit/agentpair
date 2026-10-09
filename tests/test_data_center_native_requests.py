"""Real Codex metadata shapes and explicit delegated-user provenance."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from agentpair.collection_view import CollectionView
from agentpair.data_center import DataCenter
from agentpair.devices import DeviceStore
from agentpair.session_lens import SessionStore


class NativeRequests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();root=Path(self.temp.name)
        self.devices=DeviceStore(root/'devices.db');self.sessions=SessionStore(root/'sessions.db')
        self.identities={o:self.devices.identity(self.devices.enroll(self.devices.pairing(o)['code'],'Office Mac')['token']) for o in ['alice','bob']}
        self.center=DataCenter(CollectionView(self.devices,self.sessions))
    def tearDown(self):self.temp.cleanup()
    def event(self,label,kind,payload,session='main',stamp=1700000000,**extra):
        return dict(id=hashlib.sha256(label.encode()).hexdigest(),schemaVersion=1,source='codex',sessionId=session,
                    kind=kind,timestamp=stamp,payload=payload,evidence={'path':'/fixture/log','byteStart':0,'byteEnd':1},**extra)
    def user(self,label='user',text='修复登录按钮',turn='root-turn',session='main',stamp=1700000000):
        return self.event(label,'message',{'id':label,'role':'user','content':[{'type':'input_text','text':text}],
                          'internal_chat_message_metadata_passthrough':{'turn_id':turn}},session,stamp,role='user')
    def call(self,label='call',turn='root-turn',session='main',stamp=1700010000):
        return self.event(label,'tool_call',{'id':label,'input':'text(await tools.exec_command({cmd:"pwd"}));',
                          'internal_chat_message_metadata_passthrough':{'turn_id':turn}},session,stamp,name='exec',callId=label)
    def child(self,root='root-turn',parent='main',session='child',turn='child-turn'):
        return [self.event('meta-'+session,'session_metadata',{'parent_thread_id':parent},session),
                self.event('context-'+session,'turn_context',{'turn_id':turn,'root_turn_id':root},session,1700009000),
                self.call('child-call-'+session,turn,session)]
    def upload(self,events,owner='alice',identity=None):self.sessions.ingest(identity or self.identities[owner],{'schemaVersion':1,'events':events})
    def search(self,**params):return self.center.search(dict(q='tool=exec',**params))

    def test_native_turn_survives_long_execution_and_control_messages(self):
        self.upload([self.user(),self.user('env','<environment_context>machine</environment_context>'),self.call()])
        item=self.search()['items'][0]
        self.assertEqual(item['request']['text'],'修复登录按钮');self.assertEqual(item['request']['association'],'recorded')
        self.assertEqual(self.center.record(item['id'])['item']['taskContext']['userInput'],'修复登录按钮')

    def test_child_root_and_parent_link_recover_original_user_and_group(self):
        self.upload([self.user(text='## My request:\n用户输入 怎么没有数据'),self.call(),*self.child()])
        items=self.search()['items'];child=next(i for i in items if i['sessionId']=='child')
        self.assertEqual(child['request']['text'],'用户输入 怎么没有数据')
        self.assertEqual(child['request']['basis'],'recorded_root_turn_parent_session')
        detail=self.center.record(child['id'])['item']
        self.assertEqual(detail['taskContext']['userInput'],child['request']['text'])
        self.assertEqual(detail['taskContext']['userRecordId'],child['request']['recordId'])
        self.assertTrue(any(r['from']==child['request']['recordId'] for r in detail['relations']))
        self.assertEqual(self.search(group='request')['groupTotal'],1)

    def test_explicit_parent_chain_can_be_nested(self):
        self.upload([self.user(),*self.child(session='middle'),*self.child(parent='middle',session='leaf')])
        leaf=next(i for i in self.search()['items'] if i['sessionId']=='leaf')
        self.assertEqual(leaf['request']['text'],'修复登录按钮')

    def test_root_without_parent_never_uses_another_session(self):
        self.upload([self.user(),*self.child()[1:]])
        item=self.search()['items'][0]
        self.assertIsNone(item['request']['text']);self.assertEqual(item['request']['association'],'unknown')
        self.assertIsNone(self.center.record(item['id'])['item']['taskContext']['userInput'])

    def test_parent_without_root_does_not_copy_inherited_old_user(self):
        child=self.child();child[1]['payload'].pop('root_turn_id')
        self.upload([self.user(),*child])
        self.assertIsNone(self.search()['items'][0]['request']['text'])

    def test_root_never_crosses_owner_device_or_application(self):
        self.upload(self.child());self.upload([self.user()],owner='bob')
        second=self.devices.identity(self.devices.enroll(self.devices.pairing('alice')['code'],'Other Mac')['token'])
        self.upload([self.user('other-device')],identity=second)
        workbuddy=self.user('other-app');workbuddy['source']='workbuddy';self.upload([workbuddy])
        self.assertIsNone(self.search()['items'][0]['request']['text'])

    def test_conflicting_root_and_turn_render_unknown_even_in_details_and_groups(self):
        child=self.child();child[1]['payload']['rootTurnId']='different'
        bad=self.call('bad');bad['payload']['turn_id']='different'
        self.upload([self.user(),*child,bad])
        for item in self.search()['items']:
            self.assertEqual(item['request']['association'],'unknown')
            self.assertIsNone(self.center.record(item['id'])['item']['taskContext']['userInput'])
        self.assertTrue(all(g['request']['association']=='unknown' for g in self.search(group='request')['groups']))

    def test_duplicate_root_users_and_parent_cycles_remain_unknown(self):
        self.upload([self.user(),self.user('second','查天气'),*self.child(),*self.child(parent='cycle',session='cycle')])
        self.assertTrue(all(i['request']['association']=='unknown' for i in self.search()['items']))

    def test_unique_return_cannot_override_conflicted_call_with_its_own_turn(self):
        call=self.call();call['payload']['turn_id']='different'
        result=self.event('return','tool_result',{'output':'ok','turn_id':'root-turn'},stamp=1700010001,callId='call')
        self.upload([self.user(),call,result])
        item=self.center.search({'q':'kind=tool_result'})['items'][0]
        self.assertEqual(item['request']['association'],'unknown')
        self.assertIsNone(self.center.record(item['id'])['item']['taskContext']['userInput'])

    def test_intermediate_parent_turn_conflict_does_not_invent_a_request(self):
        first=self.user();second=self.user('second','查天气','different',stamp=1700000001)
        reasoning=self.event('reason','reasoning',{'id':'reason','parentId':'user','turn_id':'different','text':'thinking'},stamp=1700000002)
        call=self.call();call['payload']['parentId']='reason'
        self.upload([first,second,reasoning,call])
        item=self.search()['items'][0]
        self.assertEqual(item['request']['association'],'unknown')
        self.assertIsNone(self.center.record(item['id'])['item']['taskContext']['userInput'])

    def test_late_parent_user_is_excluded_from_frozen_search(self):
        self.upload(self.child());first=self.search()
        self.upload([self.user()])
        self.assertIsNone(self.search(snapshot=first['snapshot'])['items'][0]['request']['text'])
        self.assertEqual(self.search()['items'][0]['request']['text'],'修复登录按钮')

    def test_recorded_continuation_is_not_replaced_by_a_different_requirement(self):
        for text in ['。','做','补齐']:
            with self.subTest(text=text):
                turn='continue-'+text;session='task-'+text
                self.upload([self.user('need-'+text,'让工具返回可读','need',session),self.user('go-'+text,text,turn,session,1700000010),self.call('call-'+text,turn,session)])
        for item in self.search()['items']:
            r=item['request'];self.assertEqual(r['inputKind'],'continuation');self.assertIn(r['text'],['。','做','补齐'])
            self.assertEqual(r['contextHint']['text'],'让工具返回可读');self.assertEqual(r['contextHint']['association'],'candidate')

    def mirror(self,label='mirror',text='修复登录按钮',turn='root-turn',session='main'):
        return self.event(label,'user_message',{'id':label,'message':text,'turn_id':turn},session,1700000000.003,role='user')

    def test_codex_dual_user_representations_share_one_native_anchor(self):
        call=self.call();call['payload']['parentId']='mirror'
        result=self.event('return','tool_result',{'output':'ok'},stamp=1700010001,callId='call')
        reason=self.event('thought','reasoning',{'text':'核对用户输入','turn_id':'root-turn'},stamp=1700000010)
        self.upload([self.user(),self.mirror(),call,result,reason,*self.child()])
        items=self.search()['items'];ids={i['request']['recordId'] for i in items}
        self.assertEqual(len(ids),1);self.assertEqual(self.search(group='request')['groupTotal'],1)
        for item in items:
            self.assertEqual(item['request']['text'],'修复登录按钮')
            detail=self.center.record(item['id'])['item']
            self.assertEqual(detail['taskContext']['userRecordId'],item['request']['recordId'])
        returned=self.center.search({'q':'kind=tool_result'})['items'][0]
        self.assertIn(returned['request']['recordId'],ids)
        main=next(i for i in items if i['sessionId']=='main')
        self.assertNotEqual(main['request']['viaRecordId'],main['request']['recordId'])
        self.assertTrue(any(r['relationBasis']=='recorded_turn_id' for r in self.center.record(main['id'])['item']['related']))

    def test_dual_formats_with_different_input_remain_ambiguous(self):
        self.upload([self.user(),self.mirror(text='查上海天气'),*self.child()])
        self.assertEqual(self.search()['items'][0]['request']['association'],'unknown')

    def test_same_type_identical_text_is_not_a_known_mirror(self):
        self.upload([self.user(),self.user('second'),*self.child()])
        self.assertEqual(self.search()['items'][0]['request']['association'],'unknown')

    def test_third_input_is_not_hidden_by_pair_limit(self):
        self.upload([self.user(),self.mirror(),self.user('third'),*self.child()])
        self.assertEqual(self.search()['items'][0]['request']['association'],'unknown')

    def test_late_mirror_does_not_change_frozen_anchor(self):
        self.upload([self.mirror(),self.call(),*self.child()]);first=self.search()
        old=first['items'][0]['request']['recordId']
        self.upload([self.user()])
        frozen=self.search(snapshot=first['snapshot'])
        self.assertTrue(all(i['request']['recordId']==old for i in frozen['items']))
        self.assertTrue(all(i['request']['recordId']!=old for i in self.search()['items']))

    def test_non_codex_dual_formats_are_not_inferred_as_duplicates(self):
        events=[self.user(),self.mirror(),self.call()]
        for e in events:e['source']='workbuddy'
        self.upload(events)
        self.assertNotEqual(self.search()['items'][0]['request']['association'],'recorded')

    def test_legacy_metadata_repair_preserves_raw_text_and_cursors(self):
        events=[self.user(),*self.child()];self.upload(events);self.search()
        with self.center._db() as db:
            before=[tuple(r) for r in db.execute('SELECT * FROM dc_fields ORDER BY record,ordinal')]
            cursor=self.center._state(db,'session_seq')
            for row in db.execute('SELECT key,metadata FROM dc_records').fetchall():
                m=json.loads(row['metadata'])
                for key in list(m):
                    if key.startswith(('turnIdentity','rootTurn','parentSession','requestEvidence')) or key=='turnId':m.pop(key)
                db.execute('UPDATE dc_records SET metadata=? WHERE key=?',(json.dumps(m),row['key']))
        with patch.object(self.center,'_put',side_effect=AssertionError('No reindex')):
            self.assertEqual(self.center.refresh_request_evidence()['remaining'],0)
        with self.center._db() as db:
            self.assertEqual(before,[tuple(r) for r in db.execute('SELECT * FROM dc_fields ORDER BY record,ordinal')])
            self.assertEqual(cursor,self.center._state(db,'session_seq'))
        self.assertEqual(self.search()['items'][0]['request']['text'],'修复登录按钮')
        with self.sessions.connect() as db:self.assertEqual(len(events),db.execute('SELECT count(*) FROM session_events').fetchone()[0])

if __name__=='__main__':unittest.main()
