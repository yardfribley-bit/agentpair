import json
from pathlib import Path
import tempfile
import unittest
from sessionlens.core import Collector
from sessionlens.supervision import TaskStore
from sessionlens.task_lineage import history, resolve,set_override
from sessionlens.assistant import packet_for_task
from sessionlens.task_presentation import project
from sessionlens.knowledge import candidates, answer_mismatch


class LineageTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.path=self.root/'collector.db';self.log=self.root/'session.jsonl'
        self.collector=Collector(self.path);self.store=None
    def tearDown(self):
        if self.store:self.store.close()
        self.collector.db.close();self.temp.cleanup()
    def ingest(self,records,source='workbuddy'):
        with self.log.open('a') as f:
            for record in records:f.write(json.dumps({'sessionId':'s',**record},ensure_ascii=False)+'\n')
        self.collector.scan(self.log,source=source)
        if not self.store:self.store=TaskStore(self.path)
        self.store.advance(realtime=True)
        self.store.advance()
        while self.store.repair_links(1):pass
    def user(self,text):return {'type':'message','role':'user','content':text}
    def tool(self,call='c',name='Write',args=None):return {'type':'function_call','name':name,'callId':call,'arguments':args or {'file_path':'login.py'}}
    def result(self,call='c'):return {'type':'function_call_result','callId':call,'output':'written'}

    def test_discussion_confirmation_and_execution_are_one_task(self):
        self.ingest([self.user('做一个登录页面'),{'type':'message','role':'assistant','content':'建议手机号或邮箱登录'},
                     self.user('支持手机号，不要邮箱登录'),self.user('按照这个方案先画原型'),
                     self.user('这个版本可以'),self.user('做'),self.tool(),self.result()])
        tasks=self.store.tasks();self.assertEqual(len(tasks),1);task=tasks[0][0]
        self.assertEqual(tasks[0][3],'做一个登录页面')
        turns=history(self.store.db,task);self.assertEqual([r['kind'] for r in turns],['request','revision','revision','approval','execution'])
        packet=packet_for_task(self.store.db,turns[-1]['eventId'])
        self.assertEqual(packet['taskId'],task)
        link=packet['executionLinks'][0];self.assertEqual(link['requirementEvent'],turns[2]['eventId']);self.assertEqual(link['approvalEvent'],turns[4]['eventId'])
        self.assertTrue(link['requirementRef']);self.assertTrue(link['approvalRef'])
        self.assertEqual(candidates(self.store.db,['手机号'],question='手机号登录是怎么做的')[0][0],task)
        view=project(self.store.db,turns[-1]['eventId']);self.assertEqual(len(view['requirements']),5)
        self.assertEqual(view['calls'][0]['requirement']['requirementEvent'],turns[2]['eventId'])
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM events').fetchone()[0],8)

    def test_later_revision_does_not_rewrite_earlier_execution(self):
        self.ingest([self.user('编写登录页面'),self.user('支持手机号'),self.user('做'),self.tool('a'),self.result('a'),
                     self.user('改成邮箱登录'),self.user('开始做'),self.tool('b'),self.result('b')])
        task=self.store.tasks()[0][0];view=project(self.store.db,task);turns=view['requirements']
        self.assertNotEqual(view['calls'][0]['requirement']['requirementEvent'],view['calls'][1]['requirement']['requirementEvent'])
        self.assertEqual(view['calls'][0]['requirement']['requirementEvent'],turns[1]['eventId'])
        self.assertEqual(view['calls'][1]['requirement']['requirementEvent'],turns[3]['eventId'])
        self.store.close();self.store=TaskStore(self.path)
        self.assertEqual(len(self.store.tasks()),1);self.assertEqual(len(self.store.steps(task)),9)

    def test_topic_switch_resume_and_async_return_follow_call(self):
        self.ingest([self.user('做一个登录页面'),self.user('做'),self.tool('login'),
                     self.user('查上海天气'),self.tool('weather','WebSearch',{'query':'上海天气'}),self.result('login'),self.result('weather'),
                     self.user('回到登录页面，继续做'),self.tool('finish')])
        tasks=self.store.tasks();self.assertEqual(len(tasks),2)
        login=next(t[0] for t in tasks if '登录' in t[3]);weather=next(t[0] for t in tasks if '天气' in t[3])
        self.assertEqual(history(self.store.db,login)[-1]['kind'],'resume')
        self.assertEqual([s[3] for s in self.store.steps(login) if s[1]=='工具返回'],['login'])
        self.assertEqual([s[3] for s in self.store.steps(weather) if s[1]=='工具返回'],['weather'])
        self.assertEqual(self.store.tasks(live=True)[0][0],login)

    def test_missing_origin_is_unresolved_not_invented(self):
        self.ingest([self.user('做'),self.tool()])
        task=self.store.tasks()[0][0];self.assertEqual(history(self.store.db,task)[0]['kind'],'unresolved')
        self.assertIsNone(project(self.store.db,task)['calls'][0]['requirement']['approvalEvent'])

    def test_long_task_packet_keeps_origin_and_execution_requirements(self):
        records=[self.user('编写库存管理工具')]
        for i in range(90):records.append({'type':'message','role':'assistant','content':'讨论方案 '+str(i)})
        records += [self.user('增加库存导出功能'),self.user('做')]
        for i in range(90):records.append(self.tool(str(i)))
        self.ingest(records);task=self.store.tasks()[0][0];packet=packet_for_task(self.store.db,task)
        self.assertLessEqual(packet['includedRecords'],120)
        self.assertEqual(packet['requirementHistory'][0]['eventId'],task)
        for link in packet['executionLinks']:
            self.assertTrue(link['requirementRef']);self.assertTrue(link['approvalRef'])

    def test_cached_fragment_requires_requery_and_alias_resolves(self):
        self.ingest([self.user('设计库存管理工具'),self.user('按照这个方案开始开发'),self.tool()])
        task=self.store.tasks()[0][0];alias=history(self.store.db,task)[-1]['eventId']
        self.assertEqual(resolve(self.store.db,alias),task)
        self.assertIsNotNone(answer_mismatch(self.store.db,{'taskId':alias,'selection':{'version':3}}))
        self.assertIsNone(answer_mismatch(self.store.db,{'taskId':task,'packet':{'lineageVersion':1},'selection':{'version':3}}))

    def test_confirmation_keeps_proposed_plan_not_later_status_reply(self):
        self.ingest([self.user('做一个会员管理页面'),
                     {'type':'message','role':'assistant','content':'方案：手机号登录，表格显示会员名单。'},
                     self.user('确认'),{'type':'message','role':'assistant','content':'已确认，等待你开始。'},
                     self.user('做'),{'type':'message','role':'assistant','content':'现在开始修改文件。'},self.tool()])
        task=self.store.tasks()[0][0];view=project(self.store.db,task)
        plan=view['calls'][0]['requirement']['planEvent']
        self.assertEqual(view['plans'][plan],'方案：手机号登录，表格显示会员名单。')
        self.assertTrue(packet_for_task(self.store.db,task)['executionLinks'][0]['planRef'])

    def test_ambiguous_resume_does_not_pick_one_of_two_similar_tasks(self):
        self.ingest([self.user('设计手机号登录页面'),self.user('新任务：设计邮箱登录页面'),self.user('回到登录页面，继续做')])
        self.assertEqual(len(self.store.tasks()),3)
        latest=self.store.tasks()[0][0]
        self.assertEqual(history(self.store.db,latest)[0]['kind'],'unresolved')

    def test_existing_turn_index_is_migrated_without_raw_reindex(self):
        self.ingest([self.user('写一个库存页面'),self.user('增加导出功能'),self.user('做'),self.tool()])
        db=self.store.db
        before=db.execute('SELECT id,event FROM events ORDER BY rowid').fetchall()
        cursor=db.execute('SELECT name,value FROM task_cursor ORDER BY name').fetchall()
        db.execute("DELETE FROM task_link_meta WHERE name='version'");db.commit()
        self.store.close();self.store=TaskStore(self.path)
        while self.store.repair_links(1):pass
        self.assertEqual(self.store.db.execute('SELECT id,event FROM events ORDER BY rowid').fetchall(),before)
        self.assertEqual(self.store.db.execute('SELECT name,value FROM task_cursor ORDER BY name').fetchall(),cursor)
        self.assertEqual(len(self.store.tasks()),1)

    def test_user_can_correct_and_split_ambiguous_turn_without_editing_evidence(self):
        self.ingest([self.user('设计会员管理页面'),self.user('手机号登录，会员表格'),self.user('做'),self.tool()])
        rows=self.store.db.execute('SELECT t.id,t.prompt FROM tasks t JOIN events e ON e.id=t.id ORDER BY e.rowid').fetchall()
        self.assertEqual(len(self.store.tasks()),2)
        before=self.store.db.execute('SELECT event FROM events ORDER BY rowid').fetchall()
        set_override(self.store.db,rows[1][0],rows[0][0]);self.assertEqual(len(self.store.tasks()),1)
        self.assertEqual(history(self.store.db,rows[0][0])[1]['association'],'confirmed_by_user')
        set_override(self.store.db,rows[1][0],None);self.assertEqual(len(self.store.tasks()),2)
        self.assertEqual(self.store.db.execute('SELECT event FROM events ORDER BY rowid').fetchall(),before)
        with self.assertRaises(ValueError):set_override(self.store.db,rows[0][0],rows[2][0])

    def test_background_reasoning_does_not_cross_new_user_requirement(self):
        self.ingest([self.user('设计库存页面'),{'type':'message','role':'assistant','content':'先研究布局。'},
                     {'type':'reasoning','content':'先读取库存数据。'},self.user('增加导出功能'),self.tool()])
        view=project(self.store.db,self.store.tasks()[0][0])
        if view['frames']:self.assertIsNone(view['frames'][0]['call'])

    def test_completed_history_is_not_replayed_on_startup(self):
        from sessionlens.desktop import Runtime
        from unittest.mock import patch
        self.ingest([self.user('设计库存页面'),self.user('做'),self.tool()])
        runtime=Runtime(self.root,{'endpoint':''});runtime.stop.set()
        with patch.object(TaskStore,'recent_source') as warmup:runtime.project();warmup.assert_not_called()
