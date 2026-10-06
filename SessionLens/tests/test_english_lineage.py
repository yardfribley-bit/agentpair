import unittest
from sessionlens.task_lineage import classify


class EnglishLineageTests(unittest.TestCase):
    def setUp(self):
        self.active={'id':'login-task','requirements':'Build a login page for Orchard'}

    def test_confirmation_and_execution_keep_original_requirement(self):
        for text,role in [('Yes','approval'),('Looks good!','approval'),('Okay','approval'),
                          ('Go ahead.','execution'),('Implement it','execution'),('Continue','execution')]:
            with self.subTest(text=text):
                root,relation,reason=classify(text,self.active,[self.active])
                self.assertEqual((root,relation),('login-task',role))
                self.assertTrue(reason)
        self.assertEqual(self.active['requirements'],'Build a login page for Orchard')

    def test_initial_approval_is_unresolved_and_new_subject_stays_separate(self):
        self.assertEqual(classify('Go ahead',None,[])[1],'unresolved')
        for text in ['Create a weather dashboard','New task: write a poem','Why is it raining in Shanghai?']:
            self.assertEqual(classify(text,self.active,[self.active])[:2],(None,'request'))

    def test_clear_revision_and_named_resume_are_conservative(self):
        self.assertEqual(classify('Change this to a two-column layout',self.active,[self.active])[:2],('login-task','revision'))
        self.assertEqual(classify('Why this approach?',self.active,[self.active])[:2],('login-task','discussion'))
        other={'id':'weather-task','requirements':'Find the weather in Shanghai'}
        self.assertEqual(classify('Return to the Orchard login page',other,[self.active,other])[:2],('login-task','resume'))
        self.assertEqual(classify('Return to the previous task',other,[self.active,other])[:2],(None,'request'))


if __name__=='__main__':unittest.main()
