import unittest,tempfile,json
from pathlib import Path
from agentpair.devices import DeviceStore
class IdentityTests(unittest.TestCase):
 def test_repair_reuses_installation_but_different_accounts_do_not(self):
  with tempfile.TemporaryDirectory() as t:
   s=DeviceStore(Path(t)/'d.db');key='12345678-1234-1234-1234-123456789012'
   a=s.enroll(s.pairing('alice')['code'],'same',installation_id=key)
   b=s.enroll(s.pairing('alice')['code'],'same',a['token'],key)
   self.assertEqual(a['deviceId'],b['deviceId']);self.assertEqual(len(s.list('alice')),1)
   with self.assertRaises(PermissionError):s.identity(a['token'])
   c=s.enroll(s.pairing('bob')['code'],'same',b['token'],key)
   self.assertNotEqual(b['deviceId'],c['deviceId'])
 def test_explicit_merge_preserves_upload_tokens_and_old_links(self):
  with tempfile.TemporaryDirectory() as t:
   s=DeviceStore(Path(t)/'d.db');a=s.enroll(s.pairing()['code'],'mac');b=s.enroll(s.pairing()['code'],'mac')
   r={'id':'a'*64,'source':'workbuddy_generation_context','body':json.dumps([{'role':'user','content':'hello'}])}
   s.ingest_model_context(a['token'],{'requests':[r]});s.merge_registrations('admin',b['deviceId'],[a['deviceId']])
   self.assertEqual(len(s.list()),1);self.assertEqual(s.identity(a['token'])['id'],b['deviceId'])
   self.assertEqual(s.interaction_audit('admin',a['deviceId'])['totalRecords'],1)
   s.report(a['token'],{'os':'macOS','processes':[],'applications':[]})
   self.assertEqual(s.get(b['deviceId'])['snapshot']['os'],'macOS')
   s.revoke(b['deviceId']);self.assertFalse(s.list())
   with self.assertRaises(PermissionError):s.identity(a['token'])
 def test_global_audit_has_no_control_or_secret_credentials(self):
  with tempfile.TemporaryDirectory() as t:
   s=DeviceStore(Path(t)/'d.db');d=s.enroll(s.pairing('alice')['code'],'mac')
   s.ingest_model_context(d['token'],{'requests':[{'id':'a'*64,'source':'workbuddy_generation_context','body':json.dumps([{'role':'user','content':'api_key=sk-abcdefghijklmnopqrstuvwx'}])}]})
   self.assertNotIn('token',json.dumps(s.audit_inventory()))
   with self.assertRaises(PermissionError):s.interaction_audit('bob',d['deviceId'])
   self.assertNotIn('sk-abcdefghijklmnopqrstuvwx',json.dumps(s.interaction_audit('bob',d['deviceId'],global_view=True)))
   with self.assertRaises(PermissionError):s.revoke(d['deviceId'],'bob')
