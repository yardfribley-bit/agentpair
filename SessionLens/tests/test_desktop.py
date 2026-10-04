import json
from pathlib import Path
import tempfile
import unittest
from sessionlens.core import Collector
from sessionlens.desktop import category,LABELS,summary

class DesktopTests(unittest.TestCase):
    def test_workbuddy_tool_roundtrip_and_source_preservation(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'task.jsonl'
            p.write_text('\n'.join(json.dumps(r) for r in [
                {'type':'message','sessionId':'wb-session','role':'user','content':'查天气'},
                {'type':'function_call','sessionId':'wb-session','callId':'c1','name':'web','arguments':{'query':'溧阳天气'}},
                {'type':'function_call_result','sessionId':'wb-session','callId':'c1','output':'晴','providerData':{'trace':'test'}}])+'\n')
            c=Collector(Path(tmp)/'db');self.assertEqual(c.scan(p,source='workbuddy'),3)
            items=c.pending('d');self.assertEqual([category(e) for e in items],LABELS[:1]+LABELS[3:5]);self.assertEqual(items[-1]['payload']['providerData']['trace'],'test');self.assertEqual(items[-1]['source'],'workbuddy');self.assertEqual(items[-1]['callId'],'c1');self.assertIn('web',summary(items[1]));c.db.close()
    def test_oversized_event_does_not_block_small_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'rollout.jsonl';p.write_text(json.dumps({'type':'session_meta','payload':{'id':'test','text':'x'*2200000}})+'\n'+json.dumps({'type':'response_item','payload':{'type':'reasoning','text':'small'}})+'\n')
            c=Collector(Path(tmp)/'db');c.scan(p);items=c.pending('d');self.assertEqual(len(items),1);self.assertEqual(items[0]['kind'],'reasoning');self.assertEqual(c.counts()['events'],2);c.db.close()
