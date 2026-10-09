"""Local, reversible multi-turn task projection over immutable per-turn evidence.

Explicit continuations can be linked locally. Unclear references stay unresolved;
this projection never invents a final specification from conflicting user turns.
"""
import re,hashlib,json
from contextlib import nullcontext

VERSION = 1
CLASSIFIER_VERSION = 5
LABELS = {'request': '原始需求', 'discussion': '继续讨论', 'revision': '调整要求',
          'approval': '确认方案', 'execution': '开始执行', 'resume': '恢复任务',
          'unresolved': '所指任务待确认'}


def exists(db):
    return bool(db.execute("SELECT 1 FROM sqlite_master WHERE name='task_groups'").fetchone())


def task_table(db):
    return 'task_groups' if exists(db) else 'tasks'


def step_table(db):
    return 'linked_task_steps' if exists(db) else 'task_steps'


def resolve(db, task):
    if not exists(db): return task
    row = db.execute('SELECT root FROM task_links WHERE turn_task=?', (task,)).fetchone()
    return row[0] if row else task


def initialize(db):
    db.executescript('''
    CREATE INDEX IF NOT EXISTS tasks_session ON tasks(source,session,last_row);
    CREATE TABLE IF NOT EXISTS task_groups(
      id TEXT PRIMARY KEY,source TEXT,session TEXT,prompt TEXT,updated TEXT,
      state TEXT,last_row INTEGER,search TEXT,requirements TEXT,turn_count INTEGER);
    CREATE INDEX IF NOT EXISTS task_groups_recent ON task_groups(updated DESC,last_row DESC);
    CREATE INDEX IF NOT EXISTS task_groups_session ON task_groups(source,session);
    CREATE TABLE IF NOT EXISTS task_links(
      turn_task TEXT PRIMARY KEY,root TEXT,seq INTEGER,relation TEXT,reason TEXT);
    CREATE INDEX IF NOT EXISTS task_links_root ON task_links(root,seq);
    CREATE TABLE IF NOT EXISTS task_executions(
      event TEXT PRIMARY KEY,task TEXT,turn_event TEXT,requirement_event TEXT,approval_event TEXT,plan_event TEXT);
    CREATE INDEX IF NOT EXISTS task_execution_task ON task_executions(task);
    CREATE TABLE IF NOT EXISTS task_step_owners(event TEXT PRIMARY KEY,task TEXT,turn_event TEXT);
    CREATE INDEX IF NOT EXISTS task_step_owner_task ON task_step_owners(task);
    CREATE TABLE IF NOT EXISTS task_link_queue(
      source TEXT,session TEXT,updated INTEGER,PRIMARY KEY(source,session));
    CREATE TABLE IF NOT EXISTS task_link_meta(name TEXT PRIMARY KEY,value INTEGER);
    CREATE TABLE IF NOT EXISTS task_link_overrides(turn_task TEXT PRIMARY KEY,target_turn TEXT);
    CREATE TABLE IF NOT EXISTS task_semantic_links(turn_task TEXT PRIMARY KEY,parent_turn TEXT,relation TEXT,reason TEXT,signature TEXT,evidence_turns TEXT);
    CREATE TABLE IF NOT EXISTS task_semantic_cache(signature TEXT PRIMARY KEY,result TEXT);
    CREATE VIEW IF NOT EXISTS linked_task_steps AS
      SELECT s.event,o.task,s.seq,s.kind,s.excerpt,s.call_id,
             o.turn_event,x.requirement_event,x.approval_event,x.plan_event
      FROM task_step_owners o JOIN task_steps s ON s.event=o.event
      LEFT JOIN task_executions x ON x.event=s.event;
    ''')
    version = db.execute("SELECT value FROM task_link_meta WHERE name='version'").fetchone()
    if version and version[0] == VERSION:
        refresh_classifier(db);return
    with db:
        # No deletion/reindex of raw events, legacy projection, marks or chats.
        for table in ('task_groups', 'task_links', 'task_executions', 'task_link_queue','task_step_owners'):
            db.execute('DELETE FROM ' + table)
        db.execute('INSERT INTO task_groups SELECT id,source,session,prompt,updated,state,last_row,prompt,prompt,1 FROM tasks')
        db.execute('INSERT INTO task_step_owners SELECT event,task,task FROM task_steps')
        db.execute('INSERT INTO task_link_queue SELECT source,session,max(last_row) FROM tasks GROUP BY source,session')
        db.execute("INSERT OR REPLACE INTO task_link_meta VALUES('version',?)", (VERSION,))
    refresh_classifier(db)

