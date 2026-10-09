import copy
import hashlib
import json
import sqlite3
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
from pathlib import Path
from unittest.mock import patch

from agentpair.session_lens import SessionStore
from agentpair.session_packets import MAX_PACKET_BYTES, make_packet, source_seconds


def event(n, kind='message', text='', role=None, stamp=None, source='workbuddy', session='same', call=None):
    payload={'content':text}
    if role:payload['role']=role
    if kind=='tool_call':payload={'arguments':{'url':text}}
    if kind=='tool_result':payload={'output':text}
    return {'schemaVersion':1,'id':hashlib.sha256(str(n).encode()).hexdigest(),'source':source,
            'sessionId':session,'kind':kind,'role':role,'callId':call,'timestamp':stamp,
            'payload':payload,'evidence':{'path':'/fixture/source.jsonl','fileIdentity':'fixture',
                                         'epoch':0,'byteStart':n*100,'byteEnd':n*100+90}}


class SessionPacketTests(unittest.TestCase):
    def packet(self, rows, source='workbuddy'):
        return make_packet(rows,'device','alice','same',source)

    def test_source_times_order_late_uploads_and_preserve_latest_return(self):
        rows=[event(1,text='查上海天气',role='user',stamp='2026-10-07T02:00:00Z'),
              event(2,'tool_call','https://weather.example/Shanghai',stamp=1791338401000,call='weather'),
              event(3,'tool_result','上海晴，24℃',stamp='1791338402',call='weather'),
              event(4,text='上海晴，24℃',role='assistant',stamp='2026-10-07T02:00:03+00:00')]
        packet=self.packet([rows[3],rows[2],rows[0],rows[1]])
        self.assertEqual([f['eventId'] for f in packet['fragments']],[r['id'] for r in rows])
        self.assertEqual(packet['toolPairs'][0]['callRef'],'E002')
        self.assertEqual(packet['toolPairs'][0]['resultRef'],'E003')
        self.assertIn('weather.example/Shanghai',packet['fragments'][1]['text'])
        self.assertIn('24℃',packet['fragments'][2]['text'])
        self.assertEqual(source_seconds(rows[1]['timestamp']),source_seconds('2026-10-07T02:00:01Z'))

    def test_multiple_user_rounds_and_original_request_are_candidates(self):
        rows=[event(1,text='生成一个五秒SSH协议动画，展现握手',role='user'),
              event(2,text='使用线条和蓝色',role='user'),event(3,text='做',role='user'),
              event(4,'reasoning','先查视频工具，再制作提示词'),
              event(5,'tool_call','video://SSH prompt',call='video'),
              event(6,'tool_result','/fixture/ssh.mp4',call='video'),
              event(7,text='视频已生成，尚未验证时长',role='assistant')]
        packet=self.packet(rows)
        bodies=[f['text'] for f in packet['fragments']]
        self.assertTrue(any('五秒SSH' in s for s in bodies))
        self.assertTrue(any('先查视频工具' in s for s in bodies))
        self.assertTrue(any('ssh.mp4' in s for s in bodies))
        self.assertEqual(len(packet['candidateTurns']),3)
        self.assertEqual(packet['coverage']['taskAssociation'],'not_determined')
        self.assertTrue(all(c['association']=='separate_candidate_round' for c in packet['candidateTurns']))
        self.assertFalse(packet['coverage']['complete'])

    def test_distinct_requests_do_not_become_one_task(self):
        packet=self.packet([event(1,text='查溧阳天气',role='user'),
                            event(2,text='已查询天气',role='assistant'),
                            event(3,text='生成SSH协议动画',role='user'),
                            event(4,text='视频已生成',role='assistant')])
        self.assertEqual(packet['titleHint'],'生成SSH协议动画')
        self.assertEqual([c['user'] for c in packet['candidateTurns']],['查溧阳天气','生成SSH协议动画'])
        self.assertEqual(packet['coverage']['taskAssociation'],'not_determined')

    def test_workbuddy_without_end_marker_is_eligible_but_not_complete(self):
        packet=self.packet([event(1,text='只回复收到',role='user'),event(2,text='收到',role='assistant')])
        self.assertTrue(packet['eligible'])
        self.assertEqual(packet['coverage']['closure'],'assistant_message_endpoint')
        self.assertEqual(packet['coverage']['silenceWindowSeconds'],300)
        self.assertFalse(packet['coverage']['complete'])

    def test_control_messages_remain_evidence_not_new_requirements(self):
        rows=[event(1,text='<in-app-browser-context>URL env</in-app-browser-context>\n查上海天气',role='user'),
              event(2,text='>>> APPROVAL REQUEST BEGIN\n允许本次测试\n>>> APPROVAL REQUEST END',role='user'),
              event(3,text='<send_user_message_question_reply>[{"answer":"允许"}]</send_user_message_question_reply>',role='user'),
              event(4,text='<environment_context>cwd=/fixture</environment_context>',role='user')]
        packet=self.packet(rows)
        self.assertEqual(packet['titleHint'],'查上海天气')
        self.assertTrue(packet['eligible']);self.assertFalse(packet['onlyApproval'])
        self.assertEqual(len(packet['candidateTurns']),1)
        self.assertEqual(packet['coverage']['controlEvents'],3)
        self.assertEqual(len(packet['fragments']),4)
        self.assertTrue(any(f['control']=='approval' for f in packet['fragments']))

    def test_only_approval_is_not_automatic_analysis_eligible(self):
        packet=self.packet([event(1,text='>>> APPROVAL REQUEST BEGIN\n确认\n>>> APPROVAL REQUEST END',role='user')])
        self.assertFalse(packet['eligible']);self.assertTrue(packet['onlyApproval'])
        self.assertEqual(packet['titleHint'],'仅授权与环境记录')
        self.assertEqual(packet['candidateTurns'],[])

    def test_background_notification_inside_query_does_not_replace_task_title(self):
        notice='<task-notification> <task-id>fixture-background</task-id><status>completed</status><summary>后台完成</summary></task-notification>'
        rows=[event(1,text='生成SSH协议视频',role='user'),
              event(2,text='<user_query>'+notice+'</user_query>',role='user'),
              event(3,text=notice,role='user')]
        packet=self.packet(rows)
        self.assertEqual(packet['titleHint'],'生成SSH协议视频')
        self.assertEqual(len(packet['candidateTurns']),1)
        notifications=[f for f in packet['fragments'] if f['eventId'] in {rows[1]['id'],rows[2]['id']}]
        self.assertEqual(len(notifications),2)
        self.assertTrue(all(f['control']=='control' and 'fixture-background' in f['text'] for f in notifications))
        self.assertFalse(self.packet([rows[1]])['eligible'])

    def test_user_request_mentioning_notification_xml_remains_a_requirement(self):
        for text in ('请解释 <task-notification><task-id>sample</task-id></task-notification> 的结构',
                     '<task-notification> 这个标签是什么意思？'):
            packet=self.packet([event(1,text=text,role='user')])
            self.assertEqual(packet['titleHint'],text)
            self.assertTrue(packet['eligible'])

    def test_scope_isolation_and_order_independent_duplicate_revision(self):
        actual=event(1,text='实际需求',role='user')
        foreign=[dict(event(2,text='foreign owner',role='user'),owner='bob'),
                 dict(event(3,text='foreign device',role='user'),deviceId='other'),
                 event(4,text='foreign app',role='user',source='codex'),
                 event(5,text='foreign session',role='user',session='other')]
        packet=self.packet([actual,*foreign,actual])
        self.assertEqual(packet['totalEvents'],1)
        self.assertEqual(packet['revision'],self.packet([actual])['revision'])
        self.assertEqual(packet['coverage']['excludedForeignEvents'],4)
        self.assertNotIn('foreign',json.dumps(packet,ensure_ascii=False))
        added=event(6,text='回应',role='assistant')
        self.assertEqual(self.packet([added,actual])['revision'],self.packet([actual,added,actual])['revision'])

    def test_conflicting_duplicate_fails_instead_of_changing_source_evidence(self):
        row=event(1,text='原始',role='user')
        with self.assertRaises(ValueError):self.packet([row,dict(row,payload={'content':'冲突'})])

    def test_ambiguous_call_id_does_not_claim_a_matched_pair(self):
        rows=[event(1,text='查天气',role='user'),event(2,'tool_call','one',call='x'),
              event(3,'tool_call','two',call='x'),event(4,'tool_result','result',call='x')]
        packet=self.packet(rows)
        self.assertEqual(packet['toolPairs'],[])
        self.assertEqual(packet['coverage']['unmatchedToolEvents'],3)

    def test_tool_result_keeps_exit_status_beside_output(self):
        result=event(3,'tool_result','curl output',call='curl')
        result['payload'].update(exit_code=7,stderr='connection refused')
        packet=self.packet([event(1,text='查天气',role='user'),event(2,'tool_call','https://weather.example',call='curl'),result])
        returned=next(f for f in packet['fragments'] if f['kind']=='tool_result')
        self.assertIn('curl output',returned['text'])
        self.assertIn('exit_code: 7',returned['text'])
        self.assertIn('connection refused',returned['text'])

    def test_exact_call_id_pair_survives_clock_disagreement_with_explicit_limit(self):
        packet=self.packet([event(1,text='查询',role='user',stamp=1000),
                            event(2,'tool_call','action',stamp=1100,call='x'),
                            event(3,'tool_result','return',stamp=1050,call='x')])
        self.assertEqual(len(packet['toolPairs']),1)
        self.assertEqual(packet['coverage']['pairOrderConflicts'],1)
        self.assertTrue(any('时间顺序不一致' in text for text in packet['limitations']))

    def test_unreadable_reasoning_does_not_become_an_invented_thought(self):
        reasoning=event(2,'reasoning')
        reasoning['payload']={'encrypted_content':'opaque-reasoning'}
        packet=self.packet([event(1,text='生成方案',role='user'),reasoning])
        self.assertEqual(next(f['text'] for f in packet['fragments'] if f['kind']=='reasoning'),'')
        self.assertTrue(any('没有可读正文' in text for text in packet['limitations']))
        self.assertTrue(any('源时间缺失' in text for text in packet['limitations']))

    def test_long_multiturn_window_retains_early_candidate_and_latest_goal(self):
        rows=[event(i,text=('原始要求' if i==1 else '最新要求' if i==30 else '需求调整'+str(i)),role='user') for i in range(1,31)]
        packet=self.packet(rows)
        self.assertEqual(packet['coverage']['candidateRounds'],30)
        self.assertEqual(packet['coverage']['includedRounds'],13)
        self.assertEqual(packet['candidateTurns'][0]['position'],'earlier_session_candidate')
        self.assertEqual(packet['titleHint'],'最新要求')
        self.assertTrue(any(f['text']=='原始要求' for f in packet['fragments']))
        self.assertEqual(packet['coverage']['taskAssociation'],'not_determined')

    def test_large_unicode_history_respects_budget_retains_atomic_pairs_and_does_not_mutate(self):
        rows=[event(1,text='原始需求：批量核对每个文件',role='user')]
        for i in range(2,182,2):
            rows.extend([event(i,'tool_call','开始'+('中文细节'*2000)+'https://end.example/'+str(i),call=str(i)),
                         event(i+1,'tool_result','返回'+('天气内容'*2000)+'结果尾部'+str(i),call=str(i))])
        rows.append(event(183,text='最新交付已记录，尚未独立验证',role='assistant'))
        original=copy.deepcopy(rows)
        packet=self.packet(rows)
        self.assertLessEqual(len(packet['fragments']),80)
        self.assertLessEqual(len(json.dumps(packet,ensure_ascii=False).encode()),MAX_PACKET_BYTES)
        self.assertEqual(rows,original)
        self.assertGreater(packet['coverage']['truncatedFragments'],0)
        refs={f['evidenceId'] for f in packet['fragments']}
        self.assertTrue(all(pair['callRef'] in refs and pair['resultRef'] in refs for pair in packet['toolPairs']))
        ids={f['eventId'] for f in packet['fragments']}
        self.assertIn(rows[-1]['id'],ids)
        self.assertIn(rows[-2]['id'],ids)
        self.assertIn(rows[-3]['id'],ids)
        self.assertTrue(any('原始需求' in f['text'] for f in packet['fragments']))


class SessionHeadTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'sessions.db'
        self.store=SessionStore(self.path);self.identity={'owner':'alice','id':'device'}
    def tearDown(self):self.tmp.cleanup()
    def ingest(self,rows,identity=None):
        return self.store.ingest(identity or self.identity,{'schemaVersion':1,'events':rows})

    def test_heads_are_incremental_idempotent_and_match_packet_revision(self):
        rows=[event(1,text='query',role='user',stamp='2026-10-07T02:00:00Z'),
              event(2,text='answer',role='assistant',stamp=1791338401000)]
        self.assertEqual(self.ingest(rows)['newEvents'],2)
        head=self.store.metadata('alice','device','same','workbuddy')
        self.assertEqual(head['sourceTime'],1791338401)
        packet=make_packet(self.store.events_for('alice','device','same','workbuddy'),'device','alice','same','workbuddy')
        self.assertEqual(head['revision'],packet['revision'])
        self.assertEqual(self.ingest(rows)['newEvents'],0)
        replay=self.store.metadata('alice','device','same','workbuddy')
        self.assertEqual(replay['revision'],head['revision']);self.assertEqual(replay['eventCount'],2)
        new=event(3,text='late old history',role='user',stamp='2020-01-01T00:00:00Z')
        self.ingest([new]);updated=self.store.metadata('alice','device','same','workbuddy')
        self.assertEqual(updated['sourceTime'],head['sourceTime'])
        self.assertNotEqual(updated['revision'],head['revision'])
        self.assertEqual(updated['revision'],self.store.analysis_input('alice','device','same')['revision'])

    def test_source_heads_are_separate_and_sessions_sort_by_source_not_backfill_receipt(self):
        self.ingest([event(1,text='new',role='user',stamp='2026-10-07T02:00:00Z')])
        self.ingest([event(2,text='old historical',role='user',stamp='2020-01-01T00:00:00Z',session='old')])
        self.ingest([event(3,text='Codex same name',role='user',stamp='2026-10-07T02:00:01Z',source='codex')])
        heads=self.store.sessions('alice')
        self.assertEqual(len(heads),3)
        self.assertEqual([(h['session'],h['source']) for h in heads],[('same','codex'),('same','workbuddy'),('old','workbuddy')])
        self.assertTrue(all(h['events']==h['eventCount']==1 for h in heads))
        self.assertEqual(self.store.analysis_input('alice','device','same')['source'],'codex')
        self.assertEqual(self.store.analysis_input('alice','device','same','workbuddy')['titleHint'],'new')

    def test_scoped_events_and_private_global_heads(self):
        self.ingest([event(1,text='Alice actual',role='user')])
        self.ingest([event(2,text='Bob actual',role='user')],{'owner':'bob','id':'bob-device'})
        self.assertEqual(len(self.store.sessions(None)),2)
        self.assertEqual(len(self.store.sessions('alice')),1)
        events=self.store.events_for('alice','device','same','workbuddy')
        self.assertEqual(events[0]['owner'],'alice');self.assertEqual(events[0]['deviceId'],'device')
        self.assertEqual(self.store.events_for('bob','device','same','workbuddy'),[])
        self.assertIsNone(self.store.metadata('bob','device','same','workbuddy'))
        with self.assertRaises(ValueError):self.store.events_for(None,'device','same','workbuddy')

    def test_conflict_rolls_back_events_and_metadata_together(self):
        row=event(1,text='original',role='user');self.ingest([row])
        head=self.store.metadata('alice','device','same','workbuddy')
        with self.assertRaises(ValueError):self.ingest([event(2,text='must rollback',role='user'),dict(row,payload={'content':'different'})])
        self.assertEqual(self.store.metadata('alice','device','same','workbuddy'),head)
        self.assertEqual(len(self.store.events_for('alice','device','same','workbuddy')),1)
        with self.assertRaises(ValueError):self.ingest([row],{'owner':'bob','id':'device'})

    def test_concurrent_batches_keep_all_events_in_head_revision(self):
        self.ingest([event(1,text='initial request',role='user')])
        original_connect=self.store.connect
        # Widen the read→write window to reproduce a lost update reliably.
        # With a writer transaction, peers wait before reading this head.
        class Cursor:
            def __init__(self,cursor):self.cursor=cursor
            def fetchone(self):
                row=self.cursor.fetchone();time.sleep(.025);return row
        class Connection:
            def __init__(self,db):self.db=db
            def execute(self,query,args=()):
                cursor=self.db.execute(query,args)
                return Cursor(cursor) if query.startswith('SELECT eventCount,eventDigest,sourceTime') else cursor
        @contextmanager
        def delayed_connect():
            with original_connect() as db:yield Connection(db)
        start=threading.Barrier(6)
        def upload(n):
            start.wait(timeout=5)
            return self.ingest([event(n,text='batch '+str(n),role='assistant',stamp=n)])
        with patch.object(self.store,'connect',side_effect=delayed_connect),ThreadPoolExecutor(max_workers=6) as workers:
            results=list(workers.map(upload,range(2,8)))
        self.assertTrue(all(result['newEvents']==1 for result in results))
        head=self.store.metadata('alice','device','same','workbuddy')
        packet=self.store.analysis_input('alice','device','same','workbuddy')
        self.assertEqual(head['eventCount'],7)
        self.assertEqual(packet['totalEvents'],7)
        self.assertEqual(head['revision'],packet['revision'])

    def test_legacy_database_backfills_once_and_preserves_raw_data(self):
        legacy=Path(self.tmp.name)/'legacy.db'
        row=event(1,text='历史需求',role='user',stamp='2026-10-07T02:00:00Z')
        raw=json.dumps(row,ensure_ascii=False)
        with closing(sqlite3.connect(legacy)) as db, db:
            db.execute('CREATE TABLE session_events(device TEXT,owner TEXT,id TEXT,session TEXT,event TEXT,PRIMARY KEY(device,id))')
            db.execute('INSERT INTO session_events VALUES(?,?,?,?,?)',('device','alice',row['id'],'same',raw))
        migrated=SessionStore(legacy)
        head=migrated.sessions('alice')[0]
        self.assertEqual(head['events'],1);self.assertEqual(head['receivedBasis'],'unavailable_historical_receipt')
        self.assertIsNone(head['lastReceived'])
        self.assertEqual(head['revision'],migrated.analysis_input('alice','device','same')['revision'])
        # Subsequent startup and list/metadata reads must not parse/scan events.
        with patch('agentpair.session_lens.json.loads',side_effect=AssertionError('unexpected event scan')):
            reopened=SessionStore(legacy)
            self.assertEqual(reopened.sessions('alice')[0]['revision'],head['revision'])
            self.assertEqual(reopened.metadata('alice','device','same','workbuddy')['eventCount'],1)
        with closing(sqlite3.connect(legacy)) as db:self.assertEqual(db.execute('SELECT event FROM session_events').fetchone()[0],raw)

    def test_legacy_replay_with_different_json_spacing_is_idempotent_without_rewriting(self):
        legacy=Path(self.tmp.name)/'legacy-replay.db'
        row=event(1,text='历史',role='user')
        raw=json.dumps(row,ensure_ascii=True,separators=(',',':'))
        with closing(sqlite3.connect(legacy)) as db, db:
            db.execute('CREATE TABLE session_events(device TEXT,owner TEXT,id TEXT,session TEXT,event TEXT,PRIMARY KEY(device,id))')
            db.execute('INSERT INTO session_events VALUES(?,?,?,?,?)',('device','alice',row['id'],'same',raw))
        store=SessionStore(legacy)
        head=store.metadata('alice','device','same','workbuddy')
        replay=store.ingest(self.identity,{'schemaVersion':1,'events':[row]})
        self.assertEqual(replay['newEvents'],0)
        self.assertEqual(store.metadata('alice','device','same','workbuddy')['revision'],head['revision'])
        with closing(sqlite3.connect(legacy)) as db:self.assertEqual(db.execute('SELECT event FROM session_events').fetchone()[0],raw)

    def test_startup_repairs_heads_after_old_version_append_without_inventing_receipts(self):
        self.ingest([event(1,text='original task',role='user',stamp=1700000001)])
        self.ingest([event(2,text='unchanged task',role='user',stamp=1700000002,session='unchanged')])
        original=self.store.metadata('alice','device','same','workbuddy')
        unchanged=self.store.metadata('alice','device','unchanged','workbuddy')
        appended=[event(3,text='old version reply',role='assistant',stamp=1700000003),
                  event(4,text='old version new task',role='user',stamp=1700000004,session='new')]
        # Simulate a deployed rollback: events continue arriving but heads and
        # their version marker remain from the previous deployment.
        with closing(sqlite3.connect(self.store.path)) as db, db:
            for row in appended:
                db.execute('INSERT INTO session_events VALUES(?,?,?,?,?)',('device','alice',row['id'],row['sessionId'],json.dumps(row)))
        reopened=SessionStore(self.store.path)
        changed=reopened.metadata('alice','device','same','workbuddy')
        self.assertEqual(changed['eventCount'],2)
        self.assertNotEqual(changed['revision'],original['revision'])
        self.assertIsNone(changed['lastReceived'])
        self.assertEqual(changed['receivedBasis'],'unavailable_historical_receipt')
        self.assertEqual(reopened.metadata('alice','device','unchanged','workbuddy'),unchanged)
        self.assertIsNone(reopened.metadata('alice','device','new','workbuddy')['lastReceived'])
        self.assertEqual(sum(h['eventCount'] for h in reopened.sessions()),4)
        self.assertEqual(changed['revision'],reopened.analysis_input('alice','device','same','workbuddy')['revision'])
        with patch('agentpair.session_lens.json.loads',side_effect=AssertionError('unexpected repeated scan')):
            again=SessionStore(self.store.path)
            self.assertEqual(again.metadata('alice','device','same','workbuddy'),changed)

    def test_startup_count_check_accounts_for_skipped_legacy_sources(self):
        row=event(1,text='valid task',role='user')
        unsupported=event(2,text='unsupported legacy source',source='other')
        with closing(sqlite3.connect(self.store.path)) as db, db:
            for item in (row,unsupported):
                db.execute('INSERT INTO session_events VALUES(?,?,?,?,?)',('device','alice',item['id'],item['sessionId'],json.dumps(item)))
        repaired=SessionStore(self.store.path)
        self.assertEqual(sum(h['eventCount'] for h in repaired.sessions()),1)
        with repaired.connect() as db:
            self.assertEqual(db.execute("SELECT value FROM session_metadata_state WHERE name='heads_backfill_skipped'").fetchone()[0],'1')
            self.assertEqual(db.execute('SELECT count(*) FROM session_events').fetchone()[0],2)
        with patch('agentpair.session_lens.json.loads',side_effect=AssertionError('skipped legacy row must not force another scan')):
            reopened=SessionStore(self.store.path)
            self.assertEqual(reopened.sessions()[0]['eventCount'],1)


if __name__=='__main__':unittest.main()
