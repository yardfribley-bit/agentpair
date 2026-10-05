"""Incremental, durable collection. Source records remain the evidence."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3

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
        CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY,session TEXT,event TEXT);
        CREATE INDEX IF NOT EXISTS event_upload_size ON events(length(CAST(event AS BLOB)));
        CREATE TABLE IF NOT EXISTS deliveries(destination TEXT,id TEXT,PRIMARY KEY(destination,id));
        ''')

    def scan(self,path,max_records=1000,source="codex"):
        path=Path(path).resolve(); added=0
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
                       **item,'sourceFields':{k:v for k,v in record.items() if k!='payload'} if source=='codex' and item['kind']!='parse_error' else None,'evidence':{'path':str(path),'fileIdentity':identity,'epoch':epoch,
                       'byteStart':start,'byteEnd':end,'sha256':digest(raw)}}
                cur=self.db.execute('INSERT OR IGNORE INTO events VALUES(?,?,?)',(event_id,session,json.dumps(event,ensure_ascii=False)))
                added+=cur.rowcount;offset=end
            self.db.execute('INSERT OR REPLACE INTO cursors VALUES(?,?,?,?,?)',(str(path),identity,epoch,offset,session))
        return added

    def pending(self,destination,limit=30,sources=None):
        if sources is not None and not sources:return []
        source_filter=" AND json_extract(event,'$.source') IN ("+','.join('?' for _ in sources)+')' if sources is not None else ''
        query='SELECT event FROM events WHERE length(CAST(event AS BLOB))<=2096000 AND id NOT IN (SELECT id FROM deliveries WHERE destination=?)'+source_filter+' ORDER BY rowid LIMIT ?'
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