def refresh_classifier(db):
    row=db.execute("SELECT value FROM task_link_meta WHERE name='classifier_version'").fetchone()
    if row and row[0]==CLASSIFIER_VERSION:return
    # Preserve existing groups while re-evaluating their compact relations.
    # Raw records, manual overrides and semantic reviews remain untouched.
    with db:
        db.execute('INSERT INTO task_link_queue SELECT source,session,max(last_row) FROM tasks GROUP BY source,session ON CONFLICT(source,session) DO UPDATE SET updated=max(updated,excluded.updated)')
        db.execute("INSERT OR REPLACE INTO task_link_meta VALUES('classifier_version',?)",(CLASSIFIER_VERSION,))


def enqueue(db, source, session, seq):
    db.execute('INSERT INTO task_link_queue VALUES(?,?,?) ON CONFLICT(source,session) DO UPDATE SET updated=max(updated,excluded.updated)', (source, session, seq))


def _compact(text):
    return re.sub(r'[\s，,。.!！?？；;：:]+', '', text).lower()


def _topics(text):
    # Remove conversational glue before comparing topics, never tool outputs.
    text = re.sub(r'(?i)workbuddy|codex|agent|请|帮我|然后|现在|之前|这个|那个|一下|任务|问题|继续|方案|怎么|如何|为什么|设计|执行|实现|开发|修改|生成|创建|查一下|查询|做一个', ' ', text)
    text = re.sub(r'\b(?:please|help|then|now|previous|this|that|task|question|continue|plan|how|why|design|execute|implement|develop|modify|generate|create|search|the|a|an|to|for|it|work|working|on)\b', ' ', text, flags=re.I)
    words = set(re.findall(r'[a-zA-Z][a-zA-Z0-9_.-]{2,}', text.lower()))
    for phrase in re.findall(r'[\u4e00-\u9fff]+', text):
        words.update(phrase[i:i+2] for i in range(len(phrase)-1))
    return words


def _topic_match(text, target):
    a, b = _topics(text), _topics(target)
    shared = a & b
    return bool(shared) and (any(re.search('[a-z]', s) for s in shared) or len(shared) >= 2)


