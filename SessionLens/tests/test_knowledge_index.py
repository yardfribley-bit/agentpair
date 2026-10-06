import json,sqlite3,tempfile,unittest
from pathlib import Path
from sessionlens.core import Collector
from sessionlens.supervision import TaskStore
from sessionlens.knowledge_index import KnowledgeIndex
from sessionlens.knowledge import retrieve_candidates
from sessionlens.task_lineage import set_override

class KnowledgeIndexTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.log=self.root/'log.jsonl'
        self.log.write_text('');self.collector=Collector(self.root/'collector.db');self.store=TaskStore(self.root/'collector.db');self.index=KnowledgeIndex(self.root/'knowledge.db')
    def tearDown(self):self.index.close();self.store.close();self.collector.db.close();self.tmp.cleanup()
    def add(self,records):
        with self.log.open('a') as file:file.write(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in records))
        self.collector.scan(self.log,source='workbuddy');self.store.advance(2000,realtime=True);self.store.repair_links(10)
    def fill(self):
        self.index.sync(self.store.db,force=True)
        for _ in range(100):
            if not self.index.pending:break
            self.index.sync(self.store.db)
    def user(self,text,session='s'):return {'type':'message','role':'user','sessionId':session,'content':text,'timestamp':'2026-10-06T00:00:00Z'}
    def test_chinese_goal_and_exact_artifact_retrieve_without_raw_scan(self):
        self.add([self.user('查上海天气'),self.user('新建上海天气网页','web'),self.user('创建 ledger.py','files'),self.user('创建 ledger_test.py','tests')])
        cursor=self.store.db.execute('SELECT * FROM task_cursor').fetchall();statements=[];self.store.db.set_trace_callback(statements.append);self.fill()
        self.assertFalse(any('FROM events' in s for s in statements));self.assertEqual(self.store.db.execute('SELECT * FROM task_cursor').fetchall(),cursor)
        hits=retrieve_candidates(self.store.db,{'terms':['上海天气'],'subjects':['上海'],'taskAction':'inspect'},'查上海天气',index=self.index)
        self.assertEqual(hits[0][1],'查上海天气')
        hits=retrieve_candidates(self.store.db,{'terms':['ledger.py'],'taskAction':'create'},'创建ledger.py 后做了什么',index=self.index)
        self.assertTrue(hits);self.assertNotIn('ledger_test.py',[r[1] for r in hits])
    def test_bounded_batches_incremental_restart_and_cleanup_after_merge(self):
        records=[self.user('分析发布方案')]+[{'type':'message','role':'assistant','sessionId':'s','content':str(i)} for i in range(80)]
        records += [self.user('进一步分析该方案')]
        self.add(records);ids=[r[0] for r in self.store.db.execute('SELECT t.id FROM tasks t JOIN events e ON e.id=t.id ORDER BY e.rowid')]
        self.assertLessEqual(self.index.sync(self.store.db,task_limit=1,step_limit=5,force=True),5)
        self.assertGreater(self.index.status()['pendingTasks'],0);self.fill()
        event=self.store.db.execute('SELECT event FROM task_steps WHERE task=? ORDER BY seq LIMIT 1',(ids[0],)).fetchone()[0]
        old_row=self.index.db.execute('SELECT rowid FROM kb_chunks WHERE id=?',('event:'+event,)).fetchone()
        self.index.close();self.index=KnowledgeIndex(self.root/'knowledge.db');self.assertEqual(self.index.sync(self.store.db,force=True),0)
        self.add([{'type':'message','role':'assistant','sessionId':'s','content':'这是新增回复'}]);self.fill()
        self.assertEqual(self.index.db.execute('SELECT rowid FROM kb_chunks WHERE id=?',('event:'+event,)).fetchone(),old_row)
        set_override(self.store.db,ids[1],ids[0]);self.fill()
        self.assertEqual(self.index.db.execute('SELECT count(*) FROM kb_tasks').fetchone()[0],1)
        self.assertEqual(self.index.db.execute('SELECT count(*) FROM kb_fts').fetchone()[0],self.index.db.execute('SELECT count(*) FROM kb_chunks').fetchone()[0])
    def test_arbitrary_fts_syntax_is_data_and_vectors_are_optional_and_versioned(self):
        self.add([self.user('修改会员登录'),self.user('分析库存报表','inventory')]);self.fill()
        for q in ('" OR NEAR( * )','abc:xyz -- [bad]',''):self.index.search([q])
        self.assertIn('未启用向量',self.index.status()['retrieval'])
        chunk=self.index.db.execute("SELECT id FROM kb_chunks WHERE kind='任务目标' AND text LIKE '%会员%'").fetchone()[0]
        self.index.put_vector(chunk,'local:model-a:2',[1.,0.])
        self.assertTrue(self.index.search([],vector=[1.,0.],identity='local:model-a:2'))
        self.assertEqual(self.index.search([],vector=[1.,0.],identity='local:model-b:2'),[])
        self.assertEqual(self.index.search([],vector=[1.,0.,0.],identity='local:model-a:2'),[])
        for value in ([float('nan')],[True],[],[0.,0.]):
            with self.assertRaises(ValueError):self.index.put_vector(chunk,'local:model-a:2',value)
        with self.index.db:self.index._chunk(chunk,self.index.db.execute('SELECT task FROM kb_chunks WHERE id=?',(chunk,)).fetchone()[0],None,None,'任务目标',0,'更新后的原始要求')
        self.assertEqual(self.index.db.execute('SELECT count(*) FROM kb_vectors').fetchone()[0],0)
    def test_quota_pauses_derived_index_without_touching_collection(self):
        self.add([self.user('生成营销方案')]);self.index.max_bytes=1;cursor=self.store.db.execute('SELECT * FROM task_cursor').fetchall()
        self.assertEqual(self.index.sync(self.store.db,force=True),0);self.assertTrue(self.index.status()['paused'])
        self.assertEqual(self.store.db.execute('SELECT * FROM task_cursor').fetchall(),cursor)
    def test_partial_index_uses_goal_only_fallback_for_older_task(self):
        self.add([self.user('设计登录页面','old'),self.user('生成周报','new')]);self.index.sync(self.store.db,task_limit=1,force=True)
        found=retrieve_candidates(self.store.db,{'terms':['登录'],'subjects':['登录']},'当时登录页面怎么做的',index=self.index)
        self.assertTrue(found);self.assertEqual(found[0][1],'设计登录页面')
    def test_existing_excerpt_repair_updates_index_without_execution_revision(self):
        self.add([self.user('检查接口'),{'type':'function_call','name':'Bash','sessionId':'s','callId':'c','arguments':{'command':'curl https://correct.example/service'}}])
        ident=self.store.db.execute("SELECT event FROM task_steps WHERE kind='工具调用'").fetchone()[0]
        with self.store.db:self.store.db.execute('UPDATE task_steps SET excerpt=? WHERE event=?',('错误旧参数',ident))
        self.fill();self.assertEqual(self.index.db.execute('SELECT text FROM kb_chunks WHERE event=?',(ident,)).fetchone()[0],'错误旧参数')
        self.store.repair_excerpts();self.index.sync(self.store.db)
        self.assertIn('correct.example',self.index.db.execute('SELECT text FROM kb_chunks WHERE event=?',(ident,)).fetchone()[0])

if __name__=='__main__':unittest.main()
