import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
from agentpair.cloud_console import CloudConsole


class TestRunConsoleTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.manager=Mock(directory=Path(self.tmp.name)/'leases')
        self.manager.release.return_value={'state':'released','releasedAt':'2026-10-11T00:00:00+00:00'}
        self.manager.api.call.return_value={'UHostSet':[]}
        self.console=CloudConsole(self.manager,{},'/not-a-real-key','/not-a-real-known-hosts')
        self.console._lease=Mock(return_value={'hostId':'exact-host-id'})
        self.console.login=Mock(return_value={'platform':'windows','loginState':'ssh_authenticated','address':'203.0.113.9'})

    def test_release_independently_checks_exact_host(self):
        result=self.console.release('a'*24)
        self.manager.release.assert_called_once_with('a'*24)
        self.manager.api.call.assert_called_once_with('DescribeUHostInstance',**{'UHostIds.0':'exact-host-id'})
        self.assertEqual(result['remainingHostCount'],0)
        self.manager.api.call.return_value={'UHostSet':[{'UHostId':'exact-host-id'}]}
        with self.assertRaises(RuntimeError):self.console.release('a'*24)

    def test_cpu_probe_uses_fixed_script_and_authenticated_target(self):
        with patch('agentpair.cloud_console.subprocess.run',return_value=SimpleNamespace(returncode=0,stdout=json.dumps({'samples':[],'observedProcessCount':0}))) as run:
            result=self.console.test_process_cpu('a'*24)
        self.assertEqual(result['exitCode'],0)
        self.assertEqual(result['metrics']['observedProcessCount'],0)
        self.assertIn('Start-Sleep -Seconds 2',result['commandSource'])
        self.assertIn('Administrator@203.0.113.9',run.call_args.args[0])
        self.assertEqual(run.call_args.kwargs['timeout'],30)
        self.console.login.return_value['loginState']='ssh_pending'
        with self.assertRaises(ValueError):self.console.test_process_cpu('a'*24)

    def test_cpu_failure_does_not_expose_raw_diagnostics(self):
        with patch('agentpair.cloud_console.subprocess.run',return_value=SimpleNamespace(returncode=1,stdout='private-password',stderr='private-token')):
            with self.assertRaises(RuntimeError) as error:self.console.test_process_cpu('a'*24)
        self.assertNotIn('private',str(error.exception))
