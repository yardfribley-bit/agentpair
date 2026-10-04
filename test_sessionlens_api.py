import json
import urllib.error
import unittest
import test_platform as harness

class SessionLensApiTests(unittest.TestCase):
    setUp=harness.PlatformTests.setUp
    tearDown=harness.PlatformTests.tearDown
    call=harness.PlatformTests.call

def test_pipeline(self):
    with self.assertRaises(urllib.error.HTTPError) as error:self.call('/api/sessionlens/events',{'schemaVersion':1,'events':[]})
    self.assertEqual(error.exception.code,401);error.exception.close()
    csrf=self.call('/api/register',{'username':'sessionlens-test','password':'long-test-password-123'})['csrf']
    pair=self.call('/api/devices/pairing',{},csrf)
    device=self.call('/api/endpoint/enroll',{'code':pair['code'],'name':'SessionLens-test'},origin='')
    e={'id':'a'*64,'schemaVersion':1,'sessionId':'codex-test','source':'codex','kind':'user_message','payload':{'message':'test'},'evidence':{'byteStart':0,'byteEnd':50}}
    receipt=self.call('/api/sessionlens/events',{'schemaVersion':1,'events':[e]},bearer=device['token'])
    self.assertEqual(receipt['ids'],[e['id']])
    sessions=self.call('/api/sessionlens/sessions')['items'];self.assertEqual(sessions[0]['events'],1)
    report=self.call('/api/sessionlens/report?device='+receipt['deviceId']+'&session=codex-test')
    self.assertEqual(report['events'][0]['id'],e['id'])
    task=self.call('/api/sessionlens/analyze',{'deviceId':receipt['deviceId'],'sessionId':'codex-test'},csrf)
    self.engine.process(task['taskId']);self.assertNotEqual(self.engine.get(task['taskId'])['status'],'queued')
    self.call('/api/register',{'username':'other-session-user','password':'long-test-password-123'})
    self.assertEqual(self.call('/api/sessionlens/sessions')['items'],[])

SessionLensApiTests.test_sessionlens_pipeline=test_pipeline
