import json,sqlite3,tempfile,unittest
from pathlib import Path
from sessionlens.core import Collector
from sessionlens.supervision import TaskStore
from sessionlens.project_inventory import ProjectInventory
from sessionlens.project_queries import local_project_query
from sessionlens.task_queries import local_task_query
from sessionlens.answer_presentation import present
from sessionlens.i18n import t

class ProjectQueryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.log=self.root/'s.jsonl'
        self.c=Collector(self.root/'collector.db');self.tasks=TaskStore(self.root/'collector.db');self.inventory=ProjectInventory(self.root/'project_inventory.db',filesystem=False)
    def tearDown(self):self.inventory.close();self.tasks.close();self.c.db.close();self.tmp.cleanup()
    def add(self,name,source='workbuddy',session='s',root=None):
        directory=root or '/work/'+name
        values=[{'type':'message','role':'user','content':'开发 '+name+' 的登录页面','sessionId':session},
                {'type':'function_call','name':'Write','sessionId':session,'arguments':{'file_path':directory+'/src/login.py','content':'print(1)'}},
                {'type':'function_call','name':'Write','sessionId':session,'arguments':{'file_path':directory+'/pyproject.toml','content':'[project]'}}]
        if source=='codex':values=[{'type':'session_meta','payload':{'id':session}}]+[{'type':'response_item','payload':v} for v in values]
        log=self.root/(source+'-'+session+'.jsonl');log.write_text(''.join(json.dumps(v)+'\n' for v in values),encoding='utf-8');self.c.scan(log,source=source);self.tasks.advance();self.tasks.advance(realtime=True);self.tasks.repair_links(10)
        while self.inventory.sync(self.tasks.db,1000):pass
        self.inventory.sync(self.tasks.db,1000,live=True)
    def test_arbitrary_project_name_task_content_tools_and_followup(self):
        self.add('random-portal-937')
        value=local_project_query(self.root,'random-portal-937项目有多少任务，里面有什么内容？')
        p=value['projectDetails'];self.assertEqual(p['name'],'random-portal-937');self.assertEqual(p['taskCount'],1);self.assertEqual(p['fileCount'],1);self.assertEqual(p['tools'][0]['name'],'Write');self.assertEqual(p['tools'][0]['count'],2)
        self.assertIn('src/login.py',p['files'][0]);self.assertEqual(local_project_query(self.root,'它里面有哪些任务？',previous=value)['projectDetails']['id'],p['id'])
        from sessionlens.project_answers import render_project_answer
        text=render_project_answer(value);self.assertIn('1 个已关联开发任务',text);self.assertIn('登录页面',text);self.assertIn('project-task:',text)
    def test_total_is_product_query_not_top_k_and_missing_project_not_substituted(self):
        self.add('q-portal');value=local_project_query(self.root,'WorkBuddy一共开发了多少项目，项目名称？');self.assertEqual(value['projectInventory']['counts']['identified'],1)
        missing=local_project_query(self.root,'never-there项目有多少任务？');self.assertEqual(missing['projectChoices'],[]);self.assertNotIn('projectDetails',missing)
        self.assertIsNone(local_project_query(self.root,'查上海天气调用了哪些工具'))
    def test_same_name_in_two_agents_requires_explicit_choice(self):
        self.add('shared-app');self.add('shared-app','codex','other')
        value=local_project_query(self.root,'shared-app 项目有哪些任务？');self.assertEqual(len(value['projectChoices']),2)
        chosen=local_project_query(self.root,'shared-app 项目有哪些任务？',selected=value['projectChoices'][0]['id']);self.assertIn('projectDetails',chosen)
        scoped=local_project_query(self.root,'WorkBuddy 的 shared-app 项目有哪些任务？');self.assertEqual(scoped['projectDetails']['source'],'workbuddy')
    def test_project_task_recommendations_choose_linked_tasks_in_both_languages(self):
        self.add('shanghai-weather',session='weather');self.add('other-project',session='outside')
        previous=local_project_query(self.root,'What tasks were completed in shanghai-weather?')
        for lang in ('zh','en'):
            for raw in present(previous)['followups']:
                question=t(raw,lang)
                with self.subTest(lang=lang,question=question):
                    chosen=local_task_query(self.root,question,previous=previous)
                    self.assertTrue(chosen['selectionNeeded']);self.assertEqual(chosen['queryKind'],'project_task_selection')
                    self.assertEqual(chosen['projectDetails']['id'],previous['projectDetails']['id'])
                    self.assertNotIn('taskId',chosen)
                    self.assertEqual({item['taskId'] for item in chosen['options']},set(previous['projectDetails']['taskIds']))
                    self.assertEqual(len(chosen['options']),1)
                    selected=local_task_query(self.root,question,previous=chosen,selected=chosen['options'][0]['taskId'])
                    self.assertEqual(selected['taskId'],chosen['options'][0]['taskId'])
                    self.assertEqual(selected['queryKind'],'task_interactions' if '调用' in raw else 'task_detail')
                    fallback=local_project_query(self.root,question,previous=previous)
                    self.assertEqual(fallback['projectDetails']['id'],previous['projectDetails']['id'])
    def test_multi_task_project_followup_refreshes_options_without_global_tasks(self):
        self.add('shanghai-weather',session='first')
        previous=local_project_query(self.root,'What tasks were completed in shanghai-weather?')
        self.add('shanghai-weather',session='second');self.add('other-project',session='outside')
        for question in ('回顾某项任务的执行过程','Review a task process','核对用户发言和模型调用次数','Check user turns and model call counts'):
            with self.subTest(question=question):
                result=local_task_query(self.root,question,previous=previous)
                self.assertEqual(len(result['options']),2);self.assertNotIn('taskId',result)
                self.assertEqual({item['taskId'] for item in result['options']},set(result['projectDetails']['taskIds']))
                self.assertEqual(result['projectDetails']['name'],'shanghai-weather')
        self.assertIsNone(local_task_query(self.root,'How was this task completed in unrelated-app?',previous=previous))
        self.assertIsNone(local_project_query(self.root,'How was this task completed in unrelated-app?',previous=previous))
        self.assertIsNone(local_task_query(self.root,'How many user turns were in this task about unrelated-app?',previous=previous))
        self.assertIsNone(local_task_query(self.root,'How was this Codex task completed?',previous=previous))
        self.assertIsNone(local_project_query(self.root,'What tasks were completed in unrelated-app?',previous=previous))
        self.assertIsNone(local_project_query(self.root,'How many tasks are in unrelated-app?',previous=previous))
        self.assertEqual(local_project_query(self.root,'What tasks were completed in it?',previous=previous)['projectDetails']['id'],previous['projectDetails']['id'])
    def test_project_selection_never_offers_task_outside_recorded_task_ids(self):
        detail={'source':'workbuddy','taskIds':['inside'],'tasks':[{'taskId':'inside','prompt':'内部任务'},
                                                                {'taskId':'outside','prompt':'其他任务'}]}
        result=local_task_query(self.root,'回顾某项任务的执行过程',previous={'projectDetails':detail})
        self.assertEqual([option['taskId'] for option in result['options']],['inside'])
        detail['taskIds']=[]
        self.assertEqual(local_task_query(self.root,'Review a task process',previous={'projectDetails':detail})['options'],[])
