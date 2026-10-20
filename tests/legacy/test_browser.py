import unittest
from agentpair.browser import WebLens
from agentpair.assistant_loop import run


class BrowserTests(unittest.TestCase):
    def test_protocol_and_minimized_observation(self):
        calls=[]
        def request(op,data):
            calls.append((op,data));return {'html':'<h1>Hello</h1><script>secret</script>','title':'Test','shot':'not-forwarded'}
        browser=WebLens({'egressIsolated':True,'allowedOrigins':['https://example.com']},request)
        result=browser.step({'op':'open','url':'https://example.com/'})
        self.assertEqual(result['text'],'Hello')
        self.assertNotIn('shot',result)
        self.assertEqual([c[0] for c in calls],['open','snapshot'])
        with self.assertRaises(ValueError):browser.step({'op':'interact','selector':'#pay'})
        browser.close();self.assertEqual(calls[-1][0],'close')

    def test_fail_closed(self):
        with self.assertRaises(RuntimeError):WebLens({})
        with self.assertRaises(ValueError):WebLens({'egressIsolated':True,'endpoint':'http://remote:8081'})
        browser=WebLens({'egressIsolated':True,'allowedOrigins':[]},lambda *a:{})
        with self.assertRaises(ValueError):browser.step({'op':'open','url':'http://169.254.169.254/'})

    def test_observe_act_loop(self):
        class Browser:
            closed=False
            def step(self,a):return {'text':'Actual page'}
            def close(self):self.closed=True
        browser=Browser();seen=[]
        def decide(messages,token):
            seen.append(str(messages))
            if len(seen)==1:return {'action':{'op':'open','url':'https://example.com'}},{'total_tokens':5}
            return {'answer':'B001 shows Actual page','status':'completed'},{'total_tokens':7}
        out=run({'task':{'engineeringMethod':'pair','executionProfile':'browser'},'history':[]},'fake',lambda:browser,decide)
        self.assertTrue(out['evidence']['complete']);self.assertTrue(browser.closed)
        self.assertIn('Actual page',seen[1]);self.assertEqual(out['usage']['total_tokens'],12)

    def test_no_evidence_cannot_complete(self):
        class Browser:
            def close(self):pass
        out=run({'task':{'engineeringMethod':'pair','executionProfile':'browser'},'history':[]},'fake',Browser,
                lambda *a:({'answer':'done','status':'completed'},{}))
        self.assertFalse(out['evidence']['complete'])
