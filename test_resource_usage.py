import tempfile
import unittest
from pathlib import Path
from agentpair.tasks import TaskEngine
from agentpair.resource_usage import snapshot
from test_tasks import FakeBackend

class UsageTests(unittest.TestCase):
    def test_counts_messages_once_and_includes_decisions(self):
        with tempfile.TemporaryDirectory() as d:
            engine=TaskEngine(Path(d)/'test.db',FakeBackend(),start=False)
            try:
                task=engine.create('test','test')
                task['messages'].append({'role':'navigator','model':'generation','usage':{'prompt_tokens':10,'completion_tokens':5},'answer':{'jev':{'status':'evaluated','model':'deepseek-v4-flash','usage':{'input_tokens':4,'output_tokens':2}}}})
                engine._save(task)
                data=snapshot(engine)
                self.assertEqual(data['tokens']['input'],14)
                self.assertEqual(data['tokens']['output'],7)
                self.assertEqual(data['tokens']['calls'],2)
                self.assertEqual(data['tasks'][0]['tokens'],21)
                self.assertIsNone(data['balanceCNY'])
                self.assertEqual(data['activeDrivers'],0)
            finally:engine.close()
