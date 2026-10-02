import json
import tempfile
import unittest
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from agentpair.devices import DeviceStore
from agentpair.mobile_auth import MobileAuth

class MobileAuthTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.devices=DeviceStore(Path(self.tmp.name)/'devices.db')
        self.device=self.enroll('alice');self.other=self.enroll('bob');self.now=100
        self.auth=MobileAuth(self.devices,lambda:self.now)
    def tearDown(self):self.tmp.cleanup()
    def enroll(self,owner):
        d=self.devices.enroll(self.devices.pairing(owner)['code'],'Android test')
        self.devices.report(d['token'],{'os':'Android','applications':[],'processes':[]})
        return d
    def create(self):return self.auth.create('alice',self.device['deviceId'],'https://www.freebuf.com','FreeBuf','task1')
    def test_automatic_single_use_private_channel(self):
        c=self.create();self.assertEqual(len(self.auth.pending(self.device['token'])),1)
        self.assertEqual(self.auth.pending(self.other['token']),[])
        self.auth.submit(self.device['token'],c['id'],'123456')
        self.assertNotIn('123456',json.dumps(self.auth.status('alice',c['id'])))
        with self.assertRaises(PermissionError):self.auth.consume(c['id'],c['consumerToken'],'https://evil.test')
        out=self.auth.consume(c['id'],c['consumerToken'],c['origin']);self.assertEqual(out['code'],'123456')
        with self.assertRaises(PermissionError):self.auth.consume(c['id'],c['consumerToken'],c['origin'])
    def test_cross_owner_expiry_and_revocation(self):
        c=self.create()
        with self.assertRaises(PermissionError):self.auth.submit(self.other['token'],c['id'],'123456')
        with self.assertRaises(KeyError):self.auth.status('bob',c['id'])
        with self.assertRaises(ValueError):self.create()
        self.now=281
        self.assertEqual(self.auth.pending(self.device['token']),[])
        with self.assertRaises(PermissionError):self.auth.submit(self.device['token'],c['id'],'123456')
        c=self.create();self.auth.submit(self.device['token'],c['id'],'987654')
        self.devices.revoke(self.device['deviceId'],'alice')
        with self.assertRaises(PermissionError):self.auth.consume(c['id'],c['consumerToken'],c['origin'])
    def test_cancel_and_atomic_consumption(self):
        c=self.create();self.auth.cancel('alice',c['id'])
        self.assertEqual(self.auth.pending(self.device['token']),[])
        c=self.create();self.auth.submit(self.device['token'],c['id'],'654321')
        def consume(_):
            try:return self.auth.consume(c['id'],c['consumerToken'],c['origin'])['state']
            except PermissionError:return 'denied'
        with ThreadPoolExecutor(max_workers=2) as workers:results=list(workers.map(consume,range(2)))
        self.assertEqual(sorted(results),['consumed','denied'])

if __name__=='__main__':unittest.main()
