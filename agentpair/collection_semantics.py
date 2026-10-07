"""Bounded task semantics over immutable collection evidence.

Source-message parents identify the user *round* owning an action. They never
decide whether two user rounds express the same goal; that is a separately
validated semantic projection. This module calls no model and rewrites no logs.
"""
from collections import defaultdict
import math

from SessionLens.sessionlens.message_graph import CALL_KINDS, build
from SessionLens.sessionlens.semantic_lineage import SYSTEM as NATIVE_SYSTEM
from SessionLens.sessionlens.semantic_lineage import validate as native_validate
from SessionLens.sessionlens.task_lineage import LABELS

SYSTEM = NATIVE_SYSTEM + '''
本包可能包含不同 Agent 或会话；只允许关联 source、sessionId、deviceId 相同的轮次。
每项 evidenceTurnIds 必须包含当前 turnId；有 parentTurnId 时也必须包含父轮次。
association=inferred 表示仅按源记录顺序初步归属，不能描述为已证明的因果连接。
coverage、gaps 与截断标记是证据范围限制；缺少 reasoning 时不得补写思路。
'''
LINEAGE_SYSTEM = SYSTEM


def _item(event):
    payload = event.get('payload') or {}
    if not isinstance(payload, dict):
        return {}
    item = payload.get('item', payload)
    return item if isinstance(item, dict) else {}


def _scope(record):
    return (record.get('source'), record.get('sessionId'), record.get('deviceId'))


def _user(record):
    return record['kind'] == 'user_message' or (
        record['kind'] == 'message' and record.get('role') == 'user')


def _assistant(record):
    return record['kind'] == 'assistant_message' or (
        record['kind'] == 'message' and record.get('role') == 'assistant')


def _excerpt(text, limit):
    if len(text) <= limit:
        return text, False
    marker = '\n[片段省略]\n'
    available = max(0, limit - len(marker))
    head = available // 3
    tail = available - head
    return text[:head] + marker + (text[-tail:] if tail else ''), True


def _turn_identifier(event):
    payload = event.get('payload') or {}
    item = _item(event)
    fields = event.get('sourceFields') or {}
    for container in (payload, item, fields):
        if isinstance(container, dict):
            for key in ('turn_id', 'turnId'):
                value = container.get(key)
                if isinstance(value, str) and value:
                    return value
    return None


def _request_identifier(event):
    provider = _item(event).get('providerData') or {}
    if not isinstance(provider, dict):
        return None
    value = provider.get('conversationRequestId')
    return value if isinstance(value, str) and value else None


def _records(records):
    result = []
    seen = set()
    for position, original in enumerate(records):
        if not isinstance(original, dict):
            raise ValueError('采集索引记录无效')
        identity = original.get('id') or original.get('recordId')
        if not isinstance(identity, str) or not identity or identity in seen:
            raise ValueError('采集记录缺少唯一身份')
        source = original.get('source')
        session = original.get('sessionId')
        if source not in ('codex', 'workbuddy') or not isinstance(session, str) or not session:
            raise ValueError('采集记录缺少有效来源或会话')
        text = original.get('text', '')
        if not isinstance(text, str):
            raise ValueError('采集记录正文无效')
        raw = original.get('event') or {}
        if not isinstance(raw, dict):
            raise ValueError('采集事件元信息无效')
        if raw.get('source') not in (None, source) or raw.get('sessionId') not in (None, session):
            raise ValueError('索引与原始事件来源不一致')
        kind = original.get('kind') or raw.get('kind')
        if not isinstance(kind, str) or not kind:
            raise ValueError('采集记录类型无效')
        # Platform rowid/seq can describe upload receipt order. The knowledge
        # window establishes source order before building a semantic packet.
        seq = original.get('windowSeq', raw.get('_seq', original.get('seq', position)))
        if isinstance(seq, bool) or not isinstance(seq, (int, float)) or not math.isfinite(seq):
            raise ValueError('采集记录顺序无效')
        role = original.get('role') or raw.get('role') or _item(raw).get('role')
        event = dict(raw, id=identity, source=source, sessionId=session,
                     kind=kind, role=role, _seq=seq)
        result.append(dict(original, id=identity, text=text, kind=kind, role=role,
                           recordId=original.get('recordId') or 'sessionlens:' + identity,
                           timestamp=original.get('timestamp', raw.get('timestamp')),
                           seq=seq, event=event, _position=position,
                           _sourceRecordId=raw.get('id') or identity))
        seen.add(identity)
    return sorted(result, key=lambda record: (record['seq'], record['_position']))


