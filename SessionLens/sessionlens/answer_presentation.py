"""Read-only answer projection from query results and their recorded evidence.

This module does no retrieval or model work.  The original result is retained
under ``raw`` so readable summaries never replace the source records.
"""
import json
import re
from datetime import datetime


FIELD_LABELS = {
    'status': '返回状态', 'error': '错误', 'message': '消息',
    'exit_code': '退出码', 'exitCode': '退出码', 'stdout': '标准输出',
    'stderr': '错误输出', 'output': '输出', 'text': '内容',
    'files': '文件', 'artifacts': '产物', 'results': '结果',
    'path': '路径', 'localPath': '本地路径', 'filePath': '文件路径',
    'url': '网址', 'downloadUrl': '下载地址', 'count': '数量',
}
BASIS_LABELS = {
    'recorded': '记录支持', 'inferred': '分析推断', 'unknown': '尚未确认',
    'source_parent_path': '原始消息链关联',
    'sequence_candidate': '按记录顺序候选关联，待核对',
}


def _text(value):
    if value is None:
        return ''
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return '是' if value else '否'
    return json.dumps(value, ensure_ascii=False)


def _short(value, limit=220):
    value = re.sub(r'\s+', ' ', _text(value)).strip()
    return value if len(value) <= limit else value[:limit - 1] + '…'


def _date(value):
    text=_text(value)
    try:return datetime.fromisoformat(text.replace('Z','+00:00')).astimezone().strftime('%Y-%m-%d %H:%M')
    except (ValueError,OverflowError,OSError):return text


def _dict(value):
    return value if isinstance(value, dict) else {}


def _rows(value):
    return [x for x in value if isinstance(x, dict)] if isinstance(value, list) else []


def _unique(values):
    return list(dict.fromkeys(x for x in values if isinstance(x, str) and x))


def _basis(block):
    return block.get('basis', 'unknown')


def _statement(block):
    text = _text(block.get('text'))
    basis = _basis(block)
    if not text:
        return ''
    if basis == 'inferred':
        return '分析推断：' + text
    if basis == 'unknown':
        return '尚未确认：' + text
    return text


def _tasks(project):
    tasks = _rows(project.get('tasks')) or _rows(project.get('latestTasks'))
    if not tasks and isinstance(project.get('latestTask'), dict):
        tasks = [project['latestTask']]
    return sorted(tasks, key=lambda t: _text(t.get('updated')), reverse=True)


def _project_row(project):
    tasks = _tasks(project)
    roots = [x for x in project.get('roots', []) if isinstance(x, str)]
    components = [x.get('name') for x in _rows(project.get('components')) if x.get('name')]
    titles = _unique(_short(x.get('prompt') or x.get('title'), 100) for x in tasks[:1])
    description = ('最近需求：' + titles[0]) if titles else (
        '记录目录：' + '、'.join(roots[:2]) if roots else '现有记录未附任务内容或目录')
    if components:
        description += '。路径组件：' + '、'.join(components[:4])
    latest = '最近任务时间：' + (_date(tasks[0].get('updated')) or '未记录') if tasks else (
        '最近记录：' + _date(project.get('lastUpdated')) + '；未附最近任务内容'
        if project.get('lastUpdated') else '未记录最近任务内容')
    meta = [_text(project.get('source'))]
    if project.get('taskCount') is not None:
        meta.append(str(project['taskCount']) + ' 项已关联任务')
    if project.get('fileCount') is not None:
        meta.append(str(project['fileCount']) + ' 个记录路径')
    if project.get('lastUpdated'):
        meta.append(_date(project['lastUpdated']))
    state = {'candidate': '归属待确认', 'confirmed': '用户已确认',
             'identified': '已识别', 'excluded': '已排除'}.get(project.get('state'))
    if state:
        meta.append(state)
    return {'id': project.get('id'), 'projectId': project.get('id'),
            'name': _text(project.get('name')), 'description': description,
            'latest': latest, 'meta': [x for x in meta if x]}


def _count_meta(stats):
    labels = [('userTurns', '轮用户发言'), ('approvalTurns', '轮方案确认'),
              ('executionTurns', '轮开始执行'), ('toolCalls', '次工具调用'),
              ('agentReplyRecords', '条 Agent 回复记录')]
    values = [str(stats[k]) + ' ' + label for k, label in labels if stats.get(k) is not None]
    if stats:
        values.append('模型 API 调用次数：' + (
            str(stats['modelCalls']) if stats.get('modelCalls') is not None else '无法确认'))
    return values


