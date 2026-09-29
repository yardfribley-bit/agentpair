import http.cookiejar
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from http.server import ThreadingHTTPServer
from agentpair.platform import handler_for
from agentpair.tasks import TaskEngine
from test_tasks import FakeBackend


class PlatformTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.engine=TaskEngine(Path(self.tmp.name)/'tasks.db',FakeBackend(),start=False)
        self.server=ThreadingHTTPServer(('127.0.0.1',0),handler_for(self.engine,'test-password-long-enough','http://127.0.0.1:18080'))
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True); self.thread.start()
        self.url='http://127.0.0.1:'+str(self.server.server_port)
        self.client=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),urllib.request.ProxyHandler({}))
    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(); self.engine.close(); self.tmp.cleanup()
    def call(self,path,data=None,csrf='',origin='http://127.0.0.1:18080'):
        headers={'Origin':origin,'Content-Type':'application/json','X-CSRF-Token':csrf}
        req=urllib.request.Request(self.url+path,data=json.dumps(data).encode() if data is not None else None,headers=headers)
        with self.client.open(req) as r:return json.load(r)
    def test_login_publish_followup_and_csrf(self):
        self.assertEqual(self.call('/api/tasks')['items'],[])
        self.assertEqual(self.call('/api/session')['role'],'viewer')
        self.assertIsNone(self.call('/api/session')['csrf'])
        login=self.call('/api/login',{'username':'admin','password':'test-password-long-enough'}); csrf=login['csrf']
        with self.assertRaises(urllib.error.HTTPError) as e:self.call('/api/tasks',{'title':'x','message':'goal'})
        self.assertEqual(e.exception.code,403); e.exception.close()
        t=self.call('/api/tasks',{'title':'x','message':'goal'},csrf); self.engine.process(t['id'])
        follow=self.call('/api/tasks/'+t['id']+'/messages',{'message':'refine'},csrf)
        self.assertEqual(follow['round'],2); self.assertEqual(len(follow['messages']),5)
        self.call('/api/logout',{},csrf)
        self.assertEqual(self.call('/api/tasks/'+t['id'])['id'],t['id'])
        for path,payload in [('/api/tasks',{'title':'x','message':'x'}),('/api/tasks/'+t['id']+'/messages',{'message':'x'}),('/api/tasks/'+t['id']+'/cancel',{})]:
            with self.assertRaises(urllib.error.HTTPError) as e:self.call(path,payload,csrf)
            self.assertEqual(e.exception.code,401);e.exception.close()
    def test_username_required(self):
        for username in ('','viewer'):
            with self.assertRaises(urllib.error.HTTPError) as e:self.call('/api/login',{'username':username,'password':'test-password-long-enough'})
            self.assertEqual(e.exception.code,401);e.exception.close()
    def test_cross_origin_and_missing_login_rejected(self):
        with self.assertRaises(urllib.error.HTTPError) as e:self.call('/api/login',{'password':'test-password-long-enough'},origin='http://evil.example')
        self.assertEqual(e.exception.code,403); e.exception.close()
        with self.assertRaises(urllib.error.HTTPError) as e:self.call('/api/tasks',{'title':'x','message':'goal'})
        self.assertEqual(e.exception.code,401); e.exception.close()


if __name__=='__main__': unittest.main()
