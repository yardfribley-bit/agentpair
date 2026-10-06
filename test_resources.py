import datetime
import tempfile
import unittest
from agentpair.resources import ResourceManager


class FakeCloud:
    region='cn-bj2'
    project_id='org-test'
    def __init__(self):self.calls=[];self.hosts=[]
    def call(self,action,**kwargs):
        self.calls.append((action,kwargs))
        if action in ('GetUHostInstancePrice','GetEIPPrice'):
            return {'PriceSet':[{'ChargeType':'Dynamic','Price':0.1}]}
        if action=='CreateUHostInstance':
            self.hosts=[{'UHostId':'test-host','Name':kwargs['Name'],'State':'Running'}]
            return {'UHostIds':['test-host']}
        if action=='DescribeUHostInstance':
            return {'UHostSet':self.hosts}
        if action=='StopUHostInstance':
            self.hosts[0]['State']='Stopped';return {}
        if action=='TerminateUHostInstance':
            self.hosts=[];return {}
        raise AssertionError(action)


class ResourceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.cloud=FakeCloud()
        self.manager=ResourceManager(self.cloud,self.temp.name,max_hosts=1,max_hourly_cny=.5)
        self.config={'Zone':'cn-bj2-04','ImageId':'image','CPU':1,'Memory':1024,
                     'SecurityGroupId':'fw'}
        self.eip={'Bandwidth':1,'PayMode':'Bandwidth','OperatorName':'Bgp'}
    def tearDown(self):self.temp.cleanup()
    def test_quote_lease_and_verified_release(self):
        r=self.manager.create_driver(self.config,self.eip,'agentpair-driver-test','ssh-ed25519 abc')
        self.assertEqual(r['price']['hourlyCNY'],.2)
        request=next(v for a,v in self.cloud.calls if a=='CreateUHostInstance')
        self.assertEqual(request['LoginMode'],'Password')
        import base64
        self.assertGreaterEqual(len(base64.b64decode(request['Password'])),8)
        self.assertLessEqual(len(base64.b64decode(request['Password'])),30)
        self.assertNotIn('Password',r)
        self.assertRaises(ValueError,self.manager.create_driver,self.config,self.eip,
                          'agentpair-driver-second','ssh-ed25519 abc')
        self.assertEqual(self.manager.release(r['id'])['state'],'released')
        self.assertEqual(self.manager.release(r['id'])['state'],'released')
        self.assertEqual([name for name,_ in self.cloud.calls].count('TerminateUHostInstance'),1)
    def test_price_guard_prevents_creation(self):
        self.manager.max_hourly_cny=.1
        with self.assertRaises(ValueError):
            self.manager.create_driver(self.config,self.eip,'agentpair-driver-test','ssh-ed25519 abc')
        self.assertNotIn('CreateUHostInstance',[name for name,_ in self.cloud.calls])
    def test_expired_only(self):
        r=self.manager.create_driver(self.config,self.eip,'agentpair-driver-test','ssh-ed25519 abc')
        self.assertEqual(self.manager.release_expired(),[])
        self.manager.clock=lambda:datetime.datetime.fromisoformat(r['expiresAt'])+datetime.timedelta(seconds=1)
        self.assertEqual(len(self.manager.release_expired()),1)
