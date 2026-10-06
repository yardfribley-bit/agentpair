import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from sessionlens.core import Collector
from sessionlens.supervision import TaskStore
from sessionlens.assistant import packet_for_task,combine_packets
from sessionlens.knowledge import ask,answer_mismatch

class QuestionEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.log=self.root/'log.jsonl'
    def tearDown(self):self.tmp.cleanup()
    def load(self,rows):
        self.log.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows));c=Collector(self.root/'collector.db');c.scan(self.log,max_records=2000,source='workbuddy');c.db.close();s=TaskStore(self.root/'collector.db');s.advance(2000);s.repair_links(10);return s
    def test_middle_specific_call_and_return_are_selected_with_requirement(self):
        rows=[{'type':'message','role':'user','sessionId':'s','content':'检查接口兼容性'}]
        for n in range(180):
            rows.append({'type':'function_call','sessionId':'s','name':'Bash','callId':str(n),'arguments':{'command':'curl https://'+('middle.example/compat' if n==90 else 'generic.example/'+str(n))}})
            rows.append({'type':'function_call_result','sessionId':'s','callId':str(n),'output':'返回正常：'+str(n)})
        store=self.load(rows);task=store.tasks()[0][0];raw=store.db.execute('SELECT count(*) FROM events').fetchone()[0]
        packet=packet_for_task(store.db,task,'middle.example/compat 请求的返回是什么？',['tools','results'])
        matches=[f for f in packet['fragments'] if f.get('callId')=='90'];self.assertEqual(len(matches),2)
        self.assertIn('middle.example/compat',matches[0]['text']);self.assertIn('90',matches[1]['text'])
        self.assertTrue(packet['requirementHistory']);self.assertLessEqual(packet['includedRecords'],120)
        self.assertEqual(store.db.execute('SELECT count(*) FROM events').fetchone()[0],raw);store.close()
    def test_comparison_preserves_ownership_ref_uniqueness_budget_and_cache_lineage(self):
        rows=[]
        for n in range(3):
            rows.extend([{'type':'message','role':'user','sessionId':str(n),'content':'检查日报接口 '+str(n)},
                         {'type':'function_call','sessionId':str(n),'name':'Bash','callId':'same-call-id','arguments':{'command':'curl https://service'+str(n)+'.example/report'}},
                         {'type':'function_call_result','sessionId':str(n),'callId':'same-call-id','output':'结果'+str(n)+'x'*5000}])
        store=self.load(rows);ids=[r[0] for r in store.tasks()];packet=combine_packets([packet_for_task(store.db,t,'比较接口返回',['tools','results']) for t in ids])
        refs=[f['evidenceId'] for f in packet['fragments']];self.assertEqual(len(refs),len(set(refs)))
        self.assertEqual({f['taskId'] for f in packet['fragments']},set(ids));self.assertLessEqual(sum(len(f['text']) for f in packet['fragments']),90000)
        result={'taskId':ids[0],'packet':packet};self.assertIsNone(answer_mismatch(store.db,result))
        for edge in packet['messageRelations']:
            owners={f['evidenceId']:f['taskId'] for f in packet['fragments']};self.assertEqual(owners[edge['fromRef']],owners[edge['toRef']])
        with store.db:store.db.execute('UPDATE task_links SET reason=? WHERE root=?',('关联依据更新',ids[1]))
        self.assertIsNotNone(answer_mismatch(store.db,result));store.close()
    def test_multiple_question_uses_independent_tasks_and_vague_followup_clarifies(self):
        store=self.load([{'type':'message','role':'user','sessionId':str(n),'content':'排查日报接口 '+str(n)} for n in range(3)]);store.close()
        config={'enabled':True,'credentialFile':'test'};plan={'terms':['日报'],'subjects':['日报'],'scope':'multiple','facets':['results'],'followup':False}
        with patch('sessionlens.relay_model.call',return_value=plan),patch('sessionlens.relay_model.answer',return_value={}) as answer:
            result=ask(self.root,config,'这几次日报接口排查有什么差异？',[],lambda _:None)
            self.assertEqual(len(result['retrievedTaskIds']),3);self.assertEqual(result['packet']['scope'],'multiple');self.assertNotIn('presentation',result)
            self.assertEqual(answer.call_args.args[3]['scope'],'multiple')
        follow={'terms':[],'subjects':[],'scope':'single','followup':True}
        with patch('sessionlens.relay_model.call',return_value=follow),patch('sessionlens.relay_model.answer') as answer:
            choice=ask(self.root,config,'它当时怎么做的？',[result],lambda _:None)
            self.assertTrue(choice['selectionNeeded']);self.assertEqual(len(choice['options']),3);answer.assert_not_called()
    def test_large_tool_body_is_labelled_bounded_and_original_remains_available(self):
        from sessionlens.task_presentation import project
        store=self.load([{'type':'message','role':'user','sessionId':'s','content':'分析大型返回'},
                         {'type':'function_call','sessionId':'s','name':'Bash','callId':'large','arguments':{'command':'curl https://large.invalid/data','fixture':'x'*400000}},
                         {'type':'function_call_result','sessionId':'s','callId':'large','output':'演示返回：'+'z'*400000}])
        task=store.tasks()[0][0];packet=packet_for_task(store.db,task,'工具返回什么',['tools','results']);view=project(store.db,task)
        self.assertTrue(view['calls'][0]['truncated']);self.assertTrue(view['calls'][0]['returns'][0]['truncated'])
        self.assertIn('参数摘录',view['calls'][0]['fields'][0]['label']);self.assertTrue(any(f['truncated'] for f in packet['fragments']))
        self.assertGreater(store.db.execute("SELECT length(event) FROM events WHERE id=?",(view['calls'][0]['id'],)).fetchone()[0],400000)
        store.close()

if __name__=='__main__':unittest.main()
