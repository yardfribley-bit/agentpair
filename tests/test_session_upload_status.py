import tempfile
import unittest
from pathlib import Path
from agentpair.session_lens import SessionStore

class UploadStatusTests(unittest.TestCase):
    def test_failures_are_scoped_and_success_is_a_server_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            store=SessionStore(Path(directory)/'sessions.db')
            store.record_upload_failure(None,401,'device_not_authorized')
            self.assertEqual(store.upload_status('admin')['lastAttempt']['status'],401)
            self.assertIsNone(store.upload_status('alice')['lastAttempt'])
            event={'id':'a'*64,'schemaVersion':1,'source':'codex','sessionId':'s','kind':'user_message','evidence':{}}
            store.ingest({'id':'d','owner':'alice'},{'schemaVersion':1,'events':[event]})
            success=store.upload_status('alice')['lastSuccess']
            self.assertEqual(success['accepted'],1)
            self.assertEqual(success['device'],'d')
            store.record_upload_failure({'id':'d','owner':'alice'},400,'invalid_batch')
            self.assertEqual(store.upload_status('alice')['lastAttempt']['status'],400)
            self.assertEqual(store.upload_status('alice')['lastSuccess'],success)
            self.assertIsNone(store.upload_status('bob')['lastSuccess'])
            self.assertIsNone(store.upload_status('admin','other')['lastAttempt'])
