"""No cloud calls: enforce readiness, pinned releases, and cleanup across failure paths."""
import copy
import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from agentpair.test_runs import GitHubBuildProof, TestRuns, stamp
from tests.test_cloud_workflow import FakeConsole, WINDOWS_RECIPE, PRIVATE_MARKERS


RECIPE = dict(WINDOWS_RECIPE, url='https://github.com/example/app/releases/download/v1/setup.exe')
BUILD = {'repository': 'example/app', 'runId': 42, 'commit': 'd'*40, 'tag': 'v1'}


def github_rows():
    return {'repos/example/app/actions/runs/42': {'status': 'completed', 'conclusion': 'success',
        'head_sha': BUILD['commit'], 'repository': {'full_name': 'example/app'}},
        'repos/example/app/git/ref/tags/v1': {'object': {'type': 'commit', 'sha': BUILD['commit']}},
        'repos/example/app/releases/tags/v1': {'draft': False, 'assets': [{'id': 1, 'state': 'uploaded',
            'browser_download_url': RECIPE['url'], 'size': 123, 'digest': 'sha256:'+RECIPE['sha256']}]}}


class BuildProofTests(unittest.TestCase):
    def test_checks_ci_tag_and_exact_asset_digest(self):
        rows = github_rows()
        verifier = GitHubBuildProof(lambda path: rows[path])
        self.assertEqual(verifier.verify(BUILD, RECIPE)['commit'], BUILD['commit'])
        for path, field, invalid in (
            ('repos/example/app/actions/runs/42', 'conclusion', 'failure'),
            ('repos/example/app/actions/runs/42', 'head_sha', 'e'*40),
            ('repos/example/app/actions/runs/42', 'status', 'in_progress'),
            ('repos/example/app/releases/tags/v1', 'draft', True)):
            with self.subTest(field=field):
                modified = copy.deepcopy(rows); modified[path][field] = invalid
                with self.assertRaises(ValueError):GitHubBuildProof(lambda p: modified[p]).verify(BUILD, RECIPE)
        rows['repos/example/app/git/ref/tags/v1']['object']['sha'] = 'f'*40
        with self.assertRaises(ValueError):verifier.verify(BUILD, RECIPE)

    def test_missing_or_mismatched_asset_never_passes(self):
        for patch in ({'digest': 'sha256:'+'f'*64}, {'size': 0}, {'state': 'new'}, {'browser_download_url': 'https://other.test/file'}):
            rows = github_rows(); rows['repos/example/app/releases/tags/v1']['assets'][0].update(patch)
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                GitHubBuildProof(lambda p: rows[p]).verify(BUILD, RECIPE)

    def test_untrusted_url_or_boolean_run_id_rejected_before_network(self):
        get = Mock()
        for patch in ({'repository': 'https://private.invalid/x'}, {'tag': '../etc'}, {'runId': True}):
            with self.assertRaises(ValueError):GitHubBuildProof(get).verify(dict(BUILD, **patch), RECIPE)
        get.assert_not_called()


class TestRunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.console = FakeConsole()
        self.console.release = Mock(return_value={'state': 'released', 'remainingHostCount': 0, 'releasedAt': stamp()})
        self.console.creation_status = Mock(return_value={'leaseId': 'b'*24,'state':'created'})
        self.console.test_process_cpu = Mock(return_value={'exitCode': 0, 'commandSource': 'fixed script', 'metrics': {'samples': []}})
        self.proof = GitHubBuildProof(lambda p: github_rows()[p])
        self.store = TestRuns(Path(self.tmp.name)/'test-runs', self.console, verifier=self.proof, start_threads=False, sleep=lambda _:None)
        self.now=dt.datetime.now(dt.timezone.utc)
        self.store.clock=lambda:self.now
        original_create=self.console.create
        def create(*args):
            lease=original_create(*args)
            lease['expiresAt']=(self.now+dt.timedelta(minutes=60)).isoformat()
            return lease
        self.console.create=create
        self.console.inspect=Mock(return_value={'expiresAt':self.now.isoformat()})
        self.store.catalog.register(RECIPE)
        def expire_hold(_):
            self.now+=dt.timedelta(minutes=1)
        self.store.sleep=expire_hold
        self.data = {'softwareIds': [RECIPE['id']], 'subjectSoftwareId': RECIPE['id'], 'build': BUILD,
                     'plan': {case:'执行受控活动并核对完整记录、指标与预期结果。' for case in TestRuns.CASES}}

    def ready(self):return self.store.prepare(copy.deepcopy(self.data))
    def confirmed(self):
        row=self.ready();self.store.confirm(row['id'],{'requestId':row['requestId'],'confirmed':True,'maxHourlyCNY':.51});return row

    def test_failed_build_prevents_even_quote(self):
        self.store.verifier = Mock();self.store.verifier.verify.side_effect=ValueError('build not ready')
        with self.assertRaises(ValueError):self.ready()
        self.assertEqual(self.console.quote_calls,[])
        self.assertEqual(self.console.create_calls,[])
        self.assertEqual(self.store.list(),[])

    def test_missing_plan_cannot_create(self):
        del self.data['plan']['security_observation']
        with self.assertRaises(ValueError):self.ready()
        self.assertEqual(self.console.quote_calls,[])

    def test_preparation_and_duplicate_confirmation_do_not_dispatch_twice(self):
        row=self.ready();self.assertEqual(self.console.create_calls,[])
        data={'requestId':row['requestId'],'confirmed':True,'maxHourlyCNY':.51}
        self.store.confirm(row['id'],data);self.store.confirm(row['id'],data)
        self.assertEqual(self.console.create_calls,[])
        self.assertEqual(len(self.store.active),1)

    def test_changed_recipe_quote_and_price_are_rejected(self):
        row=self.ready();data={'requestId':row['requestId'],'confirmed':True,'maxHourlyCNY':.51}
        with self.assertRaises(ValueError):self.store.confirm(row['id'],dict(data,maxHourlyCNY=.52))
        with self.assertRaises(ValueError):self.store.confirm(row['id'],dict(data,confirmed='true'))
        self.store.catalog.register(dict(RECIPE,version='new'))
        with self.assertRaises(ValueError):self.store.confirm(row['id'],data)
        self.assertEqual(self.console.create_calls,[])

    def test_install_failure_retains_scene_until_original_lease_ends(self):
        row=self.confirmed();self.console.operation_state='failed'
        observed=[]
        def expire(_):
            retained=self.store.get(row['id'])
            if retained['state']=='needs_reconciliation':
                self.console.release.assert_not_called()
                self.assertEqual(retained['cleanupState'],'pending')
                observed.append(retained['leaseId'])
                self.now=dt.datetime.fromisoformat(retained['expiresAt'])
        self.store.sleep=expire
        self.store.run(row['id'])
        result=self.store.get(row['id']);self.assertEqual(result['state'],'failed')
        self.assertEqual(result['cleanupState'],'released')
        self.console.release.assert_called_once_with('b'*24)
        self.assertEqual(observed,['b'*24])
        self.assertEqual(len(self.console.create_calls),1)

    def test_missing_install_receipt_keeps_machine_until_user_cancels(self):
        row=self.confirmed();self.console.operation_state='interrupted'
        def cancel(_):
            if self.store.get(row['id'])['state']=='needs_reconciliation':
                self.console.release.assert_not_called()
                self.store.cancel(row['id'])
            self.now+=dt.timedelta(minutes=1)
        self.store.sleep=cancel;self.store.run(row['id'])
        self.assertEqual(self.store.get(row['id'])['state'],'cancelled')
        self.console.release.assert_called_once_with('b'*24)

    def test_cancel_during_creation_keeps_late_lease_and_releases(self):
        row=self.confirmed();original=self.console.create
        def create(*args):
            self.store.cancel(row['id']);return original(*args)
        self.console.create=create;self.store.run(row['id'])
        result=self.store.get(row['id']);self.assertEqual(result['state'],'cancelled')
        self.assertEqual(result['leaseId'],'b'*24);self.console.release.assert_called_once()
        self.assertEqual(self.console.install_calls,[])

    def test_restart_reconciles_request_without_recreating(self):
        row=self.confirmed();self.store.run(row['id'],recovery=True)
        self.assertEqual(self.console.create_calls,[])
        self.console.creation_status.assert_called_once_with(row['requestId'])
        self.console.release.assert_called_once_with('b'*24)
        self.assertEqual(self.store.get(row['id'])['state'],'interrupted')

    def test_ambiguous_creation_keeps_cleanup_failure_visible(self):
        row=self.confirmed();self.console.create_error=TimeoutError('private-password')
        self.console.creation_status.return_value={'state':'reconcile_required','observedHosts':0}
        self.store.run(row['id']);result=self.store.get(row['id'])
        self.assertEqual(result['cleanupState'],'failed');self.console.release.assert_not_called()
        self.assertNotIn('private-password',json.dumps(result))

    def test_ambiguous_creation_with_lease_id_does_not_claim_release(self):
        row=self.confirmed();self.console.create_error=TimeoutError('network')
        self.console.creation_status.return_value={'state':'reconcile_required','leaseId':'b'*24,'observedHosts':0}
        self.store.run(row['id'])
        self.assertEqual(self.store.get(row['id'])['cleanupState'],'failed')
        self.console.release.assert_not_called()

    def test_cleanup_failure_can_be_retried_without_new_host(self):
        row=self.confirmed();self.console.operation_state='failed';self.console.release.side_effect=RuntimeError('provider secret')
        self.store.run(row['id']);self.assertEqual(self.store.get(row['id'])['cleanupState'],'failed')
        self.console.release.side_effect=None;self.store.cleanup(row['id'])
        self.assertEqual(self.store.get(row['id'])['cleanupState'],'released')
        self.assertEqual(len(self.console.create_calls),1)

    def test_install_is_not_functional_pass_and_evidence_is_immutable(self):
        row=self.confirmed();observed=[]
        def sleep(_):
            state=self.store.get(row['id'])['state']
            if state=='awaiting_login':
                self.store.confirm_login(row['id'],{'confirmed':True})
            if self.store.get(row['id'])['state']=='awaiting_results':
                observed.append(self.store.get(row['id'])['state'])
                for case in TestRuns.CASES:self.store.result(row['id'],{'case':case,'passed':True,'evidence':'人工执行受控测试，核对输出和指标；完整验收结果已记录。'})
                with self.assertRaises(ValueError):self.store.result(row['id'],{'case':TestRuns.CASES[0],'passed':True,'evidence':'不应覆盖已经提交的原始验收结果，这是一次重复提交。'})
            self.now+=dt.timedelta(minutes=1)
        self.store.sleep=sleep;self.store.run(row['id'])
        self.assertEqual(observed,['awaiting_results'])
        self.assertEqual(self.store.get(row['id'])['state'],'passed')
        self.assertEqual(self.store.get(row['id'])['cleanupState'],'released')

    def test_58_minute_question_and_59_minute_silence_release(self):
        row=self.confirmed();expiry=self.now+dt.timedelta(minutes=60)
        self.store._state(row['id'],'passed',leaseId='b'*24,expiresAt=expiry.isoformat())
        seen=[]
        def advance(_):
            current=self.store.get(row['id'])
            self.console.release.assert_not_called()
            seen.append((self.now,current.get('releaseDecision',{})))
            self.now+=dt.timedelta(seconds=30)
        self.now=expiry-dt.timedelta(minutes=3);self.store.sleep=advance
        self.store.cleanup(row['id'])
        requested=[(at,d) for at,d in seen if d.get('requestedAt')]
        self.assertEqual(dt.datetime.fromisoformat(requested[0][1]['requestedAt']),expiry-dt.timedelta(minutes=2))
        self.assertEqual(self.now,expiry-dt.timedelta(minutes=1))
        self.console.release.assert_called_once_with('b'*24)

    def test_keep_reply_preserves_machine_until_original_60_minutes(self):
        row=self.confirmed();expiry=self.now+dt.timedelta(minutes=60)
        self.store._state(row['id'],'failed',leaseId='b'*24,expiresAt=expiry.isoformat())
        def advance(_):
            self.console.release.assert_not_called()
            if self.store.get(row['id']).get('releaseDecision',{}).get('requestedAt') and not self.store.get(row['id'])['releaseDecision'].get('choice'):
                self.store.release_decision(row['id'],{'choice':'keep'})
            self.now+=dt.timedelta(seconds=30)
        self.now=expiry-dt.timedelta(minutes=3);self.store.sleep=advance
        self.store.cleanup(row['id'])
        self.assertEqual(self.now,expiry)
        self.console.release.assert_called_once_with('b'*24)

    def test_cannot_release_before_58_minutes(self):
        row=self.confirmed()
        self.store._state(row['id'],'failed',leaseId='b'*24,expiresAt=(self.now+dt.timedelta(minutes=60)).isoformat())
        with self.assertRaises(ValueError):self.store.release_decision(row['id'],{'choice':'release'})
        self.console.release.assert_not_called()

    def test_explicit_release_reply_at_58_minutes_releases_same_machine(self):
        row=self.confirmed();expiry=self.now+dt.timedelta(minutes=60)
        self.store._state(row['id'],'passed',leaseId='b'*24,expiresAt=expiry.isoformat())
        self.now=expiry-dt.timedelta(minutes=2)
        self.store.release_decision(row['id'],{'choice':'release'})
        self.store.cleanup(row['id'])
        self.console.release.assert_called_once_with('b'*24)

    def test_late_reply_cannot_override_one_minute_timeout(self):
        row=self.confirmed();expiry=self.now+dt.timedelta(minutes=60)
        self.store._state(row['id'],'failed',leaseId='b'*24,expiresAt=expiry.isoformat())
        self.now=expiry-dt.timedelta(minutes=1)
        with self.assertRaises(ValueError):self.store.release_decision(row['id'],{'choice':'keep'})

    def test_login_requires_separate_operator_confirmation(self):
        row=self.ready()
        with self.assertRaises(ValueError):self.store.confirm_login(row['id'],{'confirmed':True})
        self.store._state(row['id'],'awaiting_login',leaseId='b'*24,expiresAt=(self.now+dt.timedelta(minutes=60)).isoformat())
        with self.assertRaises(ValueError):self.store.confirm_login(row['id'],{'confirmed':True,'code':'secret'})
        result=self.store.confirm_login(row['id'],{'confirmed':True})
        self.assertEqual(result['state'],'awaiting_results')
        self.assertEqual(result['accountLogin']['source'],'administrator')

    def test_private_provider_fields_not_exposed(self):
        row=self.confirmed();self.console.operation_state='failed';self.store.run(row['id'])
        text=json.dumps(self.store.get(row['id']))
        for marker in PRIVATE_MARKERS:self.assertNotIn(marker,text)
        self.assertNotIn(RECIPE['url'],text)

    def test_cpu_probe_no_pass_and_no_arbitrary_command(self):
        row=self.ready();self.store._state(row['id'],'awaiting_results',leaseId='b'*24)
        result=self.store.probe(row['id'],{'mode':'idle'})
        self.assertEqual(result['state'],'awaiting_results');self.assertEqual(result['results'],{})
        self.console.test_process_cpu.assert_called_once_with('b'*24)
        with self.assertRaises(ValueError):self.store.probe(row['id'],{'mode':'idle','script':'malicious'})

    def test_path_traversal_rejected(self):
        with self.assertRaises(ValueError):self.store.get('../private')


