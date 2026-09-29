import base64
import sys
import types
import unittest
from unittest.mock import patch
from agentpair.repository import collect, repository_name, allowed
from agentpair.decisions import decide


class RepositoryTests(unittest.TestCase):
    def test_url_boundary(self):
        self.assertEqual(repository_name('https://github.com/org/repo.git'), 'org/repo')
        for url in ('http://github.com/o/r', 'https://github.com.evil/o/r', 'file:///etc/passwd',
                    'https://github.com/o/r?token=x'):
            with self.assertRaises(ValueError): repository_name(url)

    def test_paths(self):
        for path in ('../x.py', '/etc/a.py', '.env', 'runtime/private.json', 'a/.git/x.py'):
            self.assertFalse(allowed(path))
        self.assertTrue(allowed('src/app.tsx'))

    def test_snapshot(self):
        responses=[{'sha':'a'*40}, {'tree':[
            {'path':'app.py','type':'blob','mode':'100644','sha':'b'*40,'size':10},
            {'path':'link.py','type':'blob','mode':'120000','sha':'c'*40,'size':10}]},
            {'encoding':'base64','content':base64.b64encode(b'print(1)\n').decode()}]
        def ingest(directory, **kwargs):
            from pathlib import Path
            self.assertEqual((Path(directory)/'app.py').read_text(), '1: print(1)')
            return 'summary','tree','numbered source'
        with patch('agentpair.repository.api',side_effect=responses) as api, patch.dict(sys.modules, {'gitingest':types.SimpleNamespace(ingest=ingest)}):
            result=collect({'url':'https://github.com/o/r','ref':'feature/ui','paths':['app.py','link.py']}, 'Read https://github.com/o/r')
        self.assertEqual(result['commit'],'a'*40)
        self.assertEqual(result['omitted'],['link.py'])
        self.assertEqual(result['files'][0]['evidenceId'],'G001')
        self.assertIn('feature%2Fui',api.call_args_list[0].args[0])

    def test_unrequested_repo(self):
        with self.assertRaises(ValueError):
            collect({'url':'https://github.com/o/r'}, 'Read another project')

    def test_fail_closed(self):
        result=decide({'finalAnswer':'这里没有真正读取源码，不能确认。','checks':{}}, {'error':'HTTPError'}, 'github_repository')
        self.assertEqual(result['action'],'needs_information')


if __name__=='__main__': unittest.main()
