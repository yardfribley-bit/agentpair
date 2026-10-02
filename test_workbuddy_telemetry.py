import importlib.util,json,tempfile,unittest
from pathlib import Path
spec=importlib.util.spec_from_file_location('telemetry',Path(__file__).parent/'macos/workbuddy_telemetry.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class TelemetryTests(unittest.TestCase):
 def test_allowlist_dedup_otlp(self):
  record={'timestamp':1790840000000,'content':'secret prompt','providerData':{'conversationRequestId':'abc','model':'test-model','usage':{'inputTokens':12,'outputTokens':3},'reasoning':'secret'}}
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);(root/'source/session').mkdir(parents=True);(root/'source/session/data.jsonl').write_text(json.dumps(record)+'\n'+json.dumps(record)+'\n')
   first=m.collect(root/'source',root/'out');second=m.collect(root/'source',root/'out')
   self.assertEqual(first['requests'],1);self.assertEqual(second['newRequests'],0)
   data=(root/'out/workbuddy-otlp.json').read_text();self.assertNotIn('secret',data);self.assertIn('gen_ai.usage.input_tokens',data)
 def test_usage_missing_not_zero(self):
  self.assertIsNone(m.normalize({'providerData':{'model':'x'}}))
  r=m.normalize({'timestamp':1790840000000,'providerData':{'conversationRequestId':'abc','usage':{}}})
  self.assertIsNone(r['inputTokens']);self.assertIsNone(r['cost']);self.assertIsNone(r['latency'])
 def test_server_ingest_identity_and_dedup(self):
  from agentpair.devices import DeviceStore
  with tempfile.TemporaryDirectory() as t:
   store=DeviceStore(Path(t)/'devices.db')
   device=store.enroll(store.pairing()['code'],'telemetry test')
   row=m.normalize({'timestamp':1790840000000,'providerData':{'conversationRequestId':'abc','usage':{'inputTokens':12}}})
   payload=m.otlp([row])
   self.assertEqual(store.ingest_otlp(device['token'],payload)['applens']['storedSpans'],1)
   view=store.llm_data('admin',device['deviceId'])
   self.assertEqual(view['calls'][0]['inputTokens'],'12')
   self.assertEqual(view['items'],[])
   self.assertFalse(view['coverage']['requestBody'])
   with self.assertRaises(PermissionError):store.llm_data('another-user',device['deviceId'])
   self.assertEqual(store.ingest_otlp(device['token'],payload)['applens']['storedSpans'],1)
   with self.assertRaises(PermissionError):store.ingest_otlp('invalid',payload)
   payload['resourceSpans'][0]['scopeSpans'][0]['spans'][0]['attributes'].append({'key':'gen_ai.prompt','value':{'stringValue':'secret'}})
   with self.assertRaises(ValueError):store.ingest_otlp(device['token'],payload)
