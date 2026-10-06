"""External blockers stop model rework; repairable evidence still gets reviewed."""
import copy
import tempfile
import unittest
from pathlib import Path

from agentpair.tasks import TaskEngine, _blocking_reason, _plan_blocking_reason


def passed():
    return {'summary': 'The requested result is verified.', 'verdict': 'pass',
            'decision': {'action': 'deliver', 'checks': [
                {'id': key, 'value': 'yes'} for key in
                ('goal_met', 'grounded', 'consistent', 'delivery', 'readable_answer')]}}


class ScriptedBackend:
    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []

    def estimate(self, envelope):
        return .10

    def call(self, role, envelope, timeout):
        self.calls.append((role, copy.deepcopy(envelope)))
        expected_stage, answer = self.answers.pop(0)
        if envelope['mode'] != expected_stage:
            raise AssertionError(f"Expected {expected_stage}, got {envelope['mode']}")
        return {'answer': copy.deepcopy(answer)}


class TaskPauseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.engines = []

    def tearDown(self):
        for engine in self.engines:
            engine.close()
        self.tmp.cleanup()

    def engine(self, answers):
        backend = ScriptedBackend(answers)
        engine = TaskEngine(Path(self.tmp.name) / f'{len(self.engines)}.db',
                            backend, budget=3, start=False)
        self.engines.append(engine)
        return engine, backend

    def run_review(self, review, later=()):
        engine, backend = self.engine([
            ('plan', {'summary': 'Collect evidence', 'tool': {'name': 'none'},
                      'executionMode': 'local'}),
            ('driver', {'summary': 'Collected the available evidence.'}),
            ('review', review), *later])
        task = engine.create('Evidence review', 'Verify the requested result')
        engine.process(task['id'])
        return engine.get(task['id']), backend

    def test_local_cloud_plan_stops_second_round_before_driver(self):
        # Synthetic reproduction of the saved record: a new Windows request
        # follows an accepted local round; the plan selects cloud_driver/none.
        engine, backend = self.engine([
            ('plan', {'summary': 'Answer locally', 'tool': {'name': 'none'},
                      'executionMode': 'local'}),
            ('driver', {'summary': 'The requested result is available.'}),
            ('review', passed()),
            ('plan', {'summary': 'Prepare the requested Windows installation',
                      'steps': ['Create the instance', 'Install the application'],
                      'questions': ['Specify the instance configuration'],
                      'tool': {'name': 'none'}, 'executionMode': 'cloud_driver'})])
        task = engine.create('Installation request', 'Explain the current setup',
                             engineering_method='local', execution_profile='none')
        engine.process(task['id'])
        engine.followup(task['id'], 'Create a Windows instance and install the application')
        engine.process(task['id'])
        done = engine.get(task['id'])
        self.assertEqual(done['status'], 'unsupported_capability')
        self.assertEqual(done['blockingReason']['requiredExecutionMode'], 'cloud_driver')
        self.assertEqual([c[1]['mode'] for c in backend.calls], ['plan', 'driver', 'review', 'plan'])
        self.assertEqual(set(done['results'][1]['outputs']), {'plan'})
        self.assertFalse(any(e['kind'] == 'rework' and e['round'] == 2 for e in done['events']))
        self.assertEqual((done['engineeringMethod'], done['executionProfile']), ('local', 'none'))
        self.assertAlmostEqual(engine.usage()['estimatedReservedCNY'], .40)

    def test_explicit_review_blockers_override_actionable_rework(self):
        cases = [('missing_user_input', 'needs_information'),
                 ('needs_information', 'needs_information'),
                 ('unsupported_capability', 'unsupported_capability'),
                 ('awaiting_confirmation', 'awaiting_confirmation')]
        for reason_type, status in cases:
            for nested in (False, True):
                with self.subTest(reason_type=reason_type, nested=nested):
                    reason = {'type': reason_type, 'message': 'External prerequisite required'}
                    review = {'summary': 'Paused', 'verdict': 'retry',
                              'corrections': ['Collect the result after the prerequisite'],
                              'nextSteps': ['Resolve the external prerequisite'],
                              'decision': {'action': 'needs_information'}}
                    (review['decision'] if nested else review)['blockingReason'] = reason
                    done, backend = self.run_review(review)
                    self.assertEqual(done['status'], status)
                    self.assertEqual(done['blockingReason'], reason)
                    self.assertEqual(len(backend.calls), 3)
                    self.assertFalse(any(e['kind'] == 'rework' for e in done['events']))
                    self.assertEqual([e['stage'] for e in done['events'] if e['kind'] == 'paused'], ['review'])

    def test_requires_user_input_is_an_explicit_stop(self):
        for nested in (False, True):
            with self.subTest(nested=nested):
                review = {'summary': 'Choose the target', 'verdict': 'blocked',
                          'corrections': ['Use the selected target'],
                          'decision': {'action': 'needs_information'}}
                (review['decision'] if nested else review)['requiresUserInput'] = True
                done, backend = self.run_review(review)
                self.assertEqual(done['status'], 'needs_information')
                self.assertEqual(len(backend.calls), 3)

    def test_explicit_capability_or_confirmation_actions_stop(self):
        for action in ('unsupported_capability', 'awaiting_confirmation'):
            with self.subTest(action=action):
                done, backend = self.run_review({'summary': 'External prerequisite required',
                    'verdict': 'retry', 'decision': {'action': action}, 'corrections': ['Retry later']})
                self.assertEqual(done['status'], action)
                self.assertEqual(len(backend.calls), 3)

    def test_driver_blocker_stops_before_another_model_call(self):
        engine, backend = self.engine([
            ('plan', {'summary': 'Attempt the operation', 'tool': {'name': 'none'}}),
            ('driver', {'summary': 'Confirmation is required',
                        'blockingReason': {'type': 'awaiting_confirmation'}})])
        task = engine.create('Operation', 'Perform the operation')
        engine.process(task['id'])
        done = engine.get(task['id'])
        self.assertEqual(done['status'], 'awaiting_confirmation')
        self.assertEqual(len(backend.calls), 2)
        self.assertEqual(set(done['results'][0]['outputs']), {'plan', 'driver'})

    def test_unknown_evidence_can_be_collected_and_reviewed_again(self):
        review = {'summary': 'The source snapshot cannot yet be verified', 'verdict': 'blocked',
                  'corrections': ['Fetch the source snapshot and retain its commit identifier'],
                  'decision': {'action': 'needs_information', 'checks': [
                      {'id': 'grounded', 'value': 'unknown'}]}}
        done, backend = self.run_review(review, [
            ('driver', {'summary': 'Fetched the snapshot with its commit identifier.'}),
            ('review', passed())])
        self.assertEqual(done['status'], 'completed')
        self.assertEqual(len(backend.calls), 5)
        self.assertEqual([e['attempt'] for e in done['events'] if e['kind'] == 'rework'], [1])
        self.assertEqual(backend.calls[3][1]['outputs']['review']['answer']['corrections'],
                         review['corrections'])

    def test_unknown_evidence_rework_remains_bounded(self):
        review = {'summary': 'Snapshot unavailable', 'verdict': 'blocked',
                  'nextSteps': ['Retry fetching the fixed source snapshot'],
                  'decision': {'action': 'needs_information'}}
        done, backend = self.run_review(review, [
            ('driver', {'summary': 'Snapshot unavailable'}), ('review', review),
            ('driver', {'summary': 'Snapshot unavailable'}), ('review', review)])
        self.assertEqual(done['status'], 'blocked')
        self.assertEqual(len(backend.calls), 7)
        self.assertEqual([e['attempt'] for e in done['events'] if e['kind'] == 'rework'], [1, 2])

    def test_pass_guard_still_reworks_explicit_evidence_gaps(self):
        review = passed()
        review['blockingGaps'] = ['A required execution receipt is absent']
        review['corrections'] = ['Collect the execution receipt']
        done, backend = self.run_review(review, [
            ('driver', {'summary': 'Collected the requested receipt.'}), ('review', passed())])
        self.assertEqual(done['status'], 'completed')
        self.assertEqual(len(backend.calls), 5)
        self.assertTrue(any(e['kind'] == 'acceptance_guard' for e in done['events']))

    def test_followup_clears_prior_blocker_and_can_complete(self):
        engine, backend = self.engine([
            ('plan', {'summary': 'Select the target', 'requiresUserInput': True}),
            ('plan', {'summary': 'Use the selected target', 'tool': {'name': 'none'}}),
            ('driver', {'summary': 'Verified the selected target.'}), ('review', passed())])
        task = engine.create('Target check', 'Verify my target')
        engine.process(task['id'])
        self.assertEqual(engine.get(task['id'])['status'], 'needs_information')
        engine.followup(task['id'], 'Use the specified target')
        engine.process(task['id'])
        done = engine.get(task['id'])
        self.assertEqual(done['status'], 'completed')
        self.assertNotIn('blockingReason', done)
        self.assertEqual(len(backend.calls), 4)

    def test_cloud_capability_fallback_is_limited_to_unroutable_local_plan(self):
        plan = {'executionMode': 'cloud_driver', 'tool': {'name': 'none'}}
        self.assertEqual(_plan_blocking_reason(plan, {'engineeringMethod': 'local'})['type'],
                         'unsupported_capability')
        self.assertIsNone(_plan_blocking_reason(plan, {'engineeringMethod': 'pair'}))
        self.assertIsNone(_plan_blocking_reason({**plan, 'tool': {'name': 'cloud_management'}},
                                               {'engineeringMethod': 'local'}))
        self.assertIsNone(_plan_blocking_reason({**plan, 'executionMode': 'local'},
                                               {'engineeringMethod': 'local'}))
        self.assertIsNone(_blocking_reason({'decision': {'action': 'needs_information'}}))


if __name__ == '__main__':
    unittest.main()
