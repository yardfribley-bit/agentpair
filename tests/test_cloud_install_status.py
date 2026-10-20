"""Remote installer receipts must survive nonzero SSH exit codes."""
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from tests.test_cloud_console import CloudConsoleTests


class CloudInstallStatusTests(unittest.TestCase):
    setUp = CloudConsoleTests.setUp
    def run_receipt(self, receipt, code=1):
        operation={'id':'a'*24,'leaseId':'b'*24,'state':'queued','functionVerified':False}
        access={'loginState':'ssh_authenticated','address':'192.0.2.1'}
        recipe={'id':'test','name':'Test','platform':'Windows','version':'1','sha256':'a'*64}
        stdout=json.dumps(receipt) if receipt else 'unstructured SSH failure'
        results=[SimpleNamespace(returncode=0),SimpleNamespace(returncode=code,stdout=stdout)]
        with patch.object(self.console,'login',return_value=access), patch('agentpair.cloud_console.subprocess.run',side_effect=results):
            self.console._install_worker(operation,access,recipe)
        return self.console.operation(operation['id'])

    def test_preserves_successful_install_with_unverified_version(self):
        receipt={'state':'verification_required','stage':'verify','summary':'名称不匹配',
                 'evidence':{'installCompleted':True,'verified':False,'expectedDisplayName':'Test 1'}}
        op=self.run_receipt(receipt)
        self.assertEqual(op['state'],'verification_required')
        self.assertEqual(op['summary'],'名称不匹配')
        self.assertTrue(op['evidence']['installCompleted'])
        self.assertFalse(op['functionVerified'])

    def test_preserves_installer_failure_reason(self):
        op=self.run_receipt({'state':'failed','summary':'Installer SHA256 mismatch; not executed'})
        self.assertIn('SHA256 mismatch',op['summary'])
        self.assertEqual(op['state'],'failed')

    def test_missing_receipt_requires_reconciliation(self):
        self.assertEqual(self.run_receipt(None)['state'],'interrupted')

    def test_captured_powershell_progress_array_preserves_final_receipt(self):
        receipt={'state':'verification_required','stage':'verify','summary':'Installed; version needs reconciliation',
                 'evidence':{'installCompleted':True,'verified':False}}
        op=self.run_receipt([json.dumps({'state':'running','stage':'download'}),receipt])
        self.assertEqual(op['state'],'verification_required')
        self.assertTrue(op['evidence']['installCompleted'])
        self.assertEqual(op['transport']['parsedRecordCount'],2)

    def test_array_does_not_bypass_installer_verification(self):
        op=self.run_receipt(['progress',{'state':'completed','evidence':{'verified':True}}],code=0)
        self.assertEqual(op['state'],'failed')

    def test_invalid_success_receipt_is_not_accepted(self):
        op=self.run_receipt({'state':'completed','evidence':{'verified':True}},code=0)
        self.assertEqual(op['state'],'failed')

    def test_conflicting_exit_code_is_not_success(self):
        self.assertEqual(self.run_receipt({'state':'completed','evidence':{}})['state'],'interrupted')
