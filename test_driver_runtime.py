import tempfile
import unittest
from pathlib import Path
from agentpair.tool_registry import native_tools
from agentpair.driver_runtime import execute


class RuntimeTests(unittest.TestCase):
    def test_completion_requires_actual_delivery(self):
        sequence=iter([{'answer':'I will write it','status':'completed'},
                       {'tool':'files.write','arguments':{'pathname':'result.md','content':'Verified result'}},
                       {'answer':'Created result.md','status':'completed'}])
        with tempfile.TemporaryDirectory() as root:
            out=execute({'task':{}},'unused',native_tools(Path(root)/'workspace'),root,
                        lambda *a:(next(sequence),{}))
            self.assertEqual(out['answer']['artifacts'][0]['path'],'result.md')
            self.assertEqual(len(out['evidence']['steps']),1)

    def test_tools(self):
        with tempfile.TemporaryDirectory() as root:
            r=native_tools(root)
            r.call('files.write',{'pathname':'src/test.txt','content':'hello'})
            self.assertEqual(r.call('files.read',{'pathname':'src/test.txt'})['text'],'hello')
            for args in ({'pathname':'../outside'},{'pathname':1}):
                with self.assertRaises(ValueError):r.call('files.read',args)
            with self.assertRaises(ValueError):r.call('terminal.run',{'command':'true'})

    def test_native_shell(self):
        with tempfile.TemporaryDirectory() as root:
            r=native_tools(root,True)
            out=r.call('terminal.run',{'command':'printf runtime_ok'})
            self.assertEqual(out['exitCode'],0);self.assertEqual(out['output'],'runtime_ok')

    def test_loop_and_checkpoint(self):
        sequence=iter([{'tool':'files.write','arguments':{'pathname':'answer.txt','content':'42'}},
                       {'tool':'files.read','arguments':{'pathname':'answer.txt'}},
                       {'answer':'Created answer.txt, verified 42','status':'completed'}])
        with tempfile.TemporaryDirectory() as root:
            out=execute({'task':{}},'unused',native_tools(Path(root)/'work'),root,
                        lambda *a:(next(sequence),{'total_tokens':3}))
            self.assertTrue(out['evidence']['complete']);self.assertEqual(out['usage']['total_tokens'],9)
            self.assertTrue((Path(root)/'checkpoint.json').exists())