def classify(text, active, roots):
    """Conservative reference resolution. Return a root, role and auditable reason."""
    clean = _compact(text)
    english = re.sub(r'[.!?]+$', '', text.strip().lower()).strip()
    execution = clean in {'做', '开始', '开始做', '做吧', '开发', '开始开发', '进入开发阶段',
                          '执行', '执行吧', '开始执行', '动手', '动手吧', '继续做', '继续推进',
                          '继续开发', '继续执行', '继续', '推进', '继续吧','干','开干','干吧','开干吧','动手做','开始干活','行干','好干','好的干','行开干','好开干'}
    execution=execution or bool(re.fullmatch(r'(?:做|干|开干|执行)(?:我)?(?:想)?(?:看|看看|看一下)(?:效果|结果)',clean))
    execution=execution or english in {'go ahead','do it','implement it','proceed','continue','continue working','start implementation','build it','please proceed','please do it'}
    approval = clean in {'好', '好的', '可以', '对', '对的', '是的', '嗯', '嗯嗯', 'ok',
                         '确认', '就这样', '这个版本可以', '就按这个', '按这个来', '同意','行','行吧','好吧','可以的','没问题','收到','批准'}
    approval=approval or english in {'yes','okay','looks good','approved','agreed','sounds good','that works','sure'}
    # A named resume is resolved among previous roots, before considering active.
    resume = re.match(r'^(?:回到|回头继续|接着做|继续(?:做|开发|修改)?)(.+)', clean)
    if not resume:resume=re.match(r'^(?:go back to|return to|resume|continue working on|continue with)\s+(.+)',english)
    if resume and not execution:
        matches = [r for r in roots if _topic_match(resume[1], r['requirements'])]
        if len(matches) == 1: return matches[0]['id'], 'resume', '用户明确恢复此前主题'
        if len(matches) > 1: return None, 'unresolved', '恢复指令对应多个此前任务'
    if execution or approval:
        if active: return active['id'], 'execution' if execution else 'approval', '承接同一会话当前方案的简短指令'
        return None, 'unresolved', '指令没有可追溯的前置需求'
    explicit_new = bool(re.match(r'^(?:另一个任务|新任务|另外|换个任务|先不做这个|先不做了|停止这个任务)', clean))
    explicit_new=explicit_new or bool(re.match(r'^(?:new task|another task|switch tasks|stop this task)\b',english))
    if explicit_new: return None, 'request', '用户明确切换任务'
    # Feedback and elliptical questions are not new goals merely because they
    # don't repeat the original subject. Require an existing active goal plus
    # a reference/feedback cue, and do not absorb an explicit fresh action.
    new_goal=bool(re.search(r'另一个(?:任务|需求|项目)|新任务|(?:创建|新建|生成|查询|查一下|帮我做一个|写一个)',clean))
    feedback=bool(re.search(r'页面呢$|界面呢$|结果呢$|效果呢$|看不懂|没(?:有)?(?:任何)?(?:一点)?价值|什么价值|乱七八糟|别做玩具|不能认(?:真|真一点)|没有考虑|你没有|去思考',clean))
    pointer=bool(re.search(r'这(?:个|些|一)[\u4e00-\u9fff]{0,12}(?:东西|页面|界面|需求|方案|任务|功能)|你(?:现在|目前|这次)|目前的|现在的|刚才|核心的东西',clean))
    elliptical=bool(re.fullmatch(r'(?:页面|界面|结果|效果)呢',clean))
    existing_revision=bool(re.search(r'把(?:现在|目前)|现有(?:界面|页面)|当前(?:界面|页面)',clean) and re.search(r'重新|重组|修改|调整',clean))
    if active and not new_goal and (elliptical or feedback and pointer or existing_revision):
        return active['id'],'revision' if existing_revision else 'discussion','同一会话中对现有方案的反馈或省略追问，未提出明确独立目标（初步关联）'
    reference = bool(re.match(r'^(?:严格)?(?:按(?:照)?(?:我们|刚才|上面|这|那|现有|之前)|就按|在此基础|在这个基础|这个版本|这一个版本|这个方案|这个任务|它|这次的|那次的|(?:解决|修复|处理)(?:这个|这一个|刚才的)问题)', clean))
    revision = bool(re.match(r'^(?:改成|改为|调整为|加上|增加|补上|补齐|支持|不要|不需要|只要|保留|去掉|删除|输入框|按钮|颜色|字体)', clean))
    discuss = bool(re.match(r'^(?:为什么(?:要|这样|这么)|这样(?:可以|会|能)|这个(?:可以|不行)|感觉(?:还是|不行)|不对|不认可)', clean))
    reference=reference or bool(re.match(r'^(?:follow|use|implement) (?:this|that|the previous) (?:plan|design|approach)\b',english))
    revision=revision or bool(re.match(r'^(?:change|adjust|replace|remove|keep|update) (?:this|that|the current)\b',english))
    discuss=discuss or bool(re.match(r'^(?:why (?:this|that) (?:change|approach|decision)|why did you do that|explain this (?:change|approach)|this (?:does not|doesn.t) work)\b',english))
    if active and (reference or revision or discuss):
        role = 'execution' if reference and (re.search(r'开发|开始做|进行开发|执行', clean) or english.startswith('implement ')) else 'revision' if revision or reference else 'discussion'
        return active['id'], role, '用户明确承接或修改当前方案'
    return None, 'request', '独立需求；没有足够的承接证据'