def _parse(text):
    if isinstance(text, (dict, list)):
        return text
    if not isinstance(text, str):
        return text
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return text


def _return_summary(value, depth=0):
    """Summarize actual return fields without treating a status as verification."""
    value = _parse(value)
    if isinstance(value, dict):
        lines = []
        for key, item in list(value.items())[:14]:
            if key == 'content' and isinstance(item, list):
                parts = [_return_summary(x.get('text'), depth + 1)
                         for x in _rows(item) if x.get('type') == 'text' and 'text' in x]
                if parts:
                    lines.append('\n'.join(parts))
                    continue
            label = FIELD_LABELS.get(key, key)
            detail = _return_summary(item, depth + 1) if depth < 2 else _short(item, 180)
            lines.append(label + '：' + detail)
        if len(value) > 14:
            lines.append('还有 ' + str(len(value) - 14) + ' 个返回字段，可查看原文')
        return '\n'.join(lines) or '工具返回空对象'
    if isinstance(value, list):
        shown = [_return_summary(x, depth + 1) if depth < 2 else _short(x, 180) for x in value[:5]]
        text = '\n'.join(shown) or '空列表'
        return text + ('\n共 ' + str(len(value)) + ' 项，完整返回见原文' if len(value) > 5 else '')
    return _short(value, 650) if value is not None else 'null'


def _artifacts(value, refs):
    """Only explicit paths/URLs in returns qualify; do not inspect the filesystem."""
    found = []

    def walk(item, container=''):
        item = _parse(item)
        if isinstance(item, dict):
            for key, child in item.items():
                if key in ('path', 'localPath', 'filePath', 'file_path', 'url', 'downloadUrl') and isinstance(child, str):
                    found.append({'path': child, 'description': '工具返回声明的' + FIELD_LABELS.get(key, '路径'),
                                  'refs': list(refs), 'type': 'url' if key in ('url', 'downloadUrl') else 'file',
                                  'source': 'tool_return'})
                elif key == 'text' and isinstance(child, str) and child.lstrip().startswith(('{', '[')):
                    parsed = _parse(child)
                    if isinstance(parsed, (dict, list)):
                        walk(parsed, container)
                elif isinstance(child, (dict, list)):
                    walk(child, key)
        elif isinstance(item, list):
            for child in item:
                if isinstance(child, str) and container in ('files', 'artifacts'):
                    found.append({'path': child, 'description': '工具返回声明的文件', 'refs': list(refs),
                                  'type': 'url' if child.startswith(('http://', 'https://')) else 'file', 'source': 'tool_return'})
                else:
                    walk(child, container)

    walk(value)
    return found


def _write_paths(presentation, refs):
    """A write request proves the named input path, never successful mutation."""
    paths = {}
    for call in _rows(presentation.get('calls')):
        name = _text(call.get('name'))
        args = _dict(call.get('arguments'))
        tool = _text(args.get('toolName')) or name.rsplit(' → ', 1)[-1]
        normalized = re.sub(r'[^a-z]', '', tool.rsplit('__', 1)[-1].lower())
        if normalized not in ('write', 'edit', 'writefile', 'editfile', 'multiedit'):
            continue
        params = _parse(args.get('params', args))
        if not isinstance(params, dict):
            continue
        path = params.get('file_path') or params.get('path')
        if not isinstance(path, str) or not path:
            continue
        ref = refs.get(call.get('id'), call.get('id'))
        row = paths.setdefault(path, {'path': path,
                                     'description': '写入/修改请求涉及的路径；完成需返回核验', 'refs': []})
        row['refs'] = _unique(row['refs'] + [ref])
    return list(paths.values())


def _answer_heading(overview, kind):
    text = _statement(overview).strip()
    if not text:
        return ('原因依据待确认' if kind == 'task_reason' else '已记录的执行过程'), ''
    match = re.search(r'[。！？\n]|[.!?](?=\s)', text)
    end = match.end() if match else len(text)
    first, rest = text[:end].strip(), text[end:].strip()
    if len(first) > 70:
        return _short(first, 70), text
    return first, rest


