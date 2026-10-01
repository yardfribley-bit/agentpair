import unittest
import test_devices


class ExperienceTests(test_devices.DeviceTests):
    def setUp(self):
        super().setUp()
        self.data.update(osVersion='10.0.20348',hostRuntimeVersion='5.1')
        self.store.report(self.identity['token'],self.data)

    def dispatch(self):
        queued=self.store.dispatch('admin',self.identity['deviceId'],{'goal':'Collect',
            'action':'run_module','moduleId':'process_tcp',
            'parameters':{'pid':1,'startedAt':'2026-10-01T00:00:00Z'}})
        return self.store.pull(self.identity['token'])

    def success(self, task):
        module=task['payload']['module']
        result={'state':'completed','evidence':{'moduleId':module['id'],'moduleVersion':module['version'],
            'sha256':module['sha256'],'output':{'schemaVersion':1,'capability':module['id'],
                'target':module['parameters'],'evidence':{'connections':[]}}}}
        self.store.complete(self.identity['token'],task['taskId'],task['lease'],result)

    def test_reuse_requires_real_success_and_new_execution(self):
        first=self.dispatch()
        self.assertNotIn('experienceRef',first['payload'])
        self.success(first)
        second=self.dispatch()
        self.assertEqual(second['payload']['experienceRef']['status'],'single_execution')
        self.assertEqual(second['state'],'assigned')
        self.success(second)
        experience=self.store.experiences.list('admin')[0]
        self.assertEqual(experience['successfulRuns'],2)
        self.assertEqual(experience['status'],'repeated_execution')
        self.assertNotIn('parameters',experience['method'])
        self.assertEqual(self.store.experiences.list('another-account'),[])
        with self.assertRaises(PermissionError): self.success(second)
        self.assertEqual(self.store.experiences.list('admin')[0]['successfulRuns'],2)

    def test_environment_change_and_failed_replay(self):
        first=self.dispatch();self.success(first)
        self.data['osVersion']='10.0.26100'
        self.store.report(self.identity['token'],self.data)
        changed=self.dispatch()
        self.assertNotIn('experienceRef',changed['payload'])
        self.store.complete(self.identity['token'],changed['taskId'],changed['lease'],{'state':'failed'})
        self.assertNotIn('experienceRef',self.dispatch()['payload'])

    def test_failed_method_not_automatically_reused(self):
        first=self.dispatch();self.success(first)
        second=self.dispatch()
        self.store.complete(self.identity['token'],second['taskId'],second['lease'],{'state':'failed'})
        self.assertEqual(self.store.experiences.list('admin')[0]['status'],'needs_revalidation')
        self.assertNotIn('experienceRef',self.dispatch()['payload'])


if __name__=='__main__':unittest.main()
