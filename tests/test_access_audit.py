import datetime
from pathlib import Path
import tempfile
import unittest
from agentpair.access_audit import recent_access

class AccessAuditTests(unittest.TestCase):
    def test_time_window_excludes_proxy_and_never_exposes_queries(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'access.log'
            p.write_text('1.2.3.4 - - [07/Oct/2026:02:30:00 +0800] "GET /?token=SECRET HTTP/1.1" 200 3\n'
                         '1.2.3.5 - - [07/Oct/2026:02:30:00 +0800] "GET /vmess-secret HTTP/1.1" 101 3\n'
                         '1.2.3.6 - - [07/Oct/2026:01:00:00 +0800] "GET / HTTP/1.1" 200 3\n')
            result=recent_access(p,10,datetime.datetime.fromisoformat('2026-10-07T02:31:00+08:00'))
            self.assertEqual(len(result['items']),1)
            self.assertEqual(result['items'][0]['ip'],'1.2.3.4')
            self.assertNotIn('SECRET',str(result))
            self.assertEqual(result['items'][0]['account'],'未关联账号')
            with self.assertRaises(ValueError):recent_access(p,100)
