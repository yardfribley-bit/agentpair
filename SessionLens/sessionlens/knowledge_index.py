"""Bounded, local knowledge projection; no model calls or raw-body copies."""
from collections import deque
import hashlib,json,math,os,re,sqlite3,time
from pathlib import Path
from .task_lineage import exists,task_table,step_table
from .supervision import readable

VERSION=1

def tokens(text,limit=80):
    text=str(text).lower()
    words=re.findall(r'https?://[^\s<>"\']+|[a-z0-9][a-z0-9_./:-]*',text)
    for phrase in re.findall(r'[\u4e00-\u9fff]+',text):
        words.extend(phrase[i:i+2] for i in range(len(phrase)-1))
        if len(phrase)==1:words.append(phrase)
    return list(dict.fromkeys(w[:200] for w in words if w))[:limit]

def match_query(terms):
    words=list(dict.fromkeys(w for term in terms for w in tokens(term,32)))[:48]
    return ' OR '.join('"'+w.replace('"','""')+'"' for w in words)

def fingerprint(row):
    # Execution revisions append; changed requirement/membership replaces only
    # this task's derived projection, including manual merges/splits.
    return hashlib.sha256(json.dumps([VERSION,row[1],row[2],row[3],row[7],row[8]],ensure_ascii=False).encode()).hexdigest()