def _session_inputs(db, source, session):
    """Exact compact inputs, scoped to this stream rather than all DB writes."""
    scope = (source, session)
    return {
        'turns': [tuple(row) for row in db.execute('''SELECT t.id,t.prompt,t.updated,t.state,t.last_row,e.rowid
            FROM tasks t JOIN events e ON e.id=t.id WHERE t.source=? AND t.session=? ORDER BY e.rowid''', scope)],
        'steps': [tuple(row) for row in db.execute('''SELECT s.event,s.task,s.kind,s.call_id,s.seq
            FROM task_steps s JOIN tasks t ON t.id=s.task WHERE t.source=? AND t.session=? ORDER BY s.seq,s.event''', scope)],
        'overrides': [tuple(row) for row in db.execute('''SELECT o.turn_task,o.target_turn
            FROM task_link_overrides o JOIN tasks t ON t.id=o.turn_task
            WHERE t.source=? AND t.session=? ORDER BY o.turn_task''', scope)],
        'semantic': [tuple(row) for row in db.execute('''SELECT l.turn_task,l.parent_turn,l.relation,l.reason,l.signature,l.evidence_turns
            FROM task_semantic_links l JOIN tasks t ON t.id=l.turn_task
            WHERE t.source=? AND t.session=? ORDER BY l.turn_task''', scope)],
        'queue': db.execute('SELECT updated FROM task_link_queue WHERE source=? AND session=?', scope).fetchone(),
    }


def _session_projection(db, source, session):
    scope = (source, session)
    queries = {
        'task_links': '''SELECT l.turn_task,l.root,l.seq,l.relation,l.reason FROM task_links l
            JOIN tasks t ON t.id=l.turn_task WHERE t.source=? AND t.session=?''',
        'task_executions': '''SELECT x.event,x.task,x.turn_event,x.requirement_event,x.approval_event,x.plan_event
            FROM tasks t JOIN task_steps s ON s.task=t.id JOIN task_executions x ON x.event=s.event
            WHERE t.source=? AND t.session=?''',
        'task_step_owners': '''SELECT o.event,o.task,o.turn_event
            FROM tasks t JOIN task_steps s ON s.task=t.id JOIN task_step_owners o ON o.event=s.event
            WHERE t.source=? AND t.session=?''',
        'task_groups': 'SELECT id,source,session,prompt,updated,state,last_row,search,requirements,turn_count FROM task_groups WHERE source=? AND session=?',
    }
    return {table: {row[0]: tuple(row) for row in db.execute(query, scope)}
            for table, query in queries.items()}


def rebuild_session(db,source,session):
    # Normal repair releases its short read snapshot before classification.
    # A caller already in a transaction (semantic review) retains its atomic
    # input update and projection, rather than committing the caller's work.
    if not db.in_transaction:
        with db:
            db.execute('BEGIN')
            inputs = _session_inputs(db, source, session)
            previous = _session_projection(db, source, session)
    else:
        inputs = _session_inputs(db, source, session)
        previous = _session_projection(db, source, session)
    return _rebuild_session(db,source,session,inputs,previous)


def _projection_delta(previous, rows):
    current = {row[0]: row for row in rows}
    changed = [row for identity, row in current.items() if previous.get(identity) != row]
    return changed, previous.keys() - current.keys()


def _write_delta(db, table, columns, changed, removed):
    """Only changed/new tuples reach SQLite; comparison work stays outside lock."""
    key = columns[0]
    db.executemany('DELETE FROM '+table+' WHERE '+key+'=?', [(identity,) for identity in removed])
    updates = ','.join(column+'=excluded.'+column for column in columns[1:])
    differs = ' OR '.join(table+'.'+column+' IS NOT excluded.'+column for column in columns[1:])
    db.executemany('INSERT INTO '+table+'('+','.join(columns)+') VALUES('+','.join('?' for _ in columns)+
                   ') ON CONFLICT('+key+') DO UPDATE SET '+updates+' WHERE '+differs, changed)


