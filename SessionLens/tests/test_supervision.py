import json
from pathlib import Path
import tempfile
import unittest
from sessionlens.core import Collector
from sessionlens.supervision import TaskStore

class SupervisionTests(unittest.TestCase):
    def test_history_search_evidence_and_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'collector.db';log=Path(tmp)/'task.jsonl'
            records=[{'type':'message','role':'user','sessionId':'s','content':'查溧阳天气'},
                     {'type':'function_call','sessionId':'s','name':'web','callId':'c','arguments':{'query':'溧阳天气'}},
                     {'type':'function_call_result','sessionId':'s','callId':'c','output':'搜索来源：天气站'},
                     {'type':'message','role':'assistant','sessionId':'s','content':'按搜索结果整理天气'},
                     {'type':'message','role':'user','sessionId':'s','content':'改写方案'}]
            log.write_text(''.join(json.dumps(r)+'\n' for r in records));c=Collector(path);c.scan(log,source='workbuddy');c.db.close()
            store=TaskStore(path);self.assertEqual(store.advance(),5);tasks=store.tasks('天气站');self.assertEqual(len(tasks),1);steps=store.steps(tasks[0][0]);self.assertEqual(len(steps),4);self.assertEqual(steps[1][3],steps[2][3]);self.assertEqual(store.evidence(steps[2][0])['payload']['output'],'搜索来源：天气站')
            store.mark(steps[2][0]);store.close();store=TaskStore(path);self.assertTrue(store.marked(steps[2][0]));self.assertEqual(store.advance(),0);self.assertEqual(len(store.tasks()),2);self.assertEqual(len(store.tasks(live=True)),1);store.close()
    def test_codex_mirrored_prompt_does_not_split_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            log=Path(tmp)/'rollout.jsonl';path=Path(tmp)/'db'
            log.write_text(''.join(json.dumps(r)+'\n' for r in [
                {'type':'session_meta','payload':{'id':'s'}},
                {'type':'event_msg','payload':{'type':'user_message','message':'检查文件'}},
                {'type':'response_item','payload':{'type':'message','role':'user','content':[{'type':'input_text','text':'检查文件'}]}},
                {'type':'response_item','payload':{'type':'function_call','name':'read','call_id':'c','arguments':'a.txt'}},
                {'type':'event_msg','payload':{'type':'task_complete'}}]))
            c=Collector(path);c.scan(log);c.db.close();store=TaskStore(path);store.advance();self.assertEqual(len(store.tasks()),1);self.assertEqual(store.tasks()[0][5],'已记录结束');self.assertEqual(len(store.steps(store.tasks()[0][0])),3);store.close()
