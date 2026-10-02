import json,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
from agentpair.devices import DeviceStore
from agentpair.credential_threats import candidates,mask

class CredentialTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.store=DeviceStore(Path(self.tmp.name)/'devices.db');self.device=self.store.enroll(self.store.pairing('alice')['code'],'Mac')
 def tearDown(self):self.tmp.cleanup()
 def record(self,n=1,text='云主机登录：root/ExampleValid5931!',session='before',source='workbuddy_network_context',stamp=100,truncated=False):
  r={'id':f'{n:064x}','source':source,'timestamp':stamp,'sessionId':session,'truncated':truncated,'body':json.dumps({'messages':[{'role':'system','content':text},{'role':'user','content':'<user_query>检查报错</user_query>'}]})}
  self.store.ingest_model_context(self.device['token'],{'requests':[r]});return r
 def findings(self):return self.store.credential_threats.inventory()['items']
 def test_natural_credentials_detected_and_masked(self):
  # Use a non-placeholder value for actual format tests.
  self.record(text='云主机登录：root/Kj9sF3p8Qv1! 当前 Lab Token：pR4x9Y2j6N3t8M5a')
  items=self.findings();self.assertEqual(len(items),2)
  encoded=json.dumps(items);self.assertNotIn('Kj9sF3p8Qv1!',encoded);self.assertNotIn('pR4x9Y2j6N3t8M5a',encoded)
  self.assertEqual({i['kind'] for i in items},{'server_password','lab_token'})
  self.assertTrue(all(i['networkRecords']==1 for i in items));
  password=next(i for i in items if i['kind']=='server_password');self.assertEqual(password['evidence'][0]['credentialDisplay'],'Kj9sF3p8****');self.assertEqual(password['evidence'][0]['account'],'root');self.assertNotIn('Kj9sF3p8',json.dumps(self.store.credential_threats.review_packet('alice',password['id'])));
  self.assertEqual(items[0]['evidence'][0]['task'],'检查报错')
 def test_directories_placeholders_code_literals_not_passwords(self):
  for text in ('登录目录 root/.local/share','云主机 root/example_password','authorization = "Bearer" + token','当前 Lab Token：example_token'):
   self.assertFalse(candidates(text),text)
 def test_repeat_records_merged_replay_does_not_inflate(self):
  text='云主机登录：root/Kj9sF3p8Qv1!'
  r=self.record(text=text);self.record(2,text=text,source='workbuddy_generation_context')
  self.store.ingest_model_context(self.device['token'],{'requests':[r]})
  f=self.findings()[0];self.assertEqual(len(self.findings()),1);self.assertEqual((f['networkRecords'],f['contextRecords']),(1,1))
  self.assertNotIn('tag',f)
 def test_unparseable_escaped_newlines_same_credential(self):
  text='当前 Lab Token：pR4x9Y2j6N3t8M5a'
  self.record(text=text)
  r={'id':f'{2:064x}','source':'workbuddy_generation_context','body':'[{"content":"'+text+'\\n\\n**背景**','timestamp':101}
  self.store.ingest_model_context(self.device['token'],{'requests':[r]})
  self.assertEqual(len(self.findings()),1)
 def test_remediation_needs_new_complete_session_reappears(self):
  text='云主机登录：root/Kj9sF3p8Qv1!';self.record(text=text)
  fid=self.findings()[0]['id']
  with self.assertRaises(PermissionError):self.store.credential_threats.report('bob',fid,{'cleanedContext':True})
  with patch('agentpair.credential_threats.time.time',return_value=200):self.store.credential_threats.report('alice',fid,{'cleanedContext':True})
  self.assertEqual(self.findings()[0]['verification']['state'],'waiting_capture')
  with patch('agentpair.devices.time.time',return_value=210):self.record(2,text='普通内容',stamp=210,session='before')
  self.assertEqual(self.findings()[0]['verification']['state'],'waiting_capture')
  with patch('agentpair.devices.time.time',return_value=220):self.record(3,text='普通内容',stamp=220,session='after')
  self.assertEqual(self.findings()[0]['verification']['state'],'not_observed')
  with patch('agentpair.devices.time.time',return_value=230):self.record(4,text=text,stamp=230,session='after')
  f=self.findings()[0];self.assertEqual(f['verification']['state'],'reappeared');self.assertEqual(f['verification']['reappearedRecords'],1)
 def test_truncated_unknown_old_or_future_records_do_not_pass(self):
  self.record(text='当前 Lab Token：pR4x9Y2j6N3t8M5a');fid=self.findings()[0]['id']
  with patch('agentpair.credential_threats.time.time',return_value=200):self.store.credential_threats.report('alice',fid,{'disabledMemory':True})
  for n,session,stamp,truncated in [(2,'new',220,True),(3,'unknown',220,False),(4,'new',150,False),(5,'new',900,False)]:
   with patch('agentpair.devices.time.time',return_value=220):self.record(n,text='普通内容',session=session,stamp=stamp,truncated=truncated)
  self.assertEqual(self.findings()[0]['verification']['state'],'waiting_capture')
 def test_private_keys_do_not_group_by_common_header(self):
  a='-----BEGIN PRIVATE KEY-----\nAAAsecret111\n-----END PRIVATE KEY-----';b=a.replace('AAAsecret111','BBBsecret222')
  self.record(text=a+'\n'+b);self.assertEqual(len(self.findings()),2);self.assertNotIn('AAAsecret111',mask(a))
 def test_targeted_review_packet_is_masked_and_owner_bound(self):
  from agentpair.tasks import TaskEngine
  from test_tasks import FakeBackend
  self.record(text='云主机登录：root/Kj9sF3p8Qv1!');fid=self.findings()[0]['id']
  with self.assertRaises(PermissionError):self.store.credential_threats.review_packet('bob',fid)
  packet=self.store.credential_threats.review_packet('alice',fid)
  self.assertNotIn('Kj9sF3p8Qv1!',json.dumps(packet));self.assertEqual(len(packet['directions']),1)
  backend=FakeBackend();engine=TaskEngine(Path(self.tmp.name)/'tasks.db',backend,start=False)
  task=engine.create('复核','复核凭据',owner='alice',security_evidence=packet)
  self.store.credential_threats.link_review(fid,packet,task['id']);self.assertEqual(self.store.credential_threats.review(fid,engine)['status'],'queued')
  engine.process(task['id']);self.assertTrue(all(e['task']['securityEvidence']==packet for _,e in backend.calls));engine.close()
 def test_old_audit_preview_also_masks_natural_password(self):
  from agentpair.interaction_audit import redact
  self.assertNotIn('Kj9sF3p8Qv1!',redact('云主机登录：root/Kj9sF3p8Qv1!'))

 def test_restart_backfill_idempotent(self):
  self.record(text='当前 Lab Token：pR4x9Y2j6N3t8M5a');before=self.findings()
  store=DeviceStore(Path(self.tmp.name)/'devices.db');self.assertEqual(store.credential_threats.inventory()['items'],before)

if __name__=='__main__':unittest.main()