class ReaperIsolationTests(unittest.TestCase):
    def test_failed_lease_does_not_prevent_other_cleanup(self):
        from agentpair.resources import ResourceManager
        with tempfile.TemporaryDirectory() as directory:
            manager=ResourceManager(Mock(), directory)
            for ident in ('first','second'):
                manager._save({'id':ident,'state':'active','expiresAt':'2000-01-01T00:00:00+00:00'})
            def release(ident):
                if ident=='first':raise RuntimeError('provider private message')
                return {'id':ident,'state':'released'}
            manager.release=Mock(side_effect=release)
            rows=manager.release_expired()
            self.assertEqual(manager.release.call_count,2)
            self.assertEqual({r['id']:r['state'] for r in rows},{'first':'cleanup_failed','second':'released'})
            self.assertNotIn('private message',json.dumps(rows))

class RecoveryOutcomeTests(unittest.TestCase):
    setUp=TestRunTests.setUp
    ready=TestRunTests.ready
    confirmed=TestRunTests.confirmed
    def test_recovery_cleanup_preserves_failed_verdict(self):
        row=self.confirmed();self.store._state(row['id'],'failed',leaseId='b'*24,cleanupState='failed')
        self.store.run(row['id'],recovery=True)
        self.assertEqual(self.store.get(row['id'])['state'],'failed')
        self.assertEqual(self.store.get(row['id'])['cleanupState'],'released')

if __name__=='__main__':unittest.main()
