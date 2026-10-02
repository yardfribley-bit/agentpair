import importlib.util
from pathlib import Path
import tempfile
import unittest

spec=importlib.util.spec_from_file_location('hook',Path(__file__).parent/'macos/workbuddy_hook.py')
hook=importlib.util.module_from_spec(spec);spec.loader.exec_module(hook)

class HookTests(unittest.TestCase):
    def test_install_preserves_existing_and_idempotent(self):
        import json
        s=importlib.util.spec_from_file_location('installer',Path(__file__).parent/'macos/install_workbuddy_hooks.py')
        installer=importlib.util.module_from_spec(s);s.loader.exec_module(installer)
        with tempfile.TemporaryDirectory() as d:
            settings=Path(d)/'settings.json';settings.write_text(json.dumps({'other':True,'hooks':{'Stop':[{'hooks':[{'command':'existing'}]}]}}))
            installer.install(settings,Path(d)/'hook.py');installer.install(settings,Path(d)/'hook.py')
            data=json.loads(settings.read_text())
            self.assertTrue(data['other']);self.assertEqual(len(data['hooks']['Stop']),2)
            self.assertEqual(data['hooks']['Stop'][0]['hooks'][0]['command'],'existing')
    def test_content_redacted_and_correlated(self):
        with tempfile.TemporaryDirectory() as d:
            result=hook.record({'session_id':'s','hook_event_name':'PreToolUse','tool_use_id':'t1',
                'tool_name':'Read','tool_input':{'path':'/tmp/a','api_key':'secret'},
                'prompt':'Bearer abc123 sk-abcdefghijklmnop'},Path(d))
            self.assertEqual(result['toolCallId'],'t1')
            self.assertEqual(result['evidence']['tool_input']['api_key'],'[REDACTED]')
            self.assertNotIn('abc123',str(result))
            self.assertEqual(len(list(Path(d).glob('*.json'))),1)
    def test_invalid_event(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError):hook.record({'session_id':'s','hook_event_name':'unknown'},Path(d))