def _selected_records(turn, limit, parents, call_parents):
    """Select compact evidence, retaining complete call/return pairs where possible."""
    records = turn['_records']
    selected = {turn['turnId']: turn['_user_record']}
    replies = [record for record in records if _assistant(record)]
    reasoning = [record for record in records if record['kind'] == 'reasoning' and record['text'].strip()]
    calls = [record for record in records if record['kind'] in CALL_KINDS]
    by_id = {record['id']: record for record in records}
    results = defaultdict(list)
    for record in records:
        if record['kind'] == 'tool_result' and record['id'] in call_parents:
            results[call_parents[record['id']]].append(record)
    if replies and len(selected) < limit:
        selected[replies[-1]['id']] = replies[-1]
    if reasoning and len(selected) < limit:
        selected[reasoning[0]['id']] = reasoning[0]
    # Prefer first and last actions over only the beginning of a long execution.
    priority_calls = calls[:1] + calls[-1:] + calls[1:-1]
    for record in priority_calls:
        if record['id'] in selected:
            continue
        pair = [record] + results.get(record['id'], [])[:1]
        if len(selected) + len(pair) <= limit:
            for member in pair:
                selected[member['id']] = member
        elif not results.get(record['id']) and len(selected) < limit:
            selected[record['id']] = record
    # Include a source reasoning parent of a selected action before unrelated text.
    candidates = []
    for record in list(selected.values()):
        parent = parents.get(record['id'])
        if parent in by_id:
            candidates.append(by_id[parent])
    candidates += reasoning[-1:] + replies[:1] + records
    for record in candidates:
        if len(selected) >= limit:
            break
        selected.setdefault(record['id'], record)
    return sorted(selected.values(), key=lambda record: (record['seq'], record['_position']))


