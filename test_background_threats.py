import json,time
from unittest.mock import patch
from test_credential_threats import CredentialTests
class BackgroundTests(CredentialTests):
 def capture(self,n,task,background,stamp=100,session='before',source='workbuddy_network_context'):
  r={'id':f'{n:064x}','source':source,'timestamp':stamp,'sessionId':session,'body':json.dumps({'messages':[{'role':'system','content':background},{'role':'user','content':'<user_query>'+task+'</user_query>'}]})}
  self.store.ingest_model_context(self.device['token'],{'requests':[r]})
 def test_greeting_background_and_scoped_verification(self):
  bg='**工作背景**\n用户开发其他项目，内部架构和服务器规划。'*30
  self.capture(1,'applens 你好',bg)
  f=self.findings()[0];self.assertEqual(f['kind'],'unrelated_background');self.assertEqual(f['networkRecords'],1)
  self.assertIn('内部架构',f['evidence'][0]['preview'])
  packet=self.store.credential_threats.review_packet('alice',f['id']);self.assertEqual(packet['directions'][0]['id'],'unrelated_background')
  with patch('agentpair.credential_threats.time.time',return_value=200):self.store.credential_threats.report('alice',f['id'],{'cleanedContext':True})
  with patch('agentpair.devices.time.time',return_value=210):self.capture(2,'检查服务器报错','普通背景',210,'technical')
  self.assertEqual(self.findings()[0]['verification']['state'],'waiting_capture')
  with patch('agentpair.devices.time.time',return_value=220):self.capture(3,'你好','普通背景',220,'greeting')
  self.assertEqual(self.findings()[0]['verification']['state'],'not_observed')
  with patch('agentpair.devices.time.time',return_value=230):self.capture(4,'你好',bg,230,'again')
  self.assertEqual(self.findings()[0]['verification']['state'],'reappeared')
 def test_technical_task_not_assumed_irrelevant(self):
  self.capture(1,'分析项目架构','工作背景\n'+('内部项目架构服务器。'*40))
  self.assertFalse(self.findings())
