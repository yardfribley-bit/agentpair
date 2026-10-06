import json,tempfile,unittest
from pathlib import Path
from sessionlens.core import Collector
from sessionlens.supervision import TaskStore
from sessionlens.project_context import ProjectStore,index_text,remote,tool_arguments
from sessionlens.knowledge import candidates
from sessionlens.embedding_store import EmbeddingStore
from sessionlens.knowledge_index import KnowledgeIndex
from tests.test_embedding import FakeEmbedding

class ProjectContextTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.log=self.root/'log.jsonl'
        self.log.write_text('',encoding='utf-8');self.c=Collector(self.root/'collector.db');self.s=TaskStore(self.root/'collector.db');self.p=ProjectStore(self.root/'project_context.db',filesystem=False)
    def tearDown(self):self.p.close();self.s.close();self.c.db.close();self.tmp.cleanup()
    def add(self,records):
        with self.log.open('a',encoding='utf-8') as f:f.write(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in records))
        self.c.scan(self.log,source='workbuddy');self.s.advance(1000,realtime=True);self.s.repair_links(10)
    def user(self,text,session='s',cwd='/work/atlas',repo='https://github.com/team/atlas.git'):
        return {'type':'message','role':'user','content':text,'sessionId':session,'cwd':cwd,'git':{'repository_url':repo,'branch':'feature/a'}}
    def call(self,args,session='s',name='Bash'):return {'type':'function_call','sessionId':session,'name':name,'arguments':args}
    def id(self,text):return self.s.db.execute('SELECT id FROM tasks WHERE prompt=?',(text,)).fetchone()[0]
    def test_same_repo_different_goals_and_weather_remains_unlinked(self):
        self.add([self.user('更新项目说明'),self.call({'command':'git status'}),self.user('修改登录逻辑'),self.call({'path':'login.py'},name='Write'),
                  self.user('查上海天气'),self.call({'command':'curl https://weather.invalid/forecast'})])
        original=self.s.db.execute('SELECT id,event FROM events').fetchall();cursors=self.s.db.execute('SELECT * FROM task_cursor').fetchall()
        a=self.p.resolve(self.s.db,self.id('更新项目说明'));b=self.p.resolve(self.s.db,self.id('修改登录逻辑'));weather=self.p.resolve(self.s.db,self.id('查上海天气'))
        self.assertEqual(a['projects'][0]['id'],b['projects'][0]['id']);self.assertNotEqual(a['taskId'],b['taskId'])
        self.assertEqual(weather['mode'],'unlinked');self.assertEqual(weather['workingDirectory'],'/work/atlas');self.assertEqual(index_text(weather),'')
        self.assertEqual(self.s.db.execute('SELECT id,event FROM events').fetchall(),original);self.assertEqual(self.s.db.execute('SELECT * FROM task_cursor').fetchall(),cursors)
    def test_workspace_is_not_target_after_directory_switch_and_multi_repos(self):
        self.add([self.user('检查两处代码'),self.call({'command':'git status','workdir':'/work/other'}),self.call({'command':'git status','workdir':'/work/atlas'})])
        value=self.p.resolve(self.s.db,self.id('检查两处代码'));self.assertEqual(len(value['projects']),1)
        self.assertEqual(value['projects'][0]['root'],'/work/atlas')
        self.p.set_override(value['taskId'],'independent');value=self.p.resolve(self.s.db,value['taskId']);self.assertEqual(value['mode'],'independent');self.assertEqual(value['workingDirectory'],'/work/atlas')
        self.p.set_override(value['taskId'],'automatic');self.assertEqual(self.p.resolve(self.s.db,value['taskId'])['mode'],'linked')
    def test_remote_normalization_and_browser_context_does_not_bind_weather(self):
        self.assertEqual(remote('git@github.com:team/atlas.git',True),remote('https://token:secret@github.com/team/atlas.git?auth=private',True))
        self.add([self.user('<in-app-browser-context>URL: https://github.com/team/atlas</in-app-browser-context>\n查天气'),self.call({'command':'curl https://weather.invalid'})])
        value=self.p.resolve(self.s.db,self.id('查天气'));self.assertEqual(value['mode'],'unlinked');self.assertTrue(value['environmentReferences'])
        self.assertNotIn('secret',json.dumps(value));self.assertNotIn('token',json.dumps(value))
    def test_current_git_target_has_provenance_and_supports_two_repositories(self):
        a=self.root/'alpha';b=self.root/'beta'
        for directory in (a,b):
            (directory/'.git').mkdir(parents=True);(directory/'.git/config').write_text('[remote "origin"]\nurl = https://user:password@github.com/team/'+directory.name+'.git\n',encoding='utf-8')
        self.p.filesystem=True;self.add([self.user('修改两个仓库',cwd=str(a),repo='https://github.com/team/alpha'),self.call({'path':str(a/'README.md')},name='Edit'),self.call({'path':str(b/'app.py')},name='Write')])
        value=self.p.resolve(self.s.db,self.id('修改两个仓库'));self.assertEqual(len(value['projects']),2)
        self.assertTrue(all(p['basis']=='current_filesystem' for p in value['projects']));self.assertTrue(all(p['repository'] is None for p in value['projects']));self.assertNotIn('password',json.dumps(value));self.assertNotIn('user:',json.dumps(value))
    def test_manual_context_updates_retrieval_vectors_and_project_scope(self):
        self.add([self.user('更新说明'),self.call({'command':'git status'}),self.user('查天气','weather'),self.call({'command':'curl https://weather.invalid'},'weather')])
        task=self.id('更新说明');self.p.resolve(self.s.db,task);self.p.resolve(self.s.db,self.id('查天气'))
        self.p.set_override(task,'linked',name='客户平台',repository='https://github.com/customer/portal')
        value=self.p.resolve(self.s.db,task);pid=value['projects'][0]['id']
        found=candidates(self.s.db,['说明'],question='查更新说明',projects=self.p,project_id=pid);self.assertEqual([r[0] for r in found],[task])
        vectors=EmbeddingStore(self.root/'embeddings.db',projects=self.p);engine=FakeEmbedding();vectors.sync(self.s.db,engine,limit=16,force=True)
        self.assertIn('客户平台',vectors.db.execute('SELECT text FROM task_vectors WHERE id=?',(task,)).fetchone()[0])
        self.p.set_override(task,'independent');vectors.sync(self.s.db,engine,limit=16,force=True)
        self.assertNotIn('客户平台',vectors.db.execute('SELECT text FROM task_vectors WHERE id=?',(task,)).fetchone()[0]);vectors.close()
    def test_local_project_marker_can_exist_without_repository(self):
        self.add([self.user('创建一个本地项目',cwd='/work/new-app',repo=''),self.call({'path':'package.json'},name='Write')])
        value=self.p.resolve(self.s.db,self.id('创建一个本地项目'));self.assertEqual(value['mode'],'linked');self.assertIsNone(value['projects'][0]['repository'])
    def test_nested_orchestration_literals_preserve_actual_workdir(self):
        code='await tools.exec_command({cmd: "python -c \'print({\\\"workdir\\\": \\\"/work/decoy\\\"})\'", workdir: "/work/atlas"})'
        args=tool_arguments({'arguments':json.dumps({'code':code})});self.assertEqual(args[0]['workdir'],'/work/atlas')
        example='const example = "tools.exec_command({cmd: \'git status\',workdir:\'/wrong\'})";'
        self.assertEqual(tool_arguments({'arguments':json.dumps({'code':example})}),[])
        self.add([self.user('更新仓库说明'),self.call({'code':'await tools.exec_command({cmd:"git status", workdir:"/work/atlas"})'})])
        value=self.p.resolve(self.s.db,self.id('更新仓库说明'));self.assertEqual(value['mode'],'linked')
    def test_modified_source_header_is_not_used_as_historical_evidence(self):
        rows=[{'type':'session_meta','payload':{'id':'coded','cwd':'/work/atlas','git':{'repository_url':'https://github.com/team/atlas'}}},
              {'type':'response_item','payload':{'type':'message','role':'user','content':[{'text':'查天气'}]}}]
        source=self.root/'rollout.jsonl';source.write_text(''.join(json.dumps(r)+'\n' for r in rows),encoding='utf-8');self.c.scan(source,source='codex');self.s.advance(1000,realtime=True);self.s.repair_links(10)
        task=self.s.db.execute("SELECT id FROM tasks WHERE source='codex'").fetchone()[0]
        self.assertTrue(self.p.resolve(self.s.db,task)['headerEvidence'])
        self.p.db.execute('DELETE FROM project_headers');self.p.db.execute('DELETE FROM task_contexts');self.p.db.commit()
        rows[0]['payload']['cwd']='/work/modified';source.write_text(''.join(json.dumps(r)+'\n' for r in rows),encoding='utf-8')
        value=self.p.resolve(self.s.db,task);self.assertIsNone(value['headerEvidence']);self.assertIsNone(value['workingDirectory'])

if __name__=='__main__':unittest.main()
