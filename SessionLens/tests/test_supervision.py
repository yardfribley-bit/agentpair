import json
from pathlib import Path
import tempfile
import unittest
from sessionlens.core import Collector
from sessionlens.supervision import TaskStore

class SupervisionTests(unittest.TestCase):
    def test_history_search_evidence_and_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'collector.db';log=Path(tmp)/'task.jsonl'
            records=[{'type':'message','role':'user','sessionId':'s','content':'查溧阳天气'},
                     {'type':'function_call','sessionId':'s','name':'web','callId':'c','arguments':{'query':'溧阳天气'}},
                     {'type':'function_call_result','sessionId':'s','callId':'c','output':'搜索来源：天气站'},
                     {'type':'message','role':'assistant','sessionId':'s','content':'按搜索结果整理天气'},
                     {'type':'message','role':'user','sessionId':'s','content':'改写方案'}]
            log.write_text(''.join(json.dumps(r)+'\n' for r in records));c=Collector(path);c.scan(log,source='workbuddy');c.db.close()
            store=TaskStore(path);self.assertEqual(store.advance(),5);tasks=store.tasks('天气站');self.assertEqual(len(tasks),1);steps=store.steps(tasks[0][0]);self.assertEqual(len(steps),4);self.assertEqual(steps[1][3],steps[2][3]);self.assertEqual(store.evidence(steps[2][0])['payload']['output'],'搜索来源：天气站')
            store.mark(steps[2][0]);store.close();store=TaskStore(path);self.assertTrue(store.marked(steps[2][0]));self.assertEqual(store.advance(),0);self.assertEqual(len(store.tasks()),2);self.assertEqual(len(store.tasks(live=True)),1);store.close()
    def test_codex_mirrored_prompt_does_not_split_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            log=Path(tmp)/'rollout.jsonl';path=Path(tmp)/'db'
            log.write_text(''.join(json.dumps(r)+'\n' for r in [
                {'type':'session_meta','payload':{'id':'s'}},
                {'type':'event_msg','payload':{'type':'user_message','message':'检查文件'}},
                {'type':'response_item','payload':{'type':'message','role':'user','content':[{'type':'input_text','text':'检查文件'}]}},
                {'type':'response_item','payload':{'type':'function_call','name':'read','call_id':'c','arguments':'a.txt'}},
                {'type':'event_msg','payload':{'type':'task_complete'}}]))
            c=Collector(path);c.scan(log);c.db.close();store=TaskStore(path);store.advance();self.assertEqual(len(store.tasks()),1);self.assertEqual(store.tasks()[0][5],'已记录结束');self.assertEqual(len(store.steps(store.tasks()[0][0])),3);store.close()
    def test_workbuddy_wrapped_prompt_search_and_background_not_a_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            log=Path(tmp)/'task.jsonl';path=Path(tmp)/'db'
            records=[{'type':'message','sessionId':'wb','role':'user','content':[{'type':'text','text':'<system-reminder>'+'背景'*2000+'</system-reminder>\n<user_query>上海天气</user_query>'}]},
                     {'type':'message','sessionId':'wb','role':'user','content':'<cb_summary>过去聊过天气</cb_summary>'},
                     {'type':'function_call','sessionId':'wb','callId':'c','name':'WebSearch','arguments':{'query':'上海的天气'}}]
            log.write_text(''.join(json.dumps(r)+'\n' for r in records));c=Collector(path);c.scan(log,source='workbuddy');c.db.close();store=TaskStore(path);store.advance();tasks=store.tasks('查上海天气','workbuddy');self.assertEqual(len(tasks),1);self.assertEqual(tasks[0][3],'上海天气');self.assertEqual(len(store.steps(tasks[0][0])),2);store.close()
    def test_new_task_priority_during_slow_history_and_latest_steps(self):
        with tempfile.TemporaryDirectory() as tmp:
            log=Path(tmp)/'task.jsonl';path=Path(tmp)/'db'
            log.write_text(json.dumps({'type':'message','sessionId':'old','role':'user','content':'老任务'})+'\n');c=Collector(path);c.scan(log,source='workbuddy');store=TaskStore(path)
            with log.open('a') as f:
                for record in [{'type':'message','sessionId':'new','role':'user','content':'新任务'},{'type':'function_call','sessionId':'new','callId':'c','name':'Read','arguments':'a.txt'},{'type':'function_call_result','sessionId':'new','callId':'c','output':'文件内容'}]:f.write(json.dumps(record)+'\n')
            c.scan(log,source='workbuddy');store.advance(realtime=True);self.assertEqual(store.tasks(live=True)[0][3],'新任务');new=store.tasks(live=True)[0][0];self.assertEqual(store.steps(new,1,latest=True)[0][1],'工具返回');store.advance();self.assertEqual(store.tasks(live=True)[0][3],'新任务');store.close();c.db.close()
    def test_notifications_and_markdown_summary_stay_background(self):
        from sessionlens.supervision import readable
        for value in ('<task-notification>后台完成</task-notification>', '# 对话历史摘要\n\n<conversation_history_summary>以前的要求</conversation_history_summary>'):
            self.assertEqual(readable(value,user=True),'')
        self.assertEqual(readable('以下是文章，帮我写发布脚本',user=True),'以下是文章，帮我写发布脚本')
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'db';log=Path(tmp)/'task.jsonl'
            records=[{'type':'message','role':'user','sessionId':'s','content':v} for v in ('创建文件','<task-notification>后台完成</task-notification>','# 对话历史摘要\n<conversation_history_summary>以前的要求</conversation_history_summary>')]
            log.write_text(''.join(json.dumps(r)+'\n' for r in records));c=Collector(path);c.scan(log,source='workbuddy');c.db.close();store=TaskStore(path);store.advance()
            self.assertEqual(len(store.tasks()),1)
            # Raw evidence remains intact, including the two background records.
            self.assertEqual(store.db.execute('SELECT count(*) FROM events').fetchone()[0],3);store.close()
