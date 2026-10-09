"""SessionLens ingestion and evidence-backed session projection; no LLM claims."""
import json
from pathlib import Path
import re
import sqlite3
from contextlib import contextmanager
import os
import time
from .session_packets import event_digest, make_packet, revision_from_digest, source_seconds

HEADS_VERSION = '1'

class SessionStore:
    def __init__(self,path):
        self.path=Path(path)
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.connect() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS session_events(device TEXT,owner TEXT,id TEXT,session TEXT,event TEXT,PRIMARY KEY(device,id));
            CREATE INDEX IF NOT EXISTS session_events_session ON session_events(owner,device,session);
            CREATE TABLE IF NOT EXISTS session_uploads(id INTEGER PRIMARY KEY,device TEXT,owner TEXT,received REAL,status INTEGER,accepted INTEGER,reason TEXT,source TEXT);
            CREATE INDEX IF NOT EXISTS session_uploads_owner ON session_uploads(owner,received);
            CREATE TABLE IF NOT EXISTS session_heads(owner TEXT NOT NULL,device TEXT NOT NULL,source TEXT NOT NULL,session TEXT NOT NULL,
                eventCount INTEGER NOT NULL,eventDigest TEXT NOT NULL,revision TEXT NOT NULL,sourceTime REAL NOT NULL,lastReceived REAL,
                receivedBasis TEXT NOT NULL,PRIMARY KEY(owner,device,source,session));
            CREATE INDEX IF NOT EXISTS session_heads_owner_recent ON session_heads(owner,sourceTime DESC,lastReceived DESC);
            CREATE INDEX IF NOT EXISTS session_heads_recent ON session_heads(sourceTime DESC,lastReceived DESC);
            CREATE TABLE IF NOT EXISTS session_metadata_state(name TEXT PRIMARY KEY,value TEXT);
            CREATE INDEX IF NOT EXISTS session_events_scoped_source ON session_events(owner,device,session,json_extract(event,'$.source'));''')
            self._backfill_heads(db)
        os.chmod(self.path,0o600)
    @contextmanager
    def connect(self):
        db=sqlite3.connect(self.path,timeout=15);db.row_factory=sqlite3.Row
        try:
            with db:yield db
        finally:db.close()

    @staticmethod
    def _update_head(db,owner,device,session,source,count,digest,source_time,received,basis):
        revision=revision_from_digest(owner,device,session,source,count,digest)
        db.execute('INSERT OR REPLACE INTO session_heads VALUES(?,?,?,?,?,?,?,?,?,?)',
                   (owner,device,source,session,count,format(digest % (1 << 256),'064x'),revision,source_time,received,basis))
        return revision

    def _backfill_heads(self,db):
        # Startup checks counts under a writer lock. A rollback may run an old
        # collector which appends raw events without updating the new heads.
        # Only an inconsistent index requires reading/parsing the raw events;
        # sessions/metadata requests always use the compact heads.
        db.execute('BEGIN IMMEDIATE')
        row=db.execute("SELECT value FROM session_metadata_state WHERE name='heads_version'").fetchone()
        skipped_row=db.execute("SELECT value FROM session_metadata_state WHERE name='heads_backfill_skipped'").fetchone()
        try:previous_skipped=int(skipped_row['value']) if skipped_row else 0
        except (ValueError,TypeError):previous_skipped=-1
        raw_count=db.execute('SELECT count(*) FROM session_events').fetchone()[0]
        indexed_count=db.execute('SELECT COALESCE(sum(eventCount),0) FROM session_heads').fetchone()[0]
        if row and row['value']==HEADS_VERSION and previous_skipped>=0 and raw_count==indexed_count+previous_skipped:return
        previous={(r['owner'],r['device'],r['session'],r['source']):dict(r)
                  for r in db.execute('SELECT * FROM session_heads')}
        heads={};skipped=0
        for row in db.execute('SELECT owner,device,session,id,event FROM session_events'):
            try:event=json.loads(row['event'])
            except (ValueError,TypeError):skipped+=1;continue
            if not isinstance(event,dict) or any(not isinstance(row[k],str) or not row[k] for k in ('owner','device','session','id')):
                skipped+=1;continue
            source=event.get('source')
            if source not in ('codex','workbuddy'):skipped+=1;continue
            key=(row['owner'],row['device'],row['session'],source)
            head=heads.setdefault(key,[0,0,0.0])
            head[0]+=1;head[1]=(head[1]+event_digest(row['id'])) % (1 << 256)
            head[2]=max(head[2],source_seconds(event.get('timestamp')))
        db.execute('DELETE FROM session_heads')
        for (owner,device,session,source),(count,digest,stamp) in heads.items():
            # Legacy uploads recorded a device receipt, not the sessions in
            # that batch. A head with identical events retains its exact known
            # receipt; newly indexed events have no attributable batch receipt.
            old=previous.get((owner,device,session,source))
            unchanged=old and old['revision']==revision_from_digest(owner,device,session,source,count,digest)
            received=old['lastReceived'] if unchanged else None
            basis=old['receivedBasis'] if unchanged else 'unavailable_historical_receipt'
            self._update_head(db,owner,device,session,source,count,digest,stamp,received,basis)
        db.execute("INSERT OR REPLACE INTO session_metadata_state VALUES('heads_version',?)",(HEADS_VERSION,))
        db.execute("INSERT OR REPLACE INTO session_metadata_state VALUES('heads_backfill_skipped',?)",(str(skipped),))

    def ingest(self,identity,payload):
        if payload.get('schemaVersion')!=1:raise ValueError('Unsupported version')
        events=payload.get('events')
        if not isinstance(events,list) or not 0<len(events)<=30:raise ValueError('Invalid batch')
        for event in events:
            if not isinstance(event,dict) or not re.fullmatch('[a-f0-9]{64}',event.get('id','')):raise ValueError('Invalid event identity')
            if event.get('source') not in ('codex','workbuddy') or event.get('schemaVersion')!=1:raise ValueError('Invalid source')
            if not isinstance(event.get('sessionId'),str) or not 0<len(event['sessionId'])<=200:raise ValueError('Invalid session')
            if not isinstance(event.get('evidence'),dict) or not isinstance(event.get('kind'),str):raise ValueError('Evidence required')
            if len(json.dumps(event,ensure_ascii=False,allow_nan=False).encode())>2097152:raise ValueError('Event too large')
        with self.connect() as db:
            # The head read and raw-event inserts must share one writer
            # transaction. Concurrent upload batches otherwise both read the
            # previous digest and overwrite each other's event-count delta.
            db.execute('BEGIN IMMEDIATE')
            received=time.time();changed={};new_events=0
            for e in events:
                prior=db.execute('SELECT owner,event FROM session_events WHERE device=? AND id=?',(identity['id'],e['id'])).fetchone()
                body=json.dumps(e,ensure_ascii=False,sort_keys=True,allow_nan=False)
                if prior and (prior['owner']!=identity['owner'] or (prior['event']!=body and json.loads(prior['event'])!=e)):
                    raise ValueError('Conflicting event replay')
                key=(identity['owner'],identity['id'],e['sessionId'],e['source'])
                if key not in changed:
                    head=db.execute('SELECT eventCount,eventDigest,sourceTime FROM session_heads WHERE owner=? AND device=? AND session=? AND source=?',key).fetchone()
                    changed[key]=[head['eventCount'],int(head['eventDigest'],16),head['sourceTime']] if head else [0,0,0.0]
                if not prior:
                    db.execute('INSERT INTO session_events VALUES(?,?,?,?,?)',(identity['id'],identity['owner'],e['id'],e['sessionId'],body))
                    head=changed[key];head[0]+=1;head[1]=(head[1]+event_digest(e['id'])) % (1 << 256)
                    head[2]=max(head[2],source_seconds(e.get('timestamp')));new_events+=1
            for (owner,device,session,source),(count,digest,stamp) in changed.items():
                self._update_head(db,owner,device,session,source,count,digest,stamp,received,'session_batch')
            db.execute('INSERT INTO session_uploads(device,owner,received,status,accepted,reason,source) VALUES(?,?,?,?,?,?,?)',(identity['id'],identity['owner'],received,200,len(events),'accepted','endpoint'))
        return {'ids':[e['id'] for e in events],'accepted':len(events),'newEvents':new_events,'deviceId':identity['id']}
    def record_upload_failure(self,identity,status,reason):
        with self.connect() as db:
            db.execute('INSERT INTO session_uploads(device,owner,received,status,accepted,reason,source) VALUES(?,?,?,?,?,?,?)',
                       ((identity or {}).get('id'),(identity or {}).get('owner'),time.time(),status,0,reason,'endpoint'))

    def upload_status(self,owner,device=None):
        # Anonymous failures cannot be attributed to a user or device.
        scope='1=1' if owner=='admin' else 'owner=?'
        args=[] if owner=='admin' else [owner]
        if device:
            scope+=' AND device=?';args.append(device)
        with self.connect() as db:
            def latest(extra=''):
                row=db.execute('SELECT device,received,status,accepted,reason,source FROM session_uploads WHERE '+scope+extra+' ORDER BY received DESC,id DESC LIMIT 1',args).fetchone()
                return dict(row) if row else None
            return {'lastAttempt':latest(),'lastSuccess':latest(' AND status=200'),
                    'timeBasis':'server_received','historicalCoverage':'仅已记录的接口回执；未记录不代表从未上报'}

    def sessions(self,owner=None):
        """Indexed heads; None is an internal global enumeration, not a policy."""
        where=' WHERE owner=?' if owner is not None else ''
        with self.connect() as db:
            return [dict(r) for r in db.execute('SELECT owner,device,source,session,eventCount AS events,eventCount,revision,sourceTime,lastReceived,receivedBasis FROM session_heads'+where+' ORDER BY sourceTime DESC,lastReceived DESC,device,source,session',([owner] if owner is not None else []))]

    def metadata(self,owner,device,session,source):
        if not owner or not device or not session or source not in ('codex','workbuddy'):raise ValueError('Session scope required')
        with self.connect() as db:
            row=db.execute('SELECT owner,device,source,session,eventCount AS events,eventCount,revision,sourceTime,lastReceived,receivedBasis FROM session_heads WHERE owner=? AND device=? AND session=? AND source=?',(owner,device,session,source)).fetchone()
            return dict(row) if row else None

    def events_for(self,owner,device,session,source):
        if not owner or not device or not session or source not in ('codex','workbuddy'):raise ValueError('Session scope required')
        with self.connect() as db:
            return [dict(json.loads(r['event']),owner=owner,deviceId=device) for r in db.execute(
                "SELECT event FROM session_events WHERE owner=? AND device=? AND session=? AND json_extract(event,'$.source')=? ORDER BY rowid",(owner,device,session,source))]

    def analysis_input(self,owner,device,session,source=None):
        if source is None:
            with self.connect() as db:
                row=db.execute('SELECT source FROM session_heads WHERE owner=? AND device=? AND session=? ORDER BY sourceTime DESC,lastReceived DESC LIMIT 1',(owner,device,session)).fetchone()
            if row is None:raise ValueError('Session not found')
            source=row['source']
        events=self.events_for(owner,device,session,source)
        if not events:raise ValueError('Session not found')
        return make_packet(events,device,owner,session,source)

    def report(self,owner,device,session,source=None):
        with self.connect() as db:
            query='SELECT event FROM session_events WHERE owner=? AND device=? AND session=?'
            args=[owner,device,session]
            if source is not None:
                if source not in ('codex','workbuddy'):raise ValueError('Invalid source')
                query+=" AND json_extract(event,'$.source')=?";args.append(source)
            events=[json.loads(r['event']) for r in db.execute(query+' ORDER BY rowid',args)]
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
