"""Evidence-scoped project links, separate from user-goal/task lineage."""
import ast,configparser,hashlib,json,ntpath,posixpath,re,shlex,sqlite3,time
from collections import deque
from pathlib import Path
from urllib.parse import urlparse
from .task_lineage import resolve,signature,step_table
from .supervision import text_content,readable

VERSION=5
PYTHON_EXECUTABLE=r'(?:python(?:\d+(?:\.\d+)*)?|py)(?:\.exe)?'

def source_fingerprint(row):
    return hashlib.sha256(json.dumps([VERSION,list(row)],ensure_ascii=False).encode()).hexdigest()

def command_paths(command):
    """Read literal Python file arguments, never strings containing example code."""
    command=command[:16000];sources=[];shell_lines=[];lines=command.splitlines();i=0;has_heredoc=False
    while i<len(lines):
        line=lines[i];shell_lines.append(line);i+=1
        heredoc=re.search(r"<<(-?)\s*(['\"]?)([A-Za-z_][A-Za-z_0-9]*)\2(?:\s|$)",line)
        if not heredoc:continue
        has_heredoc=True
        body=[]
        while i<len(lines):
            value=lines[i];i+=1
            if (value.lstrip('\t') if heredoc[1] else value)==heredoc[3]:break
            body.append(value.lstrip('\t') if heredoc[1] else value)
        # Other heredocs can be source templates or prose, not executed Python.
        head=line[:heredoc.start()]
        if re.search(r'(?:^|[\s;&|])(?:[^\s;&|]*/)?'+PYTHON_EXECUTABLE+r'(?:\s|$)',head):sources.append('\n'.join(body))
    if not has_heredoc:sources.append(command)
    try:words=shlex.split('\n'.join(shell_lines))
    except ValueError:words=[]
    for i,word in enumerate(words[:-2]):
        if not re.fullmatch(PYTHON_EXECUTABLE,basename(word),re.I):continue
        for j in range(i+1,min(i+6,len(words)-1)):
            if words[j]=='-c':sources.append(words[j+1]);break
            if not words[j].startswith('-'):break
    paths=[]
    for source in sources[:16]:
        try:tree=ast.parse(source)
        except (ValueError,SyntaxError,RecursionError):continue
        for node in sorted((n for n in ast.walk(tree) if isinstance(n,ast.Call)),key=lambda n:(n.lineno,n.col_offset)):
            if not isinstance(node,ast.Call) or not node.args:continue
            func=node.func;name=func.id if isinstance(func,ast.Name) else func.attr if isinstance(func,ast.Attribute) else ''
            value=node.args[0]
            if name in ('Path','open') and isinstance(value,ast.Constant) and isinstance(value.value,str):paths.append(value.value)
    return list(dict.fromkeys(paths))[:64]

def tool_arguments(payload):
    value=payload.get('arguments',payload.get('input',payload.get('command',{})))
    if isinstance(value,str):
        try:value=json.loads(value)
        except ValueError:value={'code':value} if 'exec_command' in value else {'command':value}
    if not isinstance(value,dict):return []
    if isinstance(value.get('params'),dict):value=value['params']
    if any(k in value for k in ('cmd','command','path','file_path','workdir','cwd')):return [value]
    code=value.get('code')
    if not isinstance(code,str):return [value]
    # Read literal exec_command objects inside recorded JS orchestration. This
    # is a bounded structural parser, never eval or a replay of the source code.
    result=[];code=code[:32000]
    masked=re.sub(r'//[^\n]*|/\*.*?\*/|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|`(?:\\.|[^`\\])*`',lambda m:' '*len(m[0]),code,flags=re.S)
    for match in list(re.finditer(r'(?:tools\.)?exec_command\(\s*\{',masked))[:16]:
        start=match.end()-1;depth=0;quote=None;escaped=False;end=None
        for i in range(start,len(code)):
            char=code[i]
            if quote:
                if escaped:escaped=False
                elif char=='\\':escaped=True
                elif char==quote:quote=None
            elif char in ('"',"'",'`'):quote=char
            elif char=='{':depth+=1
            elif char=='}':
                depth-=1
                if depth==0:end=i+1;break
        if end is None:continue
        body=code[start+1:end-1];args={};segments=[];part=0;depth=0;quote=None;escaped=False
        for i,char in enumerate(body):
            if quote:
                if escaped:escaped=False
                elif char=='\\':escaped=True
                elif char==quote:quote=None
            elif char in ('"',"'",'`'):quote=char
            elif char in ('{','[','('):depth+=1
            elif char in ('}',']',')'):depth-=1
            elif char==',' and depth==0:segments.append(body[part:i]);part=i+1
        segments.append(body[part:])
        for segment in segments:
            pattern=r'\s*(?:["\']?(cmd|command|workdir|cwd)["\']?)\s*:\s*("(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\')\s*'
            field=re.fullmatch(pattern,segment,re.S)
            if field:
                try:args[field[1]]=ast.literal_eval(field[2])
                except (ValueError,SyntaxError):pass
        if args:result.append(args)
    return result