def _evidence(presentation, packet):
    fragments = _rows(packet.get('fragments'))
    refs = {x['eventId']: x['evidenceId'] for x in fragments if x.get('eventId') and x.get('evidenceId')}
    records = {x.get('eventId'): x for x in fragments if x.get('eventId')}
    for row in _rows(presentation.get('reasoning')) + _rows(presentation.get('replies')) + _rows(presentation.get('context')):
        if row.get('id'):
            records[row['id']] = {**records.get(row['id'], {}), **row}
    graph = _dict(presentation.get('messageGraph'))
    nodes = {x['eventId']: x for x in _rows(graph.get('nodes')) if x.get('eventId')}
    edges = _rows(graph.get('edges'))[:]
    event_for_ref = {v: k for k, v in refs.items()}
    for row in _rows(packet.get('messageRelations')):
        origin, target = event_for_ref.get(row.get('fromRef')), event_for_ref.get(row.get('toRef'))
        if origin and target:
            edge = {'from': origin, 'to': target, 'relation': row.get('relation'), 'basis': row.get('basis')}
            if edge not in edges:
                edges.append(edge)
    return refs, records, nodes, edges


def _source_path(edges, start, end, nodes, records):
    """Return an explicitly recorded parent path, stopping at a new user turn."""
    if not start or not end:
        return []
    frontier = [(end, [])]
    visited = {end}
    for _ in range(64):
        later = []
        for current, path in frontier:
            node = _dict(nodes.get(current)) or _dict(records.get(current))
            if node.get('kind') in ('user_message', '用户提问') or node.get('role') == 'user':
                continue
            for edge in edges:
                if edge.get('relation') != 'source_parent' or edge.get('basis') != 'recorded' or edge.get('to') != current:
                    continue
                parent = edge.get('from')
                joined = [edge] + path
                if parent == start:
                    return joined
                if parent and parent not in visited:
                    visited.add(parent)
                    later.append((parent, joined))
        frontier = later
        if not frontier:
            break
    return []


