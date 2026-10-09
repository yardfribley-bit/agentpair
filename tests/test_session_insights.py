"""Receipt → bounded job → independent review → current public insight.

All evidence is synthetic; the real SQLite queue and model-stage validation run.
No relay or cloud account is contacted.
"""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agentpair.session_lens import SessionStore
from agentpair.session_insights import SessionInsights, validate_report


def event(n, text, kind='message', role='user', source='workbuddy', session='s'):
    return {'id': hashlib.sha256((session + source + str(n)).encode()).hexdigest(),
            'schemaVersion': 1, 'source': source, 'sessionId': session, 'kind': kind,
            'role': role, 'timestamp': 1700000000 + n, 'evidence': {'byteStart': n * 10},
            'payload': {'role': role, 'content': text}}


class FakeModel:
    def __init__(self): self.calls = []; self.fail = False; self.mutate = None
    def __call__(self, system, data, max_tokens=4000):
        stage = len(data['previousStages'])
        self.calls.append(stage)
        if not stage:
            return {'summary': '核对目标与交付', 'questions': ['执行是否完成？']}
        if self.fail:
            self.fail = False
            raise RuntimeError('临时服务失败')
        ref = data['evidence']['fragments'][0]['evidenceId']
        report = {'title': '任务执行检查', 'summary': '已记录回复', 'goal': '检查收到消息',
            'outcome': '日志记录了回复', 'completion': 'completed',
            'story': [{'title': '回应需求', 'action': '收到提问', 'result': '给出回复', 'evidenceRefs': [ref]}],
            'findings': [{'title': '测试凭据线索', 'category': 'security', 'severity': 'medium',
                'status': 'hypothesis', 'fact': '合成证据中的线索', 'impact': '需要核实',
                'remediation': '检查任务', 'evidenceRefs': [ref]}],
            'goodPractices': [], 'limitations': ['仅日志证据']}
        if self.mutate: self.mutate(report)
        return {'summary': '逐条核对证据', 'report': report, 'verdict': 'pass'}


class SessionInsightsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        self.store = SessionStore(self.root / 'sessionlens.db')
        self.owner = {'owner': 'alice', 'id': 'd'}
        self.events = [event(1, '请查询上海天气'), event(2, '已回复', role='assistant')]
        self.store.ingest(self.owner, {'schemaVersion': 1, 'events': self.events})
        self.model = FakeModel(); self.reservations = []
        self.insights = SessionInsights(self.root / 'insights.db', self.store, model=self.model, reserve=self.reservations.append)
        self.ident = self.insights.inventory()['items'][0]['id']
    def tearDown(self): self.insights.close(); self.tmp.cleanup()

    def run_job(self):
        self.insights.submit(self.ident, 'alice', share_confirmed=True)
        self.insights.process(self.ident)
        return self.insights.get(self.ident)

    def test_receipt_is_waiting_no_automatic_model_or_publish(self):
        value = self.insights.get(self.ident)
        self.assertEqual(value['state'], 'waiting'); self.assertIsNone(value['report'])
        self.assertEqual(self.model.calls, []); self.assertEqual(value['eventCount'], 2)

    def test_explicit_scope_and_account_boundary(self):
        with self.assertRaises(ValueError): self.insights.submit(self.ident, 'alice')
        with self.assertRaises(PermissionError): self.insights.submit(self.ident, 'bob', share_confirmed=True)
        self.assertEqual(self.model.calls, [])

    def test_all_stages_publish_only_validated_report_and_findings(self):
        value = self.run_job()
        self.assertEqual(value['state'], 'completed')
        self.assertEqual(self.model.calls, [0, 1, 2]); self.assertEqual(self.reservations, ['alice'] * 3)
        self.assertEqual(value['revision'], value['analyzedRevision'])
        self.assertEqual(len(self.insights.findings()['items']), 1)
        self.assertNotIn('owner', value)

    def test_replay_does_not_reanalyze_or_mark_stale(self):
        self.run_job(); before = self.insights.get(self.ident)
        self.store.ingest(self.owner, {'schemaVersion': 1, 'events': self.events})
        self.insights.sync(force=True)
        self.insights.submit(self.ident, 'alice', share_confirmed=True)
        self.assertEqual(self.insights.get(self.ident)['state'], 'completed')
        self.assertEqual(self.insights.get(self.ident)['revision'], before['revision'])
        self.assertEqual(len(self.model.calls), 3)

    def test_new_evidence_keeps_old_report_and_immutable_refs_as_stale(self):
        self.run_job(); old = self.insights.get(self.ident)
        self.store.ingest(self.owner, {'schemaVersion': 1, 'events': [event(3, '现在生成 SSH 视频')]})
        self.insights.sync(force=True); new = self.insights.get(self.ident)
        self.assertEqual(new['state'], 'stale'); self.assertNotEqual(new['revision'], new['analyzedRevision'])
        self.assertEqual(new['evidence'], old['evidence']); self.assertTrue(new['currentPreview'])
        self.assertTrue(self.insights.findings()['items'][0]['stale'])

    def test_invalid_evidence_references_never_publish(self):
        self.model.mutate = lambda r: r['story'][0].update(evidenceRefs=['E999'])
        value = self.run_job()
        self.assertEqual(value['state'], 'failed'); self.assertIsNone(value['report'])
        self.assertEqual(self.model.calls, [0, 1])
        self.assertEqual(self.insights.findings()['items'], [])

    def test_failure_retry_resumes_without_repaying_plan(self):
        self.model.fail = True; self.assertEqual(self.run_job()['state'], 'failed')
        self.assertEqual(self.run_job()['state'], 'completed')
        self.assertEqual(self.model.calls, [0, 1, 1, 2])

    def test_new_records_while_running_do_not_extend_authorized_snapshot(self):
        self.insights.submit(self.ident, 'alice', share_confirmed=True)
        self.store.ingest(self.owner, {'schemaVersion': 1, 'events': [event(3, '另一项任务')]})
        self.insights.sync(force=True); self.insights.process(self.ident)
        row = self.insights.get(self.ident)
        self.assertEqual(row['state'], 'stale')
        self.assertNotEqual(row['revision'], row['analyzedRevision'])

    def test_restart_retains_queued_and_fails_inflight_without_silent_resend(self):
        self.insights.submit(self.ident, 'alice', share_confirmed=True)
        self.insights.close()
        self.insights = SessionInsights(self.root / 'insights.db', self.store, model=self.model)
        self.assertEqual(self.insights.get(self.ident)['state'], 'queued')
        with self.insights.connect() as db: db.execute("UPDATE insights SET state='running'")
        self.insights.close()
        self.insights = SessionInsights(self.root / 'insights.db', self.store, model=self.model)
        self.assertEqual(self.insights.get(self.ident)['state'], 'failed')
        self.assertEqual(self.model.calls, [])

    def test_same_session_across_accounts_and_sources_not_mixed(self):
        self.store.ingest({'owner': 'bob', 'id': 'b'}, {'schemaVersion': 1, 'events': [event(4, 'bob-private')]})
        self.store.ingest(self.owner, {'schemaVersion': 1, 'events': [event(5, 'codex-only', source='codex')]})
        self.insights.sync(force=True)
        self.assertEqual(len(self.insights.inventory()['items']), 3)
        own = self.insights.get(self.ident)
        self.assertNotIn('bob-private', json.dumps(own)); self.assertNotIn('codex-only', json.dumps(own))

    def test_twenty_active_jobs_limit_includes_other_platform_work(self):
        self.insights.capacity = lambda: 20
        with self.assertRaises(ValueError): self.insights.submit(self.ident, 'alice', share_confirmed=True)
        self.assertEqual(self.insights.get(self.ident)['state'], 'waiting')

    def test_receipt_metadata_does_not_replace_source_time(self):
        value = self.insights.get(self.ident)
        self.assertGreater(value['lastReceived'], value['sourceTime'])

    def test_automatic_analysis_off_by_default_and_requires_admin_scope_consent(self):
        self.assertEqual(self.insights.automations(), [])
        self.insights.queue_automatic()
        self.assertEqual(self.insights.get(self.ident)['state'], 'waiting')
        with self.assertRaises(PermissionError):
            self.insights.configure_automation('alice', 'd', 'workbuddy', True, share_confirmed=True)
        with self.assertRaises(ValueError):
            self.insights.configure_automation('admin', 'd', 'workbuddy', True, admin=True)

    def test_auto_watermark_quiet_window_budget_and_no_fixed_three_cap(self):
        with patch('agentpair.session_insights.time.time', return_value=1700000010):
            self.insights.configure_automation('admin', 'd', 'workbuddy', True, admin=True, share_confirmed=True,daily_budget=2)
        for i in range(5):
            with patch('agentpair.session_insights.time.time', return_value=1700000200):
                self.store.ingest(self.owner, {'schemaVersion': 1, 'events': [event(200+i, '新的任务'+str(i), session='new'+str(i))]})
        self.insights.sync(force=True)
        self.insights.queue_automatic(now=1700000220)
        self.assertEqual(self.insights.active_count(), 0)
        self.insights.queue_automatic(now=1700000400)
        self.assertEqual(self.insights.active_count(), 5)
        self.assertEqual(self.insights.get(self.ident)['state'], 'waiting')
        self.insights.queue_automatic(now=1700000500)
        self.assertEqual(self.insights.active_count(), 5)
        ids = [i['id'] for i in self.insights.inventory()['items'] if i['state']=='queued']
        for ident in ids:self.insights.process(ident)
        self.insights.queue_automatic(now=1700000600)
        self.assertEqual(self.insights.active_count(), 0)
        self.assertEqual(len(self.model.calls), 15)
        self.insights.configure_automation('admin', 'd', 'workbuddy', False, admin=True)
        self.assertFalse(self.insights.automations()[0]['enabled'])

    def test_confirmation_does_not_expand_to_revision_that_arrived_after_dialog(self):
        revision=self.insights.get(self.ident)['revision']
        self.store.ingest(self.owner, {'schemaVersion':1,'events':[event(3,'另一份任务')]})
        self.insights.sync(force=True)
        with self.assertRaises(ValueError):
            self.insights.submit(self.ident,'alice',share_confirmed=True,expected_revision=revision)
        self.assertEqual(self.model.calls,[])

    def test_auto_budget_is_reserved_atomically_and_old_approval_is_not_new_task(self):
        with patch('agentpair.session_insights.time.time',return_value=1700000010):
            self.insights.configure_automation('admin','d','workbuddy',True,admin=True,share_confirmed=True,daily_budget=.4)
        with patch('agentpair.session_insights.time.time',return_value=1700000200):
            self.store.ingest(self.owner,{'schemaVersion':1,'events':[event(200,'>>> APPROVAL REQUEST BEGIN\ncontrol\n>>> APPROVAL REQUEST END')]})
            for i in range(3):
                self.store.ingest(self.owner,{'schemaVersion':1,'events':[event(200+i,'新用户需求',session='fresh'+str(i))]})
        self.insights.sync(force=True);self.insights.queue_automatic(now=1700000400)
        self.assertEqual(self.insights.get(self.ident)['state'],'waiting')
        self.assertEqual(self.insights.active_count(),1)
        with self.insights.connect() as db:
            self.assertAlmostEqual(db.execute('SELECT sum(reserved) FROM insight_auto_runs').fetchone()[0],.3)
        self.insights.queue_automatic(now=1700000500)
        self.assertEqual(self.insights.active_count(),1)

    def test_concurrent_upload_between_head_and_packet_cannot_expand_old_revision(self):
        self.store.ingest(self.owner,{'schemaVersion':1,'events':[event(3,'新问题')]})
        original=self.store.events_for
        def concurrent(*args):
            self.store.ingest(self.owner,{'schemaVersion':1,'events':[event(4,'又一份需求')]})
            return original(*args)
        with patch.object(self.store,'events_for',side_effect=concurrent):self.insights.sync(force=True)
        current=self.insights.get(self.ident)
        self.assertEqual(current['revision'],self.store.metadata('alice','d','s','workbuddy')['revision'])
        with self.insights.connect() as db:
            row=db.execute('SELECT packet,revision FROM insights WHERE id=?',(self.ident,)).fetchone()
            self.assertEqual(json.loads(row['packet'])['revision'],row['revision'])

    def test_corrupt_snapshot_fails_job_without_escaping_worker(self):
        self.insights.submit(self.ident,'alice',share_confirmed=True)
        with self.insights.connect() as db:db.execute("UPDATE insights SET snapshot='invalid-json' WHERE id=?",(self.ident,))
        self.insights.process(self.ident)
        self.assertEqual(self.insights.get(self.ident)['state'],'failed')
        self.assertEqual(self.model.calls,[])

    def test_one_bad_packet_does_not_block_other_sessions(self):
        self.store.ingest(self.owner,{'schemaVersion':1,'events':[event(3,'新任务',session='valid')]})
        self.store.ingest(self.owner,{'schemaVersion':1,'events':[event(4,'更新旧会话')]})
        from agentpair.session_packets import make_packet
        def builder(events,device,owner,session,source):
            if session=='s':raise ValueError('bad-synthetic')
            return make_packet(events,device,owner,session,source)
        with patch('agentpair.session_packets.make_packet',side_effect=builder):self.insights.sync(force=True)
        self.assertEqual(self.insights.get(self.ident)['state'],'failed')
        self.assertEqual(next(i for i in self.insights.inventory()['items'] if i['sessionId']=='valid')['state'],'waiting')

if __name__ == '__main__': unittest.main()
