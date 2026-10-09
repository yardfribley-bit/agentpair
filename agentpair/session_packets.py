"""Bounded, scoped source evidence for session insights; no model or task claims.

User rounds are candidates, not semantic task boundaries. Call IDs establish
only tool call/return relations. Neither message ancestry nor sharing a session
proves that two user requests belong to one task.
"""
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
import re

from .interaction_audit import redact

MAX_FRAGMENTS = 80
MAX_PACKET_BYTES = 40000
SILENCE_SECONDS = 300
REVISION_VERSION = 'session-events-v1'
CALL_KINDS = {'tool_call', 'command_execution', 'mcp_execution', 'extension_execution'}
RELEVANT = CALL_KINDS | {'tool_result', 'message', 'user_message', 'assistant_message',
                        'reasoning', 'turn_started', 'turn_completed', 'turn_aborted',
                        'file_change', 'parse_error'}
CONTROL_TAGS = ('send_user_message_question_reply', 'environment_context',
                'in-app-browser-context', 'system-reminder', 'permissions', 'instructions')


def source_seconds(value):
    """Normalize ISO, Unix seconds and Unix milliseconds without receipt time."""
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, str):
        try:
            value = float(value)
        except ValueError:
            try:
                parsed = datetime.fromisoformat(value.strip().replace('Z', '+00:00'))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                value = parsed.timestamp()
            except (ValueError, OverflowError, OSError):
                return 0.0
    if not isinstance(value, (int, float)) or not math.isfinite(value):
        return 0.0
    if abs(value) >= 100_000_000_000:
        value /= 1000
    return float(value) if 0 < value < 253402300800 else 0.0


def event_digest(identity):
    """Commutative event-set digest, for incremental heads and packet agreement."""
    return int.from_bytes(hashlib.sha256(('event:' + identity).encode()).digest(), 'big')


def revision_from_digest(owner, device, session, source, count, digest):
    # Hash a count and 256-bit additive fingerprint of UNIQUE event IDs. This
    # avoids re-reading an entire historical session after each small upload.
    value = [REVISION_VERSION, owner, device, source, session, count,
             format(digest % (1 << 256), '064x')]
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def _item(event):
    payload = event.get('payload', {})
    item = payload.get('item', payload) if isinstance(payload, dict) else payload
    return item if isinstance(item, dict) else {'content': item}


def _content(value, depth=0):
    if depth > 12:
        return '[嵌套内容省略]'
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return '\n'.join(_content(part, depth + 1) for part in value)
    if isinstance(value, dict):
        for key in ('text', 'content', 'message', 'summary', 'rawContent', 'output', 'stdout'):
            if key in value:
                return _content(value[key], depth + 1)
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return '' if value is None else str(value)


def user_text(event):
    """Exclude known environment/approval controls from original user goals."""
    item = _item(event)
    text = _content(item.get('content', item.get('message', item.get('text', '')))).strip()
    if re.search(r'APPROVAL REQUEST (?:BEGIN|END)', text) or '<send_user_message_question_reply' in text:
        return '', 'approval'
    def background_notification(value):
        # WorkBuddy can wrap a tool's background completion notice inside a
        # user_query. Recognize its envelope and task ID, not requests which
        # merely mention XML markup.
        return (re.match(r'\s*<task-notification\b[^>]*>',value) and
                re.search(r'<task-id\b[^>]*>[^<]+</task-id>',value))
    if background_notification(text):
        return '', 'control'
    queries = re.findall(r'<user_query\b[^>]*>(.*?)</user_query>', text, re.S)
    if queries:
        text = queries[-1]
    else:
        if re.match(r'\s*(?:#+\s*对话历史摘要\s*)?<(cb_summary|conversation_history_summary)\b', text):
            return '', 'control'
        for name in CONTROL_TAGS:
            text = re.sub(r'<'+re.escape(name)+r'\b[^>]*>.*?</'+re.escape(name)+r'>', '', text, flags=re.S)
    text = text.strip()
    if background_notification(text):
        return '', 'control'
    return (text, None) if text else ('', 'control')


def _role(event):
    return event.get('role') or _item(event).get('role')


def _user(event):
    return event.get('kind') == 'user_message' or (event.get('kind') == 'message' and _role(event) == 'user')


def _assistant(event):
    return event.get('kind') == 'assistant_message' or (event.get('kind') == 'message' and _role(event) == 'assistant')


