"""Product query service for aggregate and project-scoped local knowledge."""
from collections import Counter
import hashlib,json,re,sqlite3
from pathlib import Path
from .project_inventory import ProjectInventory,inventory_question,MARKERS

def matches_name(question,name):
    # Names come from the live catalogue, never a weather/video fixture.
    return bool(re.search(r'(?<![A-Za-z0-9_-])'+re.escape(name)+r'(?![A-Za-z0-9_-])',question,re.I)) if re.search(r'[a-zA-Z]',name) else name in question

def prepare_relations(root,source,tasks):
    """Prioritize pending compact relations for the project being queried."""
    if not tasks:return
    marks=','.join('?' for _ in tasks[:500])
    rows=source.execute('SELECT DISTINCT q.source,q.session FROM task_link_queue q JOIN task_groups g ON g.source=q.source AND g.session=q.session WHERE g.id IN ('+marks+') LIMIT 4',tasks[:500]).fetchall()
    if not rows:return
    from .task_lineage import rebuild_session
    with sqlite3.connect(Path(root)/'collector.db',timeout=.3) as writer:
        for provider,session in rows:
            count=writer.execute('SELECT count(*) FROM task_steps s JOIN tasks t ON t.id=s.task WHERE t.source=? AND t.session=?',(provider,session)).fetchone()[0]
            if count<=10000:
                try:rebuild_session(writer,provider,session)
                except sqlite3.OperationalError:break # ingestion has priority

def local_project_query(root,question,source='',previous=None,selected=None):
    previous=previous or {};root=Path(root)
    agent='workbuddy' if 'workbuddy' in question.lower() else 'codex' if 'codex' in question.lower() else source or ''
    with sqlite3.connect((root/'collector.db').resolve().as_uri()+'?mode=ro',uri=True,timeout=1) as db:
        path=root/'project_inventory.db'
        if not path.exists():return {'question':question,'projectChoices':[],'projectMessage':'项目知识正在准备中，采集记录仍保留在本机。'} if '项目' in question or re.search(r'\bprojects?\b',question,re.I) else None
        store=ProjectInventory(path,filesystem=False,read_only=True)
        try:
            snapshot=store.snapshot(db,agent)
            named=[p for p in snapshot['projects'] if p['state']!='excluded' and (matches_name(question,p['name']) or any(matches_name(question,Path(r).name) for r in p['roots']))]
            if selected:
                named=[p for p in snapshot['projects'] if p['id']==selected and p['state']!='excluded']
                if not named:return {'question':question,'projectChoices':[],'projectMessage':'项目归属已变化，请从项目总览重新选择。'}
            elif inventory_question(question) and not named:
                for item in snapshot['projects']:
                    tasks=item.get('taskIds',[])
                    latest=[]
                    for start in range(0,len(tasks),200):
                        batch=tasks[start:start+200];marks=','.join('?' for _ in batch)
                        latest.extend(db.execute(
                            'SELECT id,prompt,updated,last_row FROM task_groups WHERE id IN ('+marks+') ORDER BY updated DESC,last_row DESC LIMIT 2',batch))
                    item['latestTasks']=[{'taskId':r[0],'prompt':r[1],'updated':r[2]} for r in sorted(latest,key=lambda r:(r[2],r[3]),reverse=True)[:2]]
                return {'question':question,'projectInventory':snapshot,'queryKind':'project_inventory','engine':'sessionlens.local_projects.v1'}
            elif not named and previous.get('projectDetails') and (any(w in question for w in ('这个项目','该项目','它','里面','多少任务','哪些任务')) or re.search(r'\b(?:this project|that project|it|its tasks|how many tasks|which tasks|what tasks)\b',question,re.I)):
                named=[p for p in snapshot['projects'] if p['id']==previous['projectDetails']['id'] and p['state']!='excluded']
            summary_question=any(w in question for w in ('多少','几个','哪些任务','什么任务','内容','里面','模块','结构','开发情况','做了什么','做过什么','开发过程','项目介绍','项目概况','介绍一下','项目名称','职责','哪些工具','什么时候','什么项目','用来','干什么','做什么')) or bool(re.search(r'\b(?:how many|tasks?|components?|modules?|structure|overview|summary|summarize|tools?|when|purpose|tell me|worked on|contains?)\b',question,re.I))
            if not selected and not summary_question:return None
            if not named:
                if '项目' not in question and not re.search(r'\bprojects?\b',question,re.I):return None
                return {'question':question,'projectChoices':[],'projectMessage':'还没有找到这个项目。请补充项目名称，或在项目总览确认名称与目录。'}
            if len(named)>1:
                return {'question':question,'projectChoices':[{'id':p['id'],'name':p['name'],'source':p['source'],'roots':p['roots']} for p in named],
                        'projectMessage':'找到同名项目或多个 Agent 的开发记录，请选择具体项目。'}
            prepare_relations(root,db,named[0]['taskIds'])
            project=store.details(db,named[0]['id'],limit=100)
            members=[r[0] for r in store.db.execute('SELECT id,root FROM inventory_dirs WHERE source=?',(project['source'],)) if r[1] in project['roots']]
            slots=','.join('?' for _ in members)
            files=[r[0] for r in store.db.execute('SELECT DISTINCT path FROM inventory_writes WHERE project IN ('+slots+') AND is_source=1 ORDER BY path',members) if Path(r[0]).name not in MARKERS]
            languages=Counter(Path(f).suffix.lstrip('.') for f in files)
            components=Counter()
            for path in files:
                base=max((r for r in project['roots'] if path.startswith(r.rstrip('/')+'/')),key=len)
                rel=path[len(base)+1:];parts=rel.split('/')
                repeated=re.sub(r'[^a-z0-9]','',parts[0].casefold())==re.sub(r'[^a-z0-9]','',project['name'].casefold())
                if (parts[0] in ('src','internal','lib') or repeated) and len(parts)>2:key='/'.join(parts[:2])
                else:key=parts[0] if len(parts)>1 else '根目录文件'
                components[key]+=1
            project['components']=[{'name':name,'fileCount':count} for name,count in components.most_common()]
            project['languages']=dict(languages);project['files']=files[:200];project['shownFiles']=min(200,len(files))
            # Count every linked task rather than the 100 visible task rows.
            kinds=Counter();tools=Counter();task_ids=project['taskIds']
            for start in range(0,len(task_ids),200):
                batch=task_ids[start:start+200];marks=','.join('?' for _ in batch)
                for kind,excerpt,count in db.execute('SELECT kind,CASE WHEN kind=\'工具调用\' THEN substr(excerpt,1,instr(excerpt,\' · \')-1) ELSE \'\' END,count(*) FROM linked_task_steps WHERE task IN ('+marks+') GROUP BY 1,2',batch):
                    kinds[kind]+=count
                    if kind=='工具调用':tools[excerpt or '未命名工具']+=count
            project['recordKinds']=dict(kinds);project['tools']=[{'name':name,'count':count} for name,count in tools.most_common()]
            from .interactions import project_interactions,task_interactions
            project['interactions']=project_interactions(db,task_ids)
            for item in project['tasks']:item['interactions']=task_interactions(db,item['taskId'],metadata=False)
            project['coverage']='任务数来自已找到的源码修改关联，并按现有多轮需求归属去重；不是全量项目任务。目录组件按路径归类，不推断业务功能；未采集和仅 Shell 写文件尚未纳入。'
            revision=hashlib.sha256(json.dumps(project,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
            return {'question':question,'projectDetails':project,'queryKind':'project_detail','engine':'sessionlens.local_projects.v1','revision':revision,'projectIndexComplete':snapshot['complete']}
        finally:store.close()
