"""Incremental, durable collection. Source records remain the evidence."""
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import time
from .upload_queue import UploadQueue

def digest(value):
    return hashlib.sha256(value).hexdigest()

def normalize(record, source="codex"):
    if source == "workbuddy":
        typ=record.get("type")
        kind={"message":"message","reasoning":"reasoning","function_call":"tool_call","function_call_result":"tool_result","ai-title":"session_metadata","file-history-snapshot":"file_snapshot"}.get(typ,"source_record")
        return {"kind":kind,"callId":record.get("callId"),"role":record.get("role"),"name":record.get("name"),"timestamp":record.get("timestamp"),"payload":record,"sourceType":typ,"sourceSubtype":None}

    typ=record.get('type'); p=record.get('payload')
    p=p if isinstance(p,dict) else {}
    sub=p.get('type')
    kind='source_record'
    if typ=='session_meta':kind='session_metadata'
    elif typ=='response_item':
        kind={'message':'message','reasoning':'reasoning','function_call':'tool_call',
              'custom_tool_call':'tool_call','function_call_output':'tool_result',
              'custom_tool_call_output':'tool_result'}.get(sub,'unknown_response')
    elif typ=='event_msg':
        kind={'user_message':'user_message','agent_message':'assistant_message',
              'task_started':'turn_started','task_complete':'turn_completed',
              'turn_aborted':'turn_aborted','token_count':'usage'}.get(sub,'source_event')
        if sub=='item_completed':
            item=p.get('item',{})
            if isinstance(item,dict):
                kind={'UserMessage':'user_message','AgentMessage':'assistant_message','Reasoning':'reasoning',
                      'CommandExecution':'command_execution','FileChange':'file_change',
                      'McpToolCall':'mcp_execution','ImageView':'image_view','ContextCompaction':'context_compacted',
                      'Extension':'extension_execution'}.get(item.get('type'),'unknown_item')
    elif typ=='token_usage_record':kind='usage'
    elif typ=='compacted':kind='context_compacted'
    elif typ=='turn_context':kind='turn_context'
    return {'kind':kind,'callId':p.get('call_id'),'role':p.get('role'),
            'name':p.get('name'),'timestamp':record.get('timestamp'),'payload':p,
            'sourceType':typ,'sourceSubtype':sub}

