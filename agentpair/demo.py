"""Offline orchestration demonstration: no generated code execution or API calls."""
from dataclasses import asdict
import json
from .contracts import Feedback, Plan, Task, code_hash
from .coordinator import Coordinator


class DemoNavigator:
    def propose(self, task):
        return [Plan('set', 'Use a set', ('Track seen integers',)), Plan('list', 'Use a list', ('Check previous outputs',))], 'set'

    def decide(self, task, plan, code, feedback, remaining, must_switch):
        if must_switch:
            return {'decision': 'switch_plan', 'planID': remaining[0].plan_id, 'instructions': 'Use the alternative plan'}
        return {'decision': 'repair', 'planID': plan.plan_id, 'instructions': 'Preserve first-seen ordering'}


class DemoDriver:
    def generate(self, task, plan, code, feedback, strategy):
        if code is None:
            return 'def solve(items):\n    return sorted(set(items))\n'
        return 'def solve(items):\n    return list(dict.fromkeys(items))\n'


class ScriptedExecutor:
    simulated = True
    suite_hash = 'offline-scripted-fixture'

    def __init__(self): self.calls = 0

    def execute(self, code, remaining_seconds):
        self.calls += 1
        if self.calls == 1:
            return Feedback(code_hash(code), self.suite_hash, 'wrong_answer', 0, 1,
                            ({'testID': 't1', 'actual': [1, 2], 'expected': [2, 1]},))
        return Feedback(code_hash(code), self.suite_hash, 'pass', 1, 1)


def main():
    task = Task('demo-stable-dedup', 'Stable integer deduplication', ({'id': 't1', 'input': [2, 1, 2], 'expected': [2, 1]},))
    result = Coordinator(DemoNavigator(), DemoDriver(), ScriptedExecutor()).run(task)
    print(json.dumps(asdict(result), ensure_ascii=False, indent=2))


if __name__ == '__main__': main()
