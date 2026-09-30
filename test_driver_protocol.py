import unittest
from agentpair.driver_protocol import DriverTask, model_request, result


class DriverProtocolTests(unittest.TestCase):
    def test_missing_evidence_cannot_claim_completion(self):
        with self.assertRaises(ValueError):
            result('t1', 'completed', missing=['network'])

    def test_model_request_is_bounded_and_hashable(self):
        task=DriverTask('t1','inspect app',('processes',),'https://model.invalid','deepseek-v4-flash','2099-01-01')
        body,digest=model_request(task,[{'name':'app.exe'}])
        self.assertIn(b'processes',body);self.assertEqual(len(digest),64)


if __name__ == '__main__': unittest.main()
