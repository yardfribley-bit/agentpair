"""Evidence edges, independent of animation and tolerant of missing native IDs."""
from collections import defaultdict
from .store import digest


def graph(store):
    rows, after = [], 0
    while True:
        batch = store.events(after, limit=1000)
        if not batch:
            break
        rows.extend(batch)
        after = batch[-1]['seq']
    edges, missing = [], []
    calls, processes, executions = defaultdict(list), defaultdict(list), {}
    results = defaultdict(list)
    execution_tasks = {}
    for row in rows:
        if row['kind'] == 'tool.call' and row.get('tool_call_id'):
            calls[(row['source'], row['tool_call_id'])].append(row)
        if row['kind'] in ('process.snapshot', 'process.start') and row.get('process_instance_id'):
            processes[row['process_instance_id']].append(row)
        if row['kind'] == 'tool.result' and row.get('content_ref'):
            results[(row['source'], row.get('tool_call_id'), row['content_ref']['sha256'])].append(row)
        if row['kind'] == 'tool.execution':
            executions[(row.get('trace_id'), row.get('span_id'))] = row
            execution_tasks[(row.get('task_id'),row.get('span_id'))] = row
    def edge(left, right, relation, basis, status='confirmed'):
        edges.append({'from':left['event_id'], 'to':right['event_id'],
                      'relation':relation, 'basis':basis, 'status':status})
    for row in rows:
        kind = row['kind']
        if kind == 'tool.result':
            matches = calls.get((row['source'], row.get('tool_call_id')), [])
            if len(matches) == 1:
                edge(matches[0], row, 'tool_return', 'same source and native tool_call_id')
            else:
                missing.append({'event_id':row['event_id'], 'relation':'tool_return',
                                'reason':'native call missing or ambiguous'})
        if kind == 'model.request':
            # Saved manifest proves exact membership and order. Context results
            # must not be represented only in the first HTTP request that saw them.
            body = store.content(row['content_ref']['sha256'])
            for position, message in enumerate(body.get('messages', [])):
                if message.get('role') == 'tool':
                    matches = results.get((row['source'],message.get('tool_call_id'),digest(message.get('content'))), [])
                    if len(matches)==1:
                        edge(matches[0], row, 'result_in_model_input',
                             'native tool_call_id and exact saved message content; position '+str(position))
        if kind == 'network.connection':
            matches = processes.get(row.get('process_instance_id'), [])
            # Polling identity is sampled rather than recorded by the socket source.
            if matches:
                closest = min(matches,key=lambda p:abs(p['observed_at']-row['observed_at']))
                edge(closest,row,'process_socket','PID joined with nearby process snapshot', 'partial')
            missing.append({'event_id':row['event_id'], 'relation':'tool_network',
                            'reason':'tool execution to socket native identity missing'})
        if kind == 'file.snapshot':
            parent = executions.get((row.get('trace_id'),row.get('span_id')))
            if parent is None:
                # Older adapter snapshots carry span but task encodes trace.
                parent = execution_tasks.get((row.get('task_id'),row.get('span_id')))
            if parent:
                edge(parent,row,'referenced_file_snapshot','tool referenced path, sampled after execution','partial')
        if kind == 'tool.execution':
            missing.append({'event_id':row['event_id'], 'relation':'tool_process',
                            'reason':'execution span has no observed process-launch identity'})
    return {'nodes':[{'event_id':r['event_id'],'kind':r['kind'],'task_id':r.get('task_id')} for r in rows],
            'edges':edges, 'missing_links':missing,
            'network_capture_mode':'snapshot; native network flow source not connected'}
