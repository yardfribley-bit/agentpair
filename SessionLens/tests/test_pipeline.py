import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'AgentPair'))
from sessionlens.core import Collector,normalize
from agentpair.session_lens import SessionStore

class PipelineTests(unittest.TestCase):
    def test_large_unicode_event_kept_and_received(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'rollout-large.jsonl'
            source.write_text(json.dumps({'type':'session_meta','payload':{'id':'large','content':'测'*450000}},ensure_ascii=False)+'\n')
            c=Collector(root/'local.db');c.scan(source);items=c.pending('receiver')
            self.assertEqual(len(items),1)
            store=SessionStore(root/'remote.db')
            receipt=store.ingest({'id':'d','owner':'o'},{'schemaVersion':1,'events':items})
            self.assertEqual(receipt['ids'],[items[0]['id']]);c.db.close()
    def test_native_completed_item_and_tool_call_ids(self):
        event=normalize({'type':'event_msg','payload':{'type':'item_completed','turn_id':'turn1','item':{'type':'CommandExecution','exit_code':1,'stdout':'','stderr':'failed'}}})
        self.assertEqual(event['kind'],'command_execution')
        self.assertEqual(event['payload']['item']['exit_code'],1)
        call=normalize({'type':'response_item','payload':{'type':'custom_tool_call','call_id':'native-id','input':'code'}})
        self.assertEqual((call['kind'],call['callId']),('tool_call','native-id'))
    def test_partial_restart_and_delivery_accounts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'rollout-test.jsonl';db=root/'local.db'
            first=json.dumps({'type':'session_meta','payload':{'id':'real-test'}}).encode()+b'\n'
            second=json.dumps({'type':'token_usage_record','payload':{'tokens':10}}).encode()+b'\n'
            source.write_bytes(first+second[:-2]);c=Collector(db)
            self.assertEqual(c.scan(source),1);self.assertEqual(c.scan(source),0)
            with source.open('ab') as f:f.write(second[-2:])
            self.assertEqual(c.scan(source),1);c.db.close();c=Collector(db)
            self.assertEqual(c.scan(source),0)
            items=c.pending('account-a');c.acknowledge('account-a',[e['id'] for e in items])
            self.assertEqual(c.pending('account-a'),[]);self.assertEqual(len(c.pending('account-b')),2)
            self.assertEqual(items[-1]['kind'],'usage')
            c.db.close()
    def test_receiver_replay_matching_and_isolation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'rollout-test.jsonl'
            rows=[{'type':'session_meta','payload':{'id':'task'}},
                  {'type':'response_item','payload':{'type':'function_call','call_id':'a','name':'exec_command','arguments':'{}'}},
                  {'type':'response_item','payload':{'type':'function_call_output','call_id':'a','output':'done'}},
                  {'type':'new_record','payload':{'unknown':True}}]
            source.write_text(''.join(json.dumps(r)+'\n' for r in rows));c=Collector(root/'local.db');c.scan(source)
            payload={'schemaVersion':1,'events':c.pending('platform')};store=SessionStore(root/'remote.db');who={'id':'device','owner':'alice'}
            store.ingest(who,payload);store.ingest(who,payload)
            report=store.report('alice','device','task')
            self.assertEqual(len(report['events']),4);self.assertTrue(report['tools'][0]['matched'])
            self.assertEqual(report['quality']['unknownRecords'],1);self.assertEqual(store.sessions('bob'),[])
            payload['events'][0]['kind']='forged'
            with self.assertRaises(ValueError):store.ingest(who,payload)
            c.db.close()
    def test_bad_line_does_not_hide_next_record_and_truncation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'rollout-test.jsonl';source.write_bytes(b'bad\n'+json.dumps({'type':'session_meta','payload':{'id':'task'}}).encode()+b'\n')
            c=Collector(root/'local.db');self.assertEqual(c.scan(source),2)
            source.write_bytes(b'bad\n');self.assertEqual(c.scan(source),1)
            self.assertEqual(c.counts()['events'],3);c.db.close()

if __name__=='__main__':unittest.main()
