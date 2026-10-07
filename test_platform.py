import http.cookiejar
import hashlib
import io
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from agentpair.platform import handler_for
from agentpair.tasks import TaskEngine
from test_tasks import FakeBackend


class PlatformTests(unittest.TestCase):
    def test_legacy_package_route_redirects_without_transferring_bytes(self):
        from agentpair.software_install import SoftwareCatalog
        recipe={'id':'test-windows','platform':'Windows','installer':'inno','name':'Test','displayName':'Test',
                'version':'1.0','url':'https://downloads.example.org/setup.exe','sha256':'a'*64}
        SoftwareCatalog(Path(self.tmp.name)/'software-catalog.json').register(recipe)
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self,*args):return None
        client=urllib.request.build_opener(NoRedirect(),urllib.request.ProxyHandler({}))
        with self.assertRaises(urllib.error.HTTPError) as error:client.open(self.url+'/package-files/'+'a'*64)
        self.assertEqual(error.exception.code,302)
        self.assertEqual(error.exception.headers['Location'],recipe['url'])
        self.assertEqual(error.exception.headers['Content-Length'],'0');error.exception.close()
    def test_public_package_directory_and_admin_cache_controls(self):
        for route in ('/software','/software.js','/packages.js','/packages.css'):
            with self.client.open(self.url+route) as response:self.assertEqual(response.status,200)
        data=self.call('/api/packages')
        self.assertEqual(data['node']['state'],'unconfigured')
        self.assertEqual(len(data['items']),2)
        self.assertTrue(all(r['cacheState']=='source_recipe' for r in data['items']))
        self.assertNotIn('token',json.dumps(data))
        with self.client.open(self.url+'/packages') as response:
            self.assertIn('缓存节点',response.read().decode())
        with self.assertRaises(urllib.error.HTTPError) as error:self.call('/api/packages/prepare',{'softwareId':'git-linux'})
        self.assertEqual(error.exception.code,401);error.exception.close()
        csrf=self.call('/api/register',{'username':'packageviewer','password':'viewer-password-123'})['csrf']
        with self.assertRaises(urllib.error.HTTPError) as error:self.call('/api/packages/prepare',{'softwareId':'git-linux'},csrf)
        self.assertEqual(error.exception.code,403);error.exception.close()
    def test_cloud_controls_require_admin_and_configured_provider(self):
        for path in ('/api/cloud/quote','/api/cloud/create','/api/cloud/start','/api/cloud/install'):
            with self.assertRaises(urllib.error.HTTPError) as error:self.call(path,{'system':'Windows'})
            self.assertEqual(error.exception.code,401);error.exception.close()
        csrf=self.call('/api/register',{'username':'cloudviewer','password':'viewer-password-123'})['csrf']
        with self.assertRaises(urllib.error.HTTPError) as error:self.call('/api/cloud/start',{'leaseId':'a'*24},csrf)
        self.assertEqual(error.exception.code,403);error.exception.close()
        self.call('/api/logout',{},csrf)
        csrf=self.call('/api/login',{'username':'admin','password':'test-password-long-enough'})['csrf']
        with self.assertRaises(urllib.error.HTTPError) as error:self.call('/api/cloud/start',{'leaseId':'a'*24},csrf)
        self.assertEqual(error.exception.code,503);error.exception.close()
    def test_sessionlens_device_and_model_data_in_unified_routes(self):
        csrf=self.call('/api/login',{'username':'admin','password':'test-password-long-enough'})['csrf']
        pair=self.call('/api/devices/pairing',{},csrf)
        device=self.call('/api/endpoint/enroll',{'code':pair['code'],'name':'Session-only Mac'})
        event={'schemaVersion':1,'id':'b'*64,'source':'workbuddy','sessionId':'weather','kind':'user_message','timestamp':1700000000,'payload':{'text':'上海天气'},'evidence':{'line':1}}
        self.call('/api/sessionlens/events',{'schemaVersion':1,'events':[event]},bearer=device['token'])
        rows=self.call('/api/devices')['items'];self.assertEqual(rows[0]['collectors'][0]['name'],'SessionLens')
        data=self.call('/api/devices/model-data/'+device.get('deviceId',device.get('id'))+'?summary=1')
        self.assertEqual(data['calls'][0]['collector'],'sessionlens')
        detail=self.call('/api/devices/model-data/'+device.get('deviceId',device.get('id'))+'?request=sessionlens:'+event['id'])
        self.assertIn('上海天气',detail['items'][0]['rawContent'])
        with self.client.open(self.url+'/devices') as response:page=response.read().decode()
        self.assertIn('Session-only Mac',page)
        self.assertIn('SessionLens · 1 条',page)

    def test_model_data_route_is_private_and_serves_prototype(self):
        with self.client.open(self.url+'/model-data') as response:
            body=response.read().decode()
            self.assertIn('采集数据 · 原文与分类',body)
            self.assertIn('id="collector"',body)
            self.assertIn('SessionLens',body)
        with self.assertRaises(urllib.error.HTTPError) as error:self.call('/api/devices/model-data/unknown')
        self.assertEqual(error.exception.code,401);error.exception.close()
    def test_interaction_audit_requires_owner_and_renders_assets(self):
        for path in ('/model-security','/model_security.css','/model_security.js'):
            with self.client.open(self.url+path) as response:self.assertEqual(response.status,200)
        with self.assertRaises(urllib.error.HTTPError) as error:self.call('/api/devices/interaction-audit/unknown?request=x')
        self.assertEqual(error.exception.code,401);error.exception.close()
        csrf=self.call('/api/register',{'username':'auditor','password':'audit-password-123'})['csrf']
        with self.assertRaises(urllib.error.HTTPError) as error:self.call('/api/devices/interaction-audit/unknown')
        self.assertEqual(error.exception.code,403);error.exception.close()

    def test_public_global_audit_and_private_controls(self):
        from agentpair.devices import DeviceStore
        store=DeviceStore(Path(self.tmp.name)/'devices.db')
        d=store.enroll(store.pairing('alice')['code'],'shared mac')
        store.ingest_model_context(d['token'],{'requests':[{'id':'a'*64,'source':'workbuddy_network_context','body':json.dumps([{'role':'user','content':'api_key=sk-abcdefghijklmnopqrstuvwx'}])}]})
        self.assertEqual(self.call('/api/audit/devices')['items'][0]['id'],d['deviceId'])
        data=self.call('/api/devices/interaction-audit/'+d['deviceId']+'?scope=global')
        self.assertNotIn('sk-abcdefghijklmnopqrstuvwx',json.dumps(data))
        raw=self.call('/api/audit/model-data/'+d['deviceId'])
        self.assertIn('sk-abcdefghijklmnopqrstuvwx',raw['calls'][0]['body'])
        for path in ('/api/devices','/api/devices/model-data/'+d['deviceId']):
            with self.assertRaises(urllib.error.HTTPError) as error:self.call(path)
            self.assertEqual(error.exception.code,401);error.exception.close()

    def test_persistent_cookie_survives_handler_restart(self):
        login=urllib.request.Request(self.url+'/api/login',data=json.dumps({'username':'admin','password':'test-password-long-enough'}).encode(),headers={'Origin':'http://127.0.0.1:18080','Content-Type':'application/json'})
        with self.client.open(login) as response:
            self.assertIn('Max-Age=604800',response.headers['Set-Cookie'])
            csrf=json.load(response)['csrf']
        self.server.RequestHandlerClass=handler_for(self.engine,'test-password-long-enough','http://127.0.0.1:18080')
        self.assertEqual(self.call('/api/session')['csrf'],csrf)
        self.call('/api/logout',{},csrf)
        self.assertIsNone(self.call('/api/session')['csrf'])

    def test_audit_request_selection_and_home_navigation(self):
        from agentpair.devices import DeviceStore
        store=DeviceStore(Path(self.tmp.name)/'devices.db')
        device=store.enroll(store.pairing()['code'],'test')
        older={'id':'a'*64,'source':'workbuddy_network_context','body':json.dumps([{'role':'user','content':'older'}]),'timestamp':1}
        newer={**older,'id':'b'*64,'timestamp':2,'body':json.dumps([{'role':'user','content':'newer'}])}
        store.ingest_model_context(device['token'],{'requests':[older,newer]})
        self.call('/api/login',{'username':'admin','password':'test-password-long-enough'})
        audit=self.call('/api/devices/interaction-audit/'+device.get('deviceId',device.get('id'))+'?request='+older['id'])
        self.assertEqual(audit['audit']['request']['id'],older['id'])
        self.assertEqual(audit['audit']['task']['text'],'older')
        for route in ('/','/devices','/model-data'):
            with self.client.open(self.url+route) as response:self.assertIn('/model-security',response.read().decode())

    def test_public_task_is_readable_but_not_writable_by_guest(self):
        task=self.engine.create('公开试验','观察测试连接')
        with self.engine.lock:
            saved=self.engine.get(task['id']);saved['owner']='admin';saved['visibility']='public';self.engine._save(saved)
        visible=self.call('/api/tasks/'+task['id'])
        self.assertFalse(visible['permissions']['canWrite'])
        self.assertIn(task['id'],[t['id'] for t in self.call('/api/tasks')['items']])
        for suffix in ('messages','cancel'):
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.call('/api/tasks/'+task['id']+'/'+suffix,{'message':'不允许改动'})
            self.assertEqual(error.exception.code,401);error.exception.close()

    def test_user_task_followup_and_visibility(self):
        alice=self.call('/api/register',{'username':'alice','password':'a-long-password-123'})['csrf']
        task=self.call('/api/tasks',{'title':'我的应用分析','message':'分析进程并给出依据'},alice)
        tid=task['id']
        self.assertTrue(task['permissions']['canWrite'])
        self.engine.process(tid)
        self.assertEqual(self.call('/api/tasks/'+tid+'/messages',{'message':'补充验证依据'},alice)['round'],2)
        self.call('/api/tasks/'+tid+'/cancel',{},alice)
        bob=self.call('/api/register',{'username':'bob','password':'another-password-123'})['csrf']
        self.assertNotIn(tid,[t['id'] for t in self.call('/api/tasks')['items']])
        self.assertNotIn(tid,[t['id'] for t in self.call('/api/resources')['tasks']])
        for path,data in [('/api/tasks/'+tid,None),('/api/tasks/'+tid+'/messages',{'message':'越权'}),('/api/tasks/'+tid+'/cancel',{})]:
            with self.assertRaises(urllib.error.HTTPError) as error:self.call(path,data,bob)
            self.assertEqual(error.exception.code,404);error.exception.close()
        self.call('/api/logout',{},bob)
        with self.assertRaises(urllib.error.HTTPError) as error:self.call('/api/tasks/'+tid)
        self.assertEqual(error.exception.code,404);error.exception.close()

    def test_task_deep_link_serves_workspace(self):
        with self.client.open(self.url+'/?task=example-task') as response:
            self.assertEqual(response.status,200)
            self.assertIn(b'AgentPair',response.read())

    def test_android_package_download_route(self):
        assets=Path(self.tmp.name)/'download-assets';assets.mkdir()
        installer=assets/'AgentPair-Android-0.1.0.apk'
        # Exercise byte delivery with a test-only ZIP, independently of release binaries.
        with zipfile.ZipFile(installer,'w') as package:
            package.writestr('AndroidManifest.xml','<manifest package="org.agentpair.test"/>')
        body=installer.read_bytes()
        with patch('agentpair.platform.ASSETS',assets):
            with self.client.open(self.url+'/downloads/AgentPair-Android-0.1.0.apk') as response:
                self.assertEqual(response.status,200)
                self.assertEqual(response.headers.get_content_type(),'application/vnd.android.package-archive')
                self.assertEqual(response.headers['Content-Length'],str(len(body)))
                self.assertEqual(response.read(),body)
            installer.unlink()
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.client.open(self.url+'/downloads/AgentPair-Android-0.1.0.apk')
            self.assertEqual(error.exception.code,503);error.exception.close()

    def test_sessionlens_windows_package_streams_public_fixture_in_bounded_chunks(self):
        assets=Path(self.tmp.name)/'sessionlens-assets';assets.mkdir()
        archive=io.BytesIO()
        with zipfile.ZipFile(archive,'w') as package:
            package.writestr('SessionLens/fixture.txt',b'fixture data\n'*(200000))
        body=archive.getvalue();digest=hashlib.sha256(body).hexdigest()
        installer=assets/('SessionLens-Windows-'+digest+'.zip');installer.write_bytes(body)
        read_sizes=[];original_open=Path.open
        class TrackedReader:
            def __init__(self,source):self.source=source
            def __enter__(self):self.source.__enter__();return self
            def __exit__(self,*args):return self.source.__exit__(*args)
            def fileno(self):return self.source.fileno()
            def read(self,size=-1):read_sizes.append(size);return self.source.read(size)
        def open_fixture(path,*args,**kwargs):
            source=original_open(path,*args,**kwargs)
            return TrackedReader(source) if path==installer else source
        self.assertEqual(self.call('/api/session')['role'],'viewer')
        with patch('agentpair.platform.ASSETS',assets), patch('pathlib.Path.open',open_fixture), \
             patch('pathlib.Path.read_bytes',side_effect=AssertionError('Download must stream, not read the entire package')):
            with self.client.open(self.url+'/downloads/'+installer.name) as response:
                self.assertEqual(response.status,200)
                self.assertEqual(response.headers.get_content_type(),'application/zip')
                self.assertEqual(response.headers['Content-Disposition'],'attachment; filename="SessionLens-Windows.zip"')
                self.assertEqual(response.headers['Content-Length'],str(len(body)))
                self.assertEqual(response.read(),body)
        self.assertGreater(len(read_sizes),2)
        self.assertTrue(all(0<size<=1024*1024 for size in read_sizes))

    def test_sessionlens_windows_package_missing_and_malformed_hash_routes(self):
        assets=Path(self.tmp.name)/'sessionlens-assets';assets.mkdir()
        # A malformed filename remains forbidden even if such a file exists.
        (assets/('SessionLens-Windows-'+'A'*64+'.zip')).write_bytes(b'test fixture')
        with patch('agentpair.platform.ASSETS',assets):
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.client.open(self.url+'/downloads/SessionLens-Windows-'+'b'*64+'.zip')
            self.assertEqual(error.exception.code,503);error.exception.close()
            suffixes=['A'*64+'.zip','a'*63+'.zip','a'*65+'.zip','g'*64+'.zip',
                      'a'*64+'.ZIP','a'*64+'.zip/extra','../SessionLens-Windows.zip',
                      '%2e%2e%2fSessionLens-Windows.zip']
            for suffix in suffixes:
                with self.subTest(suffix=suffix),self.assertRaises(urllib.error.HTTPError) as error:
                    self.client.open(self.url+'/downloads/SessionLens-Windows-'+suffix)
                self.assertEqual(error.exception.code,404);error.exception.close()

    def test_self_registration_device_isolation_and_permissions(self):
        alice=self.call('/api/register',{'username':'alice','password':'a-long-password-123'})['csrf']
        pair=self.call('/api/devices/pairing',{},alice)
        identity=self.call('/api/endpoint/enroll',{'code':pair['code'],'name':'Alice PC'},origin='')
        self.assertEqual(len(self.call('/api/devices')['items']),1)
        self.assertEqual(self.call('/api/balance')['remainingCNY'],2)
        for path,data in [('/api/tasks',{'title':'test','message':'test','engineeringMethod':'pair'}),('/api/credentials',{})]:
            with self.assertRaises(urllib.error.HTTPError) as error:self.call(path,data,alice)
            self.assertEqual(error.exception.code,403);error.exception.close()
        with self.assertRaises(urllib.error.HTTPError) as error:self.call('/api/credentials')
        self.assertEqual(error.exception.code,403);error.exception.close()
        bob=self.call('/api/register',{'username':'bob','password':'another-password-123'})['csrf']
        self.assertEqual(self.call('/api/devices')['items'],[])
        with self.assertRaises(urllib.error.HTTPError) as error:self.call('/api/devices/revoke',{'deviceId':identity['deviceId']},bob)
        self.assertEqual(error.exception.code,403);error.exception.close()
        self.call('/api/logout',{},bob)
        self.assertEqual(self.call('/api/session')['role'],'viewer')
        alice=self.call('/api/login',{'username':'alice','password':'a-long-password-123'})['csrf']
        self.assertEqual(len(self.call('/api/devices')['items']),1)
        self.call('/api/devices/revoke',{'deviceId':identity['deviceId']},alice)
        self.assertEqual(self.call('/api/devices')['items'],[])

    def test_registration_validation(self):
        for name,password in [('admin','a-long-password-123'),('ab','a-long-password-123'),('alice','short')]:
            with self.assertRaises(urllib.error.HTTPError) as error:self.call('/api/register',{'username':name,'password':password})
            self.assertEqual(error.exception.code,400);error.exception.close()
        self.call('/api/register',{'username':'Alice','password':'a-long-password-123'})
        with self.assertRaises(urllib.error.HTTPError) as error:self.call('/api/register',{'username':'alice','password':'a-long-password-123'})
        self.assertEqual(error.exception.code,400);error.exception.close()

    def test_device_endpoint_flow(self):
        with self.assertRaises(urllib.error.HTTPError) as e:self.call('/api/devices')
        self.assertEqual(e.exception.code,401);e.exception.close()
        csrf=self.call('/api/register',{'username':'deviceuser','password':'test-password-long-enough'})['csrf']
        pair=self.call('/api/devices/pairing',{},csrf)
        identity=self.call('/api/endpoint/enroll',{'code':pair['code'],'name':'Windows test'},origin='')
        app={'name':'TestApp','version':'1','publisher':'Test','processNames':['test.exe']}
        report={'processes':[{'name':'test.exe','pid':1,'parentPid':0}],'applications':[app]}
        req=urllib.request.Request(self.url+'/api/endpoint/report',data=json.dumps(report).encode(),headers={'Content-Type':'application/json','Authorization':'Bearer '+identity['token']})
        with self.client.open(req) as response:self.assertTrue(json.load(response)['accepted'])
        self.assertEqual(len(self.call('/api/devices')['items']),1)
        payload={'deviceId':identity['deviceId'],'kind':'applications','index':0,'selected':app,'goal':'分析进程','shareConfirmed':True}
        plan=self.call('/api/devices/plan',{**payload,'scopes':['applications','processes']},csrf)
        result=self.call('/api/devices/confirm',{'planId':plan['id'],'shareConfirmed':True},csrf)
        task=self.call('/api/tasks/'+result['taskId'])
        self.assertTrue(task['permissions']['canWrite'])
        self.assertEqual(task['engineeringMethod'],'local')
        self.assertIn('TestApp',task['messages'][0]['text'])
        self.assertEqual(self.call('/api/devices')['items'][0]['currentTaskId'],task['id'])
        self.engine.process(task['id'])
        self.assertEqual(self.call('/api/tasks/'+task['id']+'/messages',{'message':'继续解释依据'},csrf)['round'],2)
        self.call('/api/devices/revoke',{'deviceId':identity['deviceId']},csrf)
        with self.assertRaises(urllib.error.HTTPError) as e:self.client.open(req)
        self.assertEqual(e.exception.code,401);e.exception.close()

    def test_mobile_login_code_channel_never_enters_task_history(self):
        csrf=self.call('/api/register',{'username':'phoneowner','password':'a-long-password-123'})['csrf']
        pair=self.call('/api/devices/pairing',{},csrf)
        identity=self.call('/api/endpoint/enroll',{'code':pair['code'],'name':'Android test'},origin='')
        self.call('/api/endpoint/report',{'os':'Android','architecture':'arm64-v8a','processes':[],'applications':[],'errors':[]},origin='',bearer=identity['token'])
        task=self.call('/api/tasks',{'title':'FreeBuf 登录协助','message':'用户发起本次登录'},csrf)
        challenge=self.call('/api/mobile-auth',{'deviceId':identity['deviceId'],'taskId':task['id'],
            'origin':'https://www.freebuf.com','brand':'FreeBuf'},csrf)
        self.assertNotIn('code',challenge)
        self.assertIn('consumerToken',challenge)
        pending=self.call('/api/endpoint/login-requests',origin='',bearer=identity['token'])['items']
        self.assertEqual(len(pending),1)
        self.assertEqual(pending[0]['id'],challenge['id'])
        submit=self.call('/api/endpoint/login-code',{'id':challenge['id'],'code':'123456'},origin='',bearer=identity['token'])
        self.assertEqual(submit,{'accepted':True,'state':'received'})
        status=self.call('/api/mobile-auth/'+challenge['id'])
        self.assertEqual(status['state'],'received')
        self.assertNotIn('123456',json.dumps(status))
        self.assertNotIn('consumerToken',status)
        consume={'id':challenge['id'],'origin':'https://attacker.example'}
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.call('/api/mobile-auth/consume',consume,origin='',bearer=challenge['consumerToken'])
        self.assertEqual(error.exception.code,401);error.exception.close()
        consume['origin']='https://www.freebuf.com'
        result=self.call('/api/mobile-auth/consume',consume,origin='',bearer=challenge['consumerToken'])
        self.assertEqual(result['code'],'123456')
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.call('/api/mobile-auth/consume',consume,origin='',bearer=challenge['consumerToken'])
        self.assertEqual(error.exception.code,401);error.exception.close()
        self.assertNotIn('123456',json.dumps(self.call('/api/tasks/'+task['id'])))

    def test_android_device_two_way_task_round_trip(self):
        csrf=self.call('/api/register',{'username':'androidowner','password':'a-long-password-123'})['csrf']
        pair=self.call('/api/devices/pairing',{},csrf)
        device=self.call('/api/endpoint/enroll',{'code':pair['code'],'name':'Android phone'},origin='')
        self.call('/api/endpoint/report',{'os':'Android','architecture':'arm64-v8a','processes':[],'applications':[],'errors':[]},origin='',bearer=device['token'])
        dispatched=self.call('/api/devices/dispatch',{'deviceId':device['deviceId'],'task':{'title':'交互验证','goal':'确认接收后提交一条测试回执'}},csrf)
        jobs=self.call('/api/devices/tasks')['items']
        self.assertEqual(jobs[0]['taskId'],dispatched['taskId']);self.assertEqual(jobs[0]['state'],'queued')
        received=self.call('/api/endpoint/tasks',origin='',bearer=device['token'])['task']
        self.assertEqual(received['taskId'],dispatched['taskId']);self.assertEqual(received['state'],'assigned')
        self.call('/api/endpoint/tasks/result',{'taskId':received['taskId'],'lease':received['lease'],'result':{'state':'received','summary':'Android 已接收'}},origin='',bearer=device['token'])
        running=self.call('/api/endpoint/tasks',origin='',bearer=device['token'])['task']
        self.assertEqual(running['state'],'received')
        self.call('/api/endpoint/tasks/result',{'taskId':running['taskId'],'lease':running['lease'],'result':{'state':'running','summary':'用户确认开始'}},origin='',bearer=device['token'])
        active=self.call('/api/endpoint/tasks',origin='',bearer=device['token'])['task']
        self.call('/api/endpoint/tasks/result',{'taskId':active['taskId'],'lease':active['lease'],'result':{'state':'completed','summary':'手机端测试完成'}},origin='',bearer=device['token'])
        final=self.call('/api/devices/tasks')['items'][0]
        self.assertEqual(final['state'],'completed');self.assertEqual(final['result']['summary'],'手机端测试完成')
        self.call('/api/logout',{},csrf)
        other=self.call('/api/register',{'username':'otheruser','password':'another-password-123'})['csrf']
        self.assertEqual(self.call('/api/devices/tasks')['items'],[])

    def test_credentials_admin_only(self):
        with self.assertRaises(urllib.error.HTTPError) as e:self.call('/api/credentials')
        self.assertEqual(e.exception.code,401);e.exception.close()
        csrf=self.call('/api/login',{'username':'admin','password':'test-password-long-enough'})['csrf']
        data={'name':'FOFA','authType':'api_key','secrets':{'api_key':'test-secret-not-real'}}
        with self.assertRaises(urllib.error.HTTPError) as e:self.call('/api/credentials',data)
        self.assertEqual(e.exception.code,403);e.exception.close()
        saved=self.call('/api/credentials',data,csrf)
        self.assertNotIn('test-secret-not-real',json.dumps(saved))
        self.assertEqual(len(self.call('/api/credentials')['items']),1)
    def test_security_investigation_launch_and_public_review(self):
        self.call('/api/login',{'username':'admin','password':'test-password-long-enough'})
        csrf=self.call('/api/session')['csrf']
        pair=self.call('/api/devices/pairing',{},csrf)
        d=self.call('/api/endpoint/enroll',{'code':pair['code'],'name':'security-test'},origin='')
        self.call('/api/applens/model-context',{'requests':[{'id':'d'*64,'source':'workbuddy_network_context','body':json.dumps({'messages':[{'role':'user','content':'修复函数'}]})}]},origin='',bearer=d['token'])
        result=self.call('/api/devices/model-analyze',{'deviceId':d['deviceId'],'requestId':'d'*64,'shareConfirmed':True},csrf)
        self.assertEqual(result['evidence']['tool'],'applens_security')
        self.assertTrue(result['task']['securityEvidence']['coverage']['sampled'])
        self.engine.process(result['task']['id'])
        self.call('/api/logout',{},csrf)
        audit=self.call('/api/devices/interaction-audit/'+d['deviceId']+'?scope=global')
        self.assertEqual(len(audit['audit']['semanticReview']['stages']),3)
        with self.assertRaises(urllib.error.HTTPError) as e:self.call('/api/devices/model-analyze',{'deviceId':d['deviceId'],'requestId':'d'*64,'shareConfirmed':True})
        self.assertEqual(e.exception.code,401);e.exception.close()

    def test_credential_findings_public_and_owner_remediation(self):
        csrf=self.call('/api/register',{'username':'credentialowner','password':'long-test-password-123'})['csrf']
        pair=self.call('/api/devices/pairing',{},csrf)
        d=self.call('/api/endpoint/enroll',{'code':pair['code'],'name':'credential-device'},origin='')
        self.call('/api/applens/model-context',{'requests':[{'id':'e'*64,'source':'workbuddy_network_context','sessionId':'old','timestamp':100,'body':json.dumps({'messages':[{'role':'system','content':'云主机登录：root/Kj9sF3p8Qv1!'}]})}]},origin='',bearer=d['token'])
        found=self.call('/api/audit/credential-threats')['items'];self.assertEqual(len(found),1)
        self.assertNotIn('Kj9sF3p8Qv1!',json.dumps(found));fid=found[0]['id']
        with self.assertRaises(urllib.error.HTTPError) as error:self.call('/api/devices/credential-review',{'findingId':fid},csrf)
        self.assertEqual(error.exception.code,400);error.exception.close()
        review=self.call('/api/devices/credential-review',{'findingId':fid,'shareConfirmed':True},csrf)
        task=self.engine.get(review['taskId']);self.assertEqual(task['securityEvidence']['findingId'],fid)
        self.assertNotIn('Kj9sF3p8Qv1!',json.dumps(task['securityEvidence']))
        self.engine.process(review['taskId'])
        self.call('/api/devices/threat-workflow',{'findingId':fid,'action':'assign','assignee':'负责人','due':'2026-10-09'},csrf)
        self.call('/api/devices/threat-workflow',{'findingId':fid,'action':'submit','note':'已清理背景','actions':{'cleanedContext':True}},csrf)
        self.assertEqual(self.call('/api/audit/credential-threats')['items'][0]['workflow']['state'],'pending_verification')
        self.assertEqual(self.call('/api/audit/credential-threats')['items'][0]['verification']['state'],'waiting_capture')
        self.call('/api/logout',{},csrf)
        self.assertEqual(len(self.call('/api/audit/credential-threats')['items']),1)
        with self.assertRaises(urllib.error.HTTPError) as error:self.call('/api/devices/credential-remediation',{'findingId':fid,'actions':{'cleanedContext':True}})
        self.assertEqual(error.exception.code,401);error.exception.close()
        other=self.call('/api/register',{'username':'othercredential','password':'long-test-password-123'})['csrf']
        with self.assertRaises(urllib.error.HTTPError) as error:self.call('/api/devices/credential-remediation',{'findingId':fid,'actions':{'cleanedContext':True}},other)
        self.assertEqual(error.exception.code,403);error.exception.close()

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.engine=TaskEngine(Path(self.tmp.name)/'tasks.db',FakeBackend(),start=False)
        self.server=ThreadingHTTPServer(('127.0.0.1',0),handler_for(self.engine,'test-password-long-enough','http://127.0.0.1:18080'))
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True); self.thread.start()
        self.url='http://127.0.0.1:'+str(self.server.server_port)
        self.client=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),urllib.request.ProxyHandler({}))
    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(); self.engine.close(); self.tmp.cleanup()
    def call(self,path,data=None,csrf='',origin='http://127.0.0.1:18080',bearer=None):
        headers={'Origin':origin,'Content-Type':'application/json','X-CSRF-Token':csrf}
        if bearer:headers['Authorization']='Bearer '+bearer
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
