"""Adversarial functional QA of collection questions with synthetic evidence.

HTTP, authorization, workers, billing and model-output validation are real;
model outputs are controlled fakes, with no external/private data transmission.
"""
import hashlib
import http.client
import json
import sqlite3
import threading
import time
import unittest
from unittest import mock
import urllib.error
import urllib.request

try:
    from . import test_collection_questions_api as api
except ImportError:
    import test_collection_questions_api as api


class ProfessionalModel(api.EvidenceModel):
    def __init__(self):
        super().__init__()
        self.policies = {}
        self.gates = {}
        self.entered = {}
        self.stages = []

    @staticmethod
    def stage(data):
        if 'contexts' in data and 'userTurns' in data:
            return 'contexts'
        if 'turns' in data:
            return 'lineage'
        if 'candidates' in data:
            return 'select'
        if 'tasks' in data and 'evidence' in data:
            return 'review' if 'draft' in data else 'answer'
        return 'plan'

    def __call__(self, system, data, max_tokens=4000):
        stage = self.stage(data)
        self.stages.append(stage)
        self.entered.setdefault(stage, threading.Event()).set()
        gate = self.gates.get(stage)
        if gate is not None and not gate.wait(8):
            raise RuntimeError('Synthetic model gate timed out')
        policy = self.policies.get(stage)
        if policy:
            self.calls.append((system, data, max_tokens))
            return policy(system, data)
        return super().__call__(system, data, max_tokens)


