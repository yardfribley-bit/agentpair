"""Deterministic orchestration; model adapters and Executor are injected."""
from dataclasses import asdict
import copy
import time
import uuid
from .contracts import BudgetError, Plan, ProtocolError, RunResult, code_hash


class Coordinator:
    def __init__(self, navigator, driver, executor, deadline_seconds=120, clock=time.monotonic, cancelled=lambda: False):
        self.navigator = navigator
        self.driver = driver
        self.executor = executor
        self.deadline_seconds = deadline_seconds
        self.clock = clock
        self.cancelled = cancelled

    def run(self, task):
        result = RunResult('infrastructure_error', str(uuid.uuid4()), simulated=bool(getattr(self.executor, 'simulated', False)))
        end = self.clock() + self.deadline_seconds
        def guard():
            if self.cancelled(): raise InterruptedError('cancelled')
            if self.clock() >= end: raise TimeoutError('deadline')
        def request(method, *args):
            guard()
            if result.requests >= task.max_requests: raise BudgetError('request limit')
            result.requests += 1
            value = method(*copy.deepcopy(args))
            guard()
            return value
        try:
            task.validate()
            if self.deadline_seconds <= 0: raise ProtocolError('Invalid deadline')
            plans, selected = request(self.navigator.propose, task)
            if len(plans) != 2 or any(not isinstance(p, Plan) or not p.plan_id or not p.title or not p.steps for p in plans):
                raise ProtocolError('Exactly two valid plans required')
            remaining = {p.plan_id: p for p in plans}
            if len(remaining) != 2 or selected not in remaining:
                raise ProtocolError('Invalid selected or duplicate plan ID')
            current = remaining.pop(selected)
            result.decisions.append({'decision': 'initial_plan', 'planID': current.plan_id})
            code, feedback, strategy = None, None, None
            history_code, history_feedback = set(), set()
            for round_number in range(1, task.max_rounds + 1):
                guard()
                code = request(self.driver.generate, task, current, code, feedback, strategy)
                if not isinstance(code, str) or not code.strip() or len(code.encode()) > 65536:
                    raise ProtocolError('Invalid or oversized code')
                digest = code_hash(code)
                feedback = self.executor.execute(code, remaining_seconds=max(0, end-self.clock()))
                feedback.validate(digest, self.executor.suite_hash)
                result.rounds.append({'round': round_number, 'planID': current.plan_id, 'code': code, 'candidateHash': digest, 'feedback': asdict(feedback)})
                guard()
                if feedback.status == 'pass':
                    result.status = 'simulated_pass' if result.simulated else 'tested_pass'
                    return result
                if round_number == task.max_rounds:
                    result.status = 'tests_failed'
                    return result
                repeated = digest in history_code or feedback.signature() in history_feedback
                history_code.add(digest)
                history_feedback.add(feedback.signature())
                if repeated:
                    if not remaining:
                        result.status = 'plans_exhausted'
                        return result
                    decision = request(self.navigator.decide, task, current, code, feedback, list(remaining.values()), True)
                else:
                    decision = request(self.navigator.decide, task, current, code, feedback, list(remaining.values()), False)
                if not isinstance(decision, dict) or decision.get('decision') not in ('repair', 'switch_plan'):
                    raise ProtocolError('Invalid navigator decision')
                if repeated and decision['decision'] != 'switch_plan':
                    raise ProtocolError('Repeated failure requires a plan switch')
                target = decision.get('planID')
                if decision['decision'] == 'switch_plan':
                    if not remaining:
                        result.status = 'plans_exhausted'
                        return result
                    if target not in remaining: raise ProtocolError('Plan is absent or already attempted')
                    current = remaining.pop(target)
                    code, feedback = None, None
                    history_code, history_feedback = set(), set()
                elif target != current.plan_id:
                    raise ProtocolError('Repair cannot silently switch plans')
                strategy = decision.get('instructions')
                if not isinstance(strategy, str) or not strategy.strip():
                    raise ProtocolError('Missing implementation instructions')
                result.decisions.append(copy.deepcopy(decision))
            result.status = 'tests_failed'
        except ProtocolError as error:
            result.status, result.error_type = 'protocol_error', type(error).__name__
        except BudgetError as error:
            result.status, result.error_type = 'budget_exhausted', type(error).__name__
        except InterruptedError:
            result.status = 'cancelled'
        except TimeoutError:
            result.status = 'deadline_exceeded'
        except Exception as error:
            result.status, result.error_type = 'infrastructure_error', type(error).__name__
        return result
