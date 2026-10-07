"""Regression: compact association input must not hide an execution test."""
import unittest
from . import test_collection_assistant as fixture
from agentpair.collection_semantics import expand_turn_evidence, turn_packet, group_tasks

class EvidenceRestorationQA(unittest.TestCase):
    def setUp(self):
        self.f=fixture.CollectionAssistantTests('test_history_summary_does_not_become_user_goal')
        self.f.setUp();self.addCleanup(self.f.tearDown)

    def test_middle_verification_call_and_return_survive_compact_lineage(self):
        f=self.f
        goal=f.record('新建上海天气网页项目')
        for i in range(9):
            f.record(kind='tool_call',role=None,name='Write',callId='write-'+str(i),payload={'arguments':{'path':'source-'+str(i)+'.js','content':'synthetic'}})
            f.record(kind='tool_result',role=None,callId='write-'+str(i),payload={'output':'written'})
        call=f.record(kind='tool_call',role=None,name='Bash',callId='verify',payload={'arguments':{'command':'curl http://localhost:3000/api/weather'}})
        result=f.record(kind='tool_result',role=None,callId='verify',payload={'output':'HTTP 200; Shanghai 19.6 C'})
        f.record(kind='tool_call',role=None,name='Write',callId='memory',payload={'arguments':{'path':'memory.md','content':'done'}})
        f.record(kind='tool_result',role=None,callId='memory',payload={'output':'written'})
        f.record('已完成，并测试了接口',role='assistant');f.ingest()
        answer=f.assistant.answer('alice',f.device,'上海天气项目是怎么完成的？')
        evidence={r['recordId']:r for r in answer['evidence']}
        self.assertIn('sessionlens:'+call['id'],evidence)
        self.assertIn('sessionlens:'+result['id'],evidence)
        self.assertIn('HTTP 200',evidence['sessionlens:'+result['id']]['text'])
        action=next(a for a in answer['candidates'][0]['executions'] if a['recordId']=='sessionlens:'+call['id'])
        self.assertIn('sessionlens:'+result['id'],action['resultRecordIds'])
        self.assertEqual(answer['candidates'][0]['taskId'],goal['id'])
        self.assertLessEqual(len(evidence),60)

    def test_semantic_followup_uses_previous_task_identity_not_global_curl_hits(self):
        f=self.f
        goal=f.record('新建上海天气网页项目')
        f.record(kind='tool_call',role=None,name='Bash',callId='verify',payload={'arguments':{'command':'curl http://localhost:3000/api/weather'}})
        f.record(kind='tool_result',role=None,callId='verify',payload={'output':'HTTP 200'})
        f.record('网页已完成',role='assistant')
        other=f.record('查另一个地方的天气')
        f.record(kind='tool_call',role=None,name='Bash',callId='other',payload={'arguments':{'command':'curl https://other.example/weather'}})
        f.record(kind='tool_result',role=None,callId='other',payload={'output':'other city'})
        f.ingest()
        base=f.model
        def model(system,data,max_tokens=4000):
            if set(data)=={'question','history'}:return {'terms':['curl'],'focusPrevious':True,'maxTasks':1}
            if 'candidates' in data:return {'selected':[{'id':data['candidates'][0]['id'],'status':'supported','reason':'追问服务器保存的上一任务'}]}
            return base(system,data,max_tokens=max_tokens)
        f.assistant.model=model
        answer=f.assistant.answer('alice',f.device,'它 curl 到哪个网址？',history=[{'question':'上海天气网页','answer':'已写网页','tasks':[{'taskId':goal['id'],'title':'上海天气网页','source':'workbuddy','sessionId':'s','turnIds':[goal['id']]}]}])
        self.assertEqual([c['taskId'] for c in answer['candidates']],[goal['id']])
        self.assertNotIn(other['id'],{c['taskId'] for c in answer['candidates']})
        self.assertTrue(any('localhost:3000' in r['text'] for r in answer['evidence']))
        self.assertFalse(any('other.example' in r['text'] for r in answer['evidence']))

    def test_task_filter_cannot_disambiguate_reused_call_id(self):
        rows=[]
        def record(identity,kind,role=None,call=None):
            event={'id':identity,'source':'workbuddy','sessionId':'s','kind':kind,'role':role,'callId':call,'_seq':len(rows)}
            rows.append({'id':identity,'recordId':'sessionlens:'+identity,'source':'workbuddy','sessionId':'s','deviceId':'d',
                         'kind':kind,'role':role,'seq':len(rows),'text':identity,'event':event})
        record('u1','message','user');record('c1','tool_call',call='same')
        record('u2','message','user');record('c2','tool_call',call='same');record('r','tool_result',call='same')
        turns=turn_packet(rows)['turns']
        before=next(r for t in turns for r in t['records'] if r['recordId']=='sessionlens:r')
        self.assertIsNone(before['callRecordId'])
        self.assertIn('ambiguous_call',before['relationshipGaps'])
        expand_turn_evidence(turns,{r['recordId']:r for r in rows},{'u2'})
        after=next(r for t in turns for r in t['records'] if r['recordId']=='sessionlens:r')
        self.assertIsNone(after['callRecordId'])
        self.assertIn('ambiguous_call',after['relationshipGaps'])
        self.assertNotEqual(after['association'],'recorded_call_id')
        links=[{'turnId':t['turnId'],'parentTurnId':None,'relation':'request','status':'supported','reason':'独立目标','evidenceTurnIds':[t['turnId']]} for t in turns]
        tasks=group_tasks(turns,links)
        self.assertFalse(any('sessionlens:r' in action['resultRecordIds'] for t in tasks for action in t['executions']))

    def test_result_search_anchor_cannot_pick_first_of_reused_calls(self):
        f=self.f
        f.record('另一个独立任务')
        f.record(kind='tool_call',role=None,name='Bash',callId='reused',payload={'arguments':{'command':'first'}})
        goal=f.record('新建上海天气网页项目')
        f.record(kind='tool_call',role=None,name='Bash',callId='reused',payload={'arguments':{'command':'second'}})
        for i in range(10):
            f.record(kind='tool_call',role=None,name='Write',callId='w'+str(i),payload={'arguments':{'path':str(i)}})
            f.record(kind='tool_result',role=None,callId='w'+str(i),payload={'output':'written'})
        result=f.record(kind='tool_result',role=None,callId='reused',payload={'output':'上海天气 needle-result'})
        f.record(kind='tool_call',role=None,name='Write',callId='last',payload={'arguments':{'path':'last'}})
        f.record(kind='tool_result',role=None,callId='last',payload={'output':'written'})
        f.record('已完成',role='assistant');f.ingest()
        base=f.model;checked=[]
        def model(system,data,max_tokens=4000):
            if 'candidates' in data:
                hit=next(h for h in data['candidates'] if h['kind']=='tool_result' and 'needle-result' in h['text'])
                return {'selected':[{'id':hit['id'],'status':'supported','reason':'检索到待核对返回'}]}
            if 'turns' in data:
                record=next(r for t in data['turns'] for r in t['records'] if 'needle-result' in r['text'])
                self.assertIsNone(record['callRecordId'])
                self.assertIn('ambiguous_call',record['relationshipGaps'])
                checked.append(True)
            return base(system,data,max_tokens=max_tokens)
        f.assistant.model=model
        answer=f.assistant.answer('alice',f.device,'上海天气 needle-result 是哪个工具的返回？')
        self.assertTrue(checked)
        self.assertEqual(answer['candidates'][0]['taskId'],goal['id'])
        self.assertFalse(any('sessionlens:'+result['id'] in action['resultRecordIds'] for t in answer['candidates'] for action in t['executions']))

    def test_merged_window_positions_cannot_move_future_call_before_return(self):
        rows=[]
        for seq,(identity,kind,role,call) in enumerate([
            ('u1','message','user',None),('c1','tool_call',None,'reused'),('r','tool_result',None,'reused'),
            ('u2','message','user',None),('c2','tool_call',None,'reused')]):
            rows.append({'id':identity,'recordId':'sessionlens:'+identity,'source':'workbuddy','sessionId':'s','deviceId':'d',
                         'kind':kind,'role':role,'seq':100-seq,'windowSeq':seq,'timestamp':100-seq,
                         'fileIdentity':'one-log','byteStart':seq*100,'text':identity,
                         'event':{'id':identity,'source':'workbuddy','sessionId':'s','kind':kind,'role':role,'callId':call,'_seq':seq}})
        turns=turn_packet(rows)['turns']
        # A second overlapping source window resets its local ordinal.
        merged={row['recordId']:dict(row,windowSeq=row['windowSeq'] if row['id'] in ('u1','c1','r') else row['windowSeq']-3) for row in rows}
        expand_turn_evidence(turns,merged,{'u1'})
        result=next(r for t in turns for r in t['records'] if r['recordId']=='sessionlens:r')
        self.assertEqual(result['callRecordId'],'sessionlens:c1')
        self.assertEqual(result['association'],'recorded_call_id')
        self.assertNotIn('ambiguous_call',result['relationshipGaps'])

if __name__=='__main__':unittest.main()
