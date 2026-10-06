import json,tempfile,unittest
from pathlib import Path
from sessionlens.core import Collector
from sessionlens.supervision import TaskStore
from sessionlens.interactions import task_interactions
from sessionlens.task_queries import local_task_query

class InteractionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.log=self.root/'s.jsonl';self.c=Collector(self.root/'collector.db');self.s=TaskStore(self.root/'collector.db')
    def tearDown(self):self.s.close();self.c.db.close();self.tmp.cleanup()
    def add(self,rows,source='workbuddy'):
        self.log.write_text(''.join(json.dumps({'sessionId':'s',**r},ensure_ascii=False)+'\n' for r in rows));self.c.scan(self.log,source=source);self.s.advance(realtime=True);self.s.advance()
        while self.s.repair_links(2):pass
    def user(self,text):return {'type':'message','role':'user','content':text}
    def test_confirmations_join_goal_and_count_all_not_sampled_messages(self):
        self.add([self.user('写一个 Nginx 配置工具'),self.user('好的'),self.user('行'),self.user('干'),
                  {'type':'function_call','name':'Write','callId':'c','arguments':{'file_path':'/work/ngx/main.py'}},
                  {'type':'function_call_result','callId':'c','output':'written'},
                  {'type':'message','role':'assistant','content':'已写入文件'}])
        self.assertEqual(len(self.s.tasks()),1);task=self.s.tasks()[0][0];stats=task_interactions(self.s.db,task)
        self.assertEqual(stats['userTurns'],4);self.assertEqual(stats['approvalTurns'],2);self.assertEqual(stats['executionTurns'],1);self.assertEqual(stats['toolCalls'],1)
        self.assertIsNone(stats['modelCalls']);self.assertEqual(stats['agentReplyRecords'],1)
        result=local_task_query(self.root,'这个任务 Agent 与大模型交互了多少轮？',selected=task)
        self.assertEqual(result['interactions']['userTurns'],4);self.assertIn('无法确认',result['understanding']['overview']['text'])
    def test_new_goal_does_not_inherit_confirmation_or_execution(self):
        self.add([self.user('写登录页面'),self.user('好的'),self.user('新任务：查上海天气'),self.user('行，干')])
        self.assertEqual(len(self.s.tasks()),2)
        weather=next(t[0] for t in self.s.tasks() if '天气' in t[3]);login=next(t[0] for t in self.s.tasks() if '登录' in t[3])
        self.assertEqual(task_interactions(self.s.db,weather)['userTurns'],2);self.assertEqual(task_interactions(self.s.db,login)['userTurns'],2)
    def test_codex_mirrored_user_records_count_one_user_turn(self):
        self.add([{'type':'session_meta','payload':{'id':'c'}},
                  {'type':'event_msg','payload':{'type':'user_message','message':'写一个登录页面'}},
                  {'type':'response_item','payload':{'type':'message','role':'user','content':[{'text':'写一个登录页面'}]}}],source='codex')
        self.assertEqual(task_interactions(self.s.db,self.s.tasks()[0][0])['userTurns'],1)
    def test_selected_project_does_not_select_first_task_for_count(self):
        value=local_task_query(self.root,'这个任务和模型交互了多少次？',previous={'projectDetails':{'source':'workbuddy','tasks':[{'taskId':'a','prompt':'任务甲','updated':''},{'taskId':'b','prompt':'任务乙','updated':''}]}})
        self.assertTrue(value['selectionNeeded']);self.assertEqual(len(value['options']),2)
    def test_feedback_is_not_new_requirement_but_fresh_goal_is(self):
        self.add([self.user('做一个记忆观测页面'),self.user('页面呢'),self.user('这个页面能带来什么价值？'),self.user('你现在做的这个东西我看不懂'),self.user('新任务：查上海天气')])
        self.assertEqual(len(self.s.tasks()),2)
        task=next(t[0] for t in self.s.tasks() if '记忆观测' in t[3]);self.assertEqual(task_interactions(self.s.db,task)['userTurns'],4)
    def test_sdk_usage_and_repeated_message_id_are_not_api_call_count(self):
        provider={'messageId':'m1','conversationRequestId':'outer','usage':{'requests':1}}
        self.add([self.user('做一个页面'),{'type':'reasoning','content':'检查页面','providerData':provider},
                  {'type':'message','role':'assistant','content':'页面已写入','providerData':provider}])
        stats=task_interactions(self.s.db,self.s.tasks()[0][0]);self.assertEqual(stats['modelMessageIdentifiers'],1);self.assertIsNone(stats['modelCalls'])
    def test_execute_and_inspect_effect_is_not_new_goal(self):
        self.add([self.user('设计登录页面原型'),self.user('做 我想看一下效果')])
        self.assertEqual(len(self.s.tasks()),1);self.assertEqual(task_interactions(self.s.db,self.s.tasks()[0][0])['executionTurns'],1)
