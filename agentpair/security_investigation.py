"""Evidence packets for task/data comparison; hypotheses never imply compromise."""
import hashlib
import json

VERSION = '2026-10-02.1'
DIRECTIONS = [
    ('task_scope', '任务与数据范围', '比较任务目标与附加内容，说明关联依据、无关可能及反例。'),
    ('sensitive_data', '敏感资料进入输入', '复核凭据、个人信息、业务秘密；区分示例、引用与真实候选。'),
    ('instruction_origin', '不可信内容改变指令', '分析消息来源和指令冲突；文本出现不等于注入成功。'),
    ('history_reuse', '历史上下文持续携带', '识别历史内容是否继续进入当前请求；不能推断其他会话或跨用户泄露。'),
    ('tool_boundary', '任务与工具能力边界', '比较任务要求、工具定义和历史调用；工具可用或被请求不等于已执行。'),
    ('destination', '数据去向与发送状态', '区分应用记录、捕获请求、接收回执；缺少授权策略不能判断违规。'),
]


def build_investigation(audit):
    r=audit['request']; locations=[]
    for event in audit['events']:
        for f in event['findings']: locations.append(f)
    locations.extend(audit['unassignedFindings'])
    groups={}
    for f in locations:
        # Different detectors and repeated content are not separate incidents.
        key='credential_material' if f['ruleId'] in ('private_key','api_key','cloud_key','credential','bearer','url_credential') else f['ruleId']
        groups.setdefault(key,[]).append(f)
    issues=[]
    for rule,items in groups.items():
        first=items[0]
        unique={}
        for item in items:
            unique.setdefault((item['jsonPointer'],item['charStart'],item['charEnd']),item)
        positioned=list(unique.values())
        issues.append({'id':hashlib.sha256((r['bodySHA256']+rule).encode()).hexdigest()[:24],
            'title':('凭据材料候选' if rule=='credential_material' else first['title'])+'出现在'+('捕获请求' if r['evidenceState']=='captured_request' else '应用上下文'),
            'priority':first['priority'],'status':'needs_review','origin':'local_rule',
            'fact':'已保存正文中有 '+str(len(positioned))+' 处已定位候选。真实性、授权和接收状态尚未确定。',
            'hypothesis':'若为真实敏感资料且超出任务授权范围，可能造成不必要的数据暴露。' if rule not in ('instruction_override','secret_extraction') else '若内容来自不可信来源并改变后续行为，可能构成提示注入。',
            'alternative':'示例、历史引用、测试内容或任务所需信息也可能触发，需结合上下文复核。',
            'evidenceRefs':[f['id'] for f in items],'locatedOccurrences':len(positioned),
            'locations':[{'pointer':f['jsonPointer'],'start':f['charStart'],'end':f['charEnd'],'preview':f['contextPreview']} for f in positioned[:3]],
            'nextAction':first['recommendation']})
    issues.sort(key=lambda f:({'high':0,'medium':1,'low':2}[f['priority']],f['title']))
    return {'version':VERSION,'issues':issues,'directions':[{'id':i,'title':t,'question':q,'status':'待语义调查'} for i,t,q in DIRECTIONS],
            'facts':['本快照保存 '+str(r['bodyBytes'])+' 字节，包含 '+str(len(audit['events']))+' 条消息。',
                     '已捕获客户端请求正文；未取得接收回执。' if r['evidenceState']=='captured_request' else '仅有应用上下文记录；网络发送未证实。'],
            'gaps':['任务授权与数据分级策略未提供。','非模型接口上传、文件读取和打包行为未采集。','当前模型响应与独立执行记录未采集。'],
            'semanticReview':'not_run','incidentCount':None}


def semantic_packet(audit):
    """Bounded redacted content plus stable locations; not a full-body audit."""
    r=audit['request']; fragments=[];remaining=16000-len((audit.get('task') or {}).get('text',''))
    for e in audit['events']:
        text=e['content']['text']
        if not text or remaining<=0:continue
        excerpt=text[:min(1400,remaining)];remaining-=len(excerpt)
        fragments.append({'evidenceId':'E'+str(e['index']+1).zfill(3),'jsonPointer':e['pointer'],
                          'role':e['role'],'preview':excerpt,'previewTruncated':e['content']['displayTruncated'] or len(excerpt)<len(text),
                          'sourceVerified':False,'executionVerified':False})
    if not fragments and audit.get('unparsed'):
        excerpt=audit['unparsed']['text'][:remaining];remaining-=len(excerpt)
        fragments.append({'evidenceId':'Eraw','jsonPointer':None,'preview':excerpt,'previewTruncated':audit['unparsed']['displayTruncated'],'structureVerified':False})
    # Preserve rule-hit contexts even when general message previews omit their position.
    for f in [f for e in audit['events'] for f in e['findings']]+audit['unassignedFindings']:
        if remaining<=0:break
        excerpt=f['contextPreview'][:remaining];remaining-=len(excerpt)
        fragments.append({'evidenceId':f['id'],'jsonPointer':f['jsonPointer'],'charStart':f['charStart'],'charEnd':f['charEnd'],
                          'offsetBasis':f['offsetBasis'],'preview':excerpt,'ruleId':f['ruleId'],'candidateOnly':True})
    return {'tool':'applens_security','schemaVersion':1,'version':VERSION,'request':r,'task':audit['task'],
            'taskBasis':audit['taskBasis'],'requestOverview':{**audit['requestOverview'],'toolNames':audit['requestOverview']['toolNames'][:40],
            'toolsOmitted':max(0,len(audit['requestOverview']['toolNames'])-40),
            'parameters':{k:v[:250] if isinstance(v,str) else v for k,v in audit['requestOverview']['parameters'].items()}},'fragments':fragments,
            'directions':[{'id':i,'title':t,'question':q} for i,t,q in DIRECTIONS],
            'coverage':{'sampled':True,'fullBodySemanticReview':False,'totalMessages':len(audit['events']),
                        'includedMessagePreviews':sum(f['evidenceId'].startswith('E') for f in fragments),
                        'currentResponse':False,'independentExecution':False,'authorization':False,'recipientAcceptance':False},
            'limitations':audit['limitations']+['仅提供脱敏摘要与候选附近片段，未审查完整正文；未识别的业务秘密仍可能存在。']}


def validate_answer(answer,packet):
    """Validate references and epistemic labels, not model judgments of truth."""
    valid={f['evidenceId'] for f in packet['fragments']}
    findings=answer.get('findings',[])
    if not isinstance(findings,list) or len(findings)>12:raise ValueError('Invalid security findings')
    for f in findings:
        if not isinstance(f,dict):raise ValueError('Invalid security finding')
        refs=f.get('evidenceRefs')
        if not isinstance(refs,list) or not refs or any(not isinstance(ref,str) or ref not in valid for ref in refs):raise ValueError('Security finding requires supplied evidence')
        if f.get('status') not in ('observation','hypothesis','insufficient_evidence'):raise ValueError('Unsupported security conclusion status')
        for key in ('topic','claim','reason','alternative','nextAction'):
            if not isinstance(f.get(key),str) or not f[key].strip():raise ValueError('Incomplete security finding: '+key)
    answer['securityEvidenceValidated']=True
    answer['securityCoverage']=packet['coverage']
    return answer
