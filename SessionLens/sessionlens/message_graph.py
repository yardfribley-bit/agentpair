"""Bounded source-message relationships, independent of semantic task roots."""
from collections import defaultdict

CALL_KINDS={'tool_call','command_execution','mcp_execution','extension_execution'}


def identifier(value):
    return value if isinstance(value,str) and value else None

def build(events):
    nodes=[];index=defaultdict(list);edges=[];gaps=[]
    for i,e in enumerate(events):
        p=e.get('payload',{});item=p.get('item',p)
        if not isinstance(item,dict):item={}
        provider=item.get('providerData') or {}
        if not isinstance(provider,dict):provider={}
        node={'eventId':e['id'],'source':e.get('source','codex'),
              'sessionId':e.get('sessionId'),'seq':e.get('_seq',i),
              'kind':e.get('kind'),'role':e.get('role'),'sourceId':identifier(item.get('id')),
              'parentId':identifier(item.get('parentId')),'callId':identifier(e.get('callId') or item.get('callId') or item.get('call_id')),
              'requestId':identifier(provider.get('conversationRequestId')),'traceId':identifier(provider.get('traceId'))}
        nodes.append(node)
        if isinstance(node['sourceId'],str) and node['sourceId']:
            index[(node['source'],node['sessionId'],node['sourceId'])].append(node)
    calls=defaultdict(list)
    for n in sorted(nodes,key=lambda x:x['seq']):
        scope=(n['source'],n['sessionId'])
        if n['parentId']:
            parents=index.get((*scope,n['parentId']),[])
            if len(parents)==1 and parents[0]['seq']<n['seq']:
                edges.append({'from':parents[0]['eventId'],'to':n['eventId'],'relation':'source_parent','basis':'recorded'})
            else:gaps.append({'eventId':n['eventId'],'relation':'source_parent','reason':'ambiguous_parent' if len(parents)>1 else 'non_earlier_parent' if parents else 'parent_not_in_selected_records'})
        key=(*scope,n['callId'])
        if n['kind'] in CALL_KINDS and n['callId']:calls[key].append(n)
        if n['kind']=='tool_result' and n['callId']:
            matches=calls.get(key,[])
            if len(matches)==1 and matches[0]['seq']<n['seq']:
                edges.append({'from':matches[0]['eventId'],'to':n['eventId'],'relation':'call_result','basis':'recorded'})
            else:gaps.append({'eventId':n['eventId'],'relation':'call_result','reason':'ambiguous_call' if len(matches)>1 else 'call_not_in_selected_records'})
    return {'nodes':nodes,'edges':edges,'gaps':gaps,'coverage':'selected_records'}


def reasoning_bindings(graph):
    """An earlier reasoning ancestor may support several calls; stop at a user."""
    nodes={n['eventId']:n for n in graph['nodes']}
    parents={e['to']:e['from'] for e in graph['edges'] if e['relation']=='source_parent'}
    bindings={}
    for call in graph['nodes']:
        if call['kind'] not in CALL_KINDS:continue
        current=call['eventId'];path=[current]
        for _ in range(64):
            parent=parents.get(current)
            if not parent:break
            node=nodes[parent];path.append(parent)
            if node['kind']=='user_message' or (node['kind']=='message' and node.get('role')=='user'):break
            if node['kind']=='reasoning':
                bindings[call['eventId']]={'reasoningEvent':parent,'basis':'source_parent_path','path':path[::-1]};break
            current=parent
    return bindings


def records_for_turn(db,turn,limit=16):
    """Select decision/action evidence without scanning or resending all history."""
    chosen={}
    for kind,n in [('用户提问',1),('解题思路',2),('工具调用',2),('Agent 回复',1)]:
        orders=('DESC',) if kind=='Agent 回复' else ('ASC','DESC')
        for order in orders:
            for row in db.execute('SELECT event,seq,call_id FROM task_steps WHERE task=? AND kind=? ORDER BY seq '+order+' LIMIT ?',(turn,kind,n)):
                chosen[row[0]]=row
    for _,_,call in list(chosen.values()):
        if call:
            for row in db.execute("SELECT event,seq,call_id FROM task_steps WHERE task=? AND kind='工具返回' AND call_id=? ORDER BY seq LIMIT 2",(turn,call)):
                chosen[row[0]]=row
    rows=sorted(chosen.values(),key=lambda r:r[1])
    # Preserve both sides when a large turn exceeds this finite selection.
    if len(rows)>limit:rows=rows[:limit//2]+rows[-(limit-limit//2):]
    events=[]
    for ident,seq,_ in rows:
        # Read metadata and a bounded existing excerpt, not full multi-megabyte
        # tool payloads. The untouched raw event remains available on demand.
        raw=db.execute('''SELECT json_object('id',e.id,'kind',json_extract(e.event,'$.kind'),
          'source',json_extract(e.event,'$.source'),'sessionId',json_extract(e.event,'$.sessionId'),
          'role',json_extract(e.event,'$.role'),'name',json_extract(e.event,'$.name'),
          'callId',json_extract(e.event,'$.callId'),
          'payload',json_object('id',coalesce(json_extract(e.event,'$.payload.item.id'),json_extract(e.event,'$.payload.id')),
            'parentId',coalesce(json_extract(e.event,'$.payload.item.parentId'),json_extract(e.event,'$.payload.parentId')),
            'providerData',json_object('conversationRequestId',coalesce(json_extract(e.event,'$.payload.item.providerData.conversationRequestId'),json_extract(e.event,'$.payload.providerData.conversationRequestId')),
              'traceId',coalesce(json_extract(e.event,'$.payload.item.providerData.traceId'),json_extract(e.event,'$.payload.providerData.traceId')))),
          '_text',substr(s.excerpt,1,4000),'_textTruncated',length(s.excerpt)>4000)
          FROM events e JOIN task_steps s ON s.event=e.id WHERE e.id=?''',(ident,)).fetchone()
        if raw:
            import json
            e=json.loads(raw[0]);e['_seq']=seq;events.append(e)
    count=db.execute('SELECT count(*) FROM task_steps WHERE task=?',(turn,)).fetchone()[0]
    return events,count
