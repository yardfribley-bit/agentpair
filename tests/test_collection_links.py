import unittest
from agentpair.collection_links import candidates,validate_task_links

class CollectionLinksTests(unittest.TestCase):
    def test_session_is_candidate_not_task(self):
        edges=candidates([{'id':'ctx','sessionId':'s'}],[{'id':'e','source':'workbuddy','sessionId':'s'}])
        self.assertEqual(edges[0]['status'],'needs_task_review');self.assertFalse(edges[0]['taskConfirmed'])
    def test_matching_request_and_app_boundary(self):
        ctx={'id':'ctx','sessionId':'s','requestId':'r'}
        events=[{'id':'e','source':'workbuddy','payload':{'request_id':'r'}},{'id':'codex','source':'codex','sessionId':'s','requestId':'r'}]
        edges=candidates([ctx],events);self.assertEqual(len(edges),1);self.assertEqual(edges[0]['relation'],'same_request_evidence')
    def test_time_alone_does_not_link(self):
        self.assertEqual(candidates([{'id':'ctx','timestamp':123}],[{'id':'e','source':'workbuddy','timestamp':123}]),[])
    def test_semantic_output_rejects_invented_evidence(self):
        with self.assertRaises(ValueError):validate_task_links({'links':[{'recordIds':['ctx'],'evidenceIds':['fake'],'status':'supported','reason':'same goal'}]},['ctx'])
    def test_long_user_reminder_keeps_actual_request(self):
        from agentpair.collection_links import excerpt
        result=excerpt({'payload':{'content':[{'type':'text','text':'system reminder '*500+'请生成一个五秒SSH视频'}]}},1800)
        self.assertTrue(result['truncated']);self.assertIn('请生成一个五秒SSH视频',result['text'])
    def test_encrypted_reasoning_is_not_readable(self):
        from agentpair.collection_links import excerpt
        result=excerpt({'payload':{'encrypted_content':'secret_base64','content':[]}})
        self.assertEqual(result['coverage'],'no_readable_content');self.assertEqual(result['text'],'')
