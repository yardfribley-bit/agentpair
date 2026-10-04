"""SessionLens ingestion and evidence-backed session projection; no LLM claims."""
import json
from pathlib import Path
import re
import sqlite3
from contextlib import contextmanager
import os

class SessionStore:
    def __init__(self,path):
        self.path=Path(path)
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.connect() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS session_events(device TEXT,owner TEXT,id TEXT,session TEXT,event TEXT,PRIMARY KEY(device,id));
            CREATE INDEX IF NOT EXISTS session_events_session ON session_events(owner,device,session);''')
        os.chmod(self.path,0o600)
    @contextmanager
    def connect(self):
        db=sqlite3.connect(self.path,timeout=15);db.row_factory=sqlite3.Row
        try:
            with db:yield db
        finally:db.close()

    def ingest(self,identity,payload):
        if payload.get('schemaVersion')!=1:raise ValueError('Unsupported version')
        events=payload.get('events')
        if not isinstance(events,list) or not 0<len(events)<=30:raise ValueError('Invalid batch')
        for event in events:
            if not isinstance(event,dict) or not re.fullmatch('[a-f0-9]{64}',event.get('id','')):raise ValueError('Invalid event identity')
            if event.get('source') not in ('codex','workbuddy') or event.get('schemaVersion')!=1:raise ValueError('Invalid source')
            if not isinstance(event.get('sessionId'),str) or not 0<len(event['sessionId'])<=200:raise ValueError('Invalid session')
            if not isinstance(event.get('evidence'),dict) or not isinstance(event.get('kind'),str):raise ValueError('Evidence required')
            if len(json.dumps(event,ensure_ascii=False).encode())>2097152:raise ValueError('Event too large')
        with self.connect() as db:
            for e in events:
                prior=db.execute('SELECT event FROM session_events WHERE device=? AND id=?',(identity['id'],e['id'])).fetchone()
                body=json.dumps(e,ensure_ascii=False,sort_keys=True)
                if prior and prior['event']!=body:raise ValueError('Conflicting event replay')
                db.execute('INSERT OR IGNORE INTO session_events VALUES(?,?,?,?,?)',(identity['id'],identity['owner'],e['id'],e['sessionId'],body))
        return {'ids':[e['id'] for e in events],'accepted':len(events),'deviceId':identity['id']}
    def sessions(self,owner):
        with self.connect() as db:
            return [dict(r) for r in db.execute('SELECT device,session,count(*) AS events FROM session_events WHERE owner=? GROUP BY device,session',(owner,))]
    def analysis_input(self,owner,device,session):
        report=self.report(owner,device,session)
        if not report['events']:raise ValueError('Session not found')
        excerpts=[];used=0
        for e in report['events']:
            if e['kind'] not in ('user_message','message','assistant_message','tool_call','tool_result','reasoning','turn_completed','turn_aborted','parse_error'):continue
            body=json.dumps(e.get('payload',{}),ensure_ascii=False)
            item={'eventId':e['id'],'kind':e['kind'],'callId':e.get('callId'),
                  'payloadExcerpt':body[:900],'payloadTruncated':len(body)>900,'evidence':e['evidence']}
            n=len(json.dumps(item,ensure_ascii=False))
            if used+n>4200:break
            excerpts.append(item);used+=n
        return {'source':'SessionLens','sessionId':session,'deviceId':device,
                'quality':report['quality'],'totalEvents':len(report['events']),
                'includedEvents':len(excerpts),'coverage':'selected_source_events', 'events':excerpts}
    def report(self,owner,device,session):
        with self.connect() as db:
            events=[json.loads(r['event']) for r in db.execute('SELECT event FROM session_events WHERE owner=? AND device=? AND session=? ORDER BY rowid',(owner,device,session))]
        calls={};results={}
        for e in events:
            key=e.get('callId')
            if key and e['kind']=='tool_call':calls.setdefault(key,[]).append(e)
            if key and e['kind']=='tool_result':results.setdefault(key,[]).append(e)
        pairs=[]
        for key in calls.keys()|results.keys():
            c=calls.get(key,[]);r=results.get(key,[])
            pairs.append({'callId':key,'calls':c,'results':r,'matched':len(c)==len(r)==1})
        return {'sessionId':session,'deviceId':device,'events':events,'tools':pairs,
                'quality':{'unknownRecords':sum(e['kind'] in ('source_record','unknown_response','source_event') for e in events),
                           'parseErrors':sum(e['kind']=='parse_error' for e in events),
                           'unmatchedTools':sum(not x['matched'] for x in pairs)},
                'processing':'deterministic_evidence_projection','semanticAnalysis':'not_started'}
