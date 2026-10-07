import os,tempfile,unittest
from pathlib import Path
from sessionlens.desktop import Runtime
class UploadRestartTests(unittest.TestCase):
    def test_saved_token_and_destination_survive_restart(self):
        with tempfile.TemporaryDirectory() as root:
            config={'endpoint':'https://example.com/events','sources':{}}
            first=Runtime(root,config,'fixture-token');second=Runtime(root,config)
            self.assertEqual(second.token,'fixture-token');self.assertEqual(second.destination,first.destination)
            if os.name!='nt':self.assertEqual((Path(root)/'device-token').stat().st_mode&0o777,0o600)