def _rebuild_session(db, source, session, inputs=None, previous=None):
    """Only read compact derived turns/step metadata; no raw-body/history rescan."""
    if inputs is None:
        return rebuild_session(db, source, session)
    from .supervision import readable
    turns = inputs['turns']
    groups = {}; links = []; active = None; versions = {}; approvals = {}; snapshots = {}
    overrides=dict(inputs['overrides'])
    semantic={turn:(parent,role,reason) for turn,parent,role,reason,_,_ in inputs['semantic']}
    for identity, prompt, updated, state, last, seq in turns:
        if not readable(prompt, user=True) or prompt.startswith('The following is the Codex agent history'): continue
        # Retain all originals, including mirrored user events inside a turn.
        root, relation, reason = classify(prompt, active, list(groups.values())[-32:])
        if identity in semantic:
            parent,relation,reason=semantic[identity]
            root=snapshots[parent][0] if parent in snapshots else None
            if parent is not None and root is None:relation='unresolved'
            reason='模型语义关联：'+reason
        if identity in overrides:
            target=overrides[identity]
            if target is None:root,relation,reason=None,'request','用户在本机设为独立任务'
            elif target in snapshots:
                root=snapshots[target][0]
                _,relation,_=classify(prompt,groups[root],list(groups.values())[-32:])
                if relation in ('request','unresolved','resume'):relation='revision'
                reason='用户在本机修正任务关联'
        if not root:
            root = identity
            groups[root] = {'id':root, 'prompt':prompt, 'requirements':'', 'texts':[], 'updated':updated, 'state':state, 'last':last, 'count':0}
        group = groups[root]; group['count'] += 1
        if last >= group['last']: group.update(updated=updated, state=state, last=last)
        if relation!='execution':
            group['texts'].append(prompt)
            # Keep origin plus recent changes, rather than fill a task with "做".
            texts = group['texts'][:1] + group['texts'][-12:]
            group['requirements'] = '\n'.join(dict.fromkeys(texts))[:30000]
            if relation!='approval':versions[root] = identity
        if relation in ('approval', 'execution'): approvals[root] = identity
        elif relation in ('revision', 'resume', 'discussion'): approvals[root] = None
        snapshots[identity] = (root, versions.get(root, identity), approvals.get(root))
        links.append((identity, root, seq, relation, reason)); active = group
    steps = inputs['steps']
    bindings = []; calls = {}; owners=[];last_reply={};approved_plan={};seen_turns=set()
    relations={turn:role for turn,root,seq,role,reason in links}
    for identity, turn, kind, call, seq in steps:
        if turn not in snapshots:
            # Unreadable/background turns have no classified snapshot, but the
            # original turn-to-step mapping must remain addressable.
            owner = previous['task_step_owners'].get(identity)
            owners.append(owner if owner and owner[2] == turn else (identity,turn,turn))
            continue
        root, requirement, approval = snapshots[turn]
        if turn not in seen_turns:
            role=relations[turn]
            if role=='approval' or (role=='execution' and not approved_plan.get(root)):
                approved_plan[root]=last_reply.get(root)
            elif role not in ('approval','execution'):approved_plan[root]=None
            seen_turns.add(turn)
        if kind=='Agent 回复':last_reply[root]=identity
        if kind == '工具调用':
            if call: calls[call] = (root, turn, requirement, approval,approved_plan.get(root))
            bindings.append((identity, root, turn, requirement, approval,approved_plan.get(root)))
        elif kind == '工具返回' and call and call in calls:
            # Background completion can arrive after the user changes task.
            bindings.append((identity, *calls[call]))
            root=calls[call][0]
        owners.append((identity,root,turn))
        if seq>groups[root]['last']:
            # An asynchronous return belongs to its invocation, even when the
            # current user task has changed. Keep that task's revision fresh.
            from .supervision import timestamp
            updated=db.execute("SELECT json_extract(event,'$.timestamp') FROM events WHERE id=?",(identity,)).fetchone()
            groups[root].update(last=seq,updated=timestamp(updated[0]) if updated else groups[root]['updated'])
    projected = {
        'task_links': (('turn_task','root','seq','relation','reason'), links),
        'task_executions': (('event','task','turn_event','requirement_event','approval_event','plan_event'), bindings),
        'task_step_owners': (('event','task','turn_event'), owners),
        'task_groups': (('id','source','session','prompt','updated','state','last_row','search','requirements','turn_count'),
                        [(g['id'],source,session,g['prompt'],g['updated'],g['state'],g['last'],g['requirements'],g['requirements'],g['count']) for g in groups.values()]),
    }
    deltas = {table: (columns, *_projection_delta(previous[table], rows))
              for table, (columns, rows) in projected.items()}
    owns_write = not db.in_transaction
    with db if owns_write else nullcontext():
        if owns_write:db.execute('BEGIN IMMEDIATE')
        current = _session_inputs(db, source, session)
        if current != inputs or _session_projection(db, source, session) != previous:
            # The same session advanced or was manually/semantically reviewed
            # during calculation. Leave existing projections untouched and
            # preserve retry work even when an override did not advance seq.
            # Checking the projection baseline also catches input A -> B -> A
            # while another review has already replaced the old projection.
            seq = max([row[4] for row in current['turns']] + [row[4] for row in current['steps']] + [0])
            enqueue(db, source, session, seq)
            return 0
        for table, (columns, changed, removed) in deltas.items():
            _write_delta(db, table, columns, changed, removed)
        db.execute('DELETE FROM task_link_queue WHERE source=? AND session=?', (source,session))
    return len(links)