def _steps(presentation, packet):
    refs, records, nodes, edges = _evidence(presentation, packet)
    calls = _rows(presentation.get('calls'))
    steps, artifacts = [], []
    by_ref = {x.get('evidenceId'): x for x in _rows(packet.get('fragments'))}
    for index, call in enumerate(calls):
        event = call.get('id')
        call_ref = refs.get(event, event)
        returned = _rows(call.get('returns'))
        return_ids = _unique(refs.get(x.get('id'), x.get('id')) for x in returned)
        return_events = {x.get('id') for x in returned if x.get('id')}
        relations = [x for x in edges if x.get('relation') == 'call_result'
                     and x.get('from') == event and x.get('to') in return_events]
        # Existing task_presentation returns are already selected via call_result.
        # If a partial caller did not pass its graph, do not manufacture a graph edge.
        link = _dict(call.get('decisionLink'))
        reason_event = link.get('reasoningEvent')
        reason = _dict(records.get(reason_event))
        basis = link.get('basis', 'unknown')
        if not reason:
            packet_link = next((x for x in _rows(packet.get('reasoningLinks')) if x.get('callRef') == call_ref), {})
            reason = _dict(by_ref.get(packet_link.get('reasoningRef')))
            if reason:
                reason_event = reason.get('eventId')
                basis = packet_link.get('basis', 'unknown')
        reasoning_relations = _source_path(edges, reason_event, event, nodes, records) if reason and basis == 'source_parent_path' else []
        fields = [{'label': _text(x.get('label') or x.get('key')),
                   'value': _text(x.get('value'))} for x in _rows(call.get('fields'))]
        if not fields:
            args = _dict(call.get('arguments'))
            params = args.get('params', args)
            if isinstance(params, str):
                params = _parse(params)
            if isinstance(params, dict):
                fields = [{'label': FIELD_LABELS.get(k, k), 'value': _text(v)} for k, v in params.items()]
            elif params:
                fields = [{'label': '输入', 'value': _text(params)}]
        next_refs, next_relations = [], []
        frontier = list(return_events)
        visited = set(frontier)
        following = None
        for _ in range(64):
            if not frontier or following is not None:
                break
            later = []
            for current in frontier:
                for edge in edges:
                    if edge.get('relation') != 'source_parent' or edge.get('basis') != 'recorded' or edge.get('from') != current:
                        continue
                    target = edge.get('to')
                    if target in visited:
                        continue
                    visited.add(target)
                    node = _dict(nodes.get(target)) or _dict(records.get(target))
                    kind = node.get('kind')
                    if kind in ('user_message', '用户提问') or node.get('role') == 'user':
                        continue
                    next_relations.append(edge)
                    if kind in ('reasoning', '解题思路', 'assistant_message', 'Agent 回复') or (kind == 'message' and node.get('role') == 'assistant'):
                        following = records.get(target, {})
                        next_refs = [refs.get(target, target)]
                        break
                    later.append(target)
            frontier = later
        if following is not None:
            next_event = next((identity for identity, ref in refs.items() if ref in next_refs), next_refs[0])
            next_relations = []
            for origin in sorted(return_events):
                next_relations = _source_path(edges, origin, next_event, nodes, records)
                if next_relations:
                    break
            next_text = '原始消息链关联后续记录：' + (_short(following.get('text'), 170) or '该记录未附文本')
        elif index + 1 < len(calls):
            next_text = '后续记录中还有工具调用：' + _text(calls[index + 1].get('name')) + '。与本次返回的后续消息关系未确认。'
        else:
            next_text = '当前片段未确认工具返回之后的模型消息关系。'
        text = '\n\n'.join(_return_summary(x.get('text')) for x in returned) if returned else '未记录关联返回，无法确认执行结果。'
        if any(x.get('truncated') for x in returned):
            text += '\n返回摘录不完整，完整记录见原文。'
        step_refs = _unique([call_ref] + return_ids + [refs.get(reason.get('id'), reason.get('evidenceId'))])
        flow = [{'from': 'agent', 'to': 'tool', 'label': '记录的工具调用',
                 'basis': 'recorded_invocation', 'refs': [call_ref] if call_ref else []}]
        if reasoning_relations:
            flow.insert(0, {'from': 'model', 'to': 'agent', 'label': '日志思路与工具调用关联',
                            'basis': 'source_parent_path',
                            'refs': _unique([refs.get(reason_event, reason_event), call_ref])})
        if relations:
            flow.append({'from': 'tool', 'to': 'agent', 'label': '日志中关联的工具返回',
                         'basis': 'call_result', 'refs': return_ids})
        if next_refs and next_relations:
            flow.append({'from': 'agent', 'to': 'model', 'label': '源消息链关联后续模型记录',
                         'basis': 'source_parent', 'refs': _unique(return_ids + next_refs)})
        model_recorded = bool(reasoning_relations or (next_refs and next_relations))
        route = '工具调用 → 按 callId 关联的返回' if relations else (
            '工具调用 → 返回关系未附图证据' if returned else '工具调用 → 返回未记录')
        if reasoning_relations:
            route = '已记录模型思路 → ' + route
        if next_refs and next_relations:
            route += ' → 后续模型记录'
        if model_recorded:
            route += '（日志关联，不能证明实际网络 API 请求次数）'
        source = _text(presentation.get('source') or packet.get('source'))
        agent_label = {'workbuddy': 'WorkBuddy', 'codex': 'Codex'}.get(source.lower(), source) or 'Agent'
        steps.append({'title': _text(call.get('name')) or '未命名工具',
                      'call': _text(call.get('name')) or '未命名工具',
                      'eventId': event, 'callId': call.get('callId'),
                      'reasoning': _text(reason.get('text')),
                      'reasoningBasis': BASIS_LABELS.get(basis, basis) if reason else '未记录关联思路',
                      'reasoningAssociation': basis if reason else 'unknown',
                      'route': route,
                      'inputFields': fields, 'returnText': text, 'returnIds': return_ids,
                      'refs': step_refs, 'nextText': next_text, 'nextRefs': next_refs,
                      'returnRelations': relations, 'nextRelations': next_relations,
                      'reasoningRelations': reasoning_relations,
                      'flowEdges': flow, 'agentLabel': agent_label,
                      'modelLabel': '已记录模型消息' if model_recorded else '模型身份未知',
                      'model': '已记录模型消息' if model_recorded else '模型身份未知'})
        for row in returned:
            artifacts.extend(_artifacts(row.get('text'), [refs.get(row.get('id'), row.get('id'))] if row.get('id') else []))
    unique_artifacts = {}
    for item in artifacts:
        key = item['path']
        if key in unique_artifacts:
            unique_artifacts[key]['refs'] = _unique(unique_artifacts[key]['refs'] + item['refs'])
        else:
            unique_artifacts[key] = item
    return steps, list(unique_artifacts.values()), refs


