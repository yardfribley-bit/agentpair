import datetime
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from agentpair.cloud_console import CloudConsole
from agentpair.resources import ResourceManager


class FakeCloud:
    region='cn-bj2'
    project_id='org-test'
    def __init__(self):self.calls=[];self.fail_create=False
    def call(self,action,**values):
        self.calls.append((action,values))
        if action=='GetUHostInstancePrice':return {'PriceSet':[{'ChargeType':'Dynamic','Price':0.40}]}
        if action=='GetEIPPrice':return {'PriceSet':[{'ChargeType':'Dynamic','Price':0.11}]}
        if action=='CreateFirewall':return {'FWId':'firewall-test'}
        if action=='DescribeImage':return {'ImageSet':[{'ImageName':'Ubuntu 22.04 64位','ImageId':'image-ubuntu'}]}
        if action=='CreateUHostInstance':
            if self.fail_create:raise RuntimeError('cloud create failed')
            return {'UHostIds':['uhost-test']}
        raise AssertionError(action)


class CloudConsoleTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.api=FakeCloud()
        self.manager=ResourceManager(self.api,Path(self.tmp.name)/'leases',clock=lambda:datetime.datetime(2026,10,1,tzinfo=datetime.timezone.utc))
        self.console=CloudConsole(self.manager,{'sshPublicKey':'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIJhFVAkNKKSKPHai7VefyYV9dKBrCyQxFMq9SdJXtE4k test','firewallId':'firewall-shared'},'/not-used','/not-used',reaper_ready=lambda:True)

    def test_quote_is_read_only_and_create_requires_current_price(self):
        price=self.console.quote('Windows')
        self.assertAlmostEqual(price['hourlyCNY'],0.51)
        self.assertFalse(any(action.startswith('Create') for action,_ in self.api.calls))
        with self.assertRaises(ValueError):self.console.create('Windows',0.50,'request-price-too-low')
        self.assertFalse(any(action.startswith('Create') for action,_ in self.api.calls))
        lease=self.console.create('Windows',0.51,'request-valid-price')
        self.assertEqual(lease,self.console.create('Windows',0.51,'request-valid-price'))
        self.assertEqual(sum(action=='CreateUHostInstance' for action,_ in self.api.calls),1)
        self.assertEqual(lease['hostId'],'uhost-test')
        self.assertNotIn('password',lease)
        firewall=next(values for action,values in self.api.calls if action=='CreateFirewall')
        self.assertIn('0.0.0.0/0',firewall['Rule.0'])
        self.assertTrue((Path(self.tmp.name)/('windows-trial-'+lease['id']+'.private.json')).is_file())

    def test_ambiguous_cloud_failure_keeps_reconciliation_record(self):
        self.api.fail_create=True
        with self.assertRaises(RuntimeError):self.console.create('Windows',0.51,'request-create-failure')
        rows=self.manager.records()
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['state'],'reconcile_required')
        self.assertEqual(rows[0]['dedicatedFirewallId'],'firewall-test')

    def test_linux_quote_uses_ubuntu_and_omits_creation_fields(self):
        result=self.console.quote('Linux')
        self.assertEqual(result['system'],'Linux')
        request=next(v for a,v in self.api.calls if a=='GetUHostInstancePrice')
        self.assertNotIn('SecurityGroupId',request)
        self.assertFalse(any(a.startswith('Create') for a,_ in self.api.calls))

    def test_cleanup_unavailable_prevents_creation(self):
        self.console.reaper_ready=lambda:False
        with self.assertRaises(RuntimeError):self.console.create('Windows',1,'request-no-cleanup')
        self.assertEqual(self.api.calls,[])

    def test_custom_sizing_quote_and_creation_match(self):
        sizing={'cpu':4,'memoryGB':8,'systemDiskGB':60,'dataDiskGB':100}
        result=self.console.quote('Windows',sizing)
        self.assertEqual(result['sizing'],sizing)
        self.console.create('Windows',0.51,'request-custom-sizing',sizing)
        request=next(v for a,v in self.api.calls if a=='CreateUHostInstance')
        self.assertEqual((request['CPU'],request['Memory'],request['Disks.0.Size'],request['Disks.1.Size']),(4,8192,60,100))
        with self.assertRaises(ValueError):self.console.create('Windows',0.51,'request-custom-sizing',dict(sizing,cpu=2))

    def test_invalid_sizing_never_creates(self):
        for sizing in [{'cpu':0},{'memoryGB':True},{'systemDiskGB':10},{'dataDiskGB':-1}]:
            with self.assertRaises(ValueError):self.console.quote('Windows',sizing)
        self.assertFalse(any(a.startswith('Create') for a,_ in self.api.calls))

    def test_nonfinite_price_prevents_creation(self):
        with self.assertRaises(ValueError):self.console.create('Windows',float('nan'),'request-invalid-price')
        self.assertFalse(any(a.startswith('Create') for a,_ in self.api.calls))

    def test_lease_save_without_posix_uid_does_not_assume_root(self):
        chown=Mock(side_effect=AssertionError('Windows has no POSIX root identity'))
        with patch('agentpair.resources.os',SimpleNamespace(chmod=os.chmod,chown=chown)):
            lease=self.console.create('Windows',0.51,'request-without-posix-uid')
        chown.assert_not_called()
        self.assertEqual(self.manager.records()[0]['hostId'],lease['hostId'])
        self.assertEqual(sum(a=='CreateUHostInstance' for a,_ in self.api.calls),1)

    def test_posix_root_save_preserves_directory_owner(self):
        chown=Mock()
        lease={'id':'f'*24,'state':'active'}
        with patch('agentpair.resources.os',SimpleNamespace(chmod=os.chmod,geteuid=lambda:0,chown=chown)):
            self.manager._save(lease)
        owner=self.manager.directory.stat()
        chown.assert_called_once_with(self.manager.directory/('f'*24+'.tmp'),owner.st_uid,owner.st_gid)
        self.assertEqual(self.manager.records(),[lease])

    def test_nonroot_save_keeps_current_file_owner(self):
        chown=Mock(side_effect=AssertionError('Nonroot must not change ownership'))
        lease={'id':'e'*24,'state':'active'}
        with patch('agentpair.resources.os',SimpleNamespace(chmod=os.chmod,geteuid=lambda:1000,chown=chown)):
            self.manager._save(lease)
        chown.assert_not_called()
        self.assertEqual(self.manager.records(),[lease])


if __name__=='__main__':unittest.main()
