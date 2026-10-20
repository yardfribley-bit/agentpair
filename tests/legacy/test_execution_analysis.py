import unittest
from execution_analysis import analyze


class ExecutionAnalysisTests(unittest.TestCase):
    def snapshot(self, events):
        return {'schemaVersion': 1, 'task': {'taskID': 'fixture-task'}, 'capture': {'state': 'partial'}, 'events': events}

    def event(self, eid, kind, call='c1', session='s1'):
        return {'event_id': eid, 'action_kind': kind, 'tool_call_id': call, 'session_id': session, 'content': 'fixture command', 'tool_name': 'shell'}

    def test_native_call_result_pair(self):
        report = analyze(self.snapshot([self.event('a', 'tool_call'), self.event('b', 'tool_result')]))
        self.assertEqual(report['findings'], [])
        self.assertEqual(report['completion'], 'unknown')
        self.assertEqual(report['timeline'][0]['locator'], '/events/0')

    def test_never_match_other_session(self):
        report = analyze(self.snapshot([self.event('a', 'tool_call'), self.event('b', 'tool_result', session='s2')]))
        self.assertEqual(len(report['findings']), 2)

    def test_missing_result_not_execution_failure(self):
        report = analyze(self.snapshot([self.event('a', 'tool_call')]))
        self.assertEqual(report['findings'][0]['code'], 'missing_tool_result')
        self.assertEqual(report['completion'], 'unknown')

    def test_repetition_with_citations(self):
        report = analyze(self.snapshot([self.event('a', 'tool_call'), self.event('b', 'tool_call', 'c2')]))
        repeated = [f for f in report['findings'] if f['code'] == 'repeated_recorded_call']
        self.assertEqual(repeated[0]['evidenceIDs'], ['a', 'b'])

    def test_duplicate_id_rejected(self):
        with self.assertRaises(ValueError):
            analyze(self.snapshot([self.event('a', 'tool_call'), self.event('a', 'tool_call')]))

    def test_source_event_adapter(self):
        snapshot = self.snapshot([])
        snapshot['sourceEvents'] = [{'id': 'mac-e1', 'op': 'command', 'command': 'fixture only'}]
        report = analyze(snapshot)
        self.assertEqual(report['timeline'][0]['evidenceID'], 'mac-e1')
        self.assertEqual(report['timeline'][0]['locator'], '/sourceEvents/0')


if __name__ == '__main__':
    unittest.main()
