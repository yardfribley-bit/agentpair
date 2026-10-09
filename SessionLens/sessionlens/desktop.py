"""Native desktop collector. No model calls and no AppLens dependency."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

from .core import Collector
from .database import connection
from .upload_queue import encode_batch

LABELS=['用户提问','Agent 回复','解题思路','工具调用','工具返回','会话背景','用量与状态','暂未识别']

def category(e):
    k=e['kind'];role=e.get('role')
    if k=='user_message' or k=='message' and role=='user':return LABELS[0]
    if k=='assistant_message' or k=='message' and role=='assistant':return LABELS[1]
    if k=='reasoning':return LABELS[2]
    if k in ('tool_call','command_execution','mcp_execution','extension_execution'):return LABELS[3]
    if k=='tool_result':return LABELS[4]
    if k in ('session_metadata','turn_context','context_compacted','file_snapshot','file_change','image_view'):return LABELS[5]
    if k in ('usage','turn_started','turn_completed','turn_aborted'):return LABELS[6]
    return LABELS[7]

def plain(v):
    if isinstance(v,str):return v[:2048]
    if isinstance(v,list):return '\n'.join(filter(None,(plain(x) for x in v[:8])))[:2048]
    if isinstance(v,dict):
        for key in ('text','content','message','summary','output','input','arguments','command','stdout','aiTitle'):
            if key in v:return plain(v[key])
        return '; '.join(str(k)+': '+plain(value)[:180] for k,value in list(v.items())[:8])[:2048]
    return str(v) if v is not None else ''

def summary(e):
    p=e.get('payload',{})
    body=plain(p.get('item',p))
    if e.get('name'):body=e['name']+' · '+body
    return ' '.join(body.split())[:180] or e.get('sourceType') or e['kind']

def state_root():
    if os.name=='nt':return Path(os.environ.get('LOCALAPPDATA',str(Path.home())))/'SessionLens'
    return Path.home()/'Library'/'Application Support'/'SessionLens'

def defaults():
    return {'sources':{'codex':{'enabled':True,'roots':[str(Path.home()/'.codex'/'sessions'),str(Path.home()/'.codex'/'archived_sessions')]},'workbuddy':{'enabled':True,'roots':[str(Path.home()/'.workbuddy'/'projects')]}},'endpoint':''}

class Runtime:
    def __init__(self,root,config,token=''):
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True)
        self.path=self.root/'collector.db';self.config=config
        credential=self.root/'device-token'
        if token:
            fd=os.open(credential,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
            with os.fdopen(fd,'w') as f:f.write(token.strip())
            os.chmod(credential,0o600)
        self.token=token.strip() or (credential.read_text().strip() if credential.exists() else '')
        self.stop=threading.Event();self.paused=threading.Event();self.lock=threading.Lock();self.status={};self.threads=[]
        c=Collector(self.path)
        c.db.executescript('CREATE TABLE IF NOT EXISTS display_index(id TEXT PRIMARY KEY,source TEXT,category TEXT,summary TEXT,bytes INTEGER); CREATE INDEX IF NOT EXISTS display_source ON display_index(source,category); CREATE TABLE IF NOT EXISTS display_state(name TEXT PRIMARY KEY,value INTEGER);')
        c.db.close()
        self.destination=hashlib.sha256((config.get('endpoint','')+'\0'+self.token).encode()).hexdigest()
    def update(self,**v):
        with self.lock:self.status.update(v)
        if 'knowledge' in v or 'embedding_error' in v:
            # Small private support status, without source records or model keys.
            state={k:self.status.get(k) for k in ('knowledge','embedding_error','embedding_state')}
            try:
                path=self.root/'knowledge-status.json';stage=self.root/'knowledge-status.tmp'
                stage.write_text(json.dumps(state,ensure_ascii=False),encoding='utf-8');os.chmod(stage,0o600);stage.replace(path)
            except OSError:pass
    def start(self):
        for fn in (self.collect,self.display,self.upload,self.project,self.knowledge,self.inventory):
            t=threading.Thread(target=fn,name='sessionlens-'+fn.__name__,daemon=True);t.start();self.threads.append(t)
    def set_paused(self,paused):
        """Pause this client's new reads/uploads between batches, not its query DB."""
        if paused:self.paused.set()
        else:self.paused.clear()
        self.update(paused=bool(paused))
    def wait_until_active(self):
        while self.paused.is_set() and not self.stop.is_set():self.stop.wait(.2)
        return not self.stop.is_set()
    def close(self):
        self.stop.set()
        for t in self.threads:t.join(timeout=17)
    def collect(self):
        c=None;files={};discover=-15;seen={};errors={};retry={};last={};next_sources={};history_phase=0
        def failure(key,exc):
            attempts=retry.get(key,(0,0,None))[1]+1
            retry[key]=(time.monotonic()+min(30,2**min(attempts-1,5)),attempts)
            errors[key]=type(exc).__name__+' · '+str(exc)[:240]
            self.update(errors=dict(errors),heartbeat=time.time())
        def read(source,p,st,realtime):
            key=('实时' if realtime else '历史')+' · '+str(p)
            signature=(st.st_dev,st.st_ino,st.st_size,st.st_mtime_ns)
            if seen.get(key)==signature:return False
            pending=retry.get(key)
            if pending and pending[0]>time.monotonic():return False
            try:
                table='live_cursors' if realtime else 'cursors'
                previous=c.db.execute('SELECT offset FROM '+table+' WHERE path=?',(str(p),)).fetchone()
                self.update(reading=f'{source} · {p.name} · '+('实时' if realtime else '历史'),heartbeat=time.time())
                if realtime:c.scan_recent(p,100,source,max_bytes=4*1024*1024,max_seconds=.1)
                else:c.scan(p,25,source,max_bytes=4*1024*1024,max_seconds=.1)
                offset=c.db.execute('SELECT offset FROM '+table+' WHERE path=?',(str(p),)).fetchone()[0]
                if offset>=st.st_size or previous and offset==previous[0]:seen[key]=signature
                errors.pop(key,None);retry.pop(key,None)
            except (OSError,ValueError,sqlite3.Error) as exc:failure(key,exc)
            return True
        def available(p,realtime):
            st=stats[p];key=('实时' if realtime else '历史')+' · '+str(p)
            return (seen.get(key)!=(st.st_dev,st.st_ino,st.st_size,st.st_mtime_ns) and
                    retry.get(key,(0,))[0]<=time.monotonic())
        def next_path(paths,lane,source):
            paths=sorted(paths,key=str);previous=last.get((lane,source),'')
            return next((p for p in paths if str(p)>previous),paths[0])
        def source_order(lane):
            source=next_sources.get(lane);turn=base_sources.index(source) if source in base_sources else 0
            return base_sources[turn:]+base_sources[:turn]
        def checkpoint(lane,source,p,phase=None):
            # Advance scheduling before the attempt. A crash can defer this
            # file until its next turn, but never changes its evidence cursor.
            following=base_sources[(base_sources.index(source)+1)%len(base_sources)]
            with c.db:
                c.db.execute('INSERT OR REPLACE INTO collection_file_schedule VALUES(?,?,?)',(lane,source,str(p)))
                c.db.execute('INSERT OR REPLACE INTO collection_schedule VALUES(?,?)',(lane,following))
                if lane.startswith('live'):
                    c.db.execute("INSERT OR REPLACE INTO collection_schedule VALUES('live',?)",(following,))
                if phase is not None:
                    c.db.execute("INSERT OR REPLACE INTO collection_state VALUES('history_phase',?)",(phase,))
                    c.db.execute('INSERT OR REPLACE INTO history_schedule VALUES(?,?)',(source,str(p)))
                    c.db.execute("INSERT OR REPLACE INTO collection_schedule VALUES('history',?)",(following,))
            last[lane,source]=str(p);next_sources[lane]=following
            if lane.startswith('live'):next_sources['live']=following
        try:
            while not self.stop.is_set():
                if not self.wait_until_active():break
                self.update(heartbeat=time.time())
                try:
                    if c is None:
                        c=Collector(self.path);c.db.execute('PRAGMA busy_timeout=250')
                        c.db.execute('CREATE TABLE IF NOT EXISTS history_schedule(source TEXT PRIMARY KEY,path TEXT)')
                        c.db.execute('CREATE TABLE IF NOT EXISTS collection_schedule(name TEXT PRIMARY KEY,next_source TEXT)')
                        c.db.execute('CREATE TABLE IF NOT EXISTS collection_file_schedule(lane TEXT,source TEXT,path TEXT,PRIMARY KEY(lane,source))')
                        c.db.execute('CREATE TABLE IF NOT EXISTS collection_state(name TEXT PRIMARY KEY,value INTEGER NOT NULL)')
                        next_sources=dict(c.db.execute('SELECT name,next_source FROM collection_schedule'))
                        for lane in ('live','history_priority','history_all'):
                            next_sources.setdefault(lane,next_sources.get('history'))
                        last={('history_all',source):path for source,path in c.db.execute('SELECT source,path FROM history_schedule')}
                        last.update({(lane,source):path for lane,source,path in c.db.execute('SELECT lane,source,path FROM collection_file_schedule')})
                        state=c.db.execute("SELECT value FROM collection_state WHERE name='history_phase'").fetchone()
                        history_phase=state[0]%4 if state else 0;c.db.commit()
                    if time.monotonic()-discover>=15:
                        discovered={}
                        for source,cfg in self.config['sources'].items():
                            if not cfg['enabled']:continue
                            paths=[]
                            for root in cfg['roots']:
                                key='目录 · '+str(root)
                                try:
                                    p=Path(root).expanduser()
                                    if p.is_dir():paths.extend(f.resolve() for f in p.rglob('rollout*.jsonl' if source=='codex' else '*.jsonl'))
                                    errors.pop(key,None)
                                except OSError as exc:failure(key,exc)
                            discovered[source]=list(dict.fromkeys(paths))
                        files=discovered;discover=time.monotonic()
                        active={str(p) for paths in files.values() for p in paths}
                        for key in list(errors):
                            if key.startswith(('文件 · ','实时 · ','历史 · ')) and key.split(' · ',1)[1] not in active:
                                errors.pop(key,None);retry.pop(key,None);seen.pop(key,None)
                    stats={}
                    for paths in files.values():
                        for p in paths:
                            try:stats[p]=p.stat();errors.pop('文件 · '+str(p),None)
                            except OSError as exc:failure('文件 · '+str(p),exc)
                    base_sources=list(files);sources=source_order('live')
                    cutoff=time.time()-48*60*60
                    recent={source:sorted((p for p in files[source] if p in stats and stats[p].st_mtime>=cutoff),key=str) for source in sources}
                    hot={source:max((p for p in files[source] if p in stats),key=lambda p:(stats[p].st_mtime_ns,str(p)),default=None) for source in sources}
                    # Keep the hottest tail responsive; the other three slots
                    # rotate all recent files instead of fixing the first four.
                    deadline=time.monotonic()+.35
                    for rank in range(4):
                        for source in sources:
                            if self.stop.is_set() or self.paused.is_set() or time.monotonic()>=deadline:break
                            paths=[p for p in recent[source] if p!=hot[source] and available(p,True)] if rank else ([hot[source]] if hot[source] and available(hot[source],True) else [])
                            if paths:
                                p=next_path(paths,'live_recent',source) if rank else paths[0]
                                checkpoint('live_recent' if rank else 'live',source,p)
                                read(source,p,stats[p],True)
                        if time.monotonic()>=deadline:break
                    history={r[0]:r[1:] for r in c.db.execute('SELECT path,identity,offset FROM cursors')}
                    def offset(rows,p):
                        row=rows.get(str(p));st=stats[p]
                        return row[1] if row and row[0]==f'{st.st_dev}:{st.st_ino}' and 0<=row[1]<=st.st_size else 0
                    # Three recent-gap attempts followed by one all-history
                    # attempt, with independent source/file rotation per lane.
                    deadline=time.monotonic()+.15;batches=0
                    for _ in range(4):
                        for _ in base_sources:
                            if self.stop.is_set() or self.paused.is_set() or time.monotonic()>=deadline or batches>=8:break
                            preferred='history_priority' if history_phase<3 else 'history_all';selected=None
                            for lane in (preferred,'history_all' if preferred=='history_priority' else 'history_priority'):
                                for source in source_order(lane):
                                    paths=recent[source] if lane=='history_priority' else files[source]
                                    paths=[p for p in paths if p in stats and offset(history,p)<stats[p].st_size and available(p,False)]
                                    if paths:selected=(lane,source,next_path(paths,lane,source));break
                                if selected:break
                            if selected is None:break
                            lane,source,p=selected;history_phase=(history_phase+1)%4
                            checkpoint(lane,source,p,history_phase);read(source,p,stats[p],False);batches+=1
                            row=c.db.execute('SELECT identity,offset FROM cursors WHERE path=?',(str(p),)).fetchone()
                            if row:history[str(p)]=row
                        if time.monotonic()>=deadline or batches>=8:break
                    live={r[0]:r[1:] for r in c.db.execute('SELECT path,identity,offset FROM live_cursors')}
                    progress={source:{'files':len(paths),'totalBytes':sum(stats[p].st_size for p in paths if p in stats),
                        'historicalBytes':sum(offset(history,p) for p in paths if p in stats),
                        'recentFiles':len(recent[source]),'recentLagBytes':sum(max(0,stats[p].st_size-offset(live,p)) for p in recent[source])} for source,paths in files.items()}
                    errors.pop('采集线程',None)
                    self.update(files=sum(len(paths) for paths in files.values()),bytes=sum(p['totalBytes'] for p in progress.values()),
                        read=sum(p['historicalBytes'] for p in progress.values()),source_progress=progress,errors=dict(errors),heartbeat=time.time())
                except Exception as exc:
                    # A failed batch is visible and retries; raw cursors remain intact.
                    failure('采集线程',exc)
                    if c:
                        c.db.close();c=None
                    self.stop.wait(1)
                self.stop.wait(.5)
        finally:
            if c:c.db.close()
    def display(self):
        c=None
        try:
            while not self.stop.is_set():
                if not self.wait_until_active():break
                try:
                    if c is None:c=Collector(self.path);c.db.execute('PRAGMA busy_timeout=250')
                    count=self.index(c)
                    self.update(display_index='显示索引正在整理' if count else '显示索引已更新',display_error='',display_heartbeat=time.time())
                    self.stop.wait(.1 if count else .5)
                except Exception as exc:
                    self.update(display_error=type(exc).__name__+' · '+str(exc)[:240],display_index='显示索引等待重试',display_heartbeat=time.time())
                    if c:c.db.close();c=None
                    self.stop.wait(1)
        finally:
            if c:c.db.close()
    def index(self,c,limit=100):
        # Persistent rowid cursor: the idle query touches no historical payloads.
        state=c.db.execute("SELECT value FROM display_state WHERE name='indexed_rowid'").fetchone()
        if state is None:
            # Upgrade the old index once. Resume before its earliest missing row.
            missing=c.db.execute('SELECT e.rowid FROM events e WHERE NOT EXISTS (SELECT 1 FROM display_index i WHERE i.id=e.id) ORDER BY e.rowid LIMIT 1').fetchone()
            cursor=missing[0]-1 if missing else c.db.execute('SELECT COALESCE(max(rowid),0) FROM events').fetchone()[0]
        else:cursor=state[0]
        rows=c.db.execute('SELECT rowid,id,event FROM events WHERE rowid>? ORDER BY rowid LIMIT ?',(cursor,limit))
        staged=[];deadline=time.monotonic()+.2;size=0
        try:
            for rowid,identity,body in rows:
                e=json.loads(body);n=len(body.encode());staged.append((identity,e['source'],category(e),summary(e),n))
                cursor=rowid;size+=n
                if size>=4*1024*1024 or time.monotonic()>=deadline or self.stop.is_set():break
        finally:rows.close()
        # Decode summaries before taking SQLite's writer lock, then commit the
        # compact rows and checkpoint together. Collection has its own worker.
        if not staged and state is not None:return 0
        with c.db:
            c.db.executemany('INSERT OR IGNORE INTO display_index VALUES(?,?,?,?,?)',staged)
            if state is None or cursor!=state[0]:
                c.db.execute("INSERT OR REPLACE INTO display_state VALUES('indexed_rowid',?)",(cursor,))
        return len(staged)
    def upload(self):
        c=Collector(self.path);metrics_at=0
        try:
            while not self.stop.is_set():
                if not self.wait_until_active():break
                endpoint=self.config.get('endpoint','')
                if not endpoint or not self.token:
                    self.update(upload='仅本地采集 · 未配置上报');self.stop.wait(1);continue
                parsed=urlparse(endpoint)
                if parsed.scheme!='https' or not parsed.netloc or parsed.username or parsed.password:
                    self.update(upload='上报地址必须使用 HTTPS');self.stop.wait(2);continue
                items=[]
                try:
                    sources=[s for s,cfg in self.config['sources'].items() if cfg['enabled']]
                    # Freshly registered evidence can send before any historical
                    # decoding. Backfill is bounded and yields between records.
                    items=c.uploads.next_batch(self.destination,sources=sources)
                    if not items:
                        c.uploads.backfill()
                        items=c.uploads.next_batch(self.destination,sources=sources)
                    if time.monotonic()-metrics_at>5:
                        self.update(upload_queue=c.uploads.stats(self.destination,sources=sources))
                        metrics_at=time.monotonic()
                    if not items:self.update(upload='等待可发送记录 · 历史整理继续');self.stop.wait(.5);continue
                    self.update(upload=f'正在发送 {len(items)} 份记录')
                    body=encode_batch(items)
                    req=urllib.request.Request(endpoint,body,{'Content-Type':'application/json','Authorization':'Bearer '+self.token},method='POST')
                    class NoRedirect(urllib.request.HTTPRedirectHandler):
                        def redirect_request(self,*args,**kwargs):return None
                    with urllib.request.build_opener(NoRedirect()).open(req,timeout=15) as r:receipt=json.load(r)
                    ids=[e['id'] for e in items]
                    if not isinstance(receipt.get('ids'),list):raise ValueError('平台回执不完整，保留队列重试')
                    c.uploads.acknowledge(self.destination,receipt['ids'],expected_ids=ids)
                    self.update(upload='本批已接收 · 继续同步其余记录',receipt=time.time(),upload_bytes=len(body),upload_batch=len(items))
                    items=[]
                    try:c.uploads.backfill()
                    except sqlite3.Error:self.update(upload_index='历史上报索引等待重试')
                    # Bound bandwidth without making every five records wait
                    # ten seconds. Latest/history share fair record slots.
                    self.stop.wait(max(.25,len(body)/262144))
                except Exception as exc:
                    if items:
                        try:c.uploads.fail(self.destination,[e['id'] for e in items],error=type(exc).__name__)
                        except sqlite3.Error:pass
                    self.update(upload=f'上报失败：{type(exc).__name__} · 本批保留并退避，其他记录继续')
                    # Authentication is global; avoid a rapid retry storm. Other
                    # failures defer only their selected records persistently.
                    self.stop.wait(15 if isinstance(exc,urllib.error.HTTPError) and exc.code in (401,403) else .5)
        finally:c.db.close()
    def project(self):
        from .supervision import TaskStore
        store=TaskStore(self.path)
        try:
            store.repair_links(2)
            indexed=store.db.execute("SELECT value FROM task_cursor WHERE name='rowid'").fetchone()[0]
            boundary=store.db.execute("SELECT value FROM task_cursor WHERE name='boundary'").fetchone()[0]
            for source in ('workbuddy','codex'):
                # A completed historical index already has these records. Do
                # not decode/write them again at every application startup.
                if indexed>=boundary:break
                self.update(task_index='正在准备 '+source+' 近期任务 · 历史低速整理')
                records=store.recent_source(source,2000 if source=='workbuddy' else 500)
                for start in range(0,len(records),50):
                    if self.stop.is_set():return
                    store.advance(realtime=True,records=records[start:start+50]);self.stop.wait(.1)
            while not self.stop.is_set():
                try:
                    live=store.advance(100,realtime=True)
                    count=store.advance(50)
                    store.repair_excerpts(30)
                    store.repair_links(1)
                    indexed=store.db.execute("SELECT value FROM task_cursor WHERE name='rowid'").fetchone()[0]
                    boundary=store.db.execute("SELECT value FROM task_cursor WHERE name='boundary'").fetchone()[0]
                    self.update(task_index=f'历史整理 {indexed}/{boundary} · 新任务优先' if count else '历史索引已更新')
                    self.stop.wait(.5 if count else .8)
                except sqlite3.Error as exc:
                    self.update(task_index='任务索引等待重试：'+str(exc));self.stop.wait(1)
        finally:store.close()
    def snapshot(self):
        with self.lock:status=dict(self.status)
        with connection(self.path,timeout=1) as db:
            status['categories']=db.execute('SELECT source,category,count(*) FROM display_index GROUP BY source,category').fetchall()
            status['saved']=db.execute('SELECT count(*) FROM events').fetchone()[0]
            status['ack']=db.execute('SELECT count(*) FROM deliveries WHERE destination=?',(self.destination,)).fetchone()[0]
            status['oversize']=db.execute('SELECT count(*) FROM display_index WHERE bytes>2096000').fetchone()[0]
            status['recent']=db.execute('SELECT e.id,i.source,i.category,i.summary,e.session FROM events e JOIN display_index i ON e.id=i.id ORDER BY e.rowid DESC LIMIT 50').fetchall()
        return status

    def inventory(self):
        from .project_inventory import ProjectInventory
        source=store=None
        try:
            source=sqlite3.connect(self.path.resolve().as_uri()+'?mode=ro',uri=True,timeout=1)
            store=ProjectInventory(self.root/'project_inventory.db')
            while not self.stop.is_set():
                count=0
                try:
                    store.sync(source,limit=100,live=True)
                    count=store.sync(source,limit=250)
                    self.update(project_inventory='项目目录正在低速整理' if count else '项目目录已更新')
                except sqlite3.Error as exc:self.update(project_inventory='项目目录等待重试：'+str(exc)[:120])
                self.stop.wait(.5 if count else 2)
        except (OSError,sqlite3.Error) as exc:self.update(project_inventory='项目目录暂不可用：'+str(exc)[:120])
        finally:
            if source:source.close()
            if store:store.close()

    def knowledge(self):
        from .knowledge_index import KnowledgeIndex
        index=None;source=None;vectors=None;engine=None;projects=None
        try:
            from .project_context import ProjectStore
            projects=ProjectStore(self.root/'project_context.db')
            index=KnowledgeIndex(self.root/'knowledge.db',max_bytes=self.config.get('knowledge',{}).get('maxBytes',256*1024*1024),projects=projects)
            source=sqlite3.connect(self.path.resolve().as_uri()+'?mode=ro',uri=True,timeout=1)
            if self.config.get('embedding',{}).get('enabled'):
                try:
                    from .embedding import load
                    from .embedding_store import EmbeddingStore
                    self.update(knowledge='正在后台加载本机语义模型…')
                    engine=load(self.config['embedding']);vectors=EmbeddingStore(self.root/'embeddings.db',projects=projects)
                except (ImportError,OSError,ValueError,sqlite3.Error,RuntimeError) as exc:self.update(embedding_error='本机语义检索暂不可用：'+str(exc)[:160])
            self.update(knowledge='本机模型初始化结束，准备整理项目背景')
            startup_checked=False
            while not self.stop.is_set():
                try:
                    self.update(knowledge_phase='正在整理项目背景')
                    if not startup_checked:
                        import faulthandler
                        trace=(self.root/'project-startup-stack.txt').open('w',encoding='utf-8');os.chmod(self.root/'project-startup-stack.txt',0o600)
                        faulthandler.dump_traceback_later(25,file=trace)
                        try:projects.sync(source,limit=2)
                        finally:faulthandler.cancel_dump_traceback_later();trace.close()
                        startup_checked=True
                    else:projects.sync(source,limit=2)
                    self.update(knowledge_phase='正在整理关键词索引')
                    index.sync(source);state=index.status()
                    text=('知识索引达到大小预算，整理已暂停；原始采集继续' if state['paused'] else
                          f'知识库可检索 {state["indexedTasks"]} 个任务 · 证据整理完成 {state["readyTasks"]} 个 · 待整理 {state["pendingTasks"]} 个')
                    self.update(knowledge=text,knowledge_state=state)
                    if vectors and engine:
                        vectors.sync(source,engine,limit=2);vs=vectors.status(engine.identity)
                        self.update(embedding_state=vs,knowledge=text+f' · 本机语义任务 {vs["readyTasks"]} 个'+(' · 向量整理达到预算，已暂停' if vs['paused'] else ''))
                    elif self.config.get('embedding',{}).get('enabled'):
                        self.update(knowledge=text+' · '+self.status.get('embedding_error','语义检索未就绪'))
                except sqlite3.Error as exc:self.update(knowledge='知识索引等待重试：'+str(exc))
                except (ValueError,OSError,TypeError,AttributeError,KeyError) as exc:self.update(knowledge='本机知识整理等待重试：'+type(exc).__name__+' '+str(exc)[:160])
                self.stop.wait(1 if engine else .5)
        except (sqlite3.Error,OSError,ValueError,TypeError,AttributeError,KeyError,RuntimeError) as exc:self.update(knowledge='知识索引暂不可用：'+type(exc).__name__+' '+str(exc))
        finally:
            if index:index.close()
            if source:source.close()
            if vectors:vectors.close()
            if projects:projects.close()
