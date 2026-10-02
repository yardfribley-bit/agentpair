import json,tempfile,unittest
from pathlib import Path
from agentpair.interaction_audit import build_audit
from agentpair.security_investigation import semantic_packet,validate_answer
from agentpair.devices import DeviceStore
from agentpair.tasks import TaskEngine
from test_tasks import FakeBackend

class SecurityTests(unittest.TestCase):
    def audit(self,source='workbuddy_network_context',messages=None):
        r={'id':'a'*64,'source':source,'timestamp':1,'body':json.dumps({'messages':messages or [{'role':'user','content':'<user_query>修复登录函数</user_query>'},{'role':'tool','content':'api_key=sk-abcdefghijklmnopqrstuvwx'}]})}
        return build_audit({'id':'dev','os':'macOS'},r)
    def test_observation_is_not_unauthorized_leak(self):
        a=self.audit();i=a['investigation'];self.assertTrue(i['issues']);self.assertIsNone(i['incidentCount'])
        self.assertTrue(all(f['status']=='needs_review' for f in i['issues']))
        self.assertIn('未取得接收回执',' '.join(i['facts']))
        self.assertIn('网络发送未证实',' '.join(self.audit('workbuddy_session_context')['investigation']['facts']))
    def test_packet_masking_coverage_and_hit_outside_preview(self):
        a=self.audit(messages=[{'role':'user','content':'<user_query>修复函数</user_query>'},{'role':'tool','content':'x'*4000+' api_key=sk-abcdefghijklmnopqrstuvwx'}]);p=semantic_packet(a)
        self.assertNotIn('sk-abcdefghijklmnopqrstuvwx',json.dumps(p));self.assertFalse(p['coverage']['fullBodySemanticReview'])
        self.assertTrue(any(f.get('charStart',0)>4000 for f in p['fragments']))
        huge=self.audit(messages=[{'role':'user','content':'x'*2000} for _ in range(100)])
        self.assertLessEqual(sum(len(f['preview']) for f in semantic_packet(huge)['fragments']),16000)
    def test_rejects_hallucinated_refs_and_confirmed_leak_label(self):
        p=semantic_packet(self.audit());f={'topic':'候选','claim':'需复核','reason':'输入包含候选','alternative':'示例','nextAction':'核对来源','status':'hypothesis','evidenceRefs':['E002']}
        self.assertTrue(validate_answer({'findings':[f]},p)['securityEvidenceValidated'])
        for bad in ({**f,'evidenceRefs':['unknown']},{**f,'status':'confirmed_leak'},{**f,'alternative':''}):
            with self.assertRaises(ValueError):validate_answer({'findings':[bad]},p)
    def test_task_packet_persisted_and_review_public_projection(self):
        with tempfile.TemporaryDirectory() as t:
            store=DeviceStore(Path(t)/'devices.db');d=store.enroll(store.pairing('alice')['code'],'mac')
            a=self.audit();r={'id':a['request']['id'],'source':'workbuddy_network_context','timestamp':1,'body':json.dumps({'messages':[{'role':'user','content':'修复函数'}]})}
            store.ingest_model_context(d['token'],{'requests':[r]});packet,context=store.model_analysis_context('alice',d['deviceId'],r['id'])
            backend=FakeBackend();engine=TaskEngine(Path(t)/'tasks.db',backend,start=False)
            task=engine.create('安全调查',context,owner='alice',security_evidence=packet)
            store.link_security_review(d['deviceId'],r['id'],packet['request']['bodySHA256'],task['id']);engine.process(task['id'])
            self.assertEqual([e['mode'] for _,e in backend.calls],['plan','driver','review'])
            self.assertTrue(all(e['task']['securityEvidence']==packet for _,e in backend.calls))
            view=store.security_review(d['deviceId'],r['id'],packet['request']['bodySHA256'],engine)
            self.assertEqual(len(view['stages']),3);self.assertNotIn('securityEvidence',view)
            self.assertIsNone(store.security_review(d['deviceId'],r['id'],'different_hash',engine))
            engine.close()

if __name__=='__main__':unittest.main()
