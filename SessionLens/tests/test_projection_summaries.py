import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from sessionlens.core import Collector
from sessionlens.supervision import TaskStore,event_text


class ProjectionSummaryTests(unittest.TestCase):
    def test_large_tool_parameters_keep_full_evidence_but_bound_derived_excerpt(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'db';source=Path(tmp)/'rollout.jsonl'
            value='完整工具参数'*5000
            source.write_text('\n'.join(json.dumps(r,ensure_ascii=False) for r in [
                {'type':'session_meta','payload':{'id':'summary-session'}},
                {'type':'response_item','payload':{'type':'message','role':'user','content':[{'text':'生成一个视频'}]}},
                {'type':'response_item','payload':{'type':'function_call','name':'VideoGen','call_id':'call1','arguments':json.dumps({'prompt':value},ensure_ascii=False)}}
            ])+'\n')
            c=Collector(path);c.scan(source);c.db.close();store=TaskStore(path);store.advance()
            identity,excerpt=store.db.execute("SELECT event,excerpt FROM task_steps WHERE kind='工具调用'").fetchone()
            self.assertEqual(len(excerpt),16000)
            self.assertEqual(json.loads(store.evidence(identity)['payload']['arguments'])['prompt'],value)
            external=sqlite3.connect(path,timeout=.1);external.execute('CREATE TABLE probe(value INTEGER)');external.commit()
            checked=[]
            def decode(event):
                with external:external.execute('INSERT INTO probe VALUES(1)')
                checked.append(True)
                return event_text(event)
            with patch('sessionlens.supervision.event_text',side_effect=decode):store.repair_excerpts()
            self.assertTrue(checked)
            self.assertEqual(len(store.db.execute('SELECT excerpt FROM task_steps WHERE event=?',(identity,)).fetchone()[0]),16000)
            self.assertEqual(json.loads(store.evidence(identity)['payload']['arguments'])['prompt'],value)
            external.close();store.close()


if __name__=='__main__':unittest.main()