def present(result=None):
    """Return an answer-first view model without mutating or fetching anything."""
    result = result if isinstance(result, dict) else {}
    view = {'kind': 'home', 'title': '回顾已采集的 Agent 记录',
            'summary': '可以查询项目、回顾一项任务，或核对当时的工具输入与返回。',
            'meta': [], 'status': {'text': '等待查询', 'tone': 'neutral'}, 'notice': '',
            'projects': [], 'requirements': [], 'decisions': [], 'steps': [],
            'files': [], 'followups': [], 'artifacts': [], 'taskId': result.get('taskId'),
            'projectId': None, 'raw': result}
    if result.get('error'):
        view.update(kind='error', title='这次查询未完成', summary=_text(result['error']),
                    status={'text': '查询失败', 'tone': 'error'})
        return view
    if result.get('selectionNeeded') or 'projectChoices' in result:
        is_project = 'projectChoices' in result
        rows = _rows(result.get('projectChoices') if is_project else result.get('options'))
        view.update(kind='choices', title='选择要回顾的项目' if is_project else '选择要回顾的任务',
                    summary=_text(result.get('projectMessage') or result.get('selectionMessage')) or '现有记录对应多个候选，请选择具体记录。',
                    status={'text': '需要选择' if rows else '需要补充信息', 'tone': 'warning'})
        for row in rows:
            ident = row.get('id') if is_project else row.get('taskId')
            view['projects'].append({'id': ident, 'projectId' if is_project else 'taskId': ident,
                                     'name': _text(row.get('name') if is_project else row.get('title')),
                                     'description': '、'.join(row.get('roots', [])) if is_project else '',
                                     'latest': _date(row.get('updated')), 'meta': [_text(row.get('source'))]})
        return view
    if isinstance(result.get('projectInventory'), dict):
        inventory = result['projectInventory']
        projects = [x for x in _rows(inventory.get('projects')) if x.get('state') != 'excluded']
        counts = _dict(inventory.get('counts'))
        confirmed = counts.get('confirmed', sum(x.get('state') == 'confirmed' for x in projects))
        identified = counts.get('identified', sum(x.get('state') == 'identified' for x in projects))
        candidate = counts.get('candidate', sum(x.get('state') == 'candidate' for x in projects))
        view.update(kind='projects', title='已采集项目',
                    summary=f'现有记录包含 {identified} 个已识别项目、{confirmed} 个用户确认项目，以及 {candidate} 个待确认目录。',
                    projects=[_project_row(x) for x in projects], notice=_text(inventory.get('coverage')),
                    status={'text': '索引已完成' if inventory.get('complete') else '索引仍在准备',
                            'tone': 'neutral' if inventory.get('complete') else 'warning'})
        view['meta'] = [_text(inventory.get('source'))] if inventory.get('source') else []
        return view
    if isinstance(result.get('projectDetails'), dict):
        project = result['projectDetails']
        row = _project_row(project)
        view.update(kind='project', title=_text(project.get('name')) or '项目记录',
                    summary=row['description'], meta=row['meta'] + _count_meta(_dict(project.get('interactions'))),
                    projectId=project.get('id'), notice=_text(project.get('coverage')),
                    status={'text': '已关联记录', 'tone': 'neutral'})
        for task in _tasks(project):
            view['projects'].append({'id': task.get('taskId'), 'taskId': task.get('taskId'),
                                     'name': _text(task.get('prompt') or task.get('title')),
                                     'description': '已关联的用户需求', 'latest': _date(task.get('updated')),
                                     'meta': [_text(task.get('state'))] + _count_meta(_dict(task.get('interactions')))})
        evidence = _rows(project.get('evidence'))
        for path in project.get('files', []):
            if isinstance(path, str):
                view['files'].append({'path': path, 'description': '记录中的源码路径',
                                      'refs': _unique(x.get('eventId') for x in evidence if x.get('path') == path)})
        view['followups'] = ['回顾某项任务的执行过程', '核对用户发言和模型调用次数']
        return view
    presentation = _dict(result.get('presentation'))
    packet = _dict(result.get('packet'))
    understanding = _dict(result.get('understanding'))
    if not presentation and not packet and not understanding:
        return view
    question = _text(result.get('question'))
    facets = result.get('selection', {}).get('facets', packet.get('questionFacets', []))
    stats = _dict(result.get('interactions')) or _dict(presentation.get('interactions')) or _dict(packet.get('interactions'))
    is_counts = result.get('queryKind') == 'task_interactions' or bool(re.search(r'几轮|多少轮|交互|对话次数|模型.*(?:多少|几次|调用)|确认几次', question))
    is_reason = 'reasoning' in facets or bool(re.search(r'为什么|思路|考虑|理由|原因|reasoning|\bwhy\b', question, re.I))
    overview = _dict(understanding.get('overview'))
    kind = 'task_counts' if is_counts else 'task_reason' if is_reason else 'task_process'
    title, summary = _answer_heading(overview, kind)
    view.update(kind=kind, title=title,
                summary=summary,
                meta=[x for x in (_text(presentation.get('source') or packet.get('source')),
                                  _date(presentation.get('updated'))) if x] + (
                    [str(stats['toolCalls']) + ' 次工具调用'] if stats.get('toolCalls') is not None else []),
                status={'text': BASIS_LABELS.get(_basis(overview), '已关联记录'),
                        'tone': 'warning' if _basis(overview) in ('inferred', 'unknown') else 'neutral'})
    context = _dict(presentation.get('projectContext')) or _dict(packet.get('projectContext'))
    project_rows = _rows(context.get('projects'))
    if len(project_rows) == 1:
        view['projectId'] = project_rows[0].get('id')
    steps, artifacts, refs = _steps(presentation, packet)
    view.update(steps=steps, artifacts=artifacts)
    fragments = {x.get('evidenceId'): x for x in _rows(packet.get('fragments'))}
    requirements = _rows(presentation.get('requirements')) or _rows(packet.get('requirementHistory'))
    for requirement in requirements:
        ref = requirement.get('evidenceRef') or refs.get(requirement.get('eventId'))
        fragment = _dict(fragments.get(ref))
        view['requirements'].append({'text': _text(requirement.get('text') or fragment.get('text')),
                                     'label': _text(requirement.get('label') or requirement.get('kind')),
                                     'reason': _text(requirement.get('reason')),
                                     'time': _text(requirement.get('timestamp') or fragment.get('timestamp')),
                                     'refs': _unique([ref or requirement.get('eventId')]),
                                     'association': requirement.get('association', 'unknown')})
    if not requirements and (presentation.get('prompt') or packet.get('prompt')):
        view['requirements'] = [{'text': _text(presentation.get('prompt') or packet.get('prompt')),
                                 'label': '任务要求', 'reason': '任务记录中的原始要求', 'time': '', 'refs': []}]
    for block in _rows(understanding.get('steps')):
        basis = _basis(block)
        view['decisions'].append({'label': BASIS_LABELS.get(basis, basis), 'title': _text(block.get('title')),
                                  'text': _text(block.get('text')), 'refs': _unique(block.get('evidenceRefs', [])), 'basis': basis})
    if is_reason:
        for step in steps:
            if step['reasoning']:
                view['decisions'].append({'label': step['reasoningBasis'], 'title': '记录的思路 · ' + step['call'],
                                          'text': step['reasoning'], 'refs': step['refs'],
                                          'basis': step['reasoningAssociation']})
    notices = [_text(x) for x in understanding.get('gaps', []) if isinstance(x, str)]
    if any(step['modelLabel'] == '已记录模型消息' for step in steps):
        notices.append('模型节点及连线仅表示日志消息关联，不能证明实际网络 API 请求次数。')
    if stats.get('modelCalls') is None and stats.get('modelCallsReason'):
        notices.append(_text(stats['modelCallsReason']))
    if presentation.get('total') is not None and presentation.get('included') != presentation.get('total'):
        notices.append(f'过程展示选取了 {presentation.get("included", 0)} / {presentation["total"]} 条记录。')
    view['notice'] = '\n'.join(_unique(notices))
    view['followups'] = _unique(understanding.get('followups', []))
    view['counts'] = stats
    view['files'] = _write_paths(presentation, refs)
    for item in artifacts:
        if item['type'] == 'file':
            view['files'].append({'path': item['path'], 'description': item['description'], 'refs': item['refs']})
    if is_counts:
        turns = [str(stats[k]) + ' ' + label for k, label in
                 [('approvalTurns', '轮方案确认'), ('executionTurns', '轮开始执行')]
                 if stats.get(k) is not None]
        view['summary'] = '其中 ' + '、'.join(turns) + '，均计入用户发言。' if turns else '当前证据未提供用户发言的细分统计。'
        view['title'] = (str(stats['userTurns']) + ' 轮用户发言，' if stats.get('userTurns') is not None else '') + (
            '模型 API 调用次数无法确认' if stats.get('modelCalls') is None else str(stats['modelCalls']) + ' 次已记录模型 API 调用')
        view['status'] = {'text': '模型次数无法确认' if stats.get('modelCalls') is None else '记录统计',
                          'tone': 'warning' if stats.get('modelCalls') is None else 'neutral'}
    return view
