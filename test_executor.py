import tempfile
import io
from types import SimpleNamespace
import unittest
from pathlib import Path
from unittest.mock import patch
from agentpair.executor import validate_edits, execute
from agentpair.tasks import TaskEngine
from agentpair.decisions import decide
from test_tasks import FakeBackend


class ExecutorTests(unittest.TestCase):
    def test_edit_paths(self):
        for path in ('../x','/etc/passwd','.git/config','a/../../x','a\\x'):
            with self.assertRaises(ValueError):validate_edits([{'path':path,'content':'x'}])
        validate_edits([{'path':'src/main.py','content':'print(1)'}])

    def test_navigator_cannot_execute(self):
        with patch('agentpair.executor.Path.read_text',return_value='navigator'):
            with self.assertRaises(RuntimeError):execute({}, [], 'python')

    def test_execution_requires_explicit_cloud_method(self):
        with tempfile.TemporaryDirectory() as root:
            engine=TaskEngine(Path(root)/'tasks.db',FakeBackend(),start=False)
            for method in ('local','parallel'):
                with self.assertRaises(ValueError):engine.create('task','goal',engineering_method=method,execution_profile='python')
            task=engine.create('task','goal',engineering_method='pair',execution_profile='python')
            engine.process(task['id'])
            self.assertEqual(engine.backend.calls[0][1]['task']['executionProfile'],'python')
            engine.close()

    def test_failed_execution_blocks_delivery(self):
        answer={'finalAnswer':'构建测试已经成功，交付代码。','executionValidated':False,
                'checks':{k:{'value':'yes','reason':'model claim'} for k in ('goal_met','grounded','consistent','delivery')}}
        self.assertNotEqual(decide(answer)['action'],'deliver')

    def test_container_boundary_and_result(self):
        sha='a'*40; calls=[]
        original=Path.read_text
        def read(path,*args,**kwargs):
            return 'isolated-driver' if str(path)=='/etc/agentpair-driver' else original(path,*args,**kwargs)
        def run(args,**kwargs):
            if args[0]=='git' and 'init' in args:(Path(kwargs['cwd'])/'.git').mkdir()
            return SimpleNamespace(stdout=(sha+'\n').encode(),returncode=0)
        def popen(args,**kwargs):
            calls.append(args)
            return SimpleNamespace(stdout=io.BytesIO(b'Ran 1 tests\nOK\n'),wait=lambda **kw:0)
        with patch.object(Path,'read_text',read),patch('agentpair.executor.subprocess.run',side_effect=run),patch('agentpair.executor.subprocess.Popen',side_effect=popen):
            result=execute({'sourceUrl':'https://github.com/o/r','commit':sha},[{'path':'app.py','content':'print(1)\n'}],'python')
        self.assertEqual(result['status'],'passed')
        self.assertEqual(len(calls),2)
        for args in calls:
            self.assertIn('--read-only',args)
            self.assertEqual(args[args.index('--network')+1],'none')
            self.assertTrue(args[args.index('--mount')+1].endswith(',readonly'))
            self.assertNotIn('/var/run/docker.sock',' '.join(args))
