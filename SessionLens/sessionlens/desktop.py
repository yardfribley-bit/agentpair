"""Native desktop collector. No model calls and no AppLens dependency."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import threading
import time
import urllib.request
from urllib.parse import urlparse

from .core import Collector

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
    if isinstance(v,str):return v
    if isinstance(v,list):return '\n'.join(filter(None,(plain(x) for x in v)))
    if isinstance(v,dict):
        for key in ('text','content','message','summary','output','input','arguments'):
            if key in v:return plain(v[key])
        return json.dumps(v,ensure_ascii=False)
    return str(v) if v is not None else ''

def summary(e):
    p=e.get('payload',{})
    body=plain(p)
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
        self.path=self.root/'collector.db';self.config=config;self.token=token
        self.stop=threading.Event();self.lock=threading.Lock();self.status={};self.threads=[]
        c=Collector(self.path)
        c.db.executescript('CREATE TABLE IF NOT EXISTS display_index(id TEXT PRIMARY KEY,source TEXT,category TEXT,summary TEXT,bytes INTEGER); CREATE INDEX IF NOT EXISTS display_source ON display_index(source,category);')
        c.db.close()
        self.destination=hashlib.sha256((config.get('endpoint','')+'\0'+token).encode()).hexdigest()
    def update(self,**v):
        with self.lock:self.status.update(v)
    def start(self):
        for fn in (self.collect,self.upload):
            t=threading.Thread(target=fn,daemon=True);t.start();self.threads.append(t)
    def close(self):
        self.stop.set()
        for t in self.threads:t.join(timeout=17)
    def collect(self):
        c=Collector(self.path);files=[];discover=0;seen={};errors={}
        try:
            while not self.stop.is_set():
                if time.monotonic()-discover>15:
                    files=[]
                    for source,cfg in self.config['sources'].items():
                        if not cfg['enabled']:continue
                        for root in cfg['roots']:
                            p=Path(root).expanduser()
                            if p.is_dir():files.extend((source,f) for f in p.rglob('rollout*.jsonl' if source=='codex' else '*.jsonl'))
                    files=list(dict.fromkeys(files));files.sort(key=lambda x:x[1].stat().st_mtime if x[1].exists() else 0,reverse=True);discover=time.monotonic()
                for source,p in files:
                    if self.stop.is_set():break
                    try:
                        st=p.stat();signature=(st.st_ino,st.st_size,st.st_mtime_ns)
                        if seen.get(str(p))==signature:continue
                        self.update(reading=f'{source} · {p.name}')
                        c.scan(p,200,source)
                        offset=c.db.execute('SELECT offset FROM cursors WHERE path=?',(str(p.resolve()),)).fetchone()[0]
                        if offset>=st.st_size:seen[str(p)]=signature
                        errors.pop(str(p),None)
                    except (OSError,ValueError,sqlite3.Error) as exc:
                        errors[str(p)]=str(exc);seen[str(p)]=signature if 'signature' in locals() else None
                # Index a bounded slice per cycle, including pre-existing databases.
                rows=c.db.execute('SELECT id,event FROM events WHERE id NOT IN (SELECT id FROM display_index) ORDER BY rowid LIMIT 1000').fetchall()
                with c.db:
                    for identity,body in rows:
                        e=json.loads(body);c.db.execute('INSERT OR IGNORE INTO display_index VALUES(?,?,?,?,?)',(identity,e['source'],category(e),summary(e),len(body.encode())))
                total=sum(p.stat().st_size for _,p in files if p.exists())
                read=c.db.execute('SELECT COALESCE(sum(offset),0) FROM cursors').fetchone()[0]
                self.update(files=len(files),bytes=total,read=read,errors=dict(errors),heartbeat=time.time())
                self.stop.wait(.5)
        finally:c.db.close()
    def upload(self):
        c=Collector(self.path);delay=1
        try:
            while not self.stop.is_set():
                endpoint=self.config.get('endpoint','')
                if not endpoint or not self.token:
                    self.update(upload='仅本地采集 · 未配置上报');self.stop.wait(1);continue
                parsed=urlparse(endpoint)
                if parsed.scheme!='https' or not parsed.netloc or parsed.username or parsed.password:
                    self.update(upload='上报地址必须使用 HTTPS');self.stop.wait(2);continue
                try:
                    items=c.pending(self.destination)
                    if not items:self.update(upload='等待新数据');self.stop.wait(1);continue
                    self.update(upload=f'正在发送 {len(items)} 份记录')
                    req=urllib.request.Request(endpoint,json.dumps({'schemaVersion':1,'events':items},ensure_ascii=False).encode(),{'Content-Type':'application/json','Authorization':'Bearer '+self.token},method='POST')
                    with urllib.request.urlopen(req,timeout=15) as r:receipt=json.load(r)
                    ids=[e['id'] for e in items]
                    if not isinstance(receipt.get('ids'),list) or sorted(receipt['ids'])!=sorted(ids):raise ValueError('平台回执不完整，保留队列重试')
                    c.acknowledge(self.destination,ids);delay=1;self.update(upload='平台已确认接收',receipt=time.time())
                except Exception as exc:
                    self.update(upload=f'上报失败：{type(exc).__name__} · {str(exc)[:160]}');self.stop.wait(delay);delay=min(delay*2,30)
        finally:c.db.close()
    def snapshot(self):
        with self.lock:status=dict(self.status)
        with sqlite3.connect(self.path,timeout=1) as db:
            status['categories']=db.execute('SELECT source,category,count(*) FROM display_index GROUP BY source,category').fetchall()
            status['saved']=db.execute('SELECT count(*) FROM events').fetchone()[0]
            status['ack']=db.execute('SELECT count(*) FROM deliveries WHERE destination=?',(self.destination,)).fetchone()[0]
            status['oversize']=db.execute('SELECT count(*) FROM display_index WHERE bytes>2096000').fetchone()[0]
            status['recent']=db.execute('SELECT e.id,i.source,i.category,i.summary,e.session FROM events e JOIN display_index i ON e.id=i.id ORDER BY e.rowid DESC LIMIT 50').fetchall()
        return status
