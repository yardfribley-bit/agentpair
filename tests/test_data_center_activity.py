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
 def multi_mcp(self,extra=''):
  code='await tools.mcp__first__read({path:"/fixture/a"}); await tools.mcp__tinyfish__browse({url:"https://example.test"});'+extra
  event=self.event('multi-mcp','tool_call',1700000002,{'arguments':code},name='exec',namespace='functions',callId='multi')
  self.upload([event]);return event
 def test_matched_mcp_frontloads_its_own_method_and_parameters_in_list_and_detail(self):
  original=self.multi_mcp();query='mcp=tinyfish'
  hit=self.center.search({'q':query})['items'][0]
  for item in (hit,self.center.record(hit['id'],query=query)['item']):
   self.assertEqual(item['activity']['title'],'调用 tinyfish · browse')
   self.assertEqual(item['activity']['name'],'tinyfish')
   self.assertEqual(item['activity']['parameters'],[{'key':'url','label':'请求地址','value':'https://example.test'}])
   self.assertEqual([c['name'] for c in item['activity']['calls']],['tinyfish','first'])
   self.assertEqual([c['name'] for c in item['capabilities']],['first','tinyfish'])
   self.assertEqual([c['name'] for c in item['activity']['focusedCalls']],['tinyfish'])
  self.assertEqual(self.center.record(hit['id'],query=query)['item']['raw'],original)
 def test_unsatisfied_or_branch_cannot_focus_an_unrelated_capability(self):
  self.multi_mcp()
  query='mcp=first && app=codex || mcp=tinyfish && app=workbuddy'
  hit=self.center.search({'q':query})['items'][0]
  self.assertEqual(hit['activity']['name'],'tinyfish')
  self.assertNotIn('first',[m['excerpt'] for m in hit['matchBasis']])
  self.assertEqual(self.center.record(hit['id'],query=query)['item']['activity']['name'],'tinyfish')
 def test_mcp_conjunction_prefers_a_call_matching_name_and_method_together(self):
  self.multi_mcp('await tools.mcp__tinyfish__read({path:"/fixture/tinyfish"});')
  hit=self.center.search({'q':'mcp=tinyfish && mcp_method=read'})['items'][0]
  self.assertEqual(hit['activity']['title'],'调用 tinyfish · read')
  self.assertEqual(hit['activity']['parameters'],[{'key':'path','label':'文件路径','value':'/fixture/tinyfish'}])
  self.assertEqual([(c['name'],c['method']) for c in hit['activity']['focusedCalls']],[('tinyfish','read')])
 def test_record_conjunction_across_calls_does_not_invent_a_service_method_pair(self):
  self.multi_mcp()
  hit=self.center.search({'q':'mcp=first && mcp_method=browse'})['items'][0]
  pairs=[(c['name'],c['method']) for c in hit['activity']['focusedCalls']]
  self.assertEqual(pairs,[('first','read'),('tinyfish','browse')])
  self.assertEqual(hit['activity']['method'],'read')
  self.assertNotIn(('first','browse'),pairs)
 def test_negative_predicates_never_focus_the_excluded_value(self):
  self.multi_mcp()
  hit=self.center.search({'q':'mcp!=absent && mcp_method=browse'})['items'][0]
  self.assertEqual(hit['activity']['name'],'tinyfish')
  unchanged=self.center.search({'q':'mcp!=absent'})['items'][0]
  self.assertEqual(unchanged['activity']['name'],'first')
  self.assertEqual(unchanged['activity']['focusedCalls'],[])
 def test_matching_skill_frontloads_the_matching_read_without_hiding_other_skills(self):
  code='await tools.exec_command({cmd:"cat /Users/example/.agents/skills/other/SKILL.md"}); await tools.exec_command({cmd:"cat /Users/example/.agents/skills/taste-skill/SKILL.md"});'
  self.upload([self.event('skills','tool_call',1700000002,{'arguments':code},name='exec',namespace='functions')])
  hit=self.center.search({'q':'skill=taste-skill','group':'request'})['groups'][0]['items'][0]
  self.assertEqual(hit['activity']['title'],'读取 taste-skill 说明')
  self.assertEqual(hit['activity']['parameters'][0]['value'],'/Users/example/.agents/skills/taste-skill/SKILL.md')
  self.assertEqual({c['name'] for c in hit['capabilities']},{'other','taste-skill'})
 def test_nonmatching_detail_query_does_not_claim_a_hit_and_invalid_query_fails(self):
  self.multi_mcp();hit=self.center.search({'q':'mcp=tinyfish'})['items'][0]
  detail=self.center.record(hit['id'],query='mcp=absent')['item']
  self.assertEqual(detail['matchBasis'],[])
  self.assertEqual(detail['activity']['focusedCalls'],[])
  self.assertEqual(detail['activity']['name'],'first')
  with self.assertRaises(ValueError):self.center.record(hit['id'],query='mcp=')
 def test_matching_call_beyond_default_preview_is_still_frontloaded(self):
  code=''.join('await tools.mcp__service%d__read({path:"/fixture/%d"});'%(i,i) for i in range(18))
  self.upload([self.event('many-mcps','tool_call',1700000002,{'arguments':code},name='exec',namespace='functions')])
  hit=self.center.search({'q':'mcp=service17'})['items'][0]
  self.assertEqual(hit['activity']['name'],'service17')
  self.assertEqual(len(hit['capabilities']),18)
  self.assertEqual(hit['activity']['calls'][0]['arguments'],{'path':'/fixture/17'})
 def test_mcp_alias_uses_recorded_identity_and_unknown_args_do_not_become_outer_code(self):
  code='await tools.mcp__codex_apps__gmail_list_messages({query:"fixture"}); await tools.mcp__codex_apps__tinyfish_run_web_automation(dynamicArgs);'
  self.upload([self.event('aliases','tool_call',1700000002,{'arguments':code},name='exec',namespace='functions')])
  hit=self.center.search({'q':'mcp=mcp__tinyfish'})['items'][0]
  self.assertEqual(hit['activity']['name'],'tinyfish')
  self.assertEqual(hit['activity']['method'],'tinyfish_run_web_automation')
  self.assertEqual(hit['activity']['parameters'],[])
  self.assertEqual(hit['activity']['focusedCalls'][0]['name'],'codex_apps')
  self.assertEqual(hit['activity']['focusedCalls'][0]['serviceAliases'],['tinyfish'])

if __name__=='__main__':unittest.main()
