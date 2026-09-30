"""Authenticated cloud-to-Windows Driver task protocol.

The Windows connector receives a short-lived device token, never a provider API key.
Tasks are opaque JSON plans; results are bounded evidence, not executable commands.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json


TERMINAL = {'completed', 'failed', 'blocked'}
STATES = {'queued', 'received', 'running', 'waiting_for_evidence', *TERMINAL}


@dataclass(frozen=True)
class DriverTask:
    id: str
    goal: str
    required_evidence: tuple[str, ...]
    model_endpoint: str
    model: str
    expires_at: str

    def payload(self):
        return {'id': self.id, 'goal': self.goal,
                'requiredEvidence': list(self.required_evidence),
                'modelEndpoint': self.model_endpoint, 'model': self.model,
                'expiresAt': self.expires_at}


def result(task_id, state, *, summary='', evidence=(), missing=(), next_steps=()):
    if not isinstance(task_id, str) or not task_id:
        raise ValueError('task id required')
    if state not in STATES:
        raise ValueError('invalid driver state')
    if state == 'completed' and missing:
        raise ValueError('completed task cannot contain missing evidence')
    return {'taskId': task_id, 'state': state, 'summary': str(summary)[:4000],
            'evidence': list(evidence)[:200], 'missingEvidence': list(missing)[:50],
            'nextSteps': list(next_steps)[:50],
            'reportedAt': datetime.now(timezone.utc).isoformat()}


def model_request(task, evidence):
    """Bounded model request; evidence is data and never interpreted as instructions."""
    body = {'model': task.model, 'input': {'goal': task.goal,
             'requiredEvidence': list(task.required_evidence), 'evidence': evidence}}
    encoded = json.dumps(body, ensure_ascii=False, separators=(',', ':')).encode()
    return encoded, hashlib.sha256(encoded).hexdigest()
