"""Candidate recall under realistic expansion noise; synthetic data only."""
import unittest
from . import test_collection_knowledge as knowledge

class RetrievalQATests(unittest.TestCase):
    def setUp(self):
        self.fixture=knowledge.CollectionKnowledgeTests('test_inputs_are_bounded_and_query_quotes_do_not_inject_sql')
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)

    def test_short_user_goal_survives_verbose_tool_expansion_noise(self):
        f=self.fixture
        goal=f.event('帮我生成一个五秒钟的动画，动画内容描述 ssh 协议的交互过程',kind='message',role='user',session='real-goal')
        noise=[f.event('SSH 视频 密钥交换 '+str(i),kind='tool_result',session='noise-session') for i in range(180)]
        f.ingest([goal,*noise]);f.sync_all()
        hits=f.kb.search('alice',f.device,['SSH','视频','密钥交换'])
        self.assertLessEqual(len(hits),40)
        self.assertIn(goal['id'],{r['id'] for r in hits})

    def test_source_and_session_balance_keeps_other_agent_requirements(self):
        f=self.fixture
        goal=f.event('SSH 动画',source='codex',session='codex-task')
        others=[f.event('SSH 动画 '+str(i),source='workbuddy',session='busy-task') for i in range(110)]
        f.ingest([*others,goal]);f.sync_all()
        hits=f.kb.search('alice',f.device,['SSH','动画'])
        self.assertIn(goal['id'],{r['id'] for r in hits})
        self.assertEqual({r['source'] for r in hits},{'codex','workbuddy'})
        self.assertEqual(len({r['id'] for r in hits}),len(hits))

    def test_no_user_goal_still_returns_tool_candidates_and_respects_limit(self):
        f=self.fixture
        f.ingest([f.event(kind='tool_call',name='Bash',session=str(i),payload={'arguments':{'command':'curl weather.example'}}) for i in range(15)]);f.sync_all()
        hits=f.kb.search('alice',f.device,['curl'],limit=4)
        self.assertEqual(len(hits),4)
        self.assertTrue(all(r['kind']=='tool_call' for r in hits))

if __name__=='__main__':unittest.main()
