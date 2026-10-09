"""Real loopback API tests with synthetic model and source data only."""
from test_collection_questions_api import CollectionQuestionsAPITests
from test_session_insights import FakeModel


class SessionInsightsAPITests(CollectionQuestionsAPITests):
    def setUp(self):
        super().setUp()
        self.insights = self.engine.session_insights
        self.insights.model = FakeModel()
        self.insights.sync(force=True)
        self.ident = next(i['id'] for i in self.insights.inventory()['items'] if i['deviceId'] == self.assets['alice']['deviceId'])

    def test_public_new_receipts_and_read_only_details(self):
        status, inventory = self.call('/api/insights')
        self.assertEqual(status, 200); self.assertEqual(len(inventory['items']), 2)
        self.assertEqual(inventory['session']['role'], 'viewer')
        self.assertIsNone(inventory['session']['csrf'])
        status, detail = self.call('/api/insights/' + self.ident)
        self.assertEqual(detail['state'], 'waiting'); self.assertTrue(detail['evidence'])
        self.assertIsNone(detail['report'])
        self.rejected(401, '/api/insights/analyze', {'id': self.ident, 'shareConfirmed': True})

    def test_analyze_returns_real_job_that_publishes_to_both_apis(self):
        before = self.insights.get(self.ident)
        payload = {'id': self.ident, 'revision': before['revision'], 'shareConfirmed': True}
        self.rejected(403, '/api/insights/analyze', payload, actor='bob')
        status, result = self.call('/api/insights/analyze', payload, actor='alice')
        self.assertEqual(status, 202); self.assertEqual(result['state'], 'queued')
        self.insights.process(self.ident)
        _, detail = self.call('/api/insights/' + self.ident)
        self.assertEqual(detail['state'], 'completed')
        _, findings = self.call('/api/insights/findings')
        self.assertEqual(findings['items'][0]['insightId'], self.ident)
        _, summary = self.call('/api/insights/status')
        self.assertNotIn('items', summary); self.assertEqual(summary['summary']['completed'], 1)

    def test_csrf_consent_and_revision_are_required_before_any_model(self):
        payload = {'id': self.ident, 'revision': self.insights.get(self.ident)['revision'], 'shareConfirmed': True}
        self.rejected(403, '/api/insights/analyze', payload, actor='admin', csrf='wrong')
        self.rejected(400, '/api/insights/analyze', {'id': self.ident, 'shareConfirmed': True}, actor='admin')
        self.rejected(400, '/api/insights/analyze', dict(payload, shareConfirmed=False), actor='admin')
        self.rejected(400, '/api/insights/analyze', dict(payload, revision='a' * 64), actor='admin')
        self.assertEqual(self.insights.model.calls, [])
