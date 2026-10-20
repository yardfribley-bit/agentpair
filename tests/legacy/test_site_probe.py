import unittest
from agentpair.site_probe import allowed_asset, markers
from agentpair.site_worker import validate_findings


class SiteProbeTests(unittest.TestCase):
    def test_same_origin_only(self):
        self.assertEqual(allowed_asset('/app.js'),'http://102.68.79.149/app.js')
        for path in ('http://127.0.0.1/a','http://169.254.169.254/','https://102.68.79.149/a','http://other.example/a','/a?token=secret','http://a:b@102.68.79.149/a'):
            self.assertIsNone(allowed_asset(path))
    def test_fingerprint_not_generic_word(self):
        self.assertEqual(markers('we react to requests'),[])
        self.assertIn('nextjs_marker',markers('/_next/static/test.js'))
    def test_unknown_reference_rejected(self):
        with self.assertRaises(ValueError):
            validate_findings({'findings':[{'confidence':'high','evidenceRefs':['made-up']}]},{'evidence':[{'ref':'W001'}]})


if __name__=='__main__': unittest.main()