class KnowledgeIndex:
    def __init__(self,path,max_bytes=256*1024*1024,projects=None):
        self.projects=projects
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True);self.max_bytes=int(max_bytes)
        if self.max_bytes<=0:raise ValueError('知识索引大小预算必须大于零')
        self.db=sqlite3.connect(self.path,timeout=3);os.chmod(self.path,0o600)
        self.db.execute('PRAGMA journal_mode=WAL');self.db.execute('PRAGMA foreign_keys=ON')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS kb_tasks(id TEXT PRIMARY KEY,source TEXT,session TEXT,prompt TEXT,
          updated TEXT,state TEXT,revision INTEGER,turn_count INTEGER,fingerprint TEXT,cursor INTEGER,ready INTEGER);
        CREATE TABLE IF NOT EXISTS kb_chunks(id TEXT PRIMARY KEY,task TEXT REFERENCES kb_tasks(id) ON DELETE CASCADE,
          event TEXT,turn_event TEXT,kind TEXT,seq INTEGER,text TEXT,truncated INTEGER);
        CREATE INDEX IF NOT EXISTS kb_chunk_task ON kb_chunks(task,seq);
        CREATE VIRTUAL TABLE IF NOT EXISTS kb_fts USING fts5(text);
        CREATE TRIGGER IF NOT EXISTS kb_chunk_delete AFTER DELETE ON kb_chunks BEGIN DELETE FROM kb_fts WHERE rowid=old.rowid; END;
        CREATE TABLE IF NOT EXISTS kb_vectors(chunk TEXT PRIMARY KEY REFERENCES kb_chunks(id) ON DELETE CASCADE,
          identity TEXT,version TEXT,vector TEXT);
        CREATE TABLE IF NOT EXISTS kb_meta(name TEXT PRIMARY KEY,value INTEGER);
        ''')
        self.pending=deque();self.rows={};self.refreshed=0;self.supported=True;self.paused=False
    def close(self):self.db.close()
    def _chunk(self,ident,task,event,turn,kind,seq,text):
        text=str(text);excerpt=text[:2000]
        old=self.db.execute('SELECT rowid,text FROM kb_chunks WHERE id=?',(ident,)).fetchone()
        if old and old[1]==excerpt:return
        if old:self.db.execute('DELETE FROM kb_chunks WHERE id=?',(ident,))
        cur=self.db.execute('INSERT INTO kb_chunks VALUES(?,?,?,?,?,?,?,?)',(ident,task,event,turn,kind,seq,excerpt,len(text)>2000))
        self.db.execute('INSERT INTO kb_fts(rowid,text) VALUES(?,?)',(cur.lastrowid,' '.join(tokens(excerpt,2000))))
    def refresh(self,source):
        if not exists(source):self.supported=False;return
        self.supported=True
        rows=source.execute('SELECT id,source,session,prompt,updated,state,last_row,turn_count,requirements FROM task_groups ORDER BY last_row DESC').fetchall()
        self.rows={r[0]:r for r in rows if readable(r[3],user=True) and not r[3].startswith('The following is the Codex agent history')}
        indexed={r[0]:r[1:] for r in self.db.execute('SELECT id,revision,fingerprint,ready,state FROM kb_tasks')}
        with self.db:
            self.db.executemany('DELETE FROM kb_tasks WHERE id=?',[(ident,) for ident in indexed if ident not in self.rows])
        changed=[]
        for row in self.rows.values():
            old=indexed.get(row[0]);fp=fingerprint(row)
            if not old or old[:2]!=(row[6],fp) or not old[2] or old[3]!=row[5]:changed.append(row[0])
        self.pending=deque(changed);self.refreshed=time.monotonic()
        repair=source.execute("SELECT value FROM task_cursor WHERE name='semantic_fields_v1'").fetchone()
        with self.db:self.db.execute("INSERT OR IGNORE INTO kb_meta VALUES('repair_cursor',?)",(repair[0] if repair else 0,))
    def sync(self,source,task_limit=10,step_limit=50,force=False):
        if force or not self.refreshed or time.monotonic()-self.refreshed>10:self.refresh(source)
        if not self.supported:return 0
        size=self.db.execute('PRAGMA page_count').fetchone()[0]*self.db.execute('PRAGMA page_size').fetchone()[0]
        self.paused=size>=self.max_bytes
        if self.paused:return 0
        done=0
        # A task gets at most one bounded batch in this pass, even if requeued.
        for _ in range(min(task_limit,len(self.pending))):
            ident=self.pending.popleft();row=self.rows.get(ident)
            if not row:continue
            fp=fingerprint(row);old=self.db.execute('SELECT fingerprint,cursor FROM kb_tasks WHERE id=?',(ident,)).fetchone()
            cursor=old[1] if old and old[0]==fp else 0
            with self.db:
                if old and old[0]!=fp:self.db.execute('DELETE FROM kb_tasks WHERE id=?',(ident,))
                self.db.execute('INSERT INTO kb_tasks VALUES(?,?,?,?,?,?,?,?,?,?,0) ON CONFLICT(id) DO UPDATE SET updated=excluded.updated,state=excluded.state,revision=excluded.revision,ready=0',(*row[:6],row[6],row[7],fp,cursor))
                goal=row[3]+'\n'+row[8]
                if self.projects:
                    from .project_context import index_text
                    goal=index_text(self.projects.resolve(source,ident))+'\n'+goal
                self._chunk('task:'+ident,ident,ident,ident,'任务目标',0,goal)
                selected=source.execute('SELECT event,turn_event,kind,seq,substr(excerpt,1,2001) FROM linked_task_steps WHERE task=? AND seq>? AND seq<=? ORDER BY seq LIMIT ?',(ident,cursor,row[6],step_limit)).fetchall()
                for event,turn,kind,seq,text in selected:
                    self._chunk('event:'+event,ident,event,turn,kind,seq,text);cursor=seq
                more=source.execute('SELECT 1 FROM linked_task_steps WHERE task=? AND seq>? AND seq<=? LIMIT 1',(ident,cursor,row[6])).fetchone()
                self.db.execute('UPDATE kb_tasks SET cursor=?,ready=? WHERE id=?',(cursor,not bool(more),ident))
            if more:self.pending.append(ident)
            done+=len(selected)
        repair=source.execute("SELECT value FROM task_cursor WHERE name='semantic_fields_v1'").fetchone()
        checkpoint=self.db.execute("SELECT value FROM kb_meta WHERE name='repair_cursor'").fetchone()[0]
        if repair and repair[0]>checkpoint:
            # Follow the existing excerpt-repair watermark; refresh only these
            # compact derived rows, not full raw events or all task bodies.
            repaired=source.execute('SELECT event,task,turn_event,kind,seq,substr(excerpt,1,2001) FROM linked_task_steps WHERE seq>? AND seq<=? ORDER BY seq LIMIT ?',(checkpoint,repair[0],step_limit)).fetchall()
            with self.db:
                for event,task,turn,kind,seq,text in repaired:
                    if self.db.execute('SELECT 1 FROM kb_chunks WHERE id=?',('event:'+event,)).fetchone():self._chunk('event:'+event,task,event,turn,kind,seq,text)
                self.db.execute("UPDATE kb_meta SET value=? WHERE name='repair_cursor'",(repaired[-1][4] if repaired else repair[0],))
        return done
    def status(self):
        total,ready=self.db.execute('SELECT count(*),coalesce(sum(ready),0) FROM kb_tasks').fetchone()
        return {'indexedTasks':total,'readyTasks':ready,'pendingTasks':len(self.pending),
                'paused':self.paused,'supported':self.supported,'retrieval':'BM25 / 语义改写；未启用向量',
                'bytes':self.db.execute('PRAGMA page_count').fetchone()[0]*self.db.execute('PRAGMA page_size').fetchone()[0]}
    def search(self,terms,source=None,since=None,limit=60,vector=None,identity=None):
        query=match_query(terms);hits={}
        if query:
            rows=self.db.execute('''SELECT c.task,c.event,c.kind,c.text,bm25(kb_fts)
              FROM kb_fts JOIN kb_chunks c ON c.rowid=kb_fts.rowid JOIN kb_tasks t ON t.id=c.task
              WHERE kb_fts MATCH ? AND (? IS NULL OR t.source=?) AND (? IS NULL OR t.updated>=?)
              ORDER BY bm25(kb_fts) LIMIT 200''',(query,source,source,since,since)).fetchall()
            for rank,(task,event,kind,text,score) in enumerate(rows,1):
                value=hits.setdefault(task,{'taskId':task,'events':[],'text':'','score':0.,'channels':[]})
                value['score']+=1/(60+rank);value['channels']=['BM25']
                if event not in value['events']:value['events'].append(event)
                if len(value['text'])<12000:value['text']+='\n'+text
        if vector is not None:
            for rank,(task,event,text,score) in enumerate(self.vector_search(vector,identity,source,since),1):
                value=hits.setdefault(task,{'taskId':task,'events':[],'text':'','score':0.,'channels':[]})
                value['score']+=1/(60+rank)
                if 'vector' not in value['channels']:value['channels'].append('vector')
                if event not in value['events']:value['events'].append(event)
                if len(value['text'])<12000:value['text']+='\n'+text
        return sorted(hits.values(),key=lambda h:-h['score'])[:limit]
    def put_vector(self,chunk,identity,vector):
        values=_vector(vector);norm=math.sqrt(sum(x*x for x in values))
        if not identity or not norm:raise ValueError('向量身份或内容无效')
        row=self.db.execute('SELECT text FROM kb_chunks WHERE id=?',(chunk,)).fetchone()
        if not row:raise ValueError('证据片段不存在')
        version=hashlib.sha256(row[0].encode()).hexdigest()
        with self.db:self.db.execute('INSERT OR REPLACE INTO kb_vectors VALUES(?,?,?,?)',(chunk,identity,version,json.dumps(values)))
    def vector_search(self,query,identity,source=None,since=None,limit=60):
        query=_vector(query);norm=math.sqrt(sum(x*x for x in query))
        if not identity or not norm:raise ValueError('查询向量身份或内容无效')
        # Optional small local index. A large corpus should use a dedicated ANN
        # adapter; do not scan an unbounded vector corpus per question.
        rows=self.db.execute('''SELECT c.task,c.event,c.text,v.vector FROM kb_vectors v
          JOIN kb_chunks c ON c.id=v.chunk JOIN kb_tasks t ON t.id=c.task
          WHERE v.identity=? AND (? IS NULL OR t.source=?) AND (? IS NULL OR t.updated>=?)
          ORDER BY t.revision DESC LIMIT 2000''',(identity,source,source,since,since)).fetchall()
        scored=[]
        for task,event,text,raw in rows:
            values=json.loads(raw)
            if len(values)!=len(query):continue
            denominator=norm*math.sqrt(sum(x*x for x in values))
            score=sum(a*b for a,b in zip(query,values))/denominator if denominator else 0
            if score>0:scored.append((task,event,text,score))
        return sorted(scored,key=lambda r:-r[3])[:limit]

def _vector(value):
    if not isinstance(value,(list,tuple)) or not 1<=len(value)<=4096 or any(isinstance(x,bool) or not isinstance(x,(int,float)) or not math.isfinite(x) for x in value):raise ValueError('向量必须是有限数值数组')
    return list(value)
