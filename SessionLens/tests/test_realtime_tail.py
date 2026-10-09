import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from sessionlens.core import Collector


class RealtimeTailTests(unittest.TestCase):
    def rows(self,count=200):
        return [{'type':'session_meta','timestamp':'2026-10-01T00:00:00Z','payload':{'id':'actual-session'}}]+[
            {'type':'response_item','timestamp':f'2026-10-08T00:00:{i%60:02d}Z',
             'payload':{'type':'function_call','call_id':str(i),'name':'sample','arguments':json.dumps({'padding':'x'*100})}}
            for i in range(count)]

    def write(self,path,rows):
        path.write_bytes(b''.join(json.dumps(r).encode()+b'\n' for r in rows))

    def test_newest_records_arrive_without_skipping_history_or_duplicate_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'rollout.jsonl';self.write(path,self.rows())
            c=Collector(Path(tmp)/'db');c.scan(path,2)
            initial=c.db.execute('SELECT offset FROM cursors').fetchone()[0]
            self.assertGreater(c.scan_recent(path,100,tail_bytes=1200),0)
            self.assertEqual(c.db.execute('SELECT offset FROM cursors').fetchone()[0],initial)
            latest=json.loads(c.db.execute('SELECT event FROM events ORDER BY rowid DESC LIMIT 1').fetchone()[0])
            self.assertEqual(latest['callId'],'199');self.assertEqual(latest['sessionId'],'actual-session')
            while c.scan(path,20):pass
            self.assertEqual(c.counts()['events'],201)
            self.assertEqual(c.scan_recent(path),0)
            events=[json.loads(r[0]) for r in c.db.execute('SELECT event FROM events')]
            self.assertEqual(len({e['evidence']['byteStart'] for e in events}),201)
            c.db.close()

    def test_legacy_tail_registers_missing_upload_metadata_without_rewriting_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'rollout.jsonl';self.write(path,self.rows(50));db=Path(tmp)/'db'
            c=Collector(db);c.scan(path)
            identity,body=c.db.execute('SELECT id,event FROM events ORDER BY rowid DESC LIMIT 1').fetchone()
            canonical=json.loads(body);canonical['legacyField']='original representation';canonical['timestamp']='2030-01-01T00:00:00Z'
            with c.db:
                c.db.execute('UPDATE events SET event=? WHERE id=?',(json.dumps(canonical,ensure_ascii=False),identity))
                c.db.execute('UPDATE cursors SET offset=?',(len(json.dumps(self.rows(0)[0]).encode()+b'\n'),))
                c.db.execute('DELETE FROM upload_index');c.db.execute('DELETE FROM upload_index_state');c.db.execute('DELETE FROM live_cursors')
            original=c.db.execute('SELECT rowid,id,session,event FROM events ORDER BY rowid').fetchall()
            history=c.db.execute('SELECT * FROM cursors').fetchall();c.db.close();c=Collector(db)
            self.assertEqual(c.scan_recent(path,tail_bytes=600),0)
            self.assertEqual(c.db.execute('SELECT * FROM cursors').fetchall(),history)
            self.assertEqual(c.db.execute('SELECT rowid,id,session,event FROM events ORDER BY rowid').fetchall(),original)
            candidate=c.uploads.next_batch('receiver',sources=['codex'],limit=1)
            self.assertEqual(candidate,[canonical])
            self.assertEqual(c.db.execute('SELECT event_rowid FROM upload_index WHERE id=?',(identity,)).fetchone()[0],original[-1][0])
            indexed=c.db.execute('SELECT count(*) FROM upload_index').fetchone()[0]
            self.assertGreater(indexed,0);self.assertLess(indexed,len(original))
            self.assertIsNone(c.db.execute("SELECT value FROM upload_index_state WHERE name='rowid'").fetchone())
            with c.db:c.db.execute('DELETE FROM live_cursors')
            self.assertEqual(c.scan_recent(path,tail_bytes=600),0)
            self.assertEqual(c.db.execute('SELECT count(*) FROM upload_index').fetchone()[0],indexed)
            self.assertEqual(c.db.execute('SELECT * FROM cursors').fetchall(),history)
            self.assertEqual(c.db.execute('SELECT rowid,id,session,event FROM events ORDER BY rowid').fetchall(),original)
            c.db.close()

    def test_cold_tail_at_historical_eof_seeds_missing_upload_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'rollout.jsonl';self.write(path,self.rows(20));db=Path(tmp)/'db'
            c=Collector(db);c.scan(path)
            history=c.db.execute('SELECT * FROM cursors').fetchall()
            original=c.db.execute('SELECT rowid,id,session,event FROM events ORDER BY rowid').fetchall()
            self.assertEqual(c.db.execute('SELECT offset FROM cursors').fetchone()[0],path.stat().st_size)
            with c.db:
                c.db.execute('DELETE FROM upload_index');c.db.execute('DELETE FROM upload_index_state');c.db.execute('DELETE FROM live_cursors')
            c.db.close();c=Collector(db)
            self.assertEqual(c.scan_recent(path,tail_bytes=600),0)
            candidate=c.uploads.next_batch('receiver',sources=['codex'],limit=1)
            self.assertEqual(candidate[0]['callId'],'19')
            self.assertEqual(c.db.execute('SELECT * FROM cursors').fetchall(),history)
            self.assertEqual(c.db.execute('SELECT rowid,id,session,event FROM events ORDER BY rowid').fetchall(),original)
            indexed=c.db.execute('SELECT count(*) FROM upload_index').fetchone()[0]
            self.assertGreater(indexed,0);self.assertLess(indexed,len(original))
            self.assertIsNone(c.db.execute("SELECT value FROM upload_index_state WHERE name='rowid'").fetchone())
            self.assertEqual(c.scan_recent(path),0)
            self.assertEqual(c.db.execute('SELECT count(*) FROM upload_index').fetchone()[0],indexed)
            self.assertEqual(c.db.execute('SELECT offset FROM live_cursors').fetchone()[0],path.stat().st_size)
            c.db.close()

    def test_partial_boundary_line_is_not_consumed_until_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'rollout.jsonl';rows=self.rows(1);self.write(path,rows)
            partial=json.dumps(self.rows(2)[-1]).encode();path.write_bytes(path.read_bytes()+partial[:-5])
            c=Collector(Path(tmp)/'db')
            c.scan_recent(path,100,tail_bytes=20)
            offset=c.db.execute('SELECT offset FROM live_cursors').fetchone()[0]
            self.assertLess(offset,path.stat().st_size)
            with path.open('ab') as f:f.write(partial[-5:]+b'\n')
            self.assertEqual(c.scan_recent(path),1)
            self.assertEqual(json.loads(c.db.execute('SELECT event FROM events ORDER BY rowid DESC LIMIT 1').fetchone()[0])['callId'],'1')
            c.db.close()

    def test_restart_and_truncation_keep_live_history_generation_consistent(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'rollout.jsonl';self.write(path,self.rows(30));db=Path(tmp)/'db'
            c=Collector(db);c.scan_recent(path,100,tail_bytes=600);c.db.close()
            c=Collector(db);self.assertEqual(c.scan_recent(path),0)
            self.write(path,self.rows(1));c.scan_recent(path,100);c.scan(path,100)
            epochs=list(c.db.execute('SELECT epoch FROM cursors UNION ALL SELECT epoch FROM live_cursors'))
            self.assertEqual(epochs,[(1,),(1,)])
            generation=[json.loads(r[0]) for r in c.db.execute('SELECT event FROM events')]
            self.assertEqual(sum(e['evidence']['epoch']==1 for e in generation),2)
            c.db.close()

    def test_workbuddy_header_and_appended_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'other.jsonl'
            self.write(path,[{'type':'message','role':'user','sessionId':'workbuddy-session','content':'first','padding':'x'*2000},
                             {'type':'function_call','sessionId':'workbuddy-session','callId':'tool1','name':'VideoGen'}])
            c=Collector(Path(tmp)/'db');c.scan_recent(path,source='workbuddy',tail_bytes=100)
            tool=json.loads(c.db.execute('SELECT event FROM events ORDER BY rowid DESC LIMIT 1').fetchone()[0])
            self.assertEqual((tool['sessionId'],tool['callId']),('workbuddy-session','tool1'))
            with path.open('ab') as f:f.write(json.dumps({'type':'function_call_result','sessionId':'workbuddy-session','callId':'tool1','result':'done'}).encode()+b'\n')
            self.assertEqual(c.scan_recent(path,source='workbuddy'),1)
            while c.scan(path,source='workbuddy'):pass
            self.assertEqual(c.counts()['events'],3);c.db.close()

    def test_truncated_new_session_tail_never_inherits_old_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'rollout.jsonl';old=self.rows(50);old[0]['payload']['id']='old-session';self.write(path,old)
            c=Collector(Path(tmp)/'db');c.scan(path);c.scan_recent(path)
            new=self.rows(20);new[0]['payload']['id']='new-session';self.write(path,new)
            self.assertGreater(c.scan_recent(path,tail_bytes=600),0)
            tail=[json.loads(row[0]) for row in c.db.execute('SELECT event FROM events') if json.loads(row[0])['evidence']['epoch']==1]
            self.assertTrue(all(event['evidence']['byteStart']>0 for event in tail))
            self.assertEqual({event['sessionId'] for event in tail},{'new-session'})
            c.scan(path)
            generation=[json.loads(row[0]) for row in c.db.execute('SELECT event FROM events') if json.loads(row[0])['evidence']['epoch']==1]
            self.assertEqual(len(generation),21)
            self.assertEqual({event['sessionId'] for event in generation},{'new-session'})
            self.assertEqual(c.db.execute('SELECT epoch,session FROM cursors').fetchone(),(1,'new-session'))
            c.db.close()

    def test_byte_budget_restart_partial_line_and_reader_deduplication(self):
        for name,table in (('scan','cursors'),('scan_recent','live_cursors')):
            with self.subTest(reader=name),tempfile.TemporaryDirectory() as tmp:
                path=Path(tmp)/'rollout.jsonl';rows=self.rows(20);self.write(path,rows);db=Path(tmp)/'db'
                c=Collector(db);self.assertEqual(getattr(c,name)(path,max_bytes=1),1)
                self.assertEqual(c.db.execute('SELECT offset FROM '+table).fetchone()[0],len(json.dumps(rows[0]).encode()+b'\n'))
                c.db.close();c=Collector(db)
                while c.db.execute('SELECT offset FROM '+table).fetchone()[0]<path.stat().st_size:
                    getattr(c,name)(path,max_bytes=1)
                other=c.scan_recent if name=='scan' else c.scan
                self.assertEqual(other(path),0);self.assertEqual(c.counts()['events'],21)
                events=[json.loads(row[0]) for row in c.db.execute('SELECT event FROM events')]
                self.assertEqual(len({event['evidence']['byteStart'] for event in events}),21)
                previous=path.stat().st_size;partial=json.dumps(self.rows(21)[-1]).encode()
                with path.open('ab') as f:f.write(partial[:-5])
                self.assertEqual(getattr(c,name)(path,max_bytes=1),0)
                self.assertEqual(c.db.execute('SELECT offset FROM '+table).fetchone()[0],previous)
                with path.open('ab') as f:f.write(partial[-5:]+b'\n')
                self.assertEqual(getattr(c,name)(path,max_bytes=1),1)
                self.assertEqual(other(path),0);self.assertEqual(c.counts()['events'],22)
                self.assertEqual(c.db.execute('SELECT offset FROM '+table).fetchone()[0],path.stat().st_size)
                c.db.close()

    def test_time_budget_commits_first_complete_record_and_resumes(self):
        for name,table in (('scan','cursors'),('scan_recent','live_cursors')):
            with self.subTest(reader=name),tempfile.TemporaryDirectory() as tmp:
                path=Path(tmp)/'rollout.jsonl';rows=self.rows(5);self.write(path,rows)
                c=Collector(Path(tmp)/'db');clock=[100.];store=c._store_line
                def slow(*args):
                    result=store(*args);clock[0]+=.2;return result
                with patch('sessionlens.core.time.monotonic',lambda:clock[0]),patch.object(c,'_store_line',slow):
                    self.assertEqual(getattr(c,name)(path,max_seconds=.1),1)
                    self.assertEqual(c.db.execute('SELECT offset FROM '+table).fetchone()[0],len(json.dumps(rows[0]).encode()+b'\n'))
                    self.assertEqual(getattr(c,name)(path,max_seconds=0),1)
                getattr(c,name)(path)
                self.assertEqual(c.counts()['events'],6)
                self.assertEqual(c.db.execute('SELECT offset FROM '+table).fetchone()[0],path.stat().st_size)
                c.db.close()


if __name__=='__main__':unittest.main()
