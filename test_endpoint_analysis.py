import unittest
import test_experiences


class AnalysisTests(test_experiences.ExperienceTests):
    def setUp(self):
        super().setUp()
        self.data['processes'][0]['startedAt']='2026-10-01T00:00:00Z'
        self.store.report(self.identity['token'],self.data)

    def test_request_collect_followup_and_review(self):
        token=self.identity['token']
        request=self.store.analyses.request(token,'Application network','Describe observed connections',
            {'pid':1,'startedAt':'2026-10-01T00:00:00Z'})
        first=self.store.pull(token)
        self.assertEqual(first['payload']['moduleId'],'process_details')
        self.success(first)
        second=self.store.pull(token)
        self.assertEqual(second['payload']['moduleId'],'process_tcp')
        self.success(second)
        analysis=self.store.analyses.get(token,request['analysisId'])
        self.assertEqual(analysis['state'],'awaiting_review')
        calls=[]
        def create(title,message,owner):
            calls.append(message)
            return {'id':'cloud-task'}
        self.assertEqual(self.store.analyses.review(token,request['analysisId'],create),'cloud-task')
        self.assertEqual(self.store.analyses.review(token,request['analysisId'],create),'cloud-task')
        self.assertEqual(len(calls),1)
        self.assertIn('connections',calls[0])
        participants=self.store.task_participants('cloud-task')
        self.assertEqual(len(participants),1)
        self.assertEqual(participants[0]['kind'],'applens')
        self.assertEqual(participants[0]['completedSteps'],2)
        self.assertNotIn('token',participants[0])
        self.assertEqual(self.store.task_participants('unrelated-task'),[])

    def test_target_and_device_isolation(self):
        with self.assertRaises(ValueError):
            self.store.analyses.request(self.identity['token'],'App','Question',{'pid':2,'startedAt':'2026-10-01T00:00:00Z'})
        request=self.store.analyses.request(self.identity['token'],'App','Question',{'pid':1,'startedAt':'2026-10-01T00:00:00Z'})
        other=self.store.enroll(self.store.pairing()['code'],'Other')
        with self.assertRaises(PermissionError):self.store.analyses.get(other['token'],request['analysisId'])
        task=self.store.pull(self.identity['token'])
        self.store.complete(self.identity['token'],task['taskId'],task['lease'],{'state':'failed','summary':'Target exited'})
        self.assertEqual(self.store.analyses.get(self.identity['token'],request['analysisId'])['state'],'blocked')
        self.assertIsNone(self.store.pull(self.identity['token']))


if __name__=='__main__':unittest.main()