class Collector:
    def __init__(self,path):
        path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(path,timeout=15)
        os.chmod(path,0o600)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS cursors(path TEXT PRIMARY KEY,identity TEXT,epoch INTEGER,offset INTEGER,session TEXT);
        CREATE TABLE IF NOT EXISTS live_cursors(path TEXT PRIMARY KEY,identity TEXT,epoch INTEGER,offset INTEGER,session TEXT);
        CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY,session TEXT,event TEXT);
        CREATE INDEX IF NOT EXISTS event_upload_size ON events(length(CAST(event AS BLOB)));
        CREATE TABLE IF NOT EXISTS deliveries(destination TEXT,id TEXT,PRIMARY KEY(destination,id));
        ''')
        self.uploads=UploadQueue(self.db)

    def scan(self,path,max_records=1000,source="codex",max_bytes=None,max_seconds=None):
        """Commit complete records within optional soft batch budgets.

        Always process one complete record before checking a budget, so a large
        valid record can make progress. Incomplete lines retain their cursor.
        """
        path=Path(path).resolve();added=0;read_bytes=0;started=time.monotonic()
        with path.open('rb') as f, self.db:
            stat=os.fstat(f.fileno());identity=f'{stat.st_dev}:{stat.st_ino}'
            old=self.db.execute('SELECT identity,epoch,offset,session FROM cursors WHERE path=?',(str(path),)).fetchone()
            epoch=old[1] if old else 0;offset=old[2] if old else 0;session=old[3] if old else None
            if old and (old[0]!=identity or stat.st_size<offset):epoch+=1;offset=0;session=None
            f.seek(offset)
            for _ in range(max_records):
                start=f.tell();raw=f.readline(64*1024*1024+1)
                if not raw:break
                if len(raw)>64*1024*1024:
                    # Never advance past an oversized record silently.
                    raise ValueError('Source record exceeds 64 MiB; cursor retained')
                if not raw.endswith(b'\n'):break
                end=f.tell()
                count,session=self._store_line(path,identity,epoch,start,raw,session,source)
                added+=count;offset=end;read_bytes+=len(raw)
                if (max_bytes is not None and read_bytes>=max_bytes or
                    max_seconds is not None and time.monotonic()-started>=max_seconds):break
            self.db.execute('INSERT OR REPLACE INTO cursors VALUES(?,?,?,?,?)',(str(path),identity,epoch,offset,session))
        return added

    def _store_line(self,path,identity,epoch,start,raw,session,source):
        record={}
        try:
            record=json.loads(raw)
            if not isinstance(record,dict):raise ValueError('Object required')
            item=normalize(record,source)
            if record.get('type')=='session_meta':session=item['payload'].get('id') or session
            if source=='workbuddy':session=record.get('sessionId') or session or path.stem
            if not session:session='unidentified:'+digest(str(path).encode())[:24]
        except (ValueError,UnicodeError):
            item={'kind':'parse_error','rawBase64':__import__('base64').b64encode(raw).decode()}
            session=session or 'unidentified:'+digest(str(path).encode())[:24]
        event_id=digest((identity+':'+str(epoch)+':'+str(start)+':').encode()+raw)
        event={'id':event_id,'sessionId':session,'source':source,'schemaVersion':1,
               **item,'sourceFields':{k:v for k,v in record.items() if k!='payload'} if source=='codex' and item['kind']!='parse_error' else None,
               'evidence':{'path':str(path),'fileIdentity':identity,'epoch':epoch,
                           'byteStart':start,'byteEnd':start+len(raw),'sha256':digest(raw)}}
        body=json.dumps(event,ensure_ascii=False)
        cur=self.db.execute('INSERT OR IGNORE INTO events VALUES(?,?,?)',(event_id,session,body))
        if cur.rowcount:self.uploads.register(cur.lastrowid,event_id,event,len(body.encode('utf-8')))
        else:
            # A live reread can outrun upgrade backfill. Register its original
            # stored representation without rewriting evidence or its rowid.
            existing=self.db.execute('''SELECT e.rowid,e.event FROM events e WHERE e.id=?
                AND NOT EXISTS (SELECT 1 FROM upload_index i WHERE i.id=e.id)''',(event_id,)).fetchone()
            if existing:self.uploads.register(existing[0],event_id,json.loads(existing[1]),len(existing[1].encode('utf-8')))
        return cur.rowcount,session

    def scan_recent(self,path,max_records=100,source='codex',tail_bytes=2*1024*1024,max_bytes=None,max_seconds=None):
        """Tail new evidence without advancing the historical recovery cursor.

        Both readers use identical byte identities, so later historical reads
        deduplicate tail evidence. An unfinished line never advances either
        reader. A cold tail window leaves its earlier gap to the original scan.
        Optional budgets commit after a complete record, including the first.
        """
        path=Path(path).resolve();added=0;read_bytes=0;started=time.monotonic()
        with path.open('rb') as f,self.db:
            stat=os.fstat(f.fileno());identity=f'{stat.st_dev}:{stat.st_ino}'
            history=self.db.execute('SELECT identity,epoch,offset,session FROM cursors WHERE path=?',(str(path),)).fetchone()
            live=self.db.execute('SELECT identity,epoch,offset,session FROM live_cursors WHERE path=?',(str(path),)).fetchone()
            epoch=history[1] if history else live[1] if live else 0
            if history and (history[0]!=identity or stat.st_size<history[2]):epoch+=1
            elif live and live[1]>=epoch and (live[0]!=identity or stat.st_size<live[2]):epoch=live[1]+1
            session=history[3] if history and history[0]==identity and history[1]==epoch else None
            if live and live[0]==identity and live[1]==epoch and stat.st_size>=live[2]:
                offset=live[2];session=live[3] or session
            else:
                if not history and live and (live[0]!=identity or stat.st_size<live[2]):epoch=live[1]+1
                # Cold tails also revisit legacy evidence beyond a completed
                # history cursor, seeding missing upload metadata by identity.
                offset=max(stat.st_size-tail_bytes,0)
                if offset:
                    f.seek(offset-1)
                    if f.read(1)!=b'\n':
                        # Include the entire boundary line, including an
                        # unfinished final line; never start at its middle.
                        edge=offset
                        while edge:
                            start=max(0,edge-65536);f.seek(start);prefix=f.read(edge-start)
                            found=prefix.rfind(b'\n')
                            if found>=0:offset=start+found+1;break
                            edge=start
                            if offset-edge>64*1024*1024:raise ValueError('Source record exceeds 64 MiB; live cursor retained')
                        else:offset=0
            if not session:
                # Read the actual session header, rather than infer identity
                # from an arbitrary filename when tailing an old source.
                f.seek(0);header=f.readline(64*1024*1024+1)
                try:
                    first=json.loads(header)
                    if source=='codex' and first.get('type')=='session_meta':session=first.get('payload',{}).get('id')
                    elif source=='workbuddy':session=first.get('sessionId') or path.stem
                except (ValueError,AttributeError):pass
                if not session:
                    match=re.search(r'([0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})$',path.stem,re.I)
                    session=match.group(1) if match else 'unidentified:'+digest(str(path).encode())[:24]
            if not history or history[0]!=identity or history[1]!=epoch:
                # Reserve the same generation for later historical recovery.
                self.db.execute('INSERT OR REPLACE INTO cursors VALUES(?,?,?,?,?)',(str(path),identity,epoch,0,session))
            f.seek(offset)
            for _ in range(max_records):
                start=f.tell();raw=f.readline(64*1024*1024+1)
                if not raw:break
                if len(raw)>64*1024*1024:raise ValueError('Source record exceeds 64 MiB; live cursor retained')
                if not raw.endswith(b'\n'):break
                count,session=self._store_line(path,identity,epoch,start,raw,session,source)
                added+=count;offset=f.tell();read_bytes+=len(raw)
                if (max_bytes is not None and read_bytes>=max_bytes or
                    max_seconds is not None and time.monotonic()-started>=max_seconds):break
            self.db.execute('INSERT OR REPLACE INTO live_cursors VALUES(?,?,?,?,?)',(str(path),identity,epoch,offset,session))
        return added

    def pending(self,destination,limit=30,sources=None,recent=False):
        if sources is not None and not sources:return []
        source_filter=" AND json_extract(event,'$.source') IN ("+','.join('?' for _ in sources)+')' if sources is not None else ''
        query='SELECT event FROM events WHERE length(CAST(event AS BLOB))<=2096000 AND id NOT IN (SELECT id FROM deliveries WHERE destination=?)'+source_filter+(' ORDER BY rowid DESC LIMIT ?' if recent else ' ORDER BY rowid LIMIT ?')
        rows=self.db.execute(query,[destination]+(list(sources) if sources is not None else [])+[limit])
        result=[];size=0
        for row in rows:
            item=json.loads(row[0]);n=len(row[0].encode())
            if n>2*1024*1024:raise ValueError('Event too large for upload; retained locally')
            if size+n>2096000:break
            result.append(item);size+=n
        return result

    def acknowledge(self,destination,ids):
        with self.db:self.db.executemany('INSERT OR IGNORE INTO deliveries VALUES(?,?)',[(destination,i) for i in ids])

    def counts(self):
        return {'events':self.db.execute('SELECT count(*) FROM events').fetchone()[0],
                'sessions':self.db.execute('SELECT count(DISTINCT session) FROM events').fetchone()[0]}
