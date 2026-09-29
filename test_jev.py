import unittest
from agentpair.jev import JevClient,JevError,apply_jev

class JevTests(unittest.TestCase):
    def test_positive_model_cannot_override_missing_delivery(self):
        c=JevClient('test',transport=lambda _: {'answers':{k:{'type':'noul','noul':1.0} for k in ('goal_met','grounded','consistent','delivery')}})
        result=apply_jev(c,{'mode':'review','task':{},'outputs':{}},{'answer':{'finalAnswer':'{"result":"done"}'}})
        self.assertEqual(result['answer']['verdict'],'retry')
    def test_invalid_probability_rejected(self):
        for p in (True,float('nan'),1.5,None):
            c=JevClient('test',transport=lambda _: {'answers':{'x':{'type':'noul','noul':p}}})
            with self.assertRaises(JevError):c.evaluate({}, {'x':{'type':'noul','instructions':'test'}})
    def test_unavailable_cannot_pass(self):
        c=JevClient('test',transport=lambda _: {})
        result=apply_jev(c,{'mode':'review','task':{},'outputs':{}}, {'answer':{'finalAnswer':'已完成'}})
        self.assertEqual(result['answer']['verdict'],'blocked')
    def test_no_key_is_explicit(self):
        result=apply_jev(None,{'mode':'plan'},{'answer':{}})
        self.assertEqual(result['answer']['jev']['status'],'not_configured')
    def test_cloud_creation_requires_positive_decision(self):
        c=JevClient('test',transport=lambda _: {'answers':{k:{'type':'noul','noul':.1} for k in ('external_research','cloud_driver')}})
        result=apply_jev(c,{'mode':'plan','task':{}},{'answer':{'executionMode':'cloud_driver'}})
        self.assertEqual(result['answer']['executionMode'],'local')
