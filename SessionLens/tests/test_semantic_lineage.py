import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from sessionlens.core import Collector
from sessionlens.supervision import TaskStore
from sessionlens.semantic_lineage import validate,refine,context_for_task
from sessionlens.task_lineage import history,set_override,resolve
from sessionlens.task_presentation import project


class SemanticLineageTests(unittest.TestCase):
    def link(self,ident,parent=None,role='request',status='supported'):
        return {'turnId':ident,'parentTurnId':parent,'relation':role,'status':status,'reason':'结合前后方案确认需求归属','evidenceTurnIds':[ident]+([parent] if parent else [])}

    def test_rejects_future_unknown_duplicate_and_incomplete_relationships(self):
        turns=[{'turnId':'a'},{'turnId':'b'}]
        valid={'links':[self.link('a'),self.link('b','a','approval')]}
        self.assertEqual(len(validate(valid,turns)),2)
        invalid=[{'links':[self.link('a','b','revision'),self.link('b')]},
                 {'links':[self.link('a'),self.link('b','unknown','approval')]},
                 {'links':[self.link('a'),self.link('a')]},
                 {'links':[self.link('a')]},
                 {'links':[self.link('a'),self.link('b','a','approval','ambiguous')]}]
        for value in invalid:
            with self.assertRaises(ValueError):validate(value,turns)

    def test_model_semantics_links_unlisted_expressions_and_caches_locally(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);path=root/'collector.db';log=root/'log.jsonl'
            records=[{'type':'message','role':'user','sessionId':'s','content':s} for s in ('设计会员管理页面','采用第二种，邮箱登录','这版顺眼了，可以动手了')]
            records.append({'type':'function_call','sessionId':'s','name':'Write','callId':'c','arguments':{'path':'members.py'}})
            log.write_text(''.join(json.dumps(r)+'\n' for r in records));collector=Collector(path);collector.scan(log,source='workbuddy');collector.db.close();store=TaskStore(path);store.advance()
            turns=context_for_task(store.db,store.tasks()[0][0]);ids=[t['turnId'] for t in turns]
            origin=store.db.execute("SELECT id FROM tasks WHERE prompt='设计会员管理页面'").fetchone()[0]
            self.assertEqual(len(context_for_task(store.db,origin)),3)
            output={'links':[self.link('T001'),self.link('T002','T001','approval'),self.link('T003','T002','execution')]}
            config={'name':'test','url':'https://example.com'}
            with patch('sessionlens.relay_model.call',return_value=output) as call:
                task=refine(store.db,config,ids[2],'会员页面怎么做的')
                self.assertEqual(task,ids[0]);self.assertEqual(len(store.tasks()),1)
                self.assertEqual(history(store.db,task)[-1]['association'],'semantic_inference')
                self.assertEqual(project(store.db,task)['calls'][0]['requirement']['approvalEvent'],ids[2])
                # Same bounded context/cache signature reuses the result.
                refine(store.db,config,task,'为什么选邮箱登录？');self.assertEqual(call.call_count,1)
            store.close()

    def test_manual_choice_has_priority_over_model_and_scope_stays_local(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);path=root/'collector.db';log=root/'log.jsonl'
            records=[{'type':'message','role':'user','sessionId':'s','content':s} for s in ('设计库存工具','使用后一种','可以落地了')]
            records.append({'type':'message','role':'user','sessionId':'other','content':'另一会话的私有内容'})
            log.write_text(''.join(json.dumps(r)+'\n' for r in records));collector=Collector(path);collector.scan(log,source='workbuddy');collector.db.close();store=TaskStore(path);store.advance();store.repair_links(2)
            ids=[r[0] for r in store.db.execute("SELECT t.id FROM tasks t JOIN events e ON t.id=e.id WHERE t.session='s' ORDER BY e.rowid")]
            set_override(store.db,ids[1],None)
            turns=context_for_task(store.db,ids[2]);self.assertNotIn('另一会话的私有内容',str(turns))
            output={'links':[self.link('T001'),self.link('T002','T001','approval'),self.link('T003','T002','execution')]}
            with patch('sessionlens.relay_model.call',return_value=output):refine(store.db,{'name':'test','url':'https://example.com'},ids[2],'库存工具')
            self.assertEqual(resolve(store.db,ids[1]),ids[1]);self.assertEqual(resolve(store.db,ids[2]),ids[1]);store.close()
