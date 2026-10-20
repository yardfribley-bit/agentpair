import unittest
from agentpair.contracts import Feedback, Task, code_hash
from agentpair.coordinator import Coordinator
from agentpair.demo import DemoDriver, DemoNavigator, ScriptedExecutor


TASK = Task('fixture', 'Stable deduplication', ({'id': 't1', 'input': [2,1,2], 'expected': [2,1]},))


class AlwaysFails(ScriptedExecutor):
    def execute(self, code, remaining_seconds):
        return Feedback(code_hash(code), self.suite_hash, 'wrong_answer', 0, 1, ({'testID':'t1','actual':[],'expected':[2,1]},))


class CoordinatorTests(unittest.TestCase):
    def test_repair_and_simulation_label(self):
        r=Coordinator(DemoNavigator(),DemoDriver(),ScriptedExecutor()).run(TASK)
        self.assertEqual(r.status,'simulated_pass')
        self.assertEqual(r.requests,4)
        self.assertEqual(len(r.rounds),2)

    def test_repeat_requires_switch(self):
        r=Coordinator(DemoNavigator(),DemoDriver(),AlwaysFails()).run(TASK)
        self.assertEqual(r.decisions[-1]['decision'],'switch_plan')
        self.assertEqual(r.rounds[-1]['planID'],'list')
        self.assertEqual(r.status,'tests_failed')

    def test_request_limit(self):
        task=Task('fixture','description',TASK.visible_tests,max_requests=2)
        r=Coordinator(DemoNavigator(),DemoDriver(),AlwaysFails()).run(task)
        self.assertEqual(r.status,'budget_exhausted')
        self.assertEqual(r.requests,2)

    def test_cancelled_before_call(self):
        r=Coordinator(DemoNavigator(),DemoDriver(),ScriptedExecutor(),cancelled=lambda:True).run(TASK)
        self.assertEqual(r.status,'cancelled')
        self.assertEqual(r.requests,0)

    def test_deadline_before_call(self):
        ticks=iter([0,200])
        r=Coordinator(DemoNavigator(),DemoDriver(),ScriptedExecutor(),clock=lambda:next(ticks)).run(TASK)
        self.assertEqual(r.status,'deadline_exceeded')

    def test_stale_feedback_rejected(self):
        class WrongExecutor(ScriptedExecutor):
            def execute(self, code, remaining_seconds): return Feedback('different',self.suite_hash,'pass',1,1)
        r=Coordinator(DemoNavigator(),DemoDriver(),WrongExecutor()).run(TASK)
        self.assertEqual(r.status,'protocol_error')

    def test_model_cannot_claim_success(self):
        class BadNavigator(DemoNavigator):
            def decide(self,*args): return {'decision':'success'}
        r=Coordinator(BadNavigator(),DemoDriver(),AlwaysFails()).run(TASK)
        self.assertEqual(r.status,'protocol_error')

    def test_infrastructure_exception(self):
        class BrokenDriver:
            def generate(self,*args): raise OSError('fixture failure')
        r=Coordinator(DemoNavigator(),BrokenDriver(),ScriptedExecutor()).run(TASK)
        self.assertEqual(r.status,'infrastructure_error')


if __name__=='__main__': unittest.main()