def turn_packet(records, max_turns=24, max_records_per_turn=6):
    """Return user rounds with bounded, traceable action evidence and honest gaps.

    ``windowSeq`` must describe source-log order (not upload receipt order).
    Native callers without a knowledge window may provide source order in ``seq``.
    Event metadata can establish stronger round ownership than sequential fallback.
    Models receive only these selected excerpts, never the full event payloads.
    """
    if not isinstance(max_turns, int) or isinstance(max_turns, bool) or not 1 <= max_turns <= 24:
        raise ValueError('最多选择 1 至 24 个用户轮次')
    if not isinstance(max_records_per_turn, int) or isinstance(max_records_per_turn, bool) or not 1 <= max_records_per_turn <= 16:
        raise ValueError('每轮最多选择 1 至 16 条证据')
    records = _records(records)
    # Native graph scopes by source + session. Separate devices before using it
    # so identical source ids from two machines cannot establish a connection.
    device_records = defaultdict(list)
    for record in records:
        device_records[record.get('deviceId')].append(record['event'])
    graphs = [build(events) for events in device_records.values()]
    graph = {key: [value for graph in graphs for value in graph[key]] for key in ('nodes', 'edges', 'gaps')}
    nodes = {node['eventId']: node for node in graph['nodes']}
    by_id = {record['id']: record for record in records}
    parents = {edge['to']: edge['from'] for edge in graph['edges'] if edge['relation'] == 'source_parent'}
    call_parents = {edge['to']: edge['from'] for edge in graph['edges'] if edge['relation'] == 'call_result'}
    gaps = list(graph['gaps'])
    turns = []
    user_turns = {}
    heads = {}
    mirrors = {}
    owners = {}
    bases = {}
    original_turns = defaultdict(set)
    significant = set(CALL_KINDS) | {'reasoning', 'tool_result', 'assistant_message'}
    last_significant = {}
    for record in records:
        scope = _scope(record)
        if _user(record) and record['text'].strip():
            source_id = nodes[record['id']]['sourceId']
            mirrored = mirrors.get((*scope, source_id)) if source_id else None
            head = heads.get(scope)
            if mirrored and mirrored['user'] != record['text']:
                gaps.append({'eventId': record['id'], 'reason': 'conflicting_user_source_id'})
                mirrored = None
            if not mirrored and head and head['user'] == record['text'] and not last_significant.get(scope):
                mirrored = head
            if mirrored:
                turn = mirrored
                bases[record['id']] = 'recorded_mirror' if source_id else 'inferred_mirror'
            else:
                turn = {'turnId': record['id'], 'source': record['source'], 'sessionId': record['sessionId'],
                        'deviceId': record.get('deviceId'), 'seq': record['seq'], 'user': record['text'],
                        '_user_record': record, '_records': [], 'ordinal': len(turns) + 1}
                turns.append(turn)
                user_turns[turn['turnId']] = turn
                bases[record['id']] = 'recorded'
                if source_id:
                    mirrors[(*scope, source_id)] = turn
            heads[scope] = turn
            owners[record['id']] = turn['turnId']
            native_turn = _turn_identifier(record['event'])
            if native_turn:
                original_turns[(*scope, native_turn)].add(turn['turnId'])
            last_significant[scope] = False
        else:
            if heads.get(scope):
                owners[record['id']] = heads[scope]['turnId']
                bases[record['id']] = 'inferred'
            if record['kind'] in significant or _assistant(record):
                last_significant[scope] = True
    # Source-message ancestry stops at the nearest user round, including a new
    # goal whose parent points back into a prior task. It never resolves task roots.
    for record in records:
        if _user(record):
            continue
        identity = record['id']
        native_turn = _turn_identifier(record['event'])
        matches = original_turns.get((*_scope(record), native_turn), set()) if native_turn else set()
        if len(matches) == 1:
            owners[identity] = next(iter(matches))
            bases[identity] = 'recorded_turn_id'
        elif len(matches) > 1:
            gaps.append({'eventId': identity, 'reason': 'ambiguous_native_turn'})
        current = identity
        path = set()
        for _ in range(64):
            current = parents.get(current)
            if not current or current in path:
                break
            path.add(current)
            if _user(by_id[current]):
                owner = owners.get(current)
                if owner:
                    if bases.get(identity) == 'recorded_turn_id' and owners[identity] != owner:
                        gaps.append({'eventId': identity, 'reason': 'conflicting_round_metadata'})
                    else:
                        owners[identity] = owner
                        bases[identity] = 'recorded_source_parent'
                break
        if record['kind'] == 'tool_result' and identity in call_parents:
            call_owner = owners.get(call_parents[identity])
            if call_owner:
                owners[identity] = call_owner
                bases[identity] = 'recorded_call_id'
    # A unique recorded request can join unparented records to a known round;
    # a request occurring in multiple rounds is not a user-goal identifier.
    request_turns = defaultdict(set)
    for record in records:
        request = _request_identifier(record['event'])
        if request and record['id'] in owners and bases.get(record['id']) != 'inferred':
            request_turns[(*_scope(record), request)].add(owners[record['id']])
    for record in records:
        if bases.get(record['id']) not in (None, 'inferred'):
            continue
        request = _request_identifier(record['event'])
        matches = request_turns.get((*_scope(record), request), set()) if request else set()
        if len(matches) == 1:
            owners[record['id']] = next(iter(matches))
            bases[record['id']] = 'recorded_request_id'
        owner = owners.get(record['id'])
        if not owner:
            gaps.append({'eventId': record['id'], 'reason': 'no_user_round_in_window'})
    for record in records:
        owner = owners.get(record['id'])
        if owner in user_turns:
            user_turns[owner]['_records'].append(record)
    total_turns = len(turns)
    if total_turns > max_turns:
        head = (max_turns + 1) // 2
        turns = turns[:head] + (turns[-(max_turns - head):] if max_turns > head else [])
        gaps.append({'reason': 'user_turns_omitted', 'omitted': total_turns - len(turns)})
    selected = [(turn, _selected_records(turn, max_records_per_turn, parents, call_parents)) for turn in turns]
    record_labels = {record['id']: 'R' + str(i + 1).zfill(3)
                     for i, record in enumerate(record for _, members in selected for record in members)}
    public_turns = []
    for ordinal, (turn, members) in enumerate(selected, 1):
        turn_budget = 30000 // max(1, len(selected))
        user_limit = min(1100, turn_budget // 3)
        proposal_limit = min(600, turn_budget // 6)
        text_limit = min(1100, (turn_budget - user_limit - proposal_limit) // max(1, len(members)))
        all_members = turn['_records']
        readable_reasoning = [record for record in all_members if record['kind'] == 'reasoning' and record['text'].strip()]
        if not readable_reasoning:
            gaps.append({'turnId': turn['turnId'], 'reason': 'no_readable_reasoning'})
        if len(members) < len(all_members):
            gaps.append({'turnId': turn['turnId'], 'reason': 'round_records_omitted',
                         'omitted': len(all_members) - len(members)})
        records_out = []
        member_ids = {record['id'] for record in members}
        for record in members:
            text, truncated = _excerpt(record['text'], text_limit)
            raw_parent = parents.get(record['id'])
            raw_call = call_parents.get(record['id'])
            records_out.append({'recordId': record['recordId'], 'sourceRecordId': record['_sourceRecordId'],
                                'label': record_labels[record['id']], 'kind': record['kind'], 'role': record.get('role'),
                                'tool': record['event'].get('name') or _item(record['event']).get('name'),
                                'text': text, 'truncated': truncated or bool(record.get('truncated')),
                                'textCoverage': record.get('coverage', 'indexed_excerpt'),
                                'seq': record['seq'], 'timestamp': record.get('timestamp'),
                                'parentRecordId': by_id[raw_parent]['recordId'] if raw_parent in record_labels else None,
                                'callRecordId': by_id[raw_call]['recordId'] if raw_call in record_labels else None,
                                'association': bases.get(record['id'], 'inferred'),
                                'requestId': nodes[record['id']]['requestId'], 'callId': nodes[record['id']]['callId'],
                                'relationshipGaps': [gap['reason'] for gap in gaps if gap.get('eventId') == record['id']]
                                    + (['parent_record_omitted'] if raw_parent and raw_parent not in record_labels else [])
                                    + (['call_record_omitted'] if raw_call and raw_call not in record_labels else [])})
        replies = [record for record in all_members if _assistant(record)]
        proposal, proposal_truncated = _excerpt(replies[-1]['text'], proposal_limit) if replies else ('', False)
        user_text, user_truncated = _excerpt(turn['user'], user_limit)
        public_turns.append({key: turn[key] for key in ('turnId', 'source', 'sessionId', 'deviceId', 'seq', 'ordinal')})
        public_turns[-1].update(label='T' + str(ordinal).zfill(3), user=user_text, truncated=user_truncated,
                               records=records_out, recordIds=[record['recordId'] for record in records_out],
                               allRecordIds=[record['recordId'] for record in all_members],
                               agentProposalAfter=proposal, agentProposalTruncated=proposal_truncated,
                               recordId=turn['_user_record']['recordId'],
                               agentProposalRecordId=replies[-1]['recordId'] if replies and replies[-1]['id'] in member_ids else None,
                               totalRecords=len(all_members), includedRecords=len(members),
                               hasTurnGapBefore=bool(public_turns[-1]['ordinal'] > (public_turns[-2]['ordinal'] + 1 if len(public_turns) > 1 else 1)))
    return {'turns': public_turns, 'gaps': gaps,
            'coverage': {'basis': 'selected_source_records', 'totalRecords': len(records),
                         'totalTurns': total_turns, 'includedTurns': len(public_turns),
                         'includedRecords': sum(len(turn['records']) for turn in public_turns),
                         'textCharacters': sum(len(turn['user']) + len(turn['agentProposalAfter'])
                                               + sum(len(record['text']) for record in turn['records']) for turn in public_turns),
                         'semanticAssociation': 'not_reviewed'}}


def validate_lineage(result, turns):
    """Reuse native structural validation and add the platform scope boundary."""
    if not isinstance(turns, list) or not turns or len(turns) > 24:
        raise ValueError('需求关联轮次范围无效')
    identities = [turn.get('turnId') for turn in turns]
    if any(not isinstance(identity, str) or not identity for identity in identities) or len(set(identities)) != len(identities):
        raise ValueError('需求轮次身份不唯一')
    links = native_validate(result, turns)
    known = {turn['turnId']: turn for turn in turns}
    for link in links:
        identity = link['turnId']
        parent = link['parentTurnId']
        if not link['reason'].strip():
            raise ValueError('需求关联缺少可读依据')
        if identity not in link['evidenceTurnIds'] or parent and parent not in link['evidenceTurnIds']:
            raise ValueError('需求关联必须引用当前轮次和前置轮次')
        if parent and _scope(known[identity]) != _scope(known[parent]):
            raise ValueError('需求关联不能跨 Agent、会话或设备')
        if any(_scope(known[identity]) != _scope(known[ref]) for ref in link['evidenceTurnIds']):
            raise ValueError('需求依据不能引用其他 Agent、会话或设备')
    return links


def group_tasks(turns, validated_links):
    """Build reversible task candidates, freezing each action's requirement round."""
    links = validate_lineage({'links': validated_links}, turns)
    turn_by_id = {turn['turnId']: turn for turn in turns}
    roots = {}
    tasks = {}
    snapshots = {}
    records = {}
    record_turn = {}
    previous_replies = defaultdict(list)
    for link in links:
        turn = turn_by_id[link['turnId']]
        root = roots.get(link['parentTurnId']) if link['parentTurnId'] else None
        root = root or turn['turnId']
        roots[turn['turnId']] = root
        if root not in tasks:
            tasks[root] = {'id': root, 'taskId': root, 'root': root, 'title': turn['user'], 'source': turn.get('source'),
                           'sessionId': turn.get('sessionId'), 'deviceId': turn.get('deviceId'),
                           'requirements': [], 'turnIds': [], 'recordIds': [], 'stages': [], 'executions': [],
                           'contexts': [], 'gaps': [], 'turnsCount': 0, 'recordCount': 0, 'dialogues': [],
                           'association': 'semantic_inference', 'status': link['status'],
                           'unresolved': link['relation'] == 'unresolved',
                           '_requirement': turn['turnId'], '_approval': None, '_plan': None}
        task = tasks[root]
        relation = link['relation']
        if relation in ('request', 'discussion', 'revision', 'resume', 'unresolved'):
            task['_requirement'] = turn['turnId']
            task['_approval'] = None
            task['_plan'] = None
        elif relation in ('approval', 'execution'):
            task['_approval'] = turn['turnId']
            if relation == 'approval' or not task['_plan']:
                eligible = [record for record in previous_replies[root] if record.get('seq', 0) < turn.get('seq', 0)]
                task['_plan'] = max(eligible, key=lambda record: record.get('seq', 0))['recordId'] if eligible else None
        snapshots[turn['turnId']] = {'requirementTurnId': task['_requirement'],
                                     'approvalTurnId': task['_approval'], 'planRecordId': task['_plan']}
        task['turnIds'].append(turn['turnId'])
        task['requirements'].append({'turnId': turn['turnId'], 'recordId': turn.get('recordId', 'sessionlens:' + turn['turnId']),
                                     'text': turn['user'], 'relation': relation, 'reason': link['reason'],
                                     'status': link['status'], 'association': 'semantic_inference'})
        task['recordIds'].extend(turn.get('recordIds', []))
        task['dialogues'].append({'turnId': turn['turnId'], 'relation': relation,
                                  'label': LABELS[relation], 'user': turn['user'], 'reason': link['reason'],
                                  'status': link['status'], 'evidenceTurnIds': link['evidenceTurnIds'],
                                  'recordIds': turn.get('recordIds', []), 'agentProposalAfter': turn.get('agentProposalAfter', ''),
                                  'association': 'semantic_inference'})
        if turn.get('hasTurnGapBefore'):
            task['gaps'].append('所选轮次之间有未包含的用户提问，需求过程可能不完整。')
        if turn.get('truncated'):
            task['gaps'].append('部分用户提问使用首尾摘录，完整要求请查看原文。')
        if turn.get('includedRecords', 0) < turn.get('totalRecords', 0):
            task['gaps'].append('本次只选取了部分执行记录，未包含的内容不能据此判断。')
        if not any(record['kind'] == 'reasoning' and record.get('text', '').strip() for record in turn.get('records', [])):
            task['gaps'].append('本次证据未包含可读的 reasoning，不能补写 Agent 的思路。')
        for record in turn.get('records', []):
            records[record['recordId']] = record
            record_turn[record['recordId']] = turn['turnId']
            if _assistant(record):
                previous_replies[root].append(record)
    # Match returns to their actual invocation. A later user's revision must not
    # retroactively change the requirement/version which an earlier call used.
    result_records = defaultdict(list)
    for record in records.values():
        if record['kind'] == 'tool_result' and record.get('callRecordId') in records:
            result_records[record['callRecordId']].append(record['recordId'])
    for identity, record in sorted(records.items(), key=lambda item: item[1].get('seq', 0)):
        turn_id = record_turn[identity]
        task = tasks[roots[turn_id]]
        titles = {'user_message': '用户提问', 'assistant_message': 'Agent 回复', 'reasoning': '已记录思路',
                  'tool_call': '调用工具', 'tool_result': '工具返回', 'command_execution': '执行命令',
                  'mcp_execution': '调用 MCP 工具', 'file_change': '文件变化', 'turn_completed': '轮次结束',
                  'turn_aborted': '轮次中断'}
        title = titles.get(record['kind'], 'Agent 回复' if _assistant(record) else '用户提问' if _user(record) else '源记录')
        if record.get('tool'):
            title += ' · ' + record['tool']
        task['stages'].append({'title': title, 'text': record.get('text', ''), 'kind': record['kind'],
                               'recordId': identity, 'turnId': turn_id, 'collector': 'sessionlens',
                               'basis': 'recorded', 'associationBasis': record.get('association', 'inferred'),
                               'timestamp': record.get('timestamp'), 'truncated': record.get('truncated', False)})
        if record['kind'] not in CALL_KINDS:
            continue
        task['executions'].append({'recordId': identity, 'turnId': turn_id, 'tool': record.get('tool'),
                                   'association': record.get('association', 'inferred'),
                                   'resultRecordIds': result_records.get(identity, []), **snapshots[turn_id]})
    for task in tasks.values():
        task['recordIds'] = list(dict.fromkeys(task['recordIds']))
        task['turnsCount'] = len(task['turnIds'])
        task['recordCount'] = len(task['recordIds'])
        task['gaps'] = list(dict.fromkeys(task['gaps']))
        for key in ('_requirement', '_approval', '_plan'):
            task.pop(key)
    return list(tasks.values())
