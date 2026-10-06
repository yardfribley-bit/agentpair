"""Task-goal vectors in a small separate database, independent of FTS quota."""
from collections import deque
import hashlib,json,os,sqlite3,time
from pathlib import Path
from .task_lineage import exists
from .supervision import readable

def goal_text(prompt,requirements,project_text=''):
    return (project_text[:400]+'\n' if project_text else '')+'用户的原始目标：'+prompt[:800]+'\n后续要求：'+requirements[-1000:]

class EmbeddingStore:
    def __init__(self,path,max_bytes=64*1024*1024,max_tasks=10000,projects=None):
        self.projects=projects
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True)
        self.max_bytes=int(max_bytes);self.max_tasks=int(max_tasks)
        if self.max_bytes<=0 or self.max_tasks<=0:raise ValueError('向量索引预算无效')
        self.db=sqlite3.connect(self.path,timeout=3);os.chmod(self.path,0o600);self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''CREATE TABLE IF NOT EXISTS task_vectors(id TEXT PRIMARY KEY,source TEXT,session TEXT,
          prompt TEXT,text TEXT,updated TEXT,revision INTEGER,fingerprint TEXT,identity TEXT,dimensions INTEGER,vector BLOB);
          CREATE INDEX IF NOT EXISTS task_vector_scope ON task_vectors(identity,source,updated);''')
        if 'base_fingerprint' not in {r[1] for r in self.db.execute('PRAGMA table_info(task_vectors)')}:
            with self.db:self.db.execute("ALTER TABLE task_vectors ADD COLUMN base_fingerprint TEXT NOT NULL DEFAULT ''")
        self.pending=deque();self.rows={};self.refreshed=0;self.identity=None;self.paused=False
    def close(self):self.db.close()
    def refresh(self,source,engine):
        if not exists(source):return
        rows=source.execute('SELECT id,source,session,prompt,updated,last_row,requirements FROM task_groups ORDER BY last_row DESC').fetchall()
        self.rows={r[0]:(r,goal_text(r[3],r[6])) for r in rows if readable(r[3],user=True) and not r[3].startswith('The following is the Codex agent history')}
        old={r[0]:r[1:] for r in self.db.execute('SELECT id,base_fingerprint,identity FROM task_vectors')};changed=[]
        if self.projects:self.projects.refresh_overrides()
        with self.db:
            self.db.executemany('DELETE FROM task_vectors WHERE id=?',[(i,) for i in old if i not in self.rows])
            for ident,(r,text) in self.rows.items():
                fp=self._base_fingerprint(r,text)
                if old.get(ident)!=(fp,engine.identity):changed.append(ident)
                else:self.db.execute('UPDATE task_vectors SET updated=?,revision=? WHERE id=? AND (updated<>? OR revision<>?)',(r[4],r[5],ident,r[4],r[5]))
        self.pending=deque(changed);self.refreshed=time.monotonic();self.identity=engine.identity
    def _base_fingerprint(self,row,text):
        from .project_context import VERSION
        return hashlib.sha256(json.dumps([text,'project-v'+str(VERSION) if self.projects else 'goal-v1',row[5] if self.projects else None,
                                          self.projects.override_signature(row[0]) if self.projects else None]).encode()).hexdigest()
    def sync(self,source,engine,limit=2,force=False):
        if force or self.identity!=engine.identity or not self.refreshed or time.monotonic()-self.refreshed>15:self.refresh(source,engine)
        if not self.pending:return 0
        size=self.db.execute('PRAGMA page_count').fetchone()[0]*self.db.execute('PRAGMA page_size').fetchone()[0]
        self.paused=size>=self.max_bytes
        if self.paused:return 0
        ids=[];count=self.db.execute('SELECT count(*) FROM task_vectors').fetchone()[0]
        for _ in range(min(16,limit,len(self.pending))):
            ident=self.pending.popleft()
            present=self.db.execute('SELECT 1 FROM task_vectors WHERE id=?',(ident,)).fetchone()
            if count>=self.max_tasks and not present:
                self.pending.appendleft(ident);self.paused=True;break
            ids.append(ident);count+=not bool(present)
        if not ids:return 0
        # Inference runs outside SQLite transactions. Failure keeps work queued.
        try:
            texts=[]
            for ident in ids:
                r,text=self.rows[ident]
                if self.projects:
                    from .project_context import index_text
                    text=goal_text(r[3],r[6],index_text(self.projects.resolve(source,ident)))
                texts.append(text)
            values=engine.encode(texts)
            import numpy as np
            if len(values)!=len(ids):raise ValueError('向量输出缺少任务')
            with self.db:
                for ident,vector,text in zip(ids,values,texts):
                    r,base=self.rows[ident];value=np.asarray(vector,dtype='<f4')
                    if value.shape!=(engine.dimensions,) or not np.isfinite(value).all() or not np.linalg.norm(value):raise ValueError('向量输出无效')
                    fp=hashlib.sha256(text.encode()).hexdigest()
                    self.db.execute('INSERT OR REPLACE INTO task_vectors VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(r[0],r[1],r[2],r[3][:1600],text,r[4],r[5],fp,engine.identity,engine.dimensions,value.tobytes(),self._base_fingerprint(r,base)))
        except Exception:
            self.pending.extendleft(reversed(ids));raise
        return len(ids)
    def status(self,identity=None):
        identity=identity or self.identity
        count=self.db.execute('SELECT count(*) FROM task_vectors WHERE identity=?',(identity,)).fetchone()[0]
        return {'readyTasks':count,'pendingTasks':len(self.pending),'paused':self.paused,'identity':identity,
                'coverage':'task_goals_and_project_context' if self.projects else 'task_goals','bytes':self.db.execute('PRAGMA page_count').fetchone()[0]*self.db.execute('PRAGMA page_size').fetchone()[0]}
    def search(self,query,identity,source=None,since=None,limit=60):
        import numpy as np
        query=np.asarray(query,dtype=np.float32)
        if query.ndim!=1 or not np.isfinite(query).all() or not np.linalg.norm(query):raise ValueError('查询向量无效')
        rows=self.db.execute('SELECT id,dimensions,vector FROM task_vectors WHERE identity=? AND (? IS NULL OR source=?) AND (? IS NULL OR updated>=?)',(identity,source,source,since,since)).fetchall()
        ids=[];values=[]
        for ident,dimensions,raw in rows:
            if dimensions!=len(query) or len(raw)!=dimensions*4:continue
            vector=np.frombuffer(raw,dtype='<f4')
            if not np.isfinite(vector).all():continue
            ids.append(ident);values.append(vector)
        if not ids:return []
        matrix=np.vstack(values);denominator=np.linalg.norm(matrix,axis=1)*np.linalg.norm(query)
        scores=(matrix@query)/np.maximum(denominator,1e-12);order=np.argsort(-scores,kind='stable')[:limit];result=[]
        for rank,i in enumerate(order,1):
            row=self.db.execute('SELECT text FROM task_vectors WHERE id=?',(ids[i],)).fetchone()
            result.append({'taskId':ids[i],'events':[ids[i]],'text':row[0],'score':1/(60+rank),
                           'semanticScore':float(scores[i]),'semanticRank':rank,'channels':['vector']})
        return result