def _call_id(event):
    item = _item(event)
    value = event.get('callId') or item.get('callId') or item.get('call_id')
    return value if isinstance(value, str) and value else None


def _text(event):
    item = _item(event)
    kind = event.get('kind')
    if _user(event):
        text, control = user_text(event)
        if not control:
            return text
        return _content(item.get('content', item.get('message', item)))
    if kind in CALL_KINDS:
        value = item.get('arguments', item.get('input', item.get('command', item)))
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError:
                pass
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)
        name = event.get('name') or item.get('name')
        # Completed execution events sometimes include result in the same item.
        output = item.get('aggregated_output', item.get('stdout'))
        if output is not None:
            text += '\n返回：' + _content(output)
        return (str(name) + '\n' if name else '') + text
    if kind == 'tool_result':
        value = item.get('output', item.get('result', item.get('content', item)))
        text = json.dumps(value, ensure_ascii=False, sort_keys=True) if isinstance(value, dict) else _content(value)
        if value is not item:
            for key in ('exit_code', 'exitCode', 'status', 'success', 'error', 'stderr'):
                if key in item:
                    text += '\n' + key + ': ' + _content(item[key])
        return text
    if kind == 'reasoning':
        return _content(item.get('content') or item.get('rawContent') or item.get('summary') or item.get('text') or '')
    if kind == 'parse_error':
        return '源日志记录无法解析；本事件不提供可验证的执行正文。'
    return _content(item.get('content', item.get('message', item)))


def _cut(text, budget):
    raw = text.encode('utf-8')
    if len(raw) <= budget:
        return text, False
    marker = '\n[正文片段已截取]\n'
    available = max(0, budget - len(marker.encode()))
    before = available // 2
    after = available - before
    return raw[:before].decode('utf-8', 'ignore') + marker + (raw[-after:].decode('utf-8', 'ignore') if after else ''), True


def _order(event):
    evidence = event.get('evidence') or {}
    if not isinstance(evidence, dict):
        evidence = {}
    def numeric(value):
        return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else 0
    return (source_seconds(event.get('timestamp')), str(evidence.get('fileIdentity', evidence.get('path', ''))),
            numeric(evidence.get('epoch')), numeric(evidence.get('byteStart')), event['id'])


