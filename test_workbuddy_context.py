import base64,datetime,hashlib,json,tempfile,unittest
from pathlib import Path
from macos.workbuddy_context import metadata_for,record_integrity
from macos.workbuddy_network_context import project
class ContextCollectorTests(unittest.TestCase):
 def test_metadata_unique_and_ambiguous(self):
  row={'pid':7,'timestamp':10,'model':'hy3','sessionId':'session','sending':True}
  self.assertEqual(metadata_for({}, {'workerPid':7},[row],10)['model'],'hy3')
  self.assertIsNone(metadata_for({}, {'workerPid':7},[row,{**row,'model':'other'}],10)['model'])
  self.assertIsNone(metadata_for({}, {'workerPid':8},[row],10)['model'])
 def test_utf16_limit_and_parse(self):
  self.assertEqual(record_integrity('[]')['recordStatus'],'parseable')
  self.assertEqual(record_integrity('😀'*50000+'...')['recordStatus'],'truncated')
  self.assertEqual(record_integrity('['+'😀'*50001)['recordStatus'],'unparseable')
  self.assertEqual(record_integrity(json.dumps({'messages':[], 'padding':'a'*120000}))['recordStatus'],'parseable')
  self.assertEqual(record_integrity('bad')['recordStatus'],'unparseable')
 def test_full_body_over_trace_limit(self):
  body=json.dumps({'model':'hy3','messages':[{'role':'user','content':'a'*150000+'END_MARKER'}]}).encode()
  record={'host':'copilot.tencent.com','path':'/v2/chat/completions','observedAt':'2026-10-01T10:00:00Z','flowID':'test','requestBodyBase64':base64.b64encode(body).decode(),'requestSHA256':hashlib.sha256(body).hexdigest(),'declaredContentLength':len(body),'capturedWireBodyBytes':len(body)}
  r=project(record,0);self.assertTrue(r['body'].endswith('END_MARKER"}]}'));self.assertTrue(r['wireLengthMatched']);self.assertEqual(r['model'],'hy3')
  from agentpair.model_context import validate_request
  self.assertEqual(validate_request(r)['body'],body.decode())
  self.assertIsNone(project({**record,'requestSHA256':'bad'},0))
  self.assertIsNone(project({**record,'path':'/agenttool/v1/intent/recall'},0))
  self.assertFalse(project({**record,'declaredContentLength':None},0)['complete'])