def repair(db, limit=1):
    rows = db.execute('SELECT source,session FROM task_link_queue ORDER BY updated DESC LIMIT ?', (limit,)).fetchall()
    for source, session in rows: rebuild_session(db, source, session)
    return len(rows)


def history(db, task):
    if not exists(db): return []
    task = resolve(db, task)
    return [{'eventId':event, 'seq':seq, 'kind':kind, 'label':LABELS[kind], 'text':text,
             'association':'confirmed_by_user' if reason.startswith('用户在本机') else 'semantic_inference' if reason.startswith('模型语义关联') else 'provisional', 'reason':reason}
            for event,seq,kind,reason,text in db.execute('SELECT l.turn_task,l.seq,l.relation,l.reason,t.prompt FROM task_links l JOIN tasks t ON t.id=l.turn_task WHERE l.root=? ORDER BY l.seq', (task,))]


def execution_map(db, task):
    if not exists(db): return {}
    return {e:{'turnEvent':turn,'requirementEvent':req,'approvalEvent':approval,'planEvent':plan}
            for e,turn,req,approval,plan in db.execute('SELECT event,turn_event,requirement_event,approval_event,plan_event FROM task_executions WHERE task=?', (resolve(db,task),))}


def dialogues(db,task,limit=None):
    """User rounds with original replies; never substitute reasoning for a reply."""
    result=[];turns=history(db,task);indexed=list(enumerate(turns,1))
    if limit and len(indexed)>limit:indexed=indexed[:1]+indexed[-(limit-1):] if limit>1 else indexed[:1]
    for ordinal,turn in indexed:
        replies=db.execute("SELECT event,substr(excerpt,1,600) FROM task_steps WHERE task=? AND kind='Agent 回复' ORDER BY seq LIMIT 1",(turn['eventId'],)).fetchall()
        last=db.execute("SELECT event,substr(excerpt,1,600) FROM task_steps WHERE task=? AND kind='Agent 回复' ORDER BY seq DESC LIMIT 1",(turn['eventId'],)).fetchone()
        if last and last not in replies:replies.append(last)
        interrupted=db.execute("SELECT 1 FROM tasks WHERE id=? AND state='已中止' UNION ALL SELECT 1 FROM task_steps WHERE task=? AND kind='Agent 回复' AND lower(trim(excerpt))='interrupted by user' LIMIT 1",(turn['eventId'],turn['eventId'])).fetchone()
        result.append({**turn,'ordinal':ordinal,'totalTurns':len(turns),'replies':[{'eventId':event,'text':text} for event,text in replies],
                       'interrupted':bool(interrupted)})
    return result


def set_override(db,turn,target):
    current=db.execute('SELECT t.source,t.session,e.rowid FROM tasks t JOIN events e ON e.id=t.id WHERE t.id=?',(turn,)).fetchone()
    if not current:raise ValueError('原始提问不存在')
    if target:
        other=db.execute('SELECT t.source,t.session,e.rowid FROM tasks t JOIN events e ON e.id=t.id WHERE t.id=?',(target,)).fetchone()
        if not other or other[:2]!=current[:2] or other[2]>=current[2]:raise ValueError('只能关联同一 Agent、同一会话中更早的任务')
    with db:db.execute('INSERT OR REPLACE INTO task_link_overrides VALUES(?,?)',(turn,target))
    rebuild_session(db,*current[:2])


def signature(db,task):
    if not exists(db):return None
    rows=db.execute('SELECT turn_task,relation,reason FROM task_links WHERE root=? ORDER BY seq',(resolve(db,task),)).fetchall()
    return hashlib.sha256(json.dumps(rows,ensure_ascii=False).encode()).hexdigest()