def remote(value,trusted=False):
    if not isinstance(value,str) or len(value)>2048:return None
    value=value.strip().rstrip('.,);]');scp=re.match(r'^(?:[^/@\s]+@)?([^/:\s]+):([^\s]+)$',value) if '://' not in value else None
    if any(c.isspace() for c in value):return None
    if scp:host=scp[1].lower();path=scp[2]
    else:
        parsed=urlparse(value)
        if parsed.scheme not in ('https','http','ssh','git') or not parsed.hostname:return None
        host=parsed.hostname.lower();path=parsed.path.strip('/')
        try:
            port=parsed.port
        except ValueError:return None
        if ':' in host:host='['+host+']'
        if port and port!={'https':443,'http':80,'ssh':22,'git':9418}.get(parsed.scheme):host+=':'+str(port)
    path=path.split('?',1)[0].split('#',1)[0].strip('/')
    if not trusted and host not in ('github.com','gitlab.com','bitbucket.org'):return None
    parts=path.split('/')
    if host in ('github.com','bitbucket.org'):parts=parts[:2]
    elif '-' in parts:parts=parts[:parts.index('-')]
    if len(parts)<2 or any(p in ('','.', '..') for p in parts):return None
    path='/'.join(parts)
    if path.endswith('.git'):path=path[:-4]
    if not path:return None
    return 'https://'+host+'/'+path

def absolute(value,base=None):
    if not isinstance(value,str) or not value or len(value)>4096 or value.startswith(('~','$','%')):return None
    # Recorded path fields must not contain control characters or entire bodies.
    if any(ord(c)<32 for c in value):return None
    value=value.strip()
    if len(value)>1 and value[0]==value[-1] and value[0] in ('"',"'"):value=value[1:-1]
    windows=bool(re.match(r'^[a-zA-Z]:[\\/]',value) or value.startswith('\\\\') or base and re.match(r'^[a-zA-Z]:[\\/]',base))
    module=ntpath if windows else posixpath
    if not module.isabs(value):
        if not base:return None
        value=module.join(base,value)
    result=module.normpath(value)
    return result.replace('\\','/').lower() if windows else result

def within(path,root):return bool(path and root and (path==root or path.startswith(root.rstrip('/')+'/')))

def basename(value):return (value or '').replace('\\','/').rstrip('/').split('/')[-1]

def index_text(context):
    if context.get('mode')!='linked':return ''
    return '\n'.join('项目：'+p['name']+('；仓库：'+p['repository'] if p.get('repository') else '') for p in context.get('projects',[]))[:1500]