class CollectionProfessionalQATests(unittest.TestCase):
    def setUp(self):
        patch = mock.patch.object(api, 'EvidenceModel', ProfessionalModel)
        patch.start()
        self.addCleanup(patch.stop)
        self.app = api.CollectionQuestionsAPITests('test_http_submit_and_get_preserve_question_and_real_evidence')
        self.app.setUp()
        self.addCleanup(self.app.tearDown)
        self.model = self.app.model

    def wait_stage(self, stage):
        for _ in range(200):
            event = self.model.entered.get(stage)
            if event and event.is_set():
                return
            time.sleep(.01)
        self.fail('Synthetic model did not reach ' + stage)

    def model_cache_count(self):
        with sqlite3.connect(self.app.root / 'collection-assistant.db') as db:
            return db.execute('SELECT count(*) FROM model_cache').fetchone()[0]

    def test_malformed_fields_are_json_errors_without_model_or_job(self):
        for fields in ({'deviceId': {'id': 'wrong'}}, {'deviceId': ['wrong']},
                       {'scope': None}, {'scope': ['global']},
                       {'question': {'text': '会员'}}, {'question': ['会员']}):
            with self.subTest(fields=fields):
                self.app.rejected(400, '/api/collection/questions', self.app.payload(**fields), actor='alice')
        self.assertEqual(self.app.question_count(), 0)
        self.assertEqual(self.model.calls, [])

    def test_non_string_previous_id_rejects_json_instead_of_dropping_connection(self):
        for previous in ({'id': 'question-id'}, ['question-id']):
            with self.subTest(previous=previous):
                try:
                    self.app.rejected(400, '/api/collection/questions',
                        self.app.payload(previousQuestionId=previous), actor='alice')
                except (http.client.RemoteDisconnected, ConnectionResetError):
                    self.fail('Malformed previousQuestionId closed the HTTP connection instead of JSON 400')
        self.assertEqual(self.app.question_count(), 0)
        self.assertEqual(self.model.calls, [])

    def test_invalid_json_and_oversized_post_preserve_error_protocol(self):
        client = self.app.clients['alice']
        headers = {'Content-Type': 'application/json', 'Origin': self.app.ORIGIN,
                   'X-CSRF-Token': self.app.csrf['alice']}
        for body, expected in ((b'{"question":', 400), (b'x' * 32769, 413)):
            request = urllib.request.Request(self.app.url + '/api/collection/questions', data=body, headers=headers)
            with self.assertRaises(urllib.error.HTTPError) as raised:
                client.open(request, timeout=5)
            try:
                self.assertEqual(raised.exception.code, expected)
                self.assertIn('error', json.load(raised.exception))
            finally:
                raised.exception.close()
        self.assertEqual(self.app.question_count(), 0)
        self.assertEqual(self.model.calls, [])

    def test_unknown_question_is_completed_unknown_with_no_fake_evidence(self):
        identity = self.app.submit(payload=self.app.payload(question='不存在的深海 XQJ 项目做了什么？'))
        answer = self.app.completed(identity)['result']
        self.assertEqual(answer['answer']['basis'], 'unknown')
        self.assertEqual(answer['answer']['evidenceRefs'], [])
        self.assertEqual(answer['candidates'], [])
        self.assertEqual(answer['evidence'], [])
        self.assertEqual(self.model.stages, ['plan'])
        self.assertTrue(any('不代表' in gap for gap in answer['gaps']))

    def test_invalid_plan_fails_without_cache_poisoning_and_can_retry(self):
        self.model.policies['plan'] = lambda system, data: {'terms': ['']}
        identity = self.app.submit()
        failed = self.app.finished(identity)
        self.assertEqual(failed['status'], 'failed')
        self.assertIn('检索主题', failed['error'])
        self.assertEqual(self.model_cache_count(), 0)
        self.model.policies.clear()
        self.app.completed(self.app.submit())
        self.assertEqual(self.model.stages.count('plan'), 2)

    def test_unknown_answer_citation_fails_and_does_not_poison_valid_retry(self):
        self.model.policies['answer'] = lambda system, data: {
            'answer': {'text': '模型编造了一条未提供的证据。', 'basis': 'recorded', 'evidenceRefs': ['E999']}, 'gaps': []}
        identity = self.app.submit()
        failed = self.app.finished(identity)
        self.assertEqual(failed['status'], 'failed')
        self.assertIn('引用', failed['error'])
        self.assertNotIn('result', failed)
        self.assertNotIn('review', self.model.stages)
        cached = self.model_cache_count()
        self.model.policies.clear()
        completed = self.app.completed(self.app.submit())
        refs = {evidence['ref'] for evidence in completed['result']['evidence']}
        self.assertTrue(set(completed['result']['answer']['evidenceRefs']) <= refs)
        self.assertGreater(self.model_cache_count(), cached)
        self.assertEqual(self.model.stages.count('answer'), 2)

    def test_lineage_cannot_reference_unknown_or_future_turn(self):
        def invalid(system, data):
            identity = data['turns'][0]['turnId']
            return {'links': [{'turnId': identity, 'parentTurnId': 'T999', 'relation': 'execution',
                'status': 'supported', 'reason': '虚构前置需求', 'evidenceTurnIds': [identity, 'T999']}]}
        self.model.policies['lineage'] = invalid
        failed = self.app.finished(self.app.submit())
        self.assertEqual(failed['status'], 'failed')
        self.assertNotIn('result', failed)
        self.assertNotIn('answer', self.model.stages)

    def test_internal_model_exception_hides_private_marker_and_traceback(self):
        def broken(system, data):
            raise KeyError('SYNTHETIC_PRIVATE_BACKEND_VALUE')
        self.model.policies['answer'] = broken
        failed = self.app.finished(self.app.submit())
        self.assertEqual(failed['status'], 'failed')
        self.assertNotIn('SYNTHETIC_PRIVATE_BACKEND_VALUE', json.dumps(failed))
        self.assertNotIn('Traceback', failed['error'])

    def test_previous_failed_or_incomplete_job_cannot_supply_history(self):
        identity = self.app.submit()
        self.app.completed(identity)
        calls = len(self.model.calls)
        for status in ('running', 'failed'):
            with sqlite3.connect(self.app.root / 'collection-assistant.db') as db:
                db.execute('UPDATE questions SET status=? WHERE id=?', (status, identity))
            self.app.rejected(400, '/api/collection/questions', self.app.payload(previousQuestionId=identity), actor='alice')
        self.assertEqual(len(self.model.calls), calls)
        self.assertEqual(self.app.question_count(), 1)

    def test_device_revoked_before_selection_stops_query_and_hides_non_admin_result(self):
        gate = threading.Event()
        self.model.gates['plan'] = gate
        identity = self.app.submit()
        try:
            self.wait_stage('plan')
            self.app.devices.revoke(self.app.assets['alice']['deviceId'], self.app.owners['alice'])
        finally:
            gate.set()
        failed = self.app.finished(identity, 'admin')
        self.assertEqual(failed['status'], 'failed')
        self.assertEqual(self.model.stages, ['plan'])
        self.app.rejected((403, 404), '/api/collection/questions/' + identity, actor='alice')

    def test_device_revoked_during_answer_does_not_start_new_review_call(self):
        gate = threading.Event()
        self.model.gates['answer'] = gate
        identity = self.app.submit()
        try:
            self.wait_stage('answer')
            self.app.devices.revoke(self.app.assets['alice']['deviceId'], self.app.owners['alice'])
        finally:
            gate.set()
        failed = self.app.finished(identity, 'admin')
        self.assertEqual(failed['status'], 'failed', 'Revocation must stop subsequent outbound model stages')
        self.assertNotIn('review', self.model.stages)

    def test_duplicate_pending_question_reuses_job_without_double_reservation(self):
        gate = threading.Event()
        self.model.gates['plan'] = gate
        first = self.app.submit()
        try:
            self.wait_stage('plan')
            second = self.app.submit()
            self.assertEqual(first, second, 'The same pending question should not start another paid inference')
        finally:
            gate.set()
        self.app.completed(first)
        self.assertEqual(self.model.stages.count('plan'), 1)

    def test_two_distinct_pending_questions_enforce_worker_capacity(self):
        gate = threading.Event()
        self.model.gates['plan'] = gate
        first = self.app.submit()
        try:
            self.wait_stage('plan')
            second = self.app.submit(payload=self.app.payload(question='会员管理调用了哪些工具？'))
            self.assertNotEqual(first, second)
            self.app.rejected(409, '/api/collection/questions',
                self.app.payload(question='会员管理为什么改成邮箱？'), actor='alice')
            self.assertEqual(self.app.question_count(), 2)
        finally:
            gate.set()
        self.app.completed(first)
        self.app.completed(second)

    def test_global_engine_budget_rejects_without_debiting_requester_wallet(self):
        self.app.engine.budget = 0
        balance = self.app.engine.accounts.balance(self.app.owners['alice'])['remainingCNY']
        failed = self.app.finished(self.app.submit())
        self.assertEqual(failed['status'], 'failed')
        self.assertEqual(self.model.calls, [])
        self.assertEqual(self.app.engine.accounts.balance(self.app.owners['alice'])['remainingCNY'], balance,
            'No provider call happened, so a failed preflight must not debit the user wallet')

    def test_empty_requester_wallet_does_not_reserve_global_budget(self):
        with sqlite3.connect(self.app.engine.accounts.path) as db:
            db.execute('UPDATE wallets SET remaining=0 WHERE owner=?',(self.app.owners['alice'],))
        before=self.app.engine.usage()['estimatedReservedCNY']
        failed=self.app.finished(self.app.submit())
        self.assertEqual(failed['status'],'failed')
        self.assertEqual(self.model.calls,[])
        self.assertEqual(self.app.engine.usage()['estimatedReservedCNY'],before)

    def test_invalid_lineage_is_repaired_once_before_answering(self):
        tries=[]
        # ProfessionalModel inherits the original evidence model. Save its
        # implementation before installing the one-shot malformed policy.
        base=ProfessionalModel.__mro__[1].__call__
        def policy(system,data):
            tries.append(1)
            if len(tries)==1:
                ident=data['turns'][0]['turnId']
                return {'links':[{'turnId':ident,'parentTurnId':ident,'relation':'unresolved','status':'ambiguous',
                    'reason':'合成畸形结果','evidenceTurnIds':[ident]}]}
            return base(self.model,system,data)
        self.model.policies['lineage']=policy
        result=self.app.completed(self.app.submit())
        self.assertEqual(result['status'],'completed')
        self.assertEqual(len(tries),2)

    def test_new_source_event_invalidates_evidence_cache(self):
        first = self.app.completed(self.app.submit())
        calls = len(self.model.calls)
        original = self.app.alice_events[0]
        ident = hashlib.sha256(b'new audit event').hexdigest()
        event = {**original, 'id': ident, 'kind': 'tool_call', 'role': None,
            'name': 'Write', 'callId': 'new-call', 'timestamp': 1700000100,
            'payload': {'id': 'new-source-id', 'parentId': 'alice-source-3',
                        'name': 'Write', 'arguments': {'path': 'new-audit.py', 'content': 'synthetic audit'}},
            'evidence': {**original['evidence'], 'byteStart': 500}}
        status, receipt = self.app.call('/api/sessionlens/events', {'schemaVersion': 1, 'events': [event]},
            headers={'Authorization': 'Bearer ' + self.app.assets['alice']['token']})
        self.assertEqual((status, receipt['accepted']), (200, 1))
        second = self.app.completed(self.app.submit())
        self.assertGreater(len(self.model.calls), calls)
        self.assertIn('sessionlens:' + ident, {record['recordId'] for record in second['result']['evidence']})
        self.assertNotIn('sessionlens:' + ident, {record['recordId'] for record in first['result']['evidence']})

    def test_same_session_id_in_another_agent_never_shares_tool_evidence(self):
        original = self.app.alice_events[0]
        ids = [hashlib.sha256(b'codex-other-source-user').hexdigest(), hashlib.sha256(b'codex-other-source-tool').hexdigest()]
        events = [
            {**original, 'id': ids[0], 'source': 'codex', 'kind': 'user_message', 'role': 'user',
             'payload': {'message': '另一个 Codex 会员任务'},
             'evidence': {**original['evidence'], 'fileIdentity': 'codex-file', 'path': 'codex.jsonl'}},
            {**original, 'id': ids[1], 'source': 'codex', 'kind': 'tool_call', 'role': None,
             'name': 'exec_command', 'callId': 'codex-call',
             'payload': {'arguments': {'cmd': 'echo source-isolation-only'}},
             'evidence': {**original['evidence'], 'fileIdentity': 'codex-file', 'path': 'codex.jsonl', 'byteStart': 100}},
        ]
        status, _ = self.app.call('/api/sessionlens/events', {'schemaVersion': 1, 'events': events},
            headers={'Authorization': 'Bearer ' + self.app.assets['alice']['token']})
        self.assertEqual(status, 200)
        def workbuddy_only(system, data):
            candidate = next(item for item in data['candidates'] if item['source'] == 'workbuddy' and '会员' in item['text'])
            return {'selected': [{'id': candidate['id'], 'status': 'supported', 'reason': '只选择问题指定的 WorkBuddy 任务'}]}
        self.model.policies['select'] = workbuddy_only
        answer = self.app.completed(self.app.submit(payload=self.app.payload(question='WorkBuddy 的会员管理用了什么工具？')))['result']
        self.assertTrue(all(candidate['source'] == 'workbuddy' for candidate in answer['candidates']))
        selected = {record['recordId'] for record in answer['evidence']}
        self.assertFalse(selected & {'sessionlens:' + identity for identity in ids})


if __name__ == '__main__':
    unittest.main()
