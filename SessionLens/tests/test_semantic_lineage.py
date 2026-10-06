import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from sessionlens.core import Collector
from sessionlens.supervision import TaskStore
from sessionlens.semantic_lineage import validate,refine,context_for_task
from sessionlens.task_lineage import history,set_override,resolve,dialogues
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
            log.write_text(''.join(json.dumps(r)+'\n' for r in records),encoding='utf-8');collector=Collector(path);collector.scan(log,source='workbuddy');collector.db.close();store=TaskStore(path);store.advance()
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
            log.write_text(''.join(json.dumps(r)+'\n' for r in records),encoding='utf-8');collector=Collector(path);collector.scan(log,source='workbuddy');collector.db.close();store=TaskStore(path);store.advance();store.repair_links(2)
            ids=[r[0] for r in store.db.execute("SELECT t.id FROM tasks t JOIN events e ON t.id=e.id WHERE t.session='s' ORDER BY e.rowid")]
            set_override(store.db,ids[1],None)
            turns=context_for_task(store.db,ids[2]);self.assertNotIn('另一会话的私有内容',str(turns))
            output={'links':[self.link('T001'),self.link('T002','T001','approval'),self.link('T003','T002','execution')]}
            with patch('sessionlens.relay_model.call',return_value=output):refine(store.db,{'name':'test','url':'https://example.com'},ids[2],'库存工具')
            self.assertEqual(resolve(store.db,ids[1]),ids[1]);self.assertEqual(resolve(store.db,ids[2]),ids[1]);store.close()

    def test_reanalysis_retry_links_user_rounds_without_absorbing_weather(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);log=root/'log.jsonl';records=[]
            def user(text):records.append({'type':'message','role':'user','sessionId':'s','content':text})
            def reply(text):records.append({'type':'message','role':'assistant','sessionId':'s','content':text})
            user('分析接入方案文档');reply('已给出方案概览与优缺点。')
            user('还是那份文档，重新看一遍');reply('回到刚才的文档，重新提取全文。')
            user('再分析一下');records.append({'type':'reasoning','sessionId':'s','content':'承接文档分析，深化实现风险。'})
            reply('Interrupted by user')
            user('再分析一下');reply('从上轮中断处继续，文件已经移到另一目录。')
            records.append({'type':'function_call','sessionId':'s','name':'Bash','callId':'read','arguments':{'command':'read analysis.docx'}})
            user('上海天气');reply('查上海天气，是另一个目标。')
            records.append({'type':'message','role':'user','sessionId':'other','content':'另一个会话'})
            log.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in records),encoding='utf-8');c=Collector(root/'collector.db');c.scan(log,source='workbuddy');c.db.close();store=TaskStore(root/'collector.db');store.advance();store.repair_links(4)
            ids=[r[0] for r in store.db.execute("SELECT t.id FROM tasks t JOIN events e ON e.id=t.id WHERE t.session='s' ORDER BY e.rowid")]
            untouched=store.db.execute('SELECT * FROM cursors').fetchall();raw=store.db.execute('SELECT id,event FROM events ORDER BY rowid').fetchall()
            links=[self.link('T001'),self.link('T002','T001','resume'),self.link('T003','T002','revision'),self.link('T004','T003','resume'),self.link('T005')]
            with patch('sessionlens.relay_model.call',return_value={'links':links}) as model:
                task=refine(store.db,{'name':'test','url':'https://example.com'},ids[2],'核对多轮对话',turn_ids=ids)
                self.assertEqual(len(model.call_args.args[2]['turns']),5)
            self.assertEqual([resolve(store.db,x) for x in ids],[ids[0]]*4+[ids[4]])
            rounds=dialogues(store.db,task);self.assertEqual(len(rounds),4);self.assertTrue(rounds[2]['interrupted']);self.assertFalse(rounds[3]['interrupted'])
            compact=dialogues(store.db,task,limit=2);self.assertEqual([r['ordinal'] for r in compact],[1,4]);self.assertEqual(compact[0]['totalTurns'],4)
            self.assertEqual(rounds[2]['replies'][0]['text'],'Interrupted by user')
            self.assertTrue(all(r['association']=='semantic_inference' for r in rounds))
            self.assertEqual(project(store.db,task)['calls'][0]['requirement']['turnEvent'],ids[3])
            self.assertEqual(store.db.execute('SELECT * FROM cursors').fetchall(),untouched)
            self.assertEqual(store.db.execute('SELECT id,event FROM events ORDER BY rowid').fetchall(),raw)
            other=store.db.execute("SELECT id FROM tasks WHERE session='other'").fetchone()[0]
            for scope in ([ids[0],other],[ids[1]],ids+[ids[0]]):
                with self.assertRaises(ValueError):context_for_task(store.db,ids[0],scope)
            store.close()