def make_packet(events, device, owner, session, source):
    """Build a bounded snapshot from source-scoped events, preserving candidates.

    Callers must fetch unlabelled raw events with an account/device-scoped query.
    ``SessionStore.events_for`` binds both fields and is the recommended entry.
    Explicit foreign account/device/source/session events are always excluded.
    """
    if source not in ('codex', 'workbuddy') or any(not isinstance(v, str) or not 0 < len(v) <= 200 for v in (device, owner, session)):
        raise ValueError('Account, device, source and session required')
    unique = {}
    excluded = 0
    for event in events:
        if not isinstance(event, dict):
            raise ValueError('Invalid source event')
        if (event.get('source') != source or event.get('sessionId') != session or
            event.get('deviceId', event.get('_device', event.get('device', device))) != device or
            event.get('owner', event.get('_owner', owner)) != owner):
            excluded += 1
            continue
        identity = event.get('id')
        if not isinstance(identity, str) or not identity or not isinstance(event.get('kind'), str):
            raise ValueError('Event identity and kind required')
        if identity in unique and unique[identity] != event:
            raise ValueError('Conflicting duplicate event')
        unique[identity] = event
    ordered = sorted(unique.values(), key=_order)
    total = len(ordered)
    revision = revision_from_digest(owner, device, session, source, total, sum(event_digest(e['id']) for e in ordered))
    users = []; controls = {}; rounds = []; current = None
    for event in ordered:
        if _user(event):
            text, control = user_text(event)
            if control:
                controls[event['id']] = control
            else:
                users.append((event, text))
                current = event['id']
                rounds.append({'turnId': current, 'user': text, 'eventIds': []})
        if current is not None:
            rounds[-1]['eventIds'].append(event['id'])
    by_id = {e['id']: e for e in ordered}
    turn_owner = {identity: r['turnId'] for r in rounds for identity in r['eventIds']}
    calls = defaultdict(list); returns = defaultdict(list)
    for event in ordered:
        key = _call_id(event)
        if key and event['kind'] in CALL_KINDS:
            calls[key].append(event)
        if key and event['kind'] == 'tool_result':
            returns[key].append(event)
    pairs = []; pair_order_conflicts = 0
    for key in calls.keys() | returns.keys():
        c, r = calls[key], returns[key]
        if len(c) == len(r) == 1:
            pairs.append((c[0], r[0]))
            if _order(c[0]) >= _order(r[0]):
                pair_order_conflicts += 1
    pairs.sort(key=lambda pair: _order(pair[1]), reverse=True)
    paired_ids = {e['id'] for pair in pairs for e in pair}
    # Preserve original candidates plus the recent discussions. The first
    # session request is explicitly an earlier candidate, not a task parent.
    chosen_rounds = rounds[-12:]
    if rounds and rounds[0] not in chosen_rounds:
        chosen_rounds = [rounds[0], *chosen_rounds]
    selected = {}; groups = []
    def group(values):
        group_ids = [e['id'] for e in values if e is not None and e['id'] not in selected]
        if not group_ids or len(selected) + len(group_ids) > MAX_FRAGMENTS:
            return
        for identity in group_ids:
            selected[identity] = by_id[identity]
        groups.append(group_ids)
    replies = [e for e in ordered if _assistant(e)]
    stops = [e for e in ordered if e['kind'] in ('turn_completed', 'turn_aborted')]
    group([users[-1][0]] if users else [])
    group(replies[-1:]); group(stops[-1:])
    for candidate in chosen_rounds:
        group([by_id[candidate['turnId']]])
    for candidate in reversed(chosen_rounds):
        entries = [by_id[ident] for ident in candidate['eventIds']]
        group([e for e in entries if _assistant(e)][-1:])
        group([e for e in entries if e['kind'] == 'reasoning'][-1:])
    # Whole matched pairs are atomic selection units, even when a result was
    # uploaded before its call or the original call is outside recent rounds.
    for pair in pairs:
        group(pair)
    for event in reversed(ordered):
        if event['id'] in paired_ids:
            continue
        if event['kind'] in RELEVANT:
            group([event])
    fragments = []
    for event in sorted(selected.values(), key=_order):
        text = redact(_text(event))
        seconds = source_seconds(event.get('timestamp'))
        evidence = event.get('evidence') or {}
        evidence = {k: evidence.get(k) for k in ('path', 'fileIdentity', 'epoch', 'byteStart', 'byteEnd', 'sha256') if k in evidence} if isinstance(evidence, dict) else {}
        fragment = {'evidenceId': 'E'+str(len(fragments)+1).zfill(3), 'eventId': event['id'],
                    'kind': event['kind'], 'role': _role(event), 'callId': _call_id(event),
                    'tool': event.get('name') or _item(event).get('name'), 'text': text,
                    'timestamp': datetime.fromtimestamp(seconds, timezone.utc).isoformat() if seconds else None,
                    'source': evidence, 'turnId': turn_owner.get(event['id']),
                    'control': controls.get(event['id']), 'truncated': False,
                    'originalTextBytes': len(text.encode())}
        fragments.append(fragment)
    references = {f['eventId']: f['evidenceId'] for f in fragments}
    candidates = [{'turnId': r['turnId'], 'user': _cut(redact(r['user']), 180)[0],
                   'evidenceRef': references.get(r['turnId']), 'association': 'separate_candidate_round',
                   'position': 'earlier_session_candidate' if r is rounds[0] and len(rounds) > 12 else 'recent_candidate'} for r in chosen_rounds]
    selected_pairs = [{'callId': _call_id(call), 'callRef': references[call['id']], 'resultRef': references[result['id']],
                       'basis': 'recorded_call_id'} for call, result in pairs if call['id'] in references and result['id'] in references]
    terminal = next((e for e in reversed(ordered) if e['kind'] in RELEVANT and e['id'] not in controls), None)
    closure = 'source_turn_end' if terminal and terminal['kind'] == 'turn_completed' else 'source_turn_aborted' if terminal and terminal['kind'] == 'turn_aborted' else 'assistant_message_endpoint' if terminal and _assistant(terminal) else 'end_marker_not_recorded'
    eligible = bool(users)
    limitations = ['同一会话可以包含多个任务；候选用户轮次尚未判定为同一需求。',
                   '工具调用与返回只按唯一调用编号关联；日志声明不是独立运行结果核验。']
    if source == 'workbuddy' or closure not in ('source_turn_end', 'source_turn_aborted'):
        limitations.append('此来源未提供可靠任务结束标记；回复终点或静默窗口不证明任务完整结束。')
    if not all(source_seconds(e.get('timestamp')) for e in ordered):
        limitations.append('部分源时间缺失，相关记录按源文件位置作稳定排序，不能证明完整时间顺序。')
    if pair_order_conflicts:
        limitations.append('部分调用与返回的源时间顺序不一致；调用编号支持配对，但不支持推断准确先后时间。')
    if any(f['kind'] == 'reasoning' and not f['text'].strip() for f in fragments):
        limitations.append('部分 reasoning 记录没有可读正文；不能补写或还原未记录的思路。')
    if excluded:
        limitations.append('输入中不同账号、设备、来源或会话的记录已排除。')
    if not eligible:
        limitations.append('本包没有真实用户需求；授权与环境消息不作为业务任务自动分析。')
    packet = {'deviceId': device, 'owner': owner, 'sessionId': session, 'source': source,
              'revision': revision, 'totalEvents': total, 'includedEvents': len(fragments),
              'fragments': fragments, 'candidateTurns': candidates, 'toolPairs': selected_pairs,
              'eligible': eligible, 'onlyApproval': not eligible and 'approval' in controls.values(),
              'titleHint': _cut(' '.join(redact(users[-1][1]).split()), 240)[0] if users else '仅授权与环境记录' if controls else '未记录用户需求',
              'coverage': {'mode': 'bounded_candidate_rounds', 'complete': False, 'closure': closure,
                           'silenceWindowSeconds': SILENCE_SECONDS, 'taskAssociation': 'not_determined',
                           'candidateRounds': len(rounds), 'includedRounds': len(candidates),
                           'omittedEvents': total-len(fragments), 'excludedForeignEvents': excluded,
                           'matchedToolPairs': len(pairs), 'includedToolPairs': len(selected_pairs),
                           'pairOrderConflicts': pair_order_conflicts,
                           'latestSourceTime': max((source_seconds(e.get('timestamp')) for e in ordered), default=0.0),
                           'unmatchedToolEvents': sum(e['kind'] in CALL_KINDS | {'tool_result'} and e['id'] not in paired_ids for e in ordered),
                           'controlEvents': len(controls)}, 'limitations': limitations}
    # Keep all selected atomic pairs under a UTF-8 packet budget. Never slice
    # encoded JSON, lose a paired event, or imply a clipped payload is complete.
    budget = 2600
    texts = {f['eventId']: f['text'] for f in fragments}
    def size():
        return len(json.dumps(packet, ensure_ascii=False).encode())
    while True:
        for fragment in fragments:
            fragment['text'], fragment['truncated'] = _cut(texts[fragment['eventId']], budget)
        # Leave room for the final coverage counters and truncation limitations.
        if size() <= MAX_PACKET_BYTES - 1000 or budget <= 80:
            break
        budget = max(80, int(budget * .7))
    # Unusually long evidence metadata can exceed the bound with even minimal
    # text. Drop lowest-priority groups atomically; never keep half a known pair.
    while size() > MAX_PACKET_BYTES - 1000 and groups:
        removed = set(groups.pop())
        paired_removed = {e['id'] for pair in pairs if any(e['id'] in removed for e in pair) for e in pair}
        removed |= paired_removed
        fragments[:] = [f for f in fragments if f['eventId'] not in removed]
        live_refs = {f['evidenceId'] for f in fragments}
        packet['candidateTurns'] = [r for r in candidates if r['evidenceRef'] in live_refs]
        packet['toolPairs'] = [p for p in selected_pairs if p['callRef'] in live_refs and p['resultRef'] in live_refs]
    packet['includedEvents'] = len(fragments)
    packet['coverage'].update(includedRounds=len(packet['candidateTurns']), omittedEvents=total-len(fragments),
                              includedToolPairs=len(packet['toolPairs']), truncatedFragments=sum(f['truncated'] for f in fragments))
    if total > len(fragments):
        limitations.append('部分历史事件未包含；不能据本包推断全会话执行或安全情况。')
    if packet['coverage']['truncatedFragments']:
        limitations.append('部分正文按字节截取；完整原文保留在平台采集数据库。')
    if size() > MAX_PACKET_BYTES:
        raise ValueError('Scoped packet metadata exceeds byte limit')
    return packet
