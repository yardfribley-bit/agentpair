import tempfile,unittest,json
from pathlib import Path
from agentpair.devices import DeviceStore
from agentpair.llm_evidence import classify

class EvidenceTests(unittest.TestCase):
    def test_sensitive_not_confirmed(self):
        item=classify({'source':'workbuddy_transcript','event':'message','role':'user','evidence':{'content':'token=abc123 手机号 13812345678'}})
        self.assertEqual(item['sendEvidence'],'local')
        self.assertEqual(item['sensitivity'],'sensitive')
        self.assertNotIn('abc123',item['redactedContent']);self.assertNotIn('13812345678',item['redactedContent'])
    def test_isolation_and_idempotency(self):
        with tempfile.TemporaryDirectory() as d:
            store=DeviceStore(Path(d)/'d.db');device=store.enroll(store.pairing()['code'],'test')
            payload={'events':[{'source':'workbuddy_hook','event':'PostToolUse','evidence':{'tool_response':'ok'}}]}
            store.ingest_llm_evidence(device['token'],payload);store.ingest_llm_evidence(device['token'],payload)
            view=store.llm_data('admin',device['deviceId'])
            self.assertEqual(len(view['items']),0);self.assertFalse(view['coverage']['requestBody'])
            with self.assertRaises(PermissionError):store.llm_data('other',device['deviceId'])
