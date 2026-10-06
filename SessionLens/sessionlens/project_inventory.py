"""Local, incremental project inventory. A write request is evidence, not delivery."""
import hashlib,json,os,posixpath,re,sqlite3
from pathlib import Path
from .project_context import absolute,basename,within

SOURCE_EXT={'.py','.js','.jsx','.ts','.tsx','.go','.rs','.swift','.kt','.java','.c','.cpp','.h','.hpp','.html','.vue','.svelte','.cs','.rb','.php','.dart'}
MARKERS={'package.json','pyproject.toml','Cargo.toml','go.mod','pom.xml','settings.gradle','settings.gradle.kts','Package.swift'}
IGNORED={'.workbuddy','.codex','.git','node_modules','.venv','venv','__pycache__','Library','memory','outputs','output'}

def inventory_question(question):
    return bool(re.search(r'多少(?:个)?(?:开发)?项目|项目(?:总数|数量|清单|列表|名称)|哪些项目|开发了.*项目|(?:列一下|列出).*项目',question)) and not any(w in question for w in ('怎么修改','为什么','工具参数','调用多少','多少工具'))

def write_path(excerpt):
    name,sep,body=excerpt.partition(' · ')
    if not sep or name.split('.')[-1].lower() not in ('write','edit','writefile','editfile'):return None
    # Only top-level fields before content/patch: source text may contain a
    # fabricated `file_path:`. Never interpret that text as a second command.
    header=re.split(r'\n\n(?:content|new_string|old_string|text|patch):\n',body,1)[0]
    field=re.search(r'(?:^|\n\n)(?:file_path|FilePath|filePath|path):\n([^\n]+)',header)
    return absolute(field[1]) if field else None

def candidate_root(path):
    parts=path.split('/')
    if any(part in IGNORED for part in parts) or not (Path(path).suffix.lower() in SOURCE_EXT or basename(path) in MARKERS):return None
    # These are workspace containers, not names of any particular application.
    folded=[p.casefold() for p in parts]
    if 'workbuddy' in folded:
        i=folded.index('workbuddy')+1
        if i<len(parts) and (parts[i]=='code' or re.match(r'^\d{4}-\d{2}-\d{2}',parts[i])):i+=1
    elif len(parts)>3 and parts[1] in ('Users','home'):i=3
    elif len(parts)>2 and parts[1]=='tmp':i=2
    elif len(parts)>3 and parts[1:3]==['private','tmp']:i=3
    elif re.match(r'^[a-z]:/',path):i=3 if len(parts)>3 and folded[1]=='users' else 1
    else:i=2
    containers={'Documents','Desktop','Downloads','ChatGPT','workspace','projects','code','src','repos'}
    while i<len(parts)-1 and parts[i] in containers:i+=1
    if i>=len(parts)-1:return None # loose scripts do not make an application
    root='/'.join(parts[:i+1]);return root or None

