"""HTTP delivery checks for the shared Web shell, using isolated stores."""
import test_platform as harness
import unittest


class UnifiedDeliveryTests(unittest.TestCase):
    setUp = harness.PlatformTests.setUp
    tearDown = harness.PlatformTests.tearDown

    def test_shared_assets_have_their_own_mime_and_no_html_fallback(self):
        for path, mime in (('/product_ui.css?v=desktop-revision', 'text/css'),
                           ('/product_ui.js?v=desktop-revision', 'application/javascript')):
            with self.client.open(self.url + path) as response:
                self.assertEqual(response.status, 200)
                self.assertTrue(response.headers['Content-Type'].startswith(mime))
                self.assertIn("script-src 'self'", response.headers['Content-Security-Policy'])
                self.assertNotIn(b'<!doctype html', response.read().lower())

    def test_platform_entry_pages_reference_shared_assets_and_keep_controllers(self):
        for path, controller in (('/', 'workspace.js'), ('/devices', 'devices.js'),
                                 ('/model-data', 'data_center.js'),
                                 ('/model-data/raw', 'model_data.js'),
                                 ('/model-security', 'model_security.js'),
                                 ('/cloud-machines', 'cloud_machines.js'),
                                 ('/packages', 'packages.js'), ('/software', 'software.js')):
            with self.subTest(path=path), self.client.open(self.url + path) as response:
                text = response.read().decode('utf-8')
                self.assertIn('/product_ui.css', text)
                self.assertIn('/product_ui.js', text)
                self.assertIn('/' + controller, text)
                self.assertIn('AgentPair', text)


if __name__ == '__main__':
    unittest.main()
