"""Application-level AgentPair handoff envelopes, independent of the byte transport."""
from datetime import datetime, timezone
from uuid import uuid4


PROTOCOL = 'agentpair.handoff.v1'
KINDS = {'assignment', 'finding', 'peer_review', 'revision_request', 'result'}


def message(sender, recipient, kind, summary, *, task_id, round_number, phase,
            evidence_refs=(), correlation_id=None):
    if kind not in KINDS or not sender or not recipient or sender == recipient:
        raise ValueError('Invalid collaboration handoff')
    return {
        'protocol': PROTOCOL,
        'id': uuid4().hex,
        'correlationId': correlation_id,
        'taskId': task_id,
        'round': round_number,
        'phase': phase,
        'kind': kind,
        'from': sender,
        'to': recipient,
        'summary': str(summary)[:500],
        'evidenceRefs': [str(ref)[:160] for ref in evidence_refs][:20],
        'createdAt': datetime.now(timezone.utc).isoformat(),
    }


def validate(value, *, task_id=None, recipient=None):
    if not isinstance(value, dict) or value.get('protocol') != PROTOCOL:
        raise ValueError('Unsupported collaboration protocol')
    if value.get('kind') not in KINDS or not value.get('id'):
        raise ValueError('Invalid collaboration message')
    if task_id is not None and value.get('taskId') != task_id:
        raise ValueError('Collaboration task mismatch')
    if recipient is not None and value.get('to') != recipient:
        raise ValueError('Collaboration recipient mismatch')
    return value
