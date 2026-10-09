"""One threat family, persistent findings and evidence-bound follow-up observations."""
import hashlib,hmac,json,re,secrets,time
from datetime import date
from .model_security import COMPILED,PLACEHOLDERS,segments,safe_destination
VERSION='input-threats-2026-10-02.6'
ACCOUNT_KINDS={'server_password','host_user_password','admin_password'}
CREDENTIAL_RULES={'private_key','api_key','cloud_key','credential','bearer','url_credential'}
ACCOUNT=re.compile(r'\b(root|admin|administrator|ubuntu)/([^\\\s/）)"\x27,;，；]{8,})',re.I)
LAB=re.compile(r'(?:当前\s*)?(?:Lab\s*Token|实验(?:环境)?(?:令牌|Token))\s*[:：=]\s*([^\\\s"\x27,;，；）)]+)',re.I)
CONTEXT=re.compile(r'主机|服务器|登录|凭据|后台|云|ssh|basic\s*auth|password',re.I)


GREETING=re.compile(r'^(?:applens\s*)?(?:你好|您好|hello(?:\s+world)?|hi|嗨)[！!。.?？\s]*$',re.I)
def background_hits(text,role,task):
    # A greeting is a narrow, auditable case: don't label technical task context irrelevant.
    if role!='system' or not task or not GREETING.fullmatch(task.strip()):return []
    m=re.search(r'工作背景(?:\*\*)?\s*',text)
    if not m:return []
    end=text.find('\n\n',m.end())
    end=end if end>=0 else len(text)
    section=text[m.end():end]
    if len(section)<200 or not re.search(r'项目|架构|服务器|产品|主线',section):return []
    return [(m.start(),end,'unrelated_background','unrelated_background')]


def candidates(text):
    found=[]
    for m in re.finditer(r'-----BEGIN ([A-Z ]*PRIVATE KEY)-----[\s\S]*?(?:-----END \1-----|$)',text):
        found.append((*m.span(),'private_key',m.group()))
    for rule,title,priority,pattern,_,_ in COMPILED:
        if rule not in CREDENTIAL_RULES or rule=='private_key':continue
        for m in pattern.finditer(text):
            start,end=m.span(1) if m.lastindex else m.span();value=text[start:end]
            if PLACEHOLDERS.fullmatch(value) or value.lower() in ('bearer','null','undefined'):continue
            found.append((start,end,rule,value))
    for m in ACCOUNT.finditer(text):
        value=m.group(2)
        if value.startswith(('.', '$','<')) or PLACEHOLDERS.fullmatch(value):continue
        if not CONTEXT.search(text[max(0,m.start()-500):m.start()]):continue
        kind='server_password' if m.group(1).lower()=='root' else 'host_user_password' if m.group(1).lower()=='ubuntu' else 'admin_password'
        found.append((*m.span(2),kind,value))
    for m in LAB.finditer(text):
        value=m.group(1)
        if len(value)<8 or PLACEHOLDERS.fullmatch(value) or value.startswith('$'):continue
        found.append((*m.span(1),'lab_token',value))
    # Same concrete range: prefer explicit account/Token interpretation over generic detector.
    explicit=[f for f in found if f[2] in ACCOUNT_KINDS|{'lab_token','private_key'}]
    unique={}
    for f in found:
        if f[2] not in ACCOUNT_KINDS|{'lab_token','private_key'} and any(f[0]<e[1] and e[0]<f[1] for e in explicit):continue
        unique[f[:2]]=f
    return list(unique.values())


def mask(text):
    from .interaction_audit import redact
    spans=sorted((a,b) for a,b,_,_ in candidates(text));merged=[]
    for a,b in spans:
        if merged and a<=merged[-1][1]:merged[-1][1]=max(b,merged[-1][1])
        else:merged.append([a,b])
    for a,b in reversed(merged):text=text[:a]+'[已隐藏]'+text[b:]
    return redact(text)


