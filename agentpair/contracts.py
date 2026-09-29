from dataclasses import dataclass, field
from hashlib import sha256
import json


class ProtocolError(ValueError):
    pass


class BudgetError(RuntimeError):
    pass


@dataclass(frozen=True)
class Plan:
    plan_id: str
    title: str
    steps: tuple[str, ...]


@dataclass(frozen=True)
class Task:
    task_id: str
    description: str
    visible_tests: tuple[dict, ...]
    max_rounds: int = 3
    max_requests: int = 6

    def validate(self):
        if not self.task_id or not self.description or not self.visible_tests:
            raise ProtocolError('Task identity, description and tests are required')
        if not 1 <= self.max_rounds <= 3 or not 1 <= self.max_requests <= 6:
            raise ProtocolError('Invalid run limits')


def code_hash(code):
    return sha256(code.encode()).hexdigest()


@dataclass(frozen=True)
class Feedback:
    candidate_hash: str
    suite_hash: str
    status: str
    passed_count: int
    total_count: int
    failures: tuple[dict, ...] = ()

    def validate(self, candidate_hash, suite_hash):
        if self.candidate_hash != candidate_hash or self.suite_hash != suite_hash:
            raise ProtocolError('Feedback belongs to a different candidate or suite')
        if self.status not in ('pass', 'wrong_answer', 'runtime_error', 'timeout'):
            raise ProtocolError('Unknown execution status')
        if self.total_count < 1 or not 0 <= self.passed_count <= self.total_count:
            raise ProtocolError('Invalid test counts')
        if self.status == 'pass' and (self.passed_count != self.total_count or self.failures):
            raise ProtocolError('Cannot claim pass with incomplete or failed tests')
        if self.status != 'pass' and not self.failures:
            raise ProtocolError('Failure must include evidence')

    def signature(self):
        return json.dumps([self.status, self.failures], sort_keys=True)


@dataclass
class RunResult:
    status: str
    run_id: str
    rounds: list = field(default_factory=list)
    decisions: list = field(default_factory=list)
    requests: int = 0
    error_type: str | None = None
    simulated: bool = False
