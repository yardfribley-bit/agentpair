import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
import threading
from http.server import ThreadingHTTPServer
from urllib.request import Request,urlopen
from unittest.mock import patch
from agentpair.package_cache import PackageCache, PackageNode
from agentpair.package_cache import handler_for


class Response(io.BytesIO):
    def geturl(self):return 'https://downloads.example.org/setup.exe'


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.cache=PackageCache(self.root/'cache')
        self.content=b'verified installer';self.key=hashlib.sha256(self.content).hexdigest()
        self.recipe={'id':'app-windows','platform':'Windows','url':'https://downloads.example.org/setup.exe','sha256':self.key}

    def download(self,content):
        record={'sha256':self.key,'state':'downloading','bytes':0,'startedAt':__import__('time').time()}
        with patch('agentpair.package_cache.urlopen',return_value=Response(content)):
            self.cache.download(self.recipe,record)
        return record

    def test_verified_download_published_and_reused(self):
        record=self.download(self.content)
        self.assertEqual(record['state'],'cached')
        self.assertEqual(self.cache.path(self.key).read_bytes(),self.content)
        with patch('agentpair.package_cache.urlopen',side_effect=AssertionError('must not download')):
            self.assertEqual(self.cache.prepare(self.recipe)['state'],'cached')

    def test_mismatch_never_published(self):
        record=self.download(b'wrong content')
        self.assertEqual(record['state'],'failed');self.assertFalse(self.cache.path(self.key).exists())
        self.assertFalse(list(self.cache.directory.glob('*.part')))

    def test_size_limit_and_invalid_hash(self):
        self.cache.max_bytes=2
        self.assertEqual(self.download(self.content)['state'],'failed')
        with self.assertRaises(ValueError):self.cache.path('../secret')
        with self.assertRaises(ValueError):self.cache.prepare({**self.recipe,'url':'http://example.org/a'})

    def test_restart_and_missing_file_do_not_claim_cached(self):
        self.cache.save({'sha256':self.key,'state':'downloading'})
        self.assertEqual(self.cache.records()[0]['state'],'interrupted')
        self.cache.save({'sha256':self.key,'state':'cached'})
        self.assertEqual(self.cache.records()[0]['state'],'missing')

    def test_node_resolves_verified_only_and_falls_back_offline(self):
        path=self.root/'node.json';path.write_text(json.dumps({'url':'https://cache.example.org','token':'private-test-token','deliveryMode':'direct','downloadReady':True}));path.chmod(0o600)
        node=PackageNode(path)
        with patch.object(node,'snapshot',return_value={'items':[{'sha256':self.key,'state':'cached'}]}):
            resolved=node.resolve(self.recipe)
        self.assertEqual(resolved['officialUrl'],self.recipe['url'])
        self.assertTrue(resolved['url'].endswith(self.key))
        with patch.object(node,'snapshot',return_value={'items':[]}):self.assertEqual(node.resolve(self.recipe),self.recipe)
        self.assertEqual(PackageNode(self.root/'absent').snapshot()['state'],'unconfigured')

    def test_unverified_direct_download_falls_back_without_navigator_transfer(self):
        path=self.root/'node.json';path.write_text(json.dumps({'url':'https://cache.example.org','publicUrl':'https://navigator.example.org/package-files','token':'test'}));path.chmod(0o600)
        node=PackageNode(path)
        with patch.object(node,'snapshot',return_value={'items':[{'sha256':self.key,'state':'cached'}]}):
            self.assertEqual(node.resolve(self.recipe),self.recipe)

    def test_direct_head_and_range(self):
        self.download(self.content)
        server=ThreadingHTTPServer(('127.0.0.1',0),handler_for(self.cache,'test-token'))
        worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        try:
            url=f'http://127.0.0.1:{server.server_port}/packages/{self.key}'
            with urlopen(Request(url,method='HEAD')) as response:
                self.assertEqual(int(response.headers['Content-Length']),len(self.content))
                self.assertEqual(response.read(),b'')
            with urlopen(Request(url,headers={'Range':'bytes=0-7'})) as response:
                self.assertEqual(response.status,206)
                self.assertEqual(response.read(),self.content[:8])
        finally:server.shutdown();server.server_close();worker.join()
