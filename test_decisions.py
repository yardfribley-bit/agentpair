import unittest
import datetime
from agentpair.decisions import decide,QUESTIONS

class DecisionTests(unittest.TestCase):
    def good(self):return {'finalAnswer':'此方案使用集合保留元素首次出现的顺序。','checks':{k:{'value':'yes','reason':'对应交付物'} for k in QUESTIONS}}
    def test_raw_json_or_missing_answer_cannot_complete(self):
        for final in ('','{"temperature":24.2}','```json\n{"temperature":24.2}\n```','查询成功'):
            a=self.good();a['finalAnswer']=final
            self.assertEqual(decide(a)['action'],'recheck')
    def test_weather_must_deliver_actual_value(self):
        stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M')
        e={'current':{'time':stamp,'temperature_2m':24.2},'units':{'temperature_2m':'°C'},'timezone':'UTC','source':'Open-Meteo','sourceUrl':'https://example.com'}
        a=self.good();a['finalAnswer']='上海温度24.2°C，数据时间12:00 UTC，来源Open-Meteo模型估算。'
        self.assertEqual(decide(a,e,'weather')['action'],'deliver')
        a['finalAnswer']=a['finalAnswer'].replace('24.2','28.2')
        self.assertEqual(decide(a,e,'weather')['action'],'recheck')
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
