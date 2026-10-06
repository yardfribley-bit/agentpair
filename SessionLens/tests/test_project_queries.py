import json,sqlite3,tempfile,unittest
from pathlib import Path
from sessionlens.core import Collector
from sessionlens.supervision import TaskStore
from sessionlens.project_inventory import ProjectInventory
from sessionlens.project_queries import local_project_query

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
