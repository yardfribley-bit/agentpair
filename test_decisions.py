import unittest
from agentpair.decisions import decide,QUESTIONS

class DecisionTests(unittest.TestCase):
    def good(self):return {'checks':{k:{'value':'yes','reason':'对应交付物'} for k in QUESTIONS}}
    def test_missing_checks_cannot_pass(self):
        self.assertEqual(decide({'verdict':'pass'})['action'],'needs_information')
    def test_all_checks_required(self):
        a=self.good();self.assertEqual(decide(a)['action'],'deliver')
        a['checks']['grounded']['value']='no'
        self.assertEqual(decide(a)['action'],'recheck')
    def test_model_cannot_override_missing_weather(self):
        self.assertEqual(decide(self.good(),{},'weather')['action'],'recheck')
    def test_stale_evidence_overrides_claimed_freshness(self):
        e={'current':{'time':'2000-01-01T00:00','temperature_2m':20},'units':{'temperature_2m':'°C'},'fresh':True,'timezone':'UTC','sourceUrl':'https://example.com'}
        self.assertEqual(decide(self.good(),e,'weather')['action'],'recheck')
