"""Read explicit source turn IDs without guessing a task or choosing conflicts.

Only known source metadata locations are inspected. Tool arguments, returned
data, message content and other nested objects are never searched for IDs.
"""
from collections.abc import Mapping
from typing import Any


MAX_TURN_ID_LENGTH = 300
_TURN_FIELDS = ('turn_id', 'turnId')
_ROOT_TURN_FIELDS = ('root_turn_id', 'rootTurnId')
_PASSTHROUGH = 'internal_chat_message_metadata_passthrough'


def _identity(nodes, fields):
    sources: list[dict[str, str]] = []
    for path, node in nodes:
        if not isinstance(node, Mapping):
            continue
        candidates = ((path, node), (f'{path}.{_PASSTHROUGH}'.lstrip('.'), node.get(_PASSTHROUGH)))
        for candidate_path, candidate in candidates:
            if not isinstance(candidate, Mapping):
                continue
            for field in fields:
                value = candidate.get(field)
                if isinstance(value, str) and 0 < len(value) <= MAX_TURN_ID_LENGTH and value.strip():
                    sources.append({'path': f'{candidate_path}.{field}'.lstrip('.'), 'value': value})
    values = {source['value'] for source in sources}
    return sources[0]['value'] if len(values) == 1 else None, len(values) > 1, sources


def extract_turn_identity(event: Any) -> dict[str, Any]:
    """Return agreed turn/root-turn IDs and valid explicit source candidates.

    ``turnIdentitySources`` contains stable ``{path, value}`` entries, including
    agreeing duplicates, so callers can explain the evidence. Different valid
    IDs set ``turnIdentityConflict`` and leave ``turnId`` unset; no source wins
    by priority. IDs are opaque strings and are not stripped or coerced.

    Root turn fields are a separate identity family; a child turn differing
    from its root turn is expected and is not a conflict. These metadata IDs
    alone do not associate a turn with a user, task, account or device.
    """
    event = event if isinstance(event, Mapping) else {}
    payload = event.get('payload')
    payload = payload if isinstance(payload, Mapping) else {}
    nodes = (
        ('', event),
        ('payload', payload),
        ('payload.item', payload.get('item')),
        ('sourceFields', event.get('sourceFields')),
    )
    turn, turn_conflict, turn_sources = _identity(nodes, _TURN_FIELDS)
    root_turn, root_conflict, root_sources = _identity(nodes, _ROOT_TURN_FIELDS)
    return {
        'turnId': turn,
        'turnIdentityConflict': turn_conflict,
        'turnIdentitySources': turn_sources,
        'rootTurnId': root_turn,
        'rootTurnIdentityConflict': root_conflict,
        'rootTurnIdentitySources': root_sources,
    }
