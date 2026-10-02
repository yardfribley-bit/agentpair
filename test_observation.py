import tempfile
import unittest
from pathlib import Path
from agentpair.tasks import TaskEngine
from agentpair.transport import NodeBackend
from agentpair.collaboration import message
from agentpair.driver_runtime import execute
from test_tasks import FakeBackend


class ObservationTests(unittest.TestCase):
    def test_full_message_persisted_before_backend_call(self):
        with tempfile.TemporaryDirectory() as directory:
            backend=FakeBackend()
            engine=TaskEngine(Path(directory)/'tasks.db',backend,start=False)
            text='Long task '+('x'*1200)
            task=engine.create('test',text)
            backend.on_call=lambda:self.assertTrue(engine.get(task['id'])['events'][-1]['message']['content'])
            engine.process(task['id'])
            first=backend.calls[0][1]['handoff']
            self.assertEqual(first['content']['request'],text)
            self.assertEqual(len(first['summary']),500)
            engine.close()

    def test_branch_event_identity_and_correlation(self):
        backend=NodeBackend('secret','host','key','known');received=[]
        backend.event_callback=lambda tid,event:received.append(event)
        env={'mode':'driver','handoff':{'id':'stage'},'task':{'id':'task','branch':'Driver Z','collaborationMessage':{'id':'peer'}}}
        backend.observe(env,'driver',{'kind':'tool_started','text':'Running tool'})
        self.assertEqual(received[0]['role'],'Driver Z')
        self.assertEqual(received[0]['messageId'],'peer')
        self.assertNotIn('secret',str(received))

    def test_tool_start_emitted_before_execution(self):
        events=[]
        class Registry:
            def manifest(self):return []
            def call(self,name,args):
                self_observer.assertEqual(events[-1]['kind'],'tool_started')
                return {'ok':True}
        self_observer=self
        decisions=iter([({'tool':'files.write','arguments':{'path':'result.md','content':'ok'}},{}),({'answer':'done','status':'completed'},{})])
        with tempfile.TemporaryDirectory() as directory:
            execute({'task':{}},'test',Registry(),directory,decide=lambda *args:next(decisions),observe=events.append)
        self.assertEqual(events[2]['kind'],'tool_started')

if __name__=='__main__':unittest.main()
