import json,sqlite3,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from sessionlens.core import Collector
from sessionlens.supervision import TaskStore
from sessionlens.embedding_store import EmbeddingStore
from sessionlens.knowledge_index import KnowledgeIndex
from sessionlens.knowledge import retrieve_candidates,select_task
from sessionlens.task_lineage import set_override

class FakeEmbedding:
    identity='fixture-model-a';dimensions=3
    def encode(self,texts):return [[1.,0.,0.] if '手机号' in t else [0.,1.,0.] for t in texts]
    def query(self,text):return [1.,0.,0.]

class EmbeddingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.log=self.root/'log.jsonl';self.log.write_text('')
        self.c=Collector(self.root/'collector.db');self.s=TaskStore(self.root/'collector.db');self.v=EmbeddingStore(self.root/'embeddings.db');self.engine=FakeEmbedding()
    def tearDown(self):self.v.close();self.s.close();self.c.db.close();self.tmp.cleanup()
    def add(self,records):
        with self.log.open('a') as f:f.write(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in records))
        self.c.scan(self.log,source='workbuddy');self.s.advance(1000,realtime=True);self.s.repair_links(10)
    def user(self,text,session):return {'type':'message','role':'user','sessionId':session,'content':text}
    def test_local_vectors_batch_restart_identity_filter_and_raw_unchanged(self):
        self.add([self.user('实现手机号登录','a'),self.user('生成运营日报','b')]);raw=self.s.db.execute('SELECT * FROM events').fetchall();cursor=self.s.db.execute('SELECT * FROM task_cursor').fetchall()
        self.assertEqual(self.v.sync(self.s.db,self.engine,limit=1,force=True),1);self.assertEqual(self.v.status()['readyTasks'],1)
        self.v.sync(self.s.db,self.engine);found=self.v.search([1.,0.,0.],self.engine.identity,source='workbuddy');self.assertIn('手机号',found[0]['text'])
        self.assertEqual(self.v.search([1.,0.,0.],'different-model'),[])
        self.v.close();self.v=EmbeddingStore(self.root/'embeddings.db');self.assertEqual(self.v.sync(self.s.db,self.engine,force=True),0)
        self.assertEqual(self.s.db.execute('SELECT * FROM events').fetchall(),raw);self.assertEqual(self.s.db.execute('SELECT * FROM task_cursor').fetchall(),cursor)
    def test_merge_and_inference_failure_keep_queue_and_remove_stale_task(self):
        self.add([self.user('实现手机号登录','s'),self.user('继续完善这份实现','s')]);ids=[r[0] for r in self.s.db.execute('SELECT t.id FROM tasks t JOIN events e ON e.id=t.id ORDER BY e.rowid')]
        with patch.object(self.engine,'encode',side_effect=RuntimeError('inference failed')):
            with self.assertRaises(RuntimeError):self.v.sync(self.s.db,self.engine,force=True)
        self.assertTrue(self.v.pending);self.v.sync(self.s.db,self.engine,limit=16)
        set_override(self.s.db,ids[1],ids[0]);self.v.sync(self.s.db,self.engine,force=True)
        self.assertEqual(self.v.status()['readyTasks'],1)
    def test_semantic_paraphrase_recall_keeps_exact_artifact_constraints(self):
        self.add([self.user('实现手机号登录','a'),self.user('生成运营日报','b')]);self.v.sync(self.s.db,self.engine,limit=16,force=True)
        lexical=KnowledgeIndex(self.root/'knowledge.db');lexical.sync(self.s.db,force=True)
        found=retrieve_candidates(self.s.db,{'terms':[]},'用移动号码验证身份的那次工作',index=lexical,embedder=self.engine,vectors=self.v)
        self.assertTrue(found);self.assertIn('手机号',found[0][1])
        self.assertEqual(select_task(self.s.db,{},'用移动号码验证身份的那次工作',[],found),(None,'choose_task'))
        self.assertEqual(retrieve_candidates(self.s.db,{'terms':[]},'unknown_canary.py 做过什么',index=lexical,embedder=self.engine,vectors=self.v),[])
        lexical.close()
    def test_quota_pauses_without_discarding_original_tasks(self):
        self.add([self.user('实现手机号登录','a')]);self.v.max_bytes=1
        self.assertEqual(self.v.sync(self.s.db,self.engine,force=True),0);self.assertTrue(self.v.status()['paused'])
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM tasks').fetchone()[0],1)

if __name__=='__main__':unittest.main()
