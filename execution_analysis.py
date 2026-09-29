"""Offline, evidence-first execution analysis for AgentReins task snapshots."""
import argparse
import hashlib
import json
from collections import defaultdict


def analyze(snapshot):
    if snapshot.get('schemaVersion') != 1:
        raise ValueError('Only schemaVersion=1 task snapshots are supported')
    task = snapshot.get('task', {})
    if not task.get('taskID'):
        raise ValueError('Missing task.taskID')
    raw = snapshot.get('events')
    if not isinstance(raw, list) or not raw:
        raw = snapshot.get('sourceEvents', [])
    if not isinstance(raw, list):
        raise ValueError('events/sourceEvents must be arrays')
    timeline, seen, calls, results = [], set(), defaultdict(list), defaultdict(list)
    limitations = list(snapshot.get('capture', {}).get('missingCapabilities', []))
    for index, event in enumerate(raw):
        if not isinstance(event, dict):
            raise ValueError('Each event must be an object')
        native = event.get('windowsNativeEvent') or event
        eid = native.get('event_id') or event.get('id')
        if not eid:
            limitations.append(f'Event at index {index} has no ID; excluded')
            continue
        eid = str(eid)
        if eid in seen:
            raise ValueError('Duplicate evidence ID: ' + eid)
        seen.add(eid)
        kind = native.get('action_kind') or event.get('op') or 'unknown'
        if isinstance(native.get('action'), dict):
            kind = native['action'].get('kind', kind)
        content = native.get('content')
        if content is None:
            content = event.get('modelResponse') or event.get('userIntent') or event.get('command')
        timeline.append({'evidenceID': eid, 'locator': f'/{"events" if raw is snapshot.get("events") else "sourceEvents"}/{index}',
                         'timestamp': native.get('event_timestamp_unix_ms') or event.get('ts'),
                         'kind': kind, 'tool': native.get('tool_name'),
                         'toolCallID': native.get('tool_call_id') or event.get('toolCallId'),
                         'content': content})
        call_id = native.get('tool_call_id') or event.get('toolCallId')
        session = native.get('agent_session_id') or native.get('session_id') or event.get('sessionId')
        if call_id and session:
            key = (str(session), str(call_id))
            if kind == 'tool_call': calls[key].append(eid)
            if kind == 'tool_result': results[key].append(eid)
    findings = []
    def finding(code, message, ids):
        findings.append({'code': code, 'assessment': 'observed', 'message': message, 'evidenceIDs': ids})
    for key, ids in calls.items():
        if key not in results:
            finding('missing_tool_result', 'Captured tool call has no result with the same native session and call ID; this does not prove execution failed.', ids)
    for key, ids in results.items():
        if key not in calls:
            finding('missing_tool_call', 'Captured result has no matching native call; attribution is incomplete.', ids)
    signatures = defaultdict(list)
    for item in timeline:
        if item['kind'] == 'tool_call' and item['content'] is not None:
            signature = json.dumps([item['tool'], item['content']], sort_keys=True, ensure_ascii=False)
            signatures[signature].append(item['evidenceID'])
    for ids in signatures.values():
        if len(ids) > 1:
            finding('repeated_recorded_call', 'Identical tool name and recorded content appear more than once; intent and whether this is wasteful require review.', ids)
    limitations += ['No task-completion verdict without explicit acceptance criteria and verification evidence.',
                    'No temporal-only OS attribution; no inference of network payload from connection metadata.',
                    'This report includes evidence content and must remain private; no automatic model upload.']
    return {'reportVersion': 1, 'taskID': task['taskID'], 'snapshotSHA256': hashlib.sha256(json.dumps(snapshot, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
            'captureState': snapshot.get('capture', {}).get('state', 'unknown'),
            'completion': 'unknown', 'timeline': timeline, 'findings': findings,
            'limitations': limitations}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('snapshot', help='Private AgentReins schemaVersion=1 task snapshot JSON')
    args = parser.parse_args()
    with open(args.snapshot, encoding='utf-8') as source:
        report = analyze(json.load(source))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
