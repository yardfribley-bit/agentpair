import json
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from pathlib import Path
from http.server import ThreadingHTTPServer
from agentpair.web import project_run, handler_for


class WebTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.write('manifest.json', {'target': 'http://102.68.79.149/', 'runID': 'test',
                                    'relayToken': 'DO_NOT_EXPOSE', 'hosts': []})

    def tearDown(self):
        self.temp.cleanup()

    def write(self, name, value):
        (self.root/name).write_text(json.dumps(value))

    def test_no_credential_export(self):
        data = project_run(self.root)
        self.assertNotIn('DO_NOT_EXPOSE', json.dumps(data))
        self.assertEqual(len(data['stages']), 3)
        self.assertFalse(data['stages'][0]['complete'])

    def test_refuse_enterprise_context(self):
        self.write('manifest.json', {'target': 'enterprise audit'})
        with self.assertRaises(ValueError):
            project_run(self.root)

    def test_readonly_routes_and_security_headers(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler_for(self.root))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = 'http://127.0.0.1:'+str(server.server_port)
        try:
            with urllib.request.urlopen(base+'/api/run') as response:
                self.assertEqual(response.headers['Cache-Control'], 'no-store')
                self.assertIn("script-src 'self'", response.headers['Content-Security-Policy'])
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(base+'/manifest.json')
            self.assertEqual(error.exception.code, 404)
            error.exception.close()
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(urllib.request.Request(base+'/api/run', data=b'{}'))
            self.assertEqual(error.exception.code, 501)
            error.exception.close()
        finally:
            server.shutdown(); server.server_close(); thread.join()


if __name__ == '__main__':
    unittest.main()
