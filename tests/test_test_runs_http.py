"""Real HTTP auth/CSRF and task detail projection; all cloud activity is fake."""
import json
import unittest
from unittest.mock import Mock

from tests.test_cloud_workflow_http import CloudWorkflowHTTPTests as Base
from tests.test_test_runs import RECIPE, BUILD, github_rows
from agentpair.test_runs import GitHubBuildProof


class TestRunsHTTPTests(unittest.TestCase):
    ORIGIN=Base.ORIGIN
    tearDown=Base.tearDown
    call=Base.call
    admin_login=Base.admin_login
    new_client=staticmethod(Base.new_client)
    def setUp(self):
        Base.setUp(self)
        self.engine.test_runs.start_threads=False
        self.engine.test_runs.verifier=GitHubBuildProof(lambda p:github_rows()[p])
        self.engine.test_runs.catalog.register(RECIPE)
        self.plan={'softwareIds':[RECIPE['id']],'subjectSoftwareId':RECIPE['id'],'build':BUILD,
                   'plan':{c:'执行受控活动并核对事件、证据、采样窗口与指标阈值。' for c in self.engine.test_runs.CASES}}

    def test_test_runs_require_admin_and_csrf(self):
        for path in ('/api/test-runs','/api/test-runs/'+'a'*24):
            with self.assertRaises(Exception) as e:self.call(path)
            self.assertEqual(e.exception.code,403);e.exception.close()
        csrf=self.admin_login()
        with self.assertRaises(Exception) as e:self.call('/api/test-runs',self.plan,csrf='invalid')
        self.assertEqual(e.exception.code,403);e.exception.close()
        status,row=self.call('/api/test-runs',self.plan,csrf=csrf)
        self.assertEqual(status,201);self.assertEqual(row['state'],'ready')
        self.assertEqual(self.console.create_calls,[])
        self.assertEqual(self.call('/api/test-runs/'+row['id'])[1]['proof']['commit'],BUILD['commit'])

    def test_failed_proof_returns_actionable_error_without_resources(self):
        csrf=self.admin_login();self.engine.test_runs.verifier=Mock()
        self.engine.test_runs.verifier.verify.side_effect=ValueError('CI 未成功；禁止开机')
        with self.assertRaises(Exception) as e:self.call('/api/test-runs',self.plan,csrf=csrf)
        self.assertEqual(e.exception.code,400)
        self.assertIn('CI',json.load(e.exception)['error']);e.exception.close()
        self.assertEqual(self.console.create_calls,[])

    def test_cancel_ready_run_never_creates_and_unknown_id_is_404(self):
        csrf=self.admin_login();_,row=self.call('/api/test-runs',self.plan,csrf=csrf)
        _,cancelled=self.call('/api/test-runs/'+row['id']+'/cancel',{},csrf=csrf)
        self.assertEqual(cancelled['state'],'cancelled')
        self.assertEqual(cancelled['cleanupState'],'not_required')
        with self.assertRaises(Exception) as e:self.call('/api/test-runs/'+'f'*24)
        self.assertEqual(e.exception.code,404);e.exception.close()
        self.assertEqual(self.console.create_calls,[])

    def test_login_and_release_choices_reject_wrong_stage_or_missing_csrf(self):
        csrf=self.admin_login();_,row=self.call('/api/test-runs',self.plan,csrf=csrf)
        for action,data in (('login-confirm',{'confirmed':True}),('release-decision',{'choice':'release'})):
            with self.assertRaises(Exception) as e:self.call('/api/test-runs/'+row['id']+'/'+action,data,csrf='invalid')
            self.assertEqual(e.exception.code,403);e.exception.close()
            with self.assertRaises(Exception) as e:self.call('/api/test-runs/'+row['id']+'/'+action,data,csrf=csrf)
            self.assertEqual(e.exception.code,400);e.exception.close()
        self.assertEqual(self.console.create_calls,[])

if __name__=='__main__':unittest.main()
