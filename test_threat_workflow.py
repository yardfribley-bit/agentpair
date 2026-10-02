from test_credential_threats import CredentialTests
from unittest.mock import patch
class WorkflowTests(CredentialTests):
 def test_scope_transitions_close_reopen(self):
  self.record(text='云主机登录：root/Kj9sF3p8Qv1!');fid=self.findings()[0]['id'];store=self.store.credential_threats
  with self.assertRaises(PermissionError):store.manage('bob',fid,'assign',{'assignee':'负责人','due':'2026-10-09'})
  store.manage('bob',fid,'assign',{'assignee':'负责人','due':'2026-10-09'},admin=True)
  with self.assertRaises(ValueError):store.manage('alice',fid,'close',{'note':'已处理','credentialRevokedConfirmed':True})
  with patch('agentpair.credential_threats.time.time',return_value=200):store.manage('alice',fid,'submit',{'note':'已清理背景','actions':{'cleanedContext':True}})
  self.assertEqual(self.findings()[0]['workflow']['state'],'pending_verification')
  with self.assertRaises(ValueError):store.manage('alice',fid,'close',{'note':'已处理','credentialRevokedConfirmed':True})
  with patch('agentpair.devices.time.time',return_value=220):self.record(2,text='普通内容',stamp=220,session='new')
  with self.assertRaises(ValueError):store.manage('alice',fid,'close',{'note':'已处理'})
  store.manage('alice',fid,'close',{'note':'新会话未出现，已另行确认撤销','credentialRevokedConfirmed':True})
  self.assertEqual(self.findings()[0]['workflow']['state'],'closed')
  with patch('agentpair.devices.time.time',return_value=230):self.record(3,text='云主机登录：root/Kj9sF3p8Qv1!',stamp=230,session='again')
  self.assertEqual(self.findings()[0]['workflow']['state'],'reopened')
 def test_assignment_validation_and_persistence(self):
  self.record(text='云主机登录：root/Kj9sF3p8Qv1!');fid=self.findings()[0]['id'];store=self.store.credential_threats
  for d in ({'assignee':'','due':'2026-10-09'},{'assignee':'张某','due':'bad'}):
   with self.assertRaises(ValueError):store.manage('alice',fid,'assign',d)
  store.manage('alice',fid,'assign',{'assignee':'张某','due':'2026-10-09'})
  self.assertEqual(self.findings()[0]['workflow']['assignee'],'张某')
  self.assertEqual(len(self.findings()[0]['workflow']['events']),1)
