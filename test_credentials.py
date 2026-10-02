import json
import stat
import tempfile
import unittest
from agentpair.credentials import CredentialStore

class CredentialTests(unittest.TestCase):
    def test_save_and_replace_without_disclosure(self):
        with tempfile.TemporaryDirectory() as root:
            store=CredentialStore(root)
            data={'name':'Custom service','purpose':'search','endpoint':'https://api.example.org/v1','docsUrl':'','authType':'custom','secrets':{'X-Key':'private-test-value','email':'private@example.org'}}
            saved=store.save(data)
            self.assertNotIn('private-test-value',json.dumps(saved))
            self.assertNotIn('private@example.org',json.dumps(store.status()))
            store.save(dict(data,id=saved['id'],secrets={'X-Key':''}))
            path=store.root/('service-'+saved['id']+'.json')
            self.assertEqual(json.loads(path.read_text())['secrets'],{'X-Key':'private-test-value'})
            self.assertEqual(stat.S_IMODE(path.stat().st_mode),0o600)
            for changes in ({'id':'../escape'},{'endpoint':'https://api.example.org/?key=secret'},{'secrets':{'bad field':'x'}}):
                with self.assertRaises(ValueError):store.save(dict(data,**changes))
