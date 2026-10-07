"""Candidate evidence links. A shared session is not a shared user task."""
import json

IDENTIFIERS=('requestId','request_id','turnId','turn_id','callId','call_id')

def identity_fields(record):
    result={}
    # Do not scan arbitrary nested historical context for identifiers.
    containers=[record,record.get('sourceFields',{}),record.get('payload',{})]
    for container in containers:
        if not isinstance(container,dict):continue
        for key in IDENTIFIERS:
            value=container.get(key)
            if isinstance(value,str) and value:
                result.setdefault(key.replace('_','').lower(),set()).add(value)
    return result

def candidates(contexts,events):
    edges=[]
    for context in contexts:
        cids=identity_fields(context)
        for event in events:
            # WorkBuddy context must not be matched to a Codex event.
            source=event.get('source')
            if source!='workbuddy':continue
            eids=identity_fields(event)
            shared=[kind for kind,values in cids.items() if values&eids.get(kind,set())]
            same_session=bool(context.get('sessionId') and context['sessionId']==event.get('sessionId'))
            if not shared and not same_session:continue
            edges.append({'contextId':context['id'],'eventId':event['id'],
                          'relation':'same_request_evidence' if shared else 'same_session_candidate',
                          'status':'evidence_link' if shared else 'needs_task_review',
                          'basis':shared or ['sessionId'],'taskConfirmed':False})
    return edges

def validate_task_links(answer,record_ids):
    if not isinstance(answer,dict) or not isinstance(answer.get('links'),list):raise ValueError('Invalid task links')
    known=set(record_ids);seen=set();out=[]
    for link in answer['links']:
        if not isinstance(link,dict):raise ValueError('Invalid task link')
        ids=link.get('recordIds');refs=link.get('evidenceIds');state=link.get('status')
        if not isinstance(ids,list) or not ids or any(not isinstance(x,str) or x not in known for x in ids):raise ValueError('Unknown source record')
        if len(set(ids))!=len(ids) or seen.intersection(ids):raise ValueError('Conflicting task membership')
        if not isinstance(refs,list) or not refs or any(x not in ids for x in refs):raise ValueError('Unsupported evidence reference')
        if state not in ('supported','ambiguous') or not isinstance(link.get('reason'),str) or not link['reason'].strip():raise ValueError('Task link lacks basis')
        seen.update(ids);out.append(link)
    return out

def excerpt(record,limit=1800):
    """Keep the end of long user messages, where actual requests often follow reminders."""
    payload=record.get('payload',record)
    if not isinstance(payload,dict):return {'text':'','truncated':False,'coverage':'unreadable'}
    content=payload.get('content')
    if not content:content=payload.get('rawContent') or payload.get('summary') or ''
    def plain(value):
        if isinstance(value,str):return value
        if isinstance(value,list):return '\n'.join(filter(None,(plain(v) for v in value)))
        if isinstance(value,dict):
            return plain(value.get('text') or value.get('content') or '')
        return ''
    text=plain(content)
    if not text:return {'text':'','truncated':False,'coverage':'no_readable_content'}
    if len(text)<=limit:return {'text':text,'truncated':False,'coverage':'readable_content'}
    head=limit//3;tail=limit-head
    return {'text':text[:head]+'\n[片段省略]\n'+text[-tail:],'truncated':True,'coverage':'head_and_tail'}
