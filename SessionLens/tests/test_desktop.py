import json
from pathlib import Path
import tempfile
import unittest
from sessionlens.core import Collector
from sessionlens.desktop import category,LABELS,summary,Runtime,defaults

class DesktopTests(unittest.TestCase):
    def test_workbuddy_tool_roundtrip_and_source_preservation(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'task.jsonl'
            p.write_text('\n'.join(json.dumps(r) for r in [
                {'type':'message','sessionId':'wb-session','role':'user','content':'查天气'},
                {'type':'function_call','sessionId':'wb-session','callId':'c1','name':'web','arguments':{'query':'溧阳天气'}},
                {'type':'function_call_result','sessionId':'wb-session','callId':'c1','output':'晴','providerData':{'trace':'test'}}])+'\n', encoding='utf-8')
            c=Collector(Path(tmp)/'db');self.assertEqual(c.scan(p,source='workbuddy'),3)
            items=c.pending('d');self.assertEqual([category(e) for e in items],LABELS[:1]+LABELS[3:5]);self.assertEqual(items[-1]['payload']['providerData']['trace'],'test');self.assertEqual(items[-1]['source'],'workbuddy');self.assertEqual(c.pending('d',sources=['codex']),[]);self.assertEqual(items[-1]['callId'],'c1');self.assertIn('web',summary(items[1]));c.db.close()
    def test_oversized_event_does_not_block_small_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'rollout.jsonl';p.write_text(json.dumps({'type':'session_meta','payload':{'id':'test','text':'x'*(18*1024*1024)}})+'\n'+json.dumps({'type':'response_item','payload':{'type':'reasoning','text':'small'}})+'\n', encoding='utf-8')
            c=Collector(Path(tmp)/'db');c.scan(p);items=c.pending('d');self.assertEqual(len(items),1);self.assertEqual(items[0]['kind'],'reasoning');self.assertEqual(c.counts()['events'],2);c.db.close()

    def test_index_upgrade_hole_restart_and_idle_range(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);p=root/'task.jsonl'
            p.write_text(''.join(json.dumps({'type':'message','sessionId':'task','role':'user','content':str(n)})+'\n' for n in range(3)),encoding='utf-8')
            runtime=Runtime(root,defaults());c=Collector(root/'collector.db');c.scan(p,source='workbuddy')
            events=c.db.execute('SELECT id,event FROM events ORDER BY rowid').fetchall()
            with c.db:
                for identity,body in (events[0],events[2]):
                    e=json.loads(body);c.db.execute('INSERT INTO display_index VALUES(?,?,?,?,?)',(identity,e['source'],category(e),summary(e),len(body.encode())))
            runtime.index(c);self.assertEqual(c.db.execute('SELECT count(*) FROM display_index').fetchone()[0],3)
            with p.open('a',encoding='utf-8') as f:f.write(json.dumps({'type':'reasoning','sessionId':'task','content':'new'})+'\n')
            c.scan(p,source='workbuddy');Runtime(root,defaults()).index(c)
            self.assertEqual(c.db.execute('SELECT count(*) FROM display_index').fetchone()[0],4)
            statements=[];c.db.set_trace_callback(statements.append);runtime.index(c)
            self.assertFalse(any('NOT EXISTS' in sql for sql in statements));c.db.close()

    def test_recent_upload_and_history_both_progress(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'task.jsonl';p.write_text(''.join(json.dumps({'type':'message','sessionId':'s','role':'user','content':str(n)})+'\n' for n in range(10)))
            c=Collector(Path(tmp)/'db');c.scan(p,source='workbuddy')
            newest=c.pending('d',limit=2,recent=True);oldest=c.pending('d',limit=2)
            self.assertEqual([e['payload']['content'] for e in newest],['9','8'])
            self.assertEqual([e['payload']['content'] for e in oldest],['0','1'])
            c.acknowledge('d',[e['id'] for e in newest+oldest]);self.assertEqual(len(c.pending('d')),6);c.db.close()
