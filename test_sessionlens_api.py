import urllib.error
import unittest
import test_platform as harness


class SyntheticInsightModel:
    def __init__(self):
        self.calls = []

    def __call__(self, system, data, max_tokens=4000):
        stage = len(data['previousStages'])
        self.calls.append(stage)
        if stage == 0:
            return {'summary': '核对合成消息', 'questions': ['日志记录了哪些需求？']}
        ref = data['evidence']['fragments'][0]['evidenceId']
        report = {
            'title': '合成会话检查', 'summary': '日志记录了用户消息，尚无交付记录。',
            'goal': '核对用户消息', 'outcome': '仅记录到用户消息', 'completion': 'unknown',
            'story': [{'title': '用户提出需求', 'action': '记录收到用户消息',
                       'result': '尚无后续交付记录', 'evidenceRefs': [ref]}],
            'findings': [], 'goodPractices': [], 'limitations': ['仅合成日志证据'],
        }
        return {'summary': '核对已采集证据', 'report': report, 'verdict': 'pass'}


class SessionLensApiTests(unittest.TestCase):
    tearDown = harness.PlatformTests.tearDown
    call = harness.PlatformTests.call

    def setUp(self):
        harness.PlatformTests.setUp(self)
        self.model = SyntheticInsightModel()
        self.engine.session_insights.model = self.model

    def test_sessionlens_pipeline(self):
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.call('/api/sessionlens/events', {'schemaVersion': 1, 'events': []})
        self.assertEqual(error.exception.code, 401)
        error.exception.close()
        csrf = self.call('/api/register', {
            'username': 'sessionlens-test', 'password': 'long-test-password-123'})['csrf']
        pair = self.call('/api/devices/pairing', {}, csrf)
        device = self.call('/api/endpoint/enroll', {
            'code': pair['code'], 'name': 'SessionLens-test'}, origin='')
        event = {'id': 'a' * 64, 'schemaVersion': 1, 'sessionId': 'codex-test',
                 'source': 'codex', 'kind': 'user_message', 'payload': {'message': 'test'},
                 'evidence': {'byteStart': 0, 'byteEnd': 50}}
        receipt = self.call('/api/sessionlens/events', {
            'schemaVersion': 1, 'events': [event]}, bearer=device['token'])
        self.assertEqual(receipt['ids'], [event['id']])
        sessions = self.call('/api/sessionlens/sessions')['items']
        self.assertEqual(sessions[0]['events'], 1)
        report_path = ('/api/sessionlens/report?device=' + receipt['deviceId'] +
                       '&session=codex-test&source=codex')
        report = self.call(report_path)
        self.assertEqual(report['events'][0]['id'], event['id'])
        self.assertEqual(len(report['insights']), 1)
        insight = report['insights'][0]
        self.assertRegex(insight['revision'], r'^[a-f0-9]{64}$')
        self.assertEqual(insight['state'], 'waiting')
        self.assertEqual(self.model.calls, [])
        payload = {'deviceId': receipt['deviceId'], 'sessionId': 'codex-test',
                   'source': 'codex', 'revision': insight['revision'], 'shareConfirmed': True}
        missing_revision = {key: value for key, value in payload.items() if key != 'revision'}
        missing_confirmation = {key: value for key, value in payload.items() if key != 'shareConfirmed'}
        for rejected in (missing_revision, missing_confirmation, dict(payload, shareConfirmed=False)):
            with self.subTest(payload=rejected), self.assertRaises(urllib.error.HTTPError) as error:
                self.call('/api/sessionlens/analyze', rejected, csrf)
            self.assertEqual(error.exception.code, 400)
            error.exception.close()
            self.assertEqual(self.model.calls, [])
            self.assertEqual(self.engine.session_insights.get(insight['id'])['state'], 'waiting')

        job = self.call('/api/sessionlens/analyze', payload, csrf)
        self.assertEqual(job['id'], insight['id'])
        self.assertEqual(job['state'], 'queued')
        self.assertEqual(job['source'], 'codex')
        self.assertEqual(job['revision'], insight['revision'])
        self.assertEqual(self.model.calls, [])
        self.engine.session_insights.process(job['id'])
        completed = self.call('/api/insights/' + job['id'])
        self.assertEqual(completed['state'], 'completed')
        self.assertEqual(completed['analyzedRevision'], insight['revision'])
        self.assertTrue(completed['reviewValidated'])
        self.assertEqual(self.model.calls, [0, 1, 2])
        self.assertEqual(self.engine.backend.calls, [])
        self.assertEqual(self.call(report_path)['semanticAnalysis'], 'completed')
        self.call('/api/register', {
            'username': 'other-session-user', 'password': 'long-test-password-123'})
        self.assertEqual(self.call('/api/sessionlens/sessions')['items'], [])
