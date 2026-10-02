import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from agentpair.software_install import SoftwareCatalog, install_linux, verify_receipt
from agentpair.devices import DeviceStore


class SoftwareTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.catalog=SoftwareCatalog(Path(self.tmp.name)/'software-catalog.json')
        self.recipe={'id':'workbuddy-windows','platform':'Windows','installer':'inno','name':'WorkBuddy',
                     'displayName':'WorkBuddy','version':'5.6.1','url':'https://download.codebuddy.cn/setup.exe','sha256':'a'*64}

    def test_catalog_requires_pin_and_fixed_installer(self):
        self.assertEqual(self.catalog.register(self.recipe),self.recipe)
        for patch in ({'url':'http://example.com/a'},{'sha256':'abc'},{'installer':'shell'},{'url':'https://user:pass@example.com/a'}):
            with self.assertRaises(ValueError):self.catalog.register({**self.recipe,**patch})

    def test_failed_verification_cannot_be_completed(self):
        store=DeviceStore(Path(self.tmp.name)/'devices.db');d=store.enroll(store.pairing()['code'],'test')
        store.report(d['token'],{'os':'Windows','architecture':'x64','processes':[],'applications':[]})
        self.catalog.register(self.recipe)
        t=store.dispatch('admin',d['deviceId'],{'goal':'install','action':'install_software','softwareId':self.recipe['id']})
        p=store.pull(d['token'])
        with self.assertRaises(ValueError):store.complete(d['token'],t['taskId'],p['lease'],{'state':'completed'})
        good={'state':'completed','evidence':{'softwareId':self.recipe['id'],'sha256':'a'*64,'installedVersion':'5.6.1','verified':True}}
        self.assertTrue(verify_receipt(self.recipe,good))
        self.assertTrue(store.complete(d['token'],t['taskId'],p['lease'],good)['accepted'])

    def test_platform_and_owner_gate(self):
        store=DeviceStore(Path(self.tmp.name)/'devices.db');d=store.enroll(store.pairing('alice')['code'],'test')
        with self.assertRaises(PermissionError):store.dispatch('alice',d['deviceId'],{'goal':'install','action':'install_software','softwareId':'git-linux'})

    def test_linux_reuses_existing_install(self):
        calls=[]
        def run(args,**kw):
            calls.append(args);return SimpleNamespace(returncode=0,stdout='install ok installed\n2.43.0',stderr='')
        r=install_linux(self.catalog.get('git-linux'),lambda e:None,run)
        self.assertTrue(r['evidence']['reused']);self.assertEqual(len(calls),1)

    def test_linux_failure_and_invalid_recipe(self):
        def run(args,**kw):return SimpleNamespace(returncode=1,stdout='',stderr='failed')
        with self.assertRaises(RuntimeError):install_linux(self.catalog.get('git-linux'),lambda e:None,run)
        with self.assertRaises(ValueError):install_linux({'platform':'Linux','installer':'apt','package':'git;evil'},lambda e:None,run)