class ProjectStore:
    def __init__(self,path,filesystem=True):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True);self.filesystem=filesystem
        self.db=sqlite3.connect(self.path,timeout=3);self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA foreign_keys=ON')
        __import__('os').chmod(self.path,0o600)
        self.db.executescript('''CREATE TABLE IF NOT EXISTS task_contexts(task TEXT PRIMARY KEY,checksum TEXT,payload TEXT);
          CREATE TABLE IF NOT EXISTS project_overrides(task TEXT PRIMARY KEY,payload TEXT);
          CREATE TABLE IF NOT EXISTS project_headers(path TEXT PRIMARY KEY,session TEXT,payload TEXT);
          CREATE TABLE IF NOT EXISTS project_catalog(id TEXT PRIMARY KEY,name TEXT,repository TEXT,root TEXT);
          CREATE TABLE IF NOT EXISTS project_tasks(task TEXT REFERENCES task_contexts(task) ON DELETE CASCADE,
            project TEXT REFERENCES project_catalog(id),role TEXT,PRIMARY KEY(task,project));
          CREATE INDEX IF NOT EXISTS project_tasks_project ON project_tasks(project,task);''')
        if 'source_fingerprint' not in {r[1] for r in self.db.execute('PRAGMA table_info(task_contexts)')}:
            with self.db:self.db.execute("ALTER TABLE task_contexts ADD COLUMN source_fingerprint TEXT NOT NULL DEFAULT ''")
        self.git_cache={};self.overrides={};self.refresh_overrides()
        self.pending=deque();self.refreshed=0
    def close(self):self.db.close()
    def cached(self,task):
        row=self.db.execute('SELECT payload FROM task_contexts WHERE task=?',(task,)).fetchone()
        return json.loads(row[0]) if row else {}
    def sync(self,source,limit=2,force=False):
        if force or not self.refreshed or time.monotonic()-self.refreshed>15:
            rows=source.execute('SELECT id,source,session,prompt,requirements,last_row FROM task_groups ORDER BY last_row DESC').fetchall()
            existing=dict(self.db.execute('SELECT task,source_fingerprint FROM task_contexts'));current={r[0] for r in rows}
            with self.db:self.db.executemany('DELETE FROM task_contexts WHERE task=?',[(i,) for i in existing if i not in current])
            self.pending=deque(r[0] for r in rows if existing.get(r[0])!=source_fingerprint(r[1:]))
            self.refreshed=time.monotonic();self.refresh_overrides()
        count=0
        for _ in range(min(limit,len(self.pending))):self.resolve(source,self.pending.popleft());count+=1
        return count
    def refresh_overrides(self):self.overrides=dict(self.db.execute('SELECT task,payload FROM project_overrides'))
    def override_signature(self,task):return hashlib.sha256(self.overrides.get(task,'').encode()).hexdigest()
    def catalog(self):return self.db.execute('SELECT DISTINCT p.id,p.name,p.repository FROM project_catalog p JOIN project_tasks t ON t.project=p.id ORDER BY p.name').fetchall()
    def task_ids(self,project):
        if project=='__unlinked__':return {r[0] for r in self.db.execute("SELECT task FROM task_contexts WHERE json_extract(payload,'$.mode') IN ('independent','unlinked')")}
        return {r[0] for r in self.db.execute('SELECT task FROM project_tasks WHERE project=?',(project,))}
    def set_override(self,task,mode,name='',root='',repository=''):
        if mode not in ('independent','linked','automatic'):raise ValueError('项目关联类型无效')
        if mode=='automatic':
            with self.db:self.db.execute('DELETE FROM project_overrides WHERE task=?',(task,))
        else:
            repo=remote(repository,True) if repository else None;directory=absolute(root) if root else None
            if repository and not repo:raise ValueError('请输入有效的仓库地址')
            if root and not directory:raise ValueError('请输入绝对项目目录')
            if mode=='linked' and not (name.strip() or repo or directory):raise ValueError('至少提供项目名、仓库或项目目录')
            value={'mode':mode,'projects':[],'gaps':[]}
            if mode=='linked':value['projects']=[self._project(name.strip() or basename(repo or directory),directory,repo,'target','user_confirmed',None)]
            with self.db:self.db.execute('INSERT OR REPLACE INTO project_overrides VALUES(?,?)',(task,json.dumps(value,ensure_ascii=False)))
        with self.db:
            self.db.execute('DELETE FROM task_contexts WHERE task=?',(task,))
            if mode!='automatic':
                value['taskId']=task;value['signature']='manual-pending';value['workingDirectory']=None
                self.db.execute('INSERT INTO task_contexts VALUES(?,?,?,?)',(task,'manual-pending',json.dumps(value,ensure_ascii=False),''))
                for project in value['projects']:
                    self.db.execute('INSERT INTO project_catalog VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,repository=excluded.repository,root=excluded.root',(project['id'],project['name'],project['repository'],project['root']))
                    self.db.execute('INSERT INTO project_tasks VALUES(?,?,?)',(task,project['id'],project['role']))
        self.refresh_overrides()
    def _project(self,name,root,repo,role,basis,event,branch=None):
        identity=repo or str(self.path.resolve())+'\0'+(root or name)
        # A manually named subproject can share a repository without replacing
        # another project's identity or catalogue label.
        if repo and basis=='user_confirmed' and name and name.casefold()!=basename(repo).casefold():identity+='\0'+name
        return {'id':hashlib.sha256(identity.encode()).hexdigest(),'name':str(name or basename(repo or root))[:160],'root':root,
                'repository':repo,'repositoryId':hashlib.sha256(repo.encode()).hexdigest() if repo else None,
                'role':role,'basis':basis,'branch':branch[:160] if isinstance(branch,str) else None,'evidenceRefs':[event] if event else []}
    def _header(self,path,session,evidence,source_db):
        if not path:return {}
        cached=self.db.execute('SELECT session,payload FROM project_headers WHERE path=?',(path,)).fetchone()
        if cached and cached[0]==session:
            meta=json.loads(cached[1]);ident=meta.get('headerEvidence',{}).get('eventId')
            if ident and source_db.execute('SELECT 1 FROM events WHERE id=?',(ident,)).fetchone():return meta
        try:
            source=Path(path)
            if not source.is_file() or source.suffix!='.jsonl':return {}
            with source.open('rb') as file:raw=file.readline(65536)
            if not raw.endswith(b'\n'):return {}
            value=json.loads(raw);p=value.get('payload',value)
            ident=p.get('id') if value.get('type')=='session_meta' else p.get('sessionId')
            if ident!=session:return {}
            identity=evidence.get('fileIdentity');epoch=evidence.get('epoch')
            if identity is None or epoch is None:return {}
            event_id=hashlib.sha256((identity+':'+str(epoch)+':0:').encode()+raw).hexdigest()
            if not source_db.execute('SELECT 1 FROM events WHERE id=?',(event_id,)).fetchone():return {}
            meta={k:p.get(k) for k in ('cwd','git','projectPath','projectName')}
            g=meta.get('git')
            if isinstance(g,dict):meta['git']={'repository_url':remote(g.get('repository_url') or g.get('remote_url') or g.get('url'),True),'branch':g.get('branch')}
            meta['headerEvidence']={'eventId':event_id,'path':path,'byteStart':0,'byteEnd':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}
            with self.db:self.db.execute('INSERT OR REPLACE INTO project_headers VALUES(?,?,?)',(path,session,json.dumps(meta,ensure_ascii=False)))
            return meta
        except (OSError,ValueError,AttributeError):return {}
    def _git(self,directory):
        if not self.filesystem or not directory or re.match(r'^[a-z]:/',directory) and __import__('os').name!='nt':return None
        if directory in self.git_cache:return self.git_cache[directory]
        try:
            current=Path(directory)
            if not current.is_dir():current=current.parent
        except (OSError,ValueError):
            self.git_cache[directory]=None;return None
        found=None
        for _ in range(7):
            git=current/'.git'
            try:
                if git.is_file():
                    with git.open(encoding='utf-8') as file:pointer=file.read(2048).strip()
                    if not pointer.startswith('gitdir:'):break
                    git=(current/pointer[7:].strip()).resolve()
                if git.is_dir():
                    # Do not open current config here: macOS access mediation
                    # can stall a background worker indefinitely. Historical
                    # remotes come from captured metadata/results instead.
                    found={'root':absolute(str(current)),'repository':None,'basis':'current_filesystem'};break
            except (OSError,ValueError,configparser.Error):pass
            if current.parent==current:break
            current=current.parent
        self.git_cache[directory]=found;return found
    def resolve(self,source,task):
        task=resolve(source,task)
        row=source.execute('SELECT source,session,prompt,requirements,last_row FROM task_groups WHERE id=?',(task,)).fetchone()
        if not row:return {'taskId':task,'mode':'unlinked','projects':[],'workingDirectory':None,'gaps':['任务元信息尚不可用']}
        manual=self.overrides.get(task)
        checksum=hashlib.sha256(json.dumps([VERSION,list(row),signature(source,task),manual],ensure_ascii=False).encode()).hexdigest()
        cached=self.db.execute('SELECT checksum,payload FROM task_contexts WHERE task=?',(task,)).fetchone()
        if cached and cached[0]==checksum:return json.loads(cached[1])
        if manual:result={**self._derive(source,task,row,checksum),**json.loads(manual),'taskId':task,'signature':checksum}
        else:result=self._derive(source,task,row,checksum)
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO task_contexts VALUES(?,?,?,?)',(task,checksum,json.dumps(result,ensure_ascii=False),source_fingerprint(row)))
            for project in result['projects']:
                self.db.execute('INSERT INTO project_catalog VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,repository=excluded.repository,root=excluded.root',(project['id'],project['name'],project['repository'],project['root']))
                self.db.execute('INSERT INTO project_tasks VALUES(?,?,?)',(task,project['id'],project['role']))
        return result
    def _derive(self,source,task,row,checksum):
        steps=step_table(source);ids=[task]
        for kind in ('用户提问','工具调用'):
            for order in ('ASC','DESC'):ids.extend(r[0] for r in source.execute('SELECT event FROM '+steps+' WHERE task=? AND kind=? ORDER BY seq '+order+' LIMIT 4',(task,kind)))
        events=[]
        for ident in dict.fromkeys(ids):
            raw=source.execute('SELECT CASE WHEN length(event)<=65536 THEN event END FROM events WHERE id=?',(ident,)).fetchone()
            if raw and raw[0]:events.append(json.loads(raw[0]))
        root=next((e for e in events if e['id']==task),{});payload=root.get('payload',{});payload=payload.get('item',payload)
        header=self._header(root.get('evidence',{}).get('path'),row[1],root.get('evidence',{}),source)
        cwd=absolute(payload.get('cwd') or payload.get('projectPath') or header.get('cwd') or header.get('projectPath'))
        git=payload.get('git') or header.get('git') or {};git=git if isinstance(git,dict) else {}
        recorded_repo=remote(git.get('repository_url') or git.get('remote_url') or git.get('url'),True)
        projects=[];gaps=[];environment_refs=[]
        def add(project):
            old=next((p for p in projects if p['id']==project['id']),None)
            if old:
                old['evidenceRefs']=list(dict.fromkeys(old['evidenceRefs']+project['evidenceRefs']))
                if project['role']=='target':old.update({k:v for k,v in project.items() if k!='evidenceRefs'})
            else:projects.append(project)
        def associate(directory,event,file_path=None,local_project=False):
            directory=absolute(directory,cwd)
            if not directory:return
            repo_info=self._git(file_path or directory)
            if repo_info:
                add(self._project(basename(repo_info['root']),repo_info['root'],repo_info['repository'],'target',repo_info['basis'],event));return
            if recorded_repo and within(directory,cwd) and (not file_path or within(file_path,cwd)):
                add(self._project(basename(recorded_repo),cwd,recorded_repo,'target','recorded',event,git.get('branch')))
            elif local_project:add(self._project(payload.get('projectName') or basename(directory),directory,None,'target','recorded',event))
        for e in events:
            p=e.get('payload',{});p=p.get('item',p);kind=e.get('kind');eid=e['id']
            if kind in ('message','user_message') and (e.get('role')=='user' or kind=='user_message'):
                raw_text=text_content(p)[:20000]
                for block in re.findall(r'<in-app-browser-context\b[^>]*>(.*?)</in-app-browser-context>',raw_text,re.S):
                    for url in re.findall(r'https?://[^\s<>"\']+',block):
                        repo=remote(url)
                        if repo:environment_refs.append(repo)
                for url in re.findall(r'https?://[^\s<>"\']+',readable(p,user=True)[:20000]):
                    repo=remote(url)
                    if repo:add(self._project(basename(repo),None,repo,'reference','recorded',eid))
                continue
            if kind not in ('tool_call','command_execution','mcp_execution'):continue
            arguments=tool_arguments(p)
            for args in arguments:self._associate_arguments(args,p,eid,cwd,associate,gaps)
            if any(re.search(r'(?:^|[;&\n])\s*git\s',str(a.get('cmd') or a.get('command') or '')) for a in arguments):
                call=e.get('callId')
                if call:
                    returns=source.execute("SELECT event,substr(excerpt,1,16000) FROM "+steps+" WHERE task=? AND kind='工具返回' AND call_id=? ORDER BY seq LIMIT 2",(task,call)).fetchall()
                    urls=[]
                    for return_id,text in returns:
                        for url in re.findall(r'(?:To\s+|origin\s+)(https?://[^\s"\'\\]+|git@[^\s"\'\\]+)',text):
                            uri=remote(url,True)
                            if uri:urls.append((uri,return_id))
                    targets=[x for x in projects if x['role']=='target' and not x['repository'] and eid in x['evidenceRefs']]
                    if len(targets)==1 and len({u for u,_ in urls})==1:
                        old=targets[0];projects.remove(old);uri=urls[0][0]
                        new=self._project(old['name'],old['root'],uri,'target','recorded',eid);new['evidenceRefs'].extend(x[1] for x in urls);add(new)
        if recorded_repo and any(p['repository']==recorded_repo and p['role']=='target' for p in projects):
            for p in projects:
                if p['repository']==recorded_repo and p['basis']=='recorded':p['branch']=git.get('branch')
        # A repository page can be shown as a reference when this task really
        # operates on a correspondingly named project. It does not certify the
        # local checkout's remote, and an unrelated temporary query stays free.
        for uri in environment_refs:
            if any(p['role']=='target' and p['name'].casefold()==basename(uri).casefold() for p in projects):
                add(self._project(basename(uri),None,uri,'reference','recorded',task))
        result={'taskId':task,'signature':checksum,'mode':'linked' if projects else 'unlinked','projects':projects,
                'workingDirectory':cwd,'environmentRepository':recorded_repo,'gaps':list(dict.fromkeys(gaps)),
                'environmentReferences':list(dict.fromkeys(environment_refs)),
                'headerEvidence':header.get('headerEvidence'),'coverage':'bounded_recorded_context'}
        return result
    def _associate_arguments(self,args,p,eid,cwd,associate,gaps):
        directory=absolute(args.get('workdir') or args.get('cwd') or p.get('cwd'),cwd) or cwd
        command=str(args.get('cmd') or args.get('command') or '')[:16000]
        try:words=shlex.split(command)
        except ValueError:words=[]
        cds=[words[i+1] for i,w in enumerate(words[:-1]) if w=='cd']
        if len(cds)==1:directory=absolute(cds[0],directory)
        elif len(cds)>1:gaps.append('一个命令内切换多个目录，未自动确定默认项目');directory=None
        for i,w in enumerate(words[:-2]):
            if w=='git' and words[i+1]=='-C':associate(absolute(words[i+2],directory),eid)
        if re.search(r'(?:^|[;&\n])\s*git\s',command):associate(directory,eid)
        files=[args.get(k) for k in ('path','file_path','filePath') if args.get(k)]
        files+=command_paths(command)
        if words and words[0] in ('cat','sed','head','tail'):files.extend(w for w in words[1:] if re.search(r'\.[a-zA-Z0-9]{1,8}$',w))
        for value in files:
            target=absolute(value,directory)
            if not target:continue
            marker=basename(target) in ('package.json','pyproject.toml','Cargo.toml','go.mod','pom.xml')
            associate(str(Path(target).parent) if not re.match(r'^[a-z]:/',target) else ntpath.dirname(target),eid,target,marker)
