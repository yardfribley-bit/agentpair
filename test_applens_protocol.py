import unittest
from agentpair.applens_protocol import CAPABILITIES,validate_manifest

class ProtocolTests(unittest.TestCase):
    def test_all_platforms_declare_limits(self):
        for os in ('Windows','Android','macOS'):
            caps={k:'unsupported' for k in CAPABILITIES};caps['cloud_requests']='available'
            value=validate_manifest({'product':'AppLens','protocolVersion':1,'capabilities':caps})
            self.assertEqual(value['capabilities']['file_events'],'unsupported')
    def test_missing_and_fake_success_rejected(self):
        for caps in ({},{k:'success' for k in CAPABILITIES}):
            with self.assertRaises(ValueError):validate_manifest({'product':'AppLens','protocolVersion':1,'capabilities':caps})
