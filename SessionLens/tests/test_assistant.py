import json,sqlite3,tempfile,unittest
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from sessionlens.core import Collector
from sessionlens.supervision import TaskStore,event_text
from sessionlens.assistant import packet_for_task
from agentpair.session_assistant import prepare,validate_understanding

class AssistantTests(unittest.TestCase):
 def test_fields_and_citations_keep_tool_parameters_and_recorded_reasoning(self):
  with tempfile.TemporaryDirectory() as tmp:
   path=Path(tmp)/'db';log=Path(tmp)/'wb.jsonl';records=[
    {'type':'message','sessionId':'s','role':'user','content':'上海天气'},
    {'type':'reasoning','sessionId':'s','content':[],'rawContent':[{'text':'直接请求失败后改用本地接口'}]},
    {'type':'function_call','sessionId':'s','name':'Bash','callId':'c','message':{'usage':{'tokens':123}},'arguments':'{"command":"curl http://localhost:3000/api/weather?days=7"}'},
    {'type':'function_call_result','sessionId':'s','callId':'c','output':{'text':'temperature_2m=20.1'}}]
   log.write_text(''.join(json.dumps(r)+'\n' for r in records));c=Collector(path);c.scan(log,source='workbuddy');c.db.close();store=TaskStore(path);store.advance();packet=packet_for_task(store.db,store.tasks()[0][0]);store.close()
   self.assertIn('改用本地接口',packet['fragments'][1]['text']);self.assertIn('days=7',packet['fragments'][2]['text']);self.assertNotIn('tokens',packet['fragments'][2]['text']);self.assertEqual(packet['fragments'][2]['callId'],packet['fragments'][3]['callId'])
   q,clean=prepare({'question':'为什么这么做','packet':packet});answer={'overview':{'text':'改用本地接口','basis':'recorded','evidenceRefs':['E002']},'steps':[{'title':'调用','text':'查询七天','basis':'recorded','evidenceRefs':['E003']}],'gaps':[]};validate_understanding(answer,clean)
   answer['steps'][0]['evidenceRefs']=['E999']
   with self.assertRaises(ValueError):validate_understanding(answer,clean)
 def test_duplicate_refs_and_missing_basis_rejected(self):
  packet={'prompt':'test','fragments':[{'evidenceId':'E001','text':'result'},{'evidenceId':'E001','text':'other'}]}
  with self.assertRaises(ValueError):prepare({'question':'test','packet':packet})
 def test_native_reasoning_uses_summary_when_content_empty(self):
  e={'kind':'reasoning','payload':{'content':[],'summary':[{'text':'需要先检查返回结构'}]}}
  self.assertEqual(event_text(e),'需要先检查返回结构')