class CredentialThreats:
    def __init__(self,connect):
        self.connect=connect
        with connect() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS threat_workflows(finding_id TEXT PRIMARY KEY,data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS credential_meta(name TEXT PRIMARY KEY,value TEXT);
            CREATE TABLE IF NOT EXISTS credential_findings(id TEXT PRIMARY KEY,device_id TEXT,tag TEXT,kind TEXT,UNIQUE(device_id,tag));
            CREATE TABLE IF NOT EXISTS credential_captures(device_id TEXT,request_id TEXT,body_hash TEXT,version TEXT,captured REAL,received REAL,session TEXT,source TEXT,complete INTEGER,tags TEXT,PRIMARY KEY(device_id,request_id));
            CREATE TABLE IF NOT EXISTS credential_occurrences(finding_id TEXT,device_id TEXT,request_id TEXT,body_hash TEXT,data TEXT,PRIMARY KEY(finding_id,request_id,body_hash,data));
            CREATE TABLE IF NOT EXISTS credential_reviews(finding_id TEXT PRIMARY KEY,request_id TEXT,body_hash TEXT,task_id TEXT);
            CREATE TABLE IF NOT EXISTS credential_finding_aliases(alias TEXT PRIMARY KEY,canonical TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS credential_remediations(id INTEGER PRIMARY KEY AUTOINCREMENT,finding_id TEXT,actor TEXT,reported REAL,actions TEXT,baseline_sessions TEXT);
            ''')
            columns={r['name'] for r in db.execute('PRAGMA table_info(credential_captures)')}
            if 'scanned' not in columns:
                db.execute('ALTER TABLE credential_captures ADD COLUMN scanned REAL')
                db.execute('UPDATE credential_captures SET scanned=received')
            columns={r['name'] for r in db.execute('PRAGMA table_info(credential_reviews)')}
            for name,kind in [('evidence_revision','TEXT'),('linked','REAL')]:
                if name not in columns:db.execute('ALTER TABLE credential_reviews ADD COLUMN '+name+' '+kind)
            db.execute('INSERT OR IGNORE INTO credential_meta VALUES(?,?)',('hmac_key',secrets.token_hex(32)))

    @staticmethod
    def _occurrences(db,fid):
        return db.execute('SELECT o.*,c.source,c.captured,c.session,c.received,c.scanned,coalesce(m.received,c.received) AS last_received FROM credential_occurrences o JOIN credential_captures c ON c.device_id=o.device_id AND c.request_id=o.request_id AND c.body_hash=o.body_hash LEFT JOIN applens_model_context m ON m.device_id=c.device_id AND m.request_id=c.request_id WHERE o.finding_id=? ORDER BY c.captured DESC,o.request_id,o.data',(fid,)).fetchall()

    @staticmethod
    def _revision(occurrences):
        # Bind every current occurrence, including evidence outside the display sample.
        # Device-derived IDs are excluded so an explicit device merge can retain a
        # review only when the actual evidence set has stayed exactly the same.
        hashes=[]
        for o in occurrences:
            row={'requestId':o['request_id'],'bodySHA256':o['body_hash'],'data':json.loads(o['data']),
                 'source':o['source'],'captured':o['captured'],'session':o['session']}
            hashes.append(hashlib.sha256(json.dumps(row,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).digest())
        digest=hashlib.sha256(VERSION.encode())
        for value in sorted(hashes):digest.update(value)
        return digest.hexdigest()

    @staticmethod
    def _canonical_finding(db,fid):
        row=db.execute('SELECT canonical FROM credential_finding_aliases WHERE alias=?',(fid,)).fetchone()
        return row['canonical'] if row else fid

    def scan(self,db,device_id,r,received,scanned=None,refresh_cached=True):
        raw=r['body'];digest=hashlib.sha256(raw.encode()).hexdigest()
        scanned=received if scanned is None else scanned
        old=db.execute('SELECT body_hash,version FROM credential_captures WHERE device_id=? AND request_id=?',(device_id,r['id'])).fetchone()
        if old and old['body_hash']==digest and old['version']==VERSION:
            # Checking a cached detection is still a local check; preserve the
            # original receipt used for remediation verification and replay safety.
            if refresh_cached:db.execute('UPDATE credential_captures SET scanned=max(coalesce(scanned,0),?) WHERE device_id=? AND request_id=?',(scanned,device_id,r['id']))
            return
        key=bytes.fromhex(db.execute("SELECT value FROM credential_meta WHERE name='hmac_key'").fetchone()[0])
        try:body=json.loads(raw);values=list(segments(body));parsed=True
        except (ValueError,RecursionError):body=None;values=[('',raw,None)];parsed=False
        task=None
        for pointer,text,role in reversed(values):
            if role=='user':
                query=re.findall(r'<user_query>([\s\S]*?)</user_query>',text)
                if query:task=mask(query[-1])[:800];break
        db.execute('DELETE FROM credential_occurrences WHERE device_id=? AND request_id=?',(device_id,r['id']))
        tags=set()
        if task and GREETING.fullmatch(task.strip()):tags.add("__background_eligible__")
        for pointer,text,role in values:
            hits=candidates(text)+background_hits(text,role,task)
            # Explicit JSON secret fields as well as natural-language background values.
            field=pointer.rsplit('/',1)[-1].lower()
            if not hits and re.fullmatch(r'(?:.*[_-])?(?:password|passwd|api_key|secret_key|access_token|refresh_token|token)',field) and len(text)>=8 and not PLACEHOLDERS.fullmatch(text) and not text.startswith(('$','<')):
                hits=[(0,len(text),'credential',text)]
            for start,end,kind,value in hits:
                tag=hmac.new(key,(device_id+'\0'+value+('\0user_message' if role=='user' else '')).encode(),hashlib.sha256).hexdigest();tags.add(tag)
                fid=hashlib.sha256((device_id+tag).encode()).hexdigest()[:24]
                db.execute('INSERT OR IGNORE INTO credential_findings VALUES(?,?,?,?)',(fid,device_id,tag,kind))
                # Mask complete nearby text before slicing; don't leak partial neighbouring credentials.
                def safe_window(a,b):
                    window=text[a:b]
                    for left,right,hit_kind,_ in sorted(hits,reverse=True):
                        if hit_kind=='unrelated_background':continue
                        if left<b and right>a:
                            window=window[:max(0,left-a)]+'[已隐藏]'+window[min(b,right)-a:]
                    from .interaction_audit import redact
                    return redact(window)
                before=safe_window(max(0,start-400),start);after=safe_window(end,min(len(text),end+120))
                preview=mask(text[start:end])[:1600] if kind=='unrelated_background' else before+' [凭据已隐藏] '+after
                data={'pointer':pointer or None,'start':start,'end':end,'role':role,'kind':kind,'preview':preview,
                      'offsetBasis':'decoded_json_string' if parsed else 'raw_body','task':task,'taskBasis':'user_query 边界' if task else None,
                      'destination':safe_destination(r.get('destination')),'sessionId':r.get('sessionId') or 'unknown','sessionName':mask(r.get('sessionName') or '会话名称未采集'),
                      'credentialDisplay':(value[:-4]+'****') if kind in ACCOUNT_KINDS else None,
                      'account':(re.search(r'(root|admin|administrator|ubuntu)/$',text[max(0,start-25):start],re.I).group(1) if kind in ACCOUNT_KINDS else None)}
                db.execute('INSERT OR IGNORE INTO credential_occurrences VALUES(?,?,?,?,?)',(fid,device_id,r['id'],digest,json.dumps(data,ensure_ascii=False)))
        complete=parsed and any(role in ('system','developer','user','assistant','tool') and text.strip() for _,text,role in values) and not r.get('truncated') and bool(r.get('sessionId')) and r.get('sessionId')!='unknown'
        db.execute('INSERT OR REPLACE INTO credential_captures(device_id,request_id,body_hash,version,captured,received,session,source,complete,tags,scanned) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(device_id,r['id'],digest,VERSION,r.get('timestamp',0),received,r.get('sessionId') or 'unknown',r['source'],int(complete),json.dumps(sorted(tags)),scanned))

    def backfill(self):
        with self.connect() as db:
            checked=time.time()
            for row in db.execute('SELECT device_id,data,received FROM applens_model_context').fetchall():self.scan(db,row['device_id'],json.loads(row['data']),row['received'],scanned=checked,refresh_cached=False)
            # Repair registrations merged by older builds, which moved raw data
            # but left device-bound findings and handling records behind.
            for row in db.execute('SELECT alias,canonical FROM device_aliases').fetchall():self.merge_device(db,row['canonical'],row['alias'],rebuild=False)

    def report(self,owner,fid,actions):
        allowed={'cleanedContext','disabledMemory','rotatedCredential'}
        if not isinstance(actions,dict) or not actions or any(k not in allowed or not isinstance(v,bool) for k,v in actions.items()) or not any(actions.values()):raise ValueError('请选择已完成的处理事项')
        with self.connect() as db:
            fid=self._canonical_finding(db,fid)
            row=db.execute('SELECT f.device_id,f.kind FROM credential_findings f JOIN devices d ON d.id=f.device_id WHERE f.id=? AND d.owner=? AND d.revoked=0',(fid,owner)).fetchone()
            if not row:raise PermissionError('只能记录本人设备的处理情况')
            if row['kind']=='unrelated_background' and actions.get('rotatedCredential'):raise ValueError('这项发现不需要记录凭据更换')
            sessions=[r[0] for r in db.execute('SELECT DISTINCT session FROM credential_captures WHERE device_id=?',(row['device_id'],))]
            db.execute('INSERT INTO credential_remediations(finding_id,actor,reported,actions,baseline_sessions) VALUES(?,?,?,?,?)',(fid,owner,time.time(),json.dumps(actions),json.dumps(sessions)))
        return {'recorded':True,'message':'已记录你的处理情况，等待新的会话验证。'}

    def workflow(self,fid,verification):
        with self.connect() as db:
            row=db.execute('SELECT data FROM threat_workflows WHERE finding_id=?',(fid,)).fetchone()
            w=json.loads(row[0]) if row else {'state':'unassigned','events':[]}
            if w['state'] in ('closed','pending_verification') and verification['state']=='reappeared':
                w['state']='reopened';w['events'].append({'at':time.time(),'actor':'system','action':'新会话再次出现，重新打开'})
                db.execute('INSERT OR REPLACE INTO threat_workflows VALUES(?,?)',(fid,json.dumps(w,ensure_ascii=False)))
            w['overdue']=bool(w.get('due') and w['due']<date.today().isoformat() and w['state']!='closed')
            return w

    def manage(self,actor,fid,action,data,admin=False):
        with self.connect() as db:
            fid=self._canonical_finding(db,fid)
            row=db.execute('SELECT f.kind,d.owner FROM credential_findings f JOIN devices d ON d.id=f.device_id WHERE f.id=? AND d.revoked=0',(fid,)).fetchone()
        if not row or not (admin or row['owner']==actor):raise PermissionError('仅安全管理员或资产所属账号可处置')
        finding=next((f for f in self.inventory()['items'] if f['id']==fid),None)
        if not finding:raise ValueError('当前发现无对应证据')
        w=dict(finding['workflow']);w.pop('overdue',None)
        if action=='assign':
            owner=data.get('assignee');due=data.get('due')
            if not isinstance(owner,str) or not owner.strip() or len(owner)>100:raise ValueError('请填写负责人')
            try:date.fromisoformat(due)
            except (TypeError,ValueError):raise ValueError('请填写有效完成日期')
            if w['state']=='closed':raise ValueError('已关闭发现不能直接重新分派')
            w.update(state='in_progress',assignee=owner.strip(),due=due)
            note='分派给 '+owner.strip()+'，要求完成 '+due
        elif action=='submit':
            if w['state'] not in ('in_progress','reopened'):raise ValueError('请先分派这项发现')
            note=data.get('note')
            if not isinstance(note,str) or not note.strip() or len(note)>2000:raise ValueError('请填写处理结果（最多2000字）')
            actions=data.get('actions')
            self.report(row['owner'],fid,actions)
            w.update(state='pending_verification',treatment=mask(note),submittedAt=time.time())
            note='提交处理结果：'+mask(note)
        elif action=='close':
            if w['state']!='pending_verification' or finding['verification']['state']!='not_observed':raise ValueError('尚未获得合格的新采集验证，不能关闭')
            if row['kind']!='unrelated_background' and data.get('credentialRevokedConfirmed') is not True:raise ValueError('需另行确认旧凭据已更换或撤销')
            note=data.get('note')
            if not isinstance(note,str) or not note.strip() or len(note)>2000:raise ValueError('请填写关闭理由')
            w.update(state='closed',closedAt=time.time(),credentialRevokedConfirmed=data.get('credentialRevokedConfirmed') is True)
            note='关闭：'+mask(note)
        else:raise ValueError('未知处置动作')
        w.setdefault('events',[]).append({'at':time.time(),'actor':actor,'action':note})
        with self.connect() as db:db.execute('INSERT OR REPLACE INTO threat_workflows VALUES(?,?)',(fid,json.dumps(w,ensure_ascii=False)))
        return {'recorded':True,'workflow':w}

    def review_packet(self,owner,fid):
        with self.connect() as db:
            fid=self._canonical_finding(db,fid)
            owned=db.execute('SELECT f.id FROM credential_findings f JOIN devices d ON d.id=f.device_id WHERE f.id=? AND d.owner=? AND d.revoked=0',(fid,owner)).fetchone()
        if not owned:raise PermissionError('只能复核本人设备的发现')
        finding=next((f for f in self.inventory()['items'] if f['id']==fid),None)
        if not finding:raise ValueError('当前发现已无对应证据')
        evidence=finding['evidence'];primary=evidence[0]
        fragments=[{'evidenceId':'C'+str(i+1).zfill(3),'preview':e['preview'],'role':e['role'],'jsonPointer':e['pointer'],
                    'charStart':e['start'],'charEnd':e['end'],'offsetBasis':e['offsetBasis'],'source':e['source'],
                    'requestId':e['requestId'],'bodySHA256':e['bodySHA256'],'candidateOnly':True} for i,e in enumerate(evidence)]
        if primary.get('task'):fragments.append({'evidenceId':'T001','preview':primary['task'],'role':'user','basis':primary['taskBasis']})
        return {'tool':'applens_security','schemaVersion':1,'version':VERSION,'findingId':fid,'evidenceRevision':finding['evidenceRevision'],'request':{'id':primary['requestId'],'deviceId':finding['deviceId'],'bodySHA256':primary['bodySHA256']},
                'credentialType':finding['kind'],'fragments':fragments,
                'directions':[{'id':'unrelated_background','title':'问候中携带项目背景','question':'对比问候任务和系统工作背景，复核是否包含任务不需要的其他项目信息。不能推断未经授权、服务端保存或数据滥用。'}] if finding['kind']=='unrelated_background' else [{'id':'credential_input','title':'凭据进入输入','question':'仅复核这一项：是具体凭据的记录、示例、代码变量还是误报？来自什么消息，发送证据到哪一步？不能核验有效性或具体来源文件。'}],
                'coverage':{'sampled':True,'fullBodySemanticReview':False,'credentialsMasked':True,'credentialsValid':False,'recipientAcceptance':False,'independentExecution':False,'authorization':False},
                'limitations':['仅有遮蔽后的附近片段；不能确认凭据有效或是否未经授权。','应用记录不证明网络发送，捕获请求不证明服务端保存。']}

    def link_review(self,fid,packet,task_id):
        with self.connect() as db:
            fid=self._canonical_finding(db,fid)
            r=packet['request'];db.execute('INSERT OR REPLACE INTO credential_reviews(finding_id,request_id,body_hash,task_id,evidence_revision,linked) VALUES(?,?,?,?,?,?)',(fid,r['id'],r['bodySHA256'],task_id,packet.get('evidenceRevision'),time.time()))

    def review(self,fid,engine):
        with self.connect() as db:
            fid=self._canonical_finding(db,fid)
            row=db.execute('SELECT * FROM credential_reviews WHERE finding_id=?',(fid,)).fetchone()
            current=self._revision(self._occurrences(db,fid))
        if not row:return None
        try:task=engine.get(row['task_id'])
        except KeyError:
            return {'taskId':row['task_id'],'status':'unavailable','answer':None,'findings':[],'evidenceValidated':False,
                    'evidenceRevision':row['evidence_revision'],'currentEvidenceRevision':current,'stale':True,
                    'staleReason':'review_task_unavailable','requestedAt':row['linked']}
        reviews=[m['answer'] for m in task['messages'] if m.get('stage')=='review' and m.get('answer',{}).get('securityEvidenceValidated')]
        answer=reviews[-1] if reviews else {}
        stale=not row['evidence_revision'] or row['evidence_revision']!=current
        return {'taskId':task['id'],'status':task['status'],'answer':answer.get('finalAnswer'),'findings':answer.get('findings',[]),'evidenceValidated':bool(answer),
                'evidenceRevision':row['evidence_revision'],'currentEvidenceRevision':current,'stale':stale,
                'staleReason':('legacy_evidence_version_unknown' if not row['evidence_revision'] else 'evidence_changed') if stale else None,'requestedAt':row['linked']}

    def merge_device(self,db,canonical,alias,rebuild=True):
        """Rebuild device-bound detections and carry over evidence-valid decisions.

        Called inside the same transaction that copies the original contexts.
        Historical alias reviews remain archived; the canonical view can reuse a
        review only if its complete evidence revision still matches after merging.
        """
        old_findings=db.execute('SELECT f.id FROM credential_findings f WHERE f.device_id=? AND NOT EXISTS (SELECT 1 FROM credential_finding_aliases a JOIN credential_findings target ON target.id=a.canonical WHERE a.alias=f.id AND target.device_id=?)',(alias,canonical)).fetchall()
        if rebuild:
            for row in db.execute('SELECT data,received FROM applens_model_context WHERE device_id=?',(canonical,)).fetchall():
                self.scan(db,canonical,json.loads(row['data']),row['received'],scanned=time.time(),refresh_cached=False)
        def position(data):
            value=json.loads(data)
            return tuple(value.get(k) for k in ('pointer','start','end','role','kind'))
        for finding in old_findings:
            fid=finding['id'];targets=set()
            for occurrence in self._occurrences(db,fid):
                for new in db.execute('SELECT finding_id,data FROM credential_occurrences WHERE device_id=? AND request_id=? AND body_hash=?',(canonical,occurrence['request_id'],occurrence['body_hash'])):
                    if position(new['data'])==position(occurrence['data']):targets.add(new['finding_id'])
            if len(targets)!=1:continue  # A removed/changed detector is not a valid migration.
            target=targets.pop()
            db.execute('UPDATE credential_finding_aliases SET canonical=? WHERE canonical=?',(target,fid))
            db.execute('INSERT OR REPLACE INTO credential_finding_aliases VALUES(?,?)',(fid,target))
            prior=db.execute('SELECT * FROM credential_reviews WHERE finding_id=?',(fid,)).fetchone()
            current=db.execute('SELECT * FROM credential_reviews WHERE finding_id=?',(target,)).fetchone()
            revision=self._revision(self._occurrences(db,target))
            # Prefer an exact current review; otherwise retain the most recent
            # available historical review with its original version (hence stale).
            if prior and (not current or (current['evidence_revision']!=revision and
                    (prior['evidence_revision']==revision or (prior['linked'] or 0)>(current['linked'] or 0)))):
                db.execute('INSERT OR REPLACE INTO credential_reviews(finding_id,request_id,body_hash,task_id,evidence_revision,linked) VALUES(?,?,?,?,?,?)',
                           (target,prior['request_id'],prior['body_hash'],prior['task_id'],prior['evidence_revision'],prior['linked']))
            old_workflow=db.execute('SELECT data FROM threat_workflows WHERE finding_id=?',(fid,)).fetchone()
            current_workflow=db.execute('SELECT data FROM threat_workflows WHERE finding_id=?',(target,)).fetchone()
            if old_workflow:
                old=json.loads(old_workflow['data']);new=json.loads(current_workflow['data']) if current_workflow else None
                if new:
                    def touched(w):return max([w.get(k) or 0 for k in ('submittedAt','closedAt')]+[e.get('at') or 0 for e in w.get('events',[])])
                    combined=dict(old if touched(old)>touched(new) else new)
                    events={json.dumps(e,ensure_ascii=False,sort_keys=True):e for e in new.get('events',[])+old.get('events',[])}
                    combined['events']=sorted(events.values(),key=lambda e:e.get('at') or 0)
                else:combined=old
                db.execute('INSERT OR REPLACE INTO threat_workflows VALUES(?,?)',(target,json.dumps(combined,ensure_ascii=False)))
            db.execute('UPDATE credential_remediations SET finding_id=? WHERE finding_id=?',(target,fid))

    def inventory(self,device_id=None):
        with self.connect() as db:
            if device_id:
                alias=db.execute('SELECT canonical FROM device_aliases WHERE alias=?',(device_id,)).fetchone()
                if alias:device_id=alias['canonical']
            scope='d.revoked=0 AND NOT EXISTS (SELECT 1 FROM device_aliases a WHERE a.alias=d.id) AND (? IS NULL OR d.id=?)'
            received=db.execute('SELECT max(c.received) AS latest,count(*) AS records FROM applens_model_context c JOIN devices d ON d.id=c.device_id WHERE '+scope,(device_id,device_id)).fetchone()
            scanned=db.execute('SELECT max(c.scanned) AS latest,count(*) AS records FROM credential_captures c JOIN devices d ON d.id=c.device_id WHERE '+scope+' AND c.version=?',(device_id,device_id,VERSION)).fetchone()
            rows=db.execute('SELECT f.*,d.name,d.owner FROM credential_findings f JOIN devices d ON d.id=f.device_id WHERE d.revoked=0 AND NOT EXISTS (SELECT 1 FROM device_aliases a WHERE a.alias=d.id) AND (? IS NULL OR f.device_id=?)',(device_id,device_id)).fetchall()
            items=[]
            for row in rows:
                occurrences=self._occurrences(db,row['id'])
                if not occurrences:continue
                network={o['request_id'] for o in occurrences if o['source']=='workbuddy_network_context'};recorded={o['request_id'] for o in occurrences if o['source']!='workbuddy_network_context'}
                remediation=db.execute('SELECT * FROM credential_remediations WHERE finding_id=? ORDER BY id DESC LIMIT 1',(row['id'],)).fetchone()
                verification={'state':'waiting_action','checkedRecords':0,'reappearedRecords':0,'excludedRecords':0}
                if remediation:
                    baseline=set(json.loads(remediation['baseline_sessions']));checked=0;reappeared=0;excluded=0
                    for c in db.execute('SELECT * FROM credential_captures WHERE device_id=? AND received>?',(row['device_id'],remediation['reported'])):
                        if not c['complete'] or c['captured']<=remediation['reported'] or c['captured']>c['received']+300 or c['session'] in baseline:excluded+=1;continue
                        if row['kind']=='unrelated_background' and '__background_eligible__' not in json.loads(c['tags']):excluded+=1;continue
                        checked+=1;reappeared+=int(row['tag'] in json.loads(c['tags']))
                    verification={'state':'reappeared' if reappeared else 'not_observed' if checked else 'waiting_capture','checkedRecords':checked,'reappearedRecords':reappeared,'excludedRecords':excluded,'reportedAt':remediation['reported'],'actions':json.loads(remediation['actions'])}
                evidence=[]
                seen=set();shown_sources=set()
                # Captured request first, then recorded context; never hide differing evidence strength.
                ordered=sorted(occurrences,key=lambda o:o['source']!='workbuddy_network_context')
                for o in ordered:
                    data=json.loads(o['data']);identity=(o['request_id'],data['pointer'],data['start'],data['end'])
                    if identity in seen:continue
                    seen.add(identity)
                    if o['source']!='workbuddy_network_context' and o['source'] in shown_sources:continue
                    shown_sources.add(o['source'])
                    if len(evidence)<12:evidence.append({**data,'requestId':o['request_id'],'timestamp':o['captured'],'source':o['source'],'bodySHA256':o['body_hash']})
                kinds={json.loads(o['data'])['kind'] for o in occurrences};kind=next((k for k in ('server_password','host_user_password','admin_password','lab_token','private_key') if k in kinds),row['kind'])
                label={'unrelated_background':'问候中携带其他项目背景','server_password':'服务器 root 登录密码','host_user_password':'服务器 Ubuntu 登录密码','admin_password':'admin 登录密码','lab_token':'实验环境 Token','private_key':'私钥','cloud_key':'云访问密钥标识'}.get(kind,'密钥或令牌')
                items.append({'id':row['id'],'deviceId':row['device_id'],'deviceName':row['name'],'owner':row['owner'],'kind':kind,'label':label,
                              'networkRecords':len(network),'contextRecords':len(recorded),'evidence':evidence,'verification':verification,
                              'evidenceRevision':self._revision(occurrences),'lastReceivedAt':max(o['last_received'] for o in occurrences),'lastScannedAt':max(o['scanned'] or o['received'] for o in occurrences),
                              'aliases':[r['alias'] for r in db.execute('SELECT alias FROM credential_finding_aliases WHERE canonical=?',(row['id'],))],
                              'workflow':self.workflow(row['id'],verification),'firstSeen':min(o['captured'] for o in occurrences),'lastSeen':max(o['captured'] for o in occurrences),'reviewState':'needs_review'})
            items.sort(key=lambda i:(not bool(i['networkRecords']),-i['lastSeen']))
            revision=hashlib.sha256(json.dumps(sorted((i['id'],i['evidenceRevision']) for i in items)).encode()).hexdigest()
            return {'items':items,'detectorVersion':VERSION,'coverage':{'automaticOnIngest':True,'collector':'applens','timeBasis':'server_received',
                    'lastReceivedAt':received['latest'],'lastScannedAt':scanned['latest'],'receivedRecords':received['records'],'scannedRecords':scanned['records'],'evidenceRevision':revision,
                    'credentialsValid':False,'recipientAcceptance':False,'sourceFileLocated':False,'externalModelReview':False}}
