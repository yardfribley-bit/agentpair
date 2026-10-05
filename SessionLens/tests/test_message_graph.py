import json
from pathlib import Path
import tempfile
import unittest
from sessionlens.message_graph import build,reasoning_bindings
from sessionlens.core import Collector
from sessionlens.supervision import TaskStore
from sessionlens.semantic_lineage import context_for_task
from sessionlens.task_presentation import project
from sessionlens.assistant import packet_for_task


class MessageGraphTests(unittest.TestCase):
    def event(self,ident,kind,parent=None,call=None,source='workbuddy',session='s',seq=1,role=None):
        return {'id':'event-'+ident,'kind':kind,'role':role,'source':source,'sessionId':session,'callId':call,'_seq':seq,
                'payload':{'id':ident,'parentId':parent,'providerData':{'conversationRequestId':'request'}}}

    def test_parallel_calls_and_second_reasoning_have_source_links(self):
        events=[self.event('u','message',seq=1,role='user'),self.event('r','reasoning','u',seq=2),
                self.event('a','message','r',seq=3,role='assistant'),
                self.event('c1','tool_call','a','one',seq=4),self.event('c2','tool_call','r','two',seq=5),
                self.event('o1','tool_result','c1','one',seq=6),self.event('o2','tool_result','o1','two',seq=7),
                self.event('r2','reasoning','o2',seq=8),self.event('c3','tool_call','r2','three',seq=9)]
        graph=build(events);links=reasoning_bindings(graph)
        self.assertEqual(links['event-c1']['reasoningEvent'],'event-r')
        self.assertEqual(links['event-c2']['reasoningEvent'],'event-r')
        self.assertEqual(links['event-c3']['reasoningEvent'],'event-r2')
        self.assertTrue(any(e['from']=='event-c2' and e['to']=='event-o2' and e['relation']=='call_result' for e in graph['edges']))
        self.assertFalse(graph['gaps'])

    def test_parent_chain_does_not_carry_reasoning_across_new_user_goal(self):
        graph=build([self.event('r','reasoning',seq=1),self.event('u','message','r',seq=2,role='user'),self.event('c','tool_call','u','x',seq=3)])
        self.assertFalse(reasoning_bindings(graph))

    def test_scope_duplicate_and_future_parent_are_not_guessed(self):
        cross=self.event('r','reasoning',seq=1,session='other')
        call=self.event('c','tool_call','r','x',seq=3)
        self.assertFalse(reasoning_bindings(build([cross,call])))
        duplicate=self.event('r','reasoning',seq=2);duplicate['id']='duplicate-event'
        graph=build([self.event('r','reasoning',seq=1),duplicate,call])
        self.assertFalse(reasoning_bindings(graph));self.assertEqual(graph['gaps'][0]['reason'],'ambiguous_parent')
        graph=build([call,self.event('r','reasoning',seq=4)])
        self.assertEqual(graph['gaps'][0]['reason'],'non_earlier_parent')

    def test_reused_call_id_does_not_associate_result_with_arbitrary_call(self):
        graph=build([self.event('a','tool_call',call='x',seq=1),self.event('b','tool_call',call='x',seq=2),self.event('out','tool_result',call='x',seq=3)])
        self.assertFalse(graph['edges']);self.assertEqual(graph['gaps'][0]['reason'],'ambiguous_call')

    def test_context_presentation_and_answer_packet_use_same_source_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);log=root/'log.jsonl';dbpath=root/'collector.db'
            def record(ident,typ,parent=None,**fields):
                return {'id':ident,'type':typ,'parentId':parent,'sessionId':'s','providerData':{'conversationRequestId':'q'},**fields}
            records=[record('u','message',role='user',content='生成五秒 SSH 动画'),
                     record('r','reasoning','u',rawContent=[{'type':'reasoning_text','text':'先找生成工具，再准备 SSH 提示词。'}]),
                     record('c1','function_call','r',callId='a',name='ToolSearch',arguments={'queries':['VideoGen']}),
                     record('c2','function_call','r',callId='b',name='Read',arguments={'path':'notes.md'}),
                     record('o1','function_call_result','c1',callId='a',output='工具说明'),
                     record('o2','function_call_result','o1',callId='b',output='协议说明'),
                     record('r2','reasoning','o2',content='现在生成视频。'),
                     record('c3','function_call','r2',callId='c',name='VideoGen',arguments={'prompt':'SSH five seconds'}),
                     record('o3','function_call_result','c3',callId='c',output='视频生成完成'),
                     record('u2','message','o3',role='user',content='查上海天气'),
                     record('c4','function_call','u2',callId='d',name='WebSearch',arguments={'query':'上海天气'})]
            log.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in records))
            collector=Collector(dbpath);collector.scan(log,source='workbuddy');collector.db.close()
            store=TaskStore(dbpath);store.advance();task=store.db.execute("SELECT id FROM tasks WHERE prompt='生成五秒 SSH 动画'").fetchone()[0]
            turns=context_for_task(store.db,task)
            self.assertEqual(len(turns),2)
            self.assertTrue(any('先找生成工具' in r['text'] for r in turns[0]['records']))
            self.assertTrue(any(r['parentRecordId'] for r in turns[0]['records']))
            self.assertTrue(any(r['callRecordId'] for r in turns[0]['records']))
            view=project(store.db,task)
            self.assertEqual(len(view['frames'][0]['callIds']),2)
            self.assertTrue(all(c['decisionLink']['basis']=='source_parent_path' for c in view['calls']))
            weather=store.db.execute("SELECT id FROM tasks WHERE prompt='查上海天气'").fetchone()[0]
            self.assertIsNone(project(store.db,weather)['calls'][0]['decisionLink'])
            self.assertEqual(len(store.tasks()),2)
            packet=packet_for_task(store.db,task)
            self.assertEqual(len(packet['reasoningLinks']),3)
            self.assertTrue(packet['messageRelations'])
            self.assertEqual(packet['version'],6)
            self.assertTrue(all(x['reasoningRef'].startswith('E') for x in packet['reasoningLinks']))
            store.close()

    def test_missing_source_metadata_is_explicit_order_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);path=root/'collector.db';log=root/'log.jsonl'
            records=[{'type':'message','role':'user','content':'读文件','sessionId':'s'},
                     {'type':'reasoning','content':'先读文档','sessionId':'s'},
                     {'type':'function_call','name':'Read','callId':'c','arguments':{'path':'a.md'},'sessionId':'s'}]
            log.write_text(''.join(json.dumps(r)+'\n' for r in records));collector=Collector(path);collector.scan(log,source='workbuddy');collector.db.close()
            store=TaskStore(path);store.advance();task=store.tasks()[0][0]
            self.assertEqual(project(store.db,task)['calls'][0]['decisionLink']['basis'],'sequence_candidate')
            self.assertFalse(packet_for_task(store.db,task)['reasoningLinks']);store.close()
