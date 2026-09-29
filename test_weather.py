import datetime
import unittest
from unittest.mock import patch
from agentpair.weather import collect

class WeatherTests(unittest.TestCase):
    def sample(self,stamp):
        return [{'results':[{'name':'Shanghai','latitude':31.22,'longitude':121.46,'country':'China'}]},
                {'current':{'time':stamp,'temperature_2m':25},'current_units':{'temperature_2m':'°C'}}]
    def test_preserves_evidence_and_freshness(self):
        stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M')
        with patch('agentpair.weather.fetch',side_effect=self.sample(stamp)):
            e=collect('Shanghai')
        self.assertTrue(e['fresh']);self.assertEqual(e['current']['temperature_2m'],25)
        self.assertIn('api.open-meteo.com',e['sourceUrl'])
    def test_stale_weather_not_accepted(self):
        with patch('agentpair.weather.fetch',side_effect=self.sample('2020-01-01T00:00')):
            self.assertFalse(collect('Shanghai')['fresh'])
    def test_missing_city(self):
        with patch('agentpair.weather.fetch',return_value={}):
            with self.assertRaises(ValueError):collect('missing')