class ProjectInventory:
    def __init__(self,path,filesystem=True,read_only=False):
        self.path=Path(path);self.filesystem=filesystem;self.checked=set()
        if read_only:
            self.db=sqlite3.connect(self.path.resolve().as_uri()+'?mode=ro',uri=True,timeout=1);return
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(self.path,timeout=1);self.db.execute('PRAGMA journal_mode=WAL');os.chmod(self.path,0o600)
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS inventory_state(name TEXT PRIMARY KEY,value INTEGER);
          CREATE TABLE IF NOT EXISTS inventory_dirs(id TEXT PRIMARY KEY,source TEXT,root TEXT,name TEXT);
          CREATE TABLE IF NOT EXISTS inventory_writes(event TEXT PRIMARY KEY,project TEXT,task TEXT,seq INTEGER,path TEXT,is_source INTEGER);
          CREATE INDEX IF NOT EXISTS inventory_project ON inventory_writes(project,seq);
          CREATE TABLE IF NOT EXISTS inventory_review(id TEXT PRIMARY KEY,state TEXT,name TEXT,target TEXT);
        ''')
        if 'boundary' not in {r[1] for r in self.db.execute('PRAGMA table_info(inventory_dirs)')}:
            self.db.execute('ALTER TABLE inventory_dirs ADD COLUMN boundary INTEGER NOT NULL DEFAULT 0');self.db.commit()
        if 'basis' not in {r[1] for r in self.db.execute('PRAGMA table_info(inventory_dirs)')}:
            self.db.execute("ALTER TABLE inventory_dirs ADD COLUMN basis TEXT NOT NULL DEFAULT ''");self.db.execute('ALTER TABLE inventory_dirs ADD COLUMN marker_event TEXT');self.db.commit()
        if self._state('parser_version')!=4:
            with self.db:
                self.db.execute('DELETE FROM inventory_writes');self.db.execute("DELETE FROM inventory_state WHERE name IN ('history','live')")
                self.db.execute("UPDATE inventory_dirs SET boundary=0,basis='',marker_event=NULL")
                self.db.execute("INSERT OR REPLACE INTO inventory_state VALUES('parser_version',4)")
    def close(self):self.db.close()
    def _state(self,name):
        row=self.db.execute('SELECT value FROM inventory_state WHERE name=?',(name,)).fetchone();return row[0] if row else 0
    def sync(self,source,limit=250,live=False):
        # History never advances past the task projection's safe historical
        # watermark. New records have their own cursor so they cannot skip gaps.
        marks=dict(source.execute('SELECT name,value FROM task_cursor'))
        upper=source.execute('SELECT COALESCE(max(seq),0) FROM task_steps').fetchone()[0] if live else marks.get('rowid',0)
        key='live' if live else 'history';cursor=self._state(key)
        if live and not cursor:cursor=marks.get('boundary',0)
        rows=source.execute('''SELECT s.seq,s.event,s.task,t.source,
          CASE WHEN s.kind='工具调用' THEN substr(s.excerpt,1,8192) ELSE '' END
          FROM task_steps s JOIN tasks t ON t.id=s.task WHERE s.seq>? AND s.seq<=?
          ORDER BY s.seq LIMIT ?''',(cursor,upper,limit)).fetchall()
        with self.db:
            for seq,event,task,agent,excerpt in rows:
                cursor=seq;path=write_path(excerpt);root=candidate_root(path) if path else None
                if not root:continue
                recorded=basename(path) in MARKERS;marker=recorded
                # Platform/build manifests inside one workspace count as
                # components, not separate macOS/Android projects by default.
                ident=hashlib.sha256((agent+'\0'+root).encode()).hexdigest()
                if self.filesystem and root not in self.checked and not (os.name!='nt' and re.match(r'^[a-z]:/',root)):
                    self.checked.add(root)
                    try:marker=marker or (Path(root)/'.git').is_dir() or any((Path(root)/m).is_file() for m in MARKERS)
                    except OSError:pass
                basis='recorded_manifest' if recorded else 'current_directory' if marker else ''
                self.db.execute('''INSERT INTO inventory_dirs(id,source,root,name,boundary,basis,marker_event) VALUES(?,?,?,?,?,?,?)
                  ON CONFLICT(id) DO UPDATE SET boundary=max(boundary,excluded.boundary),
                  basis=CASE WHEN excluded.basis='recorded_manifest' OR basis='' THEN excluded.basis ELSE basis END,
                  marker_event=COALESCE(excluded.marker_event,marker_event)''',(ident,agent,root,basename(root),int(marker),basis,event if recorded else None))
                self.db.execute('INSERT OR IGNORE INTO inventory_writes VALUES(?,?,?,?,?,?)',(event,ident,task,seq,path,int(Path(path).suffix.lower() in SOURCE_EXT and basename(path) not in MARKERS)))
            if len(rows)<limit:cursor=upper
            self.db.execute('INSERT OR REPLACE INTO inventory_state VALUES(?,?)',(key,cursor))
        return len(rows)
    def review(self,ident,state,name='',target=None):
        if state not in ('confirmed','candidate','excluded','automatic'):raise ValueError('项目状态无效')
        src=self.db.execute('SELECT source FROM inventory_dirs WHERE id=?',(ident,)).fetchone()
        if not src:raise ValueError('项目不存在')
        if state=='automatic':
            with self.db:self.db.execute('DELETE FROM inventory_review WHERE id=?',(ident,))
            return
        if target:
            if state=='excluded':raise ValueError('请选择合并或排除其中一项')
            dst=self.db.execute('SELECT source FROM inventory_dirs WHERE id=?',(target,)).fetchone()
            if not dst or src!=dst:raise ValueError('只能合并同一 Agent 的项目目录')
            seen={ident};current=target
            links=dict(self.db.execute('SELECT id,target FROM inventory_review WHERE target IS NOT NULL'))
            while current:
                if current in seen:raise ValueError('不能循环合并项目')
                seen.add(current);current=links.get(current)
        with self.db:self.db.execute('INSERT OR REPLACE INTO inventory_review VALUES(?,?,?,?)',(ident,state,name.strip()[:160],target))
    def snapshot(self,source,agent='',query=''):
        reviews={r[0]:r[1:] for r in self.db.execute('SELECT id,state,name,target FROM inventory_review')}
        def identity(key):
            seen=set()
            while key in reviews and reviews[key][2] and key not in seen:seen.add(key);key=reviews[key][2]
            return key
        dirs={r[0]:r[1:] for r in self.db.execute('SELECT id,source,root,name,boundary,basis,marker_event FROM inventory_dirs')};groups={}
        for ident,(provider,root,name,boundary,basis,marker_event) in dirs.items():
            if agent and provider!=agent:continue
            key=identity(ident);parent=dirs[key];state,custom,_=reviews.get(key,('identified' if parent[3] else 'candidate','',None))
            group=groups.setdefault(key,{'id':key,'source':provider,'name':custom or parent[2],'state':state,'basis':'user_confirmed' if state=='confirmed' else parent[4],
              'markerEvent':parent[5],'roots':[],'files':set(),'taskIds':set(),'writes':0,'lastSeq':0})
            group['roots'].append(root)
        # Only compact inventory metadata is traversed here; no source bodies.
        for ident,task,seq,path,is_source in self.db.execute('SELECT project,task,seq,path,is_source FROM inventory_writes'):
            group=groups.get(identity(ident))
            if not group or not is_source or basename(path) in MARKERS:continue
            group['files'].add(path);group['taskIds'].add(task);group['writes']+=1;group['lastSeq']=max(seq,group['lastSeq'])
        links=dict(source.execute('SELECT turn_task,root FROM task_links'))
        dates=dict(source.execute('SELECT id,updated FROM task_groups'))
        projects=[]
        for group in groups.values():
            if not group['files']:continue # a manifest alone is not development
            group['fileCount']=len(group.pop('files'));group['taskIds']=sorted({links.get(t,t) for t in group['taskIds'] if links.get(t,t) in dates});group['taskCount']=len(group['taskIds'])
            group['lastUpdated']=max((dates.get(t) or '' for t in group['taskIds']),default='');projects.append(group)
        # Same-name copies need identity review, even when both have manifests.
        names={}
        for p in projects:
            if p['state']!='excluded':names.setdefault((p['source'],p['name'].casefold()),[]).append(p)
        for copies in names.values():
            if len(copies)>1:
                for p in copies:
                    if p['state']=='identified':p['state']='candidate'
        counts={s:sum(p['state']==s for p in projects) for s in ('identified','confirmed','candidate','excluded')}
        if query:projects=[p for p in projects if query.casefold() in (p['name']+' '+ ' '.join(p['roots'])).casefold()]
        projects.sort(key=lambda p:(p['lastUpdated'],p['lastSeq']),reverse=True)
        projects.sort(key=lambda p:p['state']=='excluded')
        marks=dict(source.execute('SELECT name,value FROM task_cursor'))
        upper=marks.get('rowid',0);cursor=self._state('history');boundary=marks.get('boundary',0)
        return {'projects':projects,'counts':counts,'source':agent,'historyCursor':cursor,'projectionCursor':upper,'projectionBoundary':boundary,
                'complete':cursor>=upper and upper>=boundary,
                'coverage':'已采集日志中的 Write/Edit 源码写入与修改请求。识别依据包括目录内记录的项目清单、本机现存项目标记或人工确认；本机目录状态不认证历史仓库。相同名称副本待确认，不自动合并。不含仅浏览、安装、文档、Shell 内写文件或未采集设备；工具请求不代表成功交付。'}
    def details(self,source,ident,limit=100):
        group=next((p for p in self.snapshot(source)['projects'] if p['id']==ident),None)
        if not group:raise ValueError('项目不存在')
        roots=set(group['roots']);ids=[r[0] for r in self.db.execute('SELECT id,root FROM inventory_dirs WHERE source=?',(group['source'],)) if r[1] in roots]
        slots=','.join('?' for _ in ids)
        writes=self.db.execute('SELECT event,task,path,seq FROM inventory_writes WHERE project IN ('+slots+') AND is_source=1 ORDER BY seq DESC LIMIT ?',[*ids,limit]).fetchall()
        tasks=[]
        for task in group['taskIds']:
            row=source.execute('SELECT prompt,updated,state FROM task_groups WHERE id=?',(task,)).fetchone()
            if row:tasks.append({'taskId':task,'prompt':row[0],'updated':row[1],'state':row[2]})
        group['tasks']=sorted(tasks,key=lambda t:t['updated'],reverse=True)[:limit]
        group['firstTasks']=sorted(tasks,key=lambda t:t['updated'])[:8]
        group['evidence']=[{'eventId':e,'turnTask':t,'path':p,'seq':s} for e,t,p,s in writes]
        return group
