"""Capability result summaries must expose evidence, not invented outcomes."""
import hashlib,json,tempfile,unittest
from pathlib import Path
from agentpair.devices import DeviceStore
from agentpair.session_lens import SessionStore
from agentpair.collection_view import CollectionView
from agentpair.data_center import DataCenter,_activity

class ActivityTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();root=Path(self.tmp.name)
  self.devices=DeviceStore(root/'devices.db');self.sessions=SessionStore(root/'sessionlens.db')
  enrolled=self.devices.enroll(self.devices.pairing('alice')['code'],'Office Mac')
  self.identity=self.devices.identity(enrolled['token']);self.center=DataCenter(CollectionView(self.devices,self.sessions))
 def tearDown(self):self.tmp.cleanup()
 def event(self,key,kind,stamp,payload,**extra):
  return dict({'id':hashlib.sha256(key.encode()).hexdigest(),'schemaVersion':1,'source':'workbuddy','sessionId':'s','kind':kind,'timestamp':stamp,'payload':payload,'evidence':{'path':'/fixture/session.jsonl','byteStart':0,'byteEnd':100}},**extra)
 def upload(self,events):self.sessions.ingest(self.identity,{'schemaVersion':1,'events':events})
 def test_list_frontloads_capability_and_meaningful_request_without_claiming_browser_result(self):
  self.upload([
   self.event('request','message',1700000000,{'content':'打开天气网站检查结果'},role='user'),
   self.event('confirm','message',1700000001,{'content':'已经连接'},role='user'),
   self.event('load','tool_call',1700000002,{'arguments':{'skill':'agent-browser'}},name='Skill',callId='a'),
   self.event('return','tool_result',1700000003,{'output':{'text':'# Agent Browser\n操作说明'*100}},callId='a')])
  value=self.center.search({'q':'skill="agent-browser"'})['items'][0];activity=value['activity']
  self.assertEqual(activity['title'],'加载 agent-browser')
  self.assertEqual(activity['request']['text'],'打开天气网站检查结果')
  self.assertEqual(activity['request']['association'],'candidate')
  self.assertEqual(activity['returnKind'],'instructions');self.assertNotIn('# Agent Browser',activity['returnSummary'])
  self.assertNotIn('成功',activity['returnSummary']);self.assertEqual(value['deviceName'],'Office Mac')
  detail=self.center.record(value['id'])['item']
  self.assertEqual(detail['arguments'],{'skill':'agent-browser'})
  self.assertIn('操作说明',detail['result']['text'])
 def test_confirmations_alone_never_become_task_description(self):
  self.upload([self.event('confirm','message',1700000000,{'content':'已经连接'},role='user'),
   self.event('load','tool_call',1700000001,{'arguments':{'skill':'browser'}},name='Skill')])
  request=self.center.search({'q':'skill=browser'})['items'][0]['activity']['request']
  self.assertIsNone(request['text']);self.assertEqual(request['association'],'unknown')
 def test_wrapper_return_not_assigned_to_each_child(self):
  cap={'type':'mcp','name':'codex_apps','serviceName':'tinyfish','method':'tinyfish_run_web_automation',
   'arguments':{'url':'https://example.com'},'sourceOffset':18,'outerCallId':'outer'}
  activity=_activity({'capabilities':[cap]},{'resultRecordId':'result','resultPreview':'completed'})
  self.assertEqual(activity['returnKind'],'wrapper_result')
  self.assertIn('独立返回尚未配对',activity['returnSummary']);self.assertNotIn('completed',activity['returnSummary'])
  self.assertEqual(activity['parameters'][0]['value'],'https://example.com')
 def test_failed_skill_return_is_not_presented_as_obtained_instructions(self):
  value=_activity({'capabilities':[{'type':'skill','name':'browser','evidence':'loaded'}]},
   {'resultRecordId':'r','outcome':{'status':'failed','error':'Skill not found'}})
  self.assertEqual(value['returnKind'],'tool_result');self.assertIn('Skill not found',value['returnSummary'])
  self.assertNotIn('已取得 Skill 操作说明',value['returnSummary'])
 def test_snapshot_does_not_take_later_uploaded_request(self):
  self.upload([self.event('load','tool_call',1700000010,{'arguments':{'skill':'browser'}},name='Skill')])
  original=self.center.search({'q':'skill=browser'});self.upload([self.event('late-request','message',1700000001,{'content':'检查登录页面'},role='user')])
  frozen=self.center.search({'q':'skill=browser','snapshot':original['snapshot']})
  self.assertIsNone(frozen['items'][0]['activity']['request']['text'])
  self.assertEqual(self.center.search({'q':'skill=browser'})['items'][0]['activity']['request']['text'],'检查登录页面')

if __name__=='__main__':unittest.main()
