"""Evidence-bound local analysis of captured WorkBuddy inputs. Never emits secrets."""
import hashlib
import json
import re
import time
from collections import Counter

RULE_VERSION = '2026-10-02.1'
# Priority is review priority, not a claim of compromise or unauthorized disclosure.
RULES = [
    ('private_key', '私钥材料', 'high', r'-----BEGIN (?:RSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY-----', 'LLM02', '确认是否为真实私钥；若为有效凭证，撤销并清理上下文。'),
    ('api_key', '疑似 API 密钥', 'high', r'\bsk-[A-Za-z0-9_-]{16,}', 'LLM02', '核对密钥有效性与用途；凭证不应作为模型输入。'),
    ('cloud_key', '疑似云访问密钥 ID', 'high', r'\b(?:LTAI[A-Za-z0-9]{12,}|AKIA[A-Z0-9]{16})\b', 'LLM02', '检查是否同时包含访问密钥秘密，以及云端审计记录。'),
    ('credential', '凭证赋值候选', 'high', r'(?i)(?:\b(?:[A-Za-z][A-Za-z0-9]*[_-])*(?:password|passwd|api[_-]?key|secret[_-]?key|access[_-]?token|refresh[_-]?token|authorization|token)\b|密码|口令|令牌)["\x27]?\s*[:=：]\s*["\x27]?([^\s"\x27,;{}]{6,})', 'LLM02', '核对是有效值、示例还是占位符；在采集源处移除实际凭证。'),
    ('bearer', 'Bearer 凭证候选', 'high', r'(?i)\bBearer\s+([A-Za-z0-9._~+/-]{16,})', 'LLM02', '核对是否为真实会话凭证；不要把请求头或令牌带入上下文。'),
    ('url_credential', 'URL 内嵌凭证候选', 'high', r'(?i)https?://[^\s/@:]+:([^\s/@]{6,})@', 'LLM02', '改用独立凭证管理，避免带密码的 URL 进入模型输入。'),
    ('email', '邮箱地址候选', 'medium', r'\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b', 'LLM02', '核对是否为个人或客户信息，按业务需要最小化。'),
    ('phone', '中国大陆手机号候选', 'medium', r'(?<!\d)1[3-9]\d{9}(?!\d)', 'LLM02', '核对真实归属和授权，避免不必要的个人信息发送。'),
    ('user_path', '本地用户目录', 'low', r'(?:/Users/[^/\s"\x27]+/|[A-Za-z]:\\Users\\[^\\\s"\x27]+\\)', 'LLM02', '路径可能透露用户名与环境信息；可替换为相对路径。'),
    ('instruction_override', '指令覆盖语句候选', 'medium', r'(?i)(?:ignore|disregard|override)\s+(?:(?:all|any|the)\s+)?(?:previous|prior|above|system)\s+(?:instructions?|prompts?|rules?)|忽略(?:之前|此前|上面|所有|系统).{0,8}(?:指令|提示|规则)', 'LLM01', '检查来源和消息角色：合法指令、引用和安全测试均可能命中，不能据此认定注入成功。'),
    ('secret_extraction', '凭证外传指令候选', 'medium', r'(?i)(?:send|upload|exfiltrate).{0,60}(?:api[_ -]?keys?|passwords?|private keys?|credentials)|(?:发送|上传|外传).{0,30}(?:密钥|密码|私钥|令牌)', 'LLM01', '复核是否为攻击指令、引用或防护说明，并检查后续执行证据。'),
]
COMPILED = [(a,b,c,re.compile(d),e,f) for a,b,c,d,e,f in RULES]
PLACEHOLDERS = re.compile(r'(?i)^(?:\[?redacted[^\]]*\]?|<[^>]+>|\$\{[^}]+\}|example.*|dummy.*|placeholder.*|your[_-].*|x{6,}|\*{6,}|test[_-].*)$')
LIMITATIONS = [
    '规则命中是待复核候选，不证明凭证有效、未经授权发送或攻击成功。',
    '未命中不等于安全：业务秘密、私有代码、图片和编码内容未被完整语义审查。',
    'HTTP 请求捕获不证明模型服务处理、保存或训练使用；应用上下文记录不证明网络发送。',
    '历史记录可能重复同一内容；命中数是出现次数，不是独立泄露次数。',
    '仅凭文件标题或路径不能证明来源文件的真实性，也不能判断任务所需的最小数据范围。',
]


def segments(value, pointer='', role=None, depth=0):
    """JSON pointers plus offsets in decoded string values, never guessed body offsets."""
    if depth > 80:
        raise ValueError('Deep JSON; scan serialized body instead')
    if isinstance(value, str):
        yield pointer or '/', value, role
    elif isinstance(value, list):
        for i, child in enumerate(value):
            yield from segments(child, pointer+'/'+str(i), role, depth+1)
    elif isinstance(value, dict):
        own_role=value.get('role')
        role=own_role if own_role in ('system','developer','user','assistant','tool') else role
        for key, child in value.items():
            part=str(key).replace('~','~0').replace('/','~1')
            yield from segments(child,pointer+'/'+part,role,depth+1)


def analyze_request(device, request):
    body=request['body'];raw=body.encode();digest=hashlib.sha256(raw).hexdigest()
    network=request.get('source')=='workbuddy_network_context'
    evidence_state='captured_request' if network else 'recorded_context'
    try:
        parsed=json.loads(body);values=list(segments(parsed));parseable=True
    except (ValueError,RecursionError):
        values=[('',body,None)];parseable=False
    counts=Counter();findings=[];total=0
    for pointer,text,role in values:
        for rule,title,priority,pattern,owasp,action in COMPILED:
            matches=list(pattern.finditer(text))
            field=pointer.rsplit('/',1)[-1].replace('~1','/').replace('~0','~').lower()
            if rule=='credential' and re.fullmatch(r'(?:.*[_-])?(?:password|passwd|apikey|api_key|secret|secret_key|accesstoken|access_token|refreshtoken|refresh_token|token)',field) and len(text)>=6 and not matches:
                matches=[re.match(r'[\s\S]+',text)]
            for match in matches:
                candidate=match.group(1) if match.lastindex else match.group(0)
                if rule in ('api_key','credential','bearer','url_credential') and PLACEHOLDERS.fullmatch(candidate):continue
                start,end=match.span(1) if match.lastindex else match.span()
                counts[rule]+=1;total+=1
                if len(findings)>=100:continue
                identity='|'.join((device['id'],request['id'],rule,pointer,str(start),str(end),RULE_VERSION))
                findings.append({'id':hashlib.sha256(identity.encode()).hexdigest()[:24],
                    'ruleId':rule,'title':title,'priority':priority,'status':'needs_review','owasp':owasp,
                    'requestId':request['id'],'deviceId':device['id'],'source':request.get('source'),
                    'evidenceState':evidence_state,'role':role,'jsonPointer':pointer or None,
                    'charStart':start,'charEnd':end,'offsetBasis':'decoded_json_string' if parseable else 'raw_body',
                    'bodySHA256':digest,'matchDisplay':'[候选值已隐藏]','recommendation':action})
    from .model_context import context_items
    categories=Counter()
    for item in context_items(request):categories[item['category']]+=item['bodyBytes']
    return {'id':request['id'],'deviceId':device['id'],'platform':device['os'] if device['os'] in ('Windows','macOS') else 'unknown',
            'source':request.get('source'),'timestamp':request.get('timestamp'),'bodyBytes':len(raw),'bodySHA256':digest,
            'evidenceState':evidence_state,'destination':safe_destination(request.get('destination')),
            'truncated':bool(request.get('truncated')),'wireLengthMatched':bool(request.get('wireLengthMatched')) if network else False,
            'jsonParseable':parseable,'categoriesBytes':dict(categories),'ruleCounts':dict(counts),
            'candidateOccurrences':total,'findings':findings,'findingLocationsOmitted':max(0,total-len(findings))}


def safe_destination(value):
    # Never copy userinfo, query credentials or arbitrary source text into summaries.
    from urllib.parse import urlsplit
    if not isinstance(value,str) or not value:return None
    try:
        u=urlsplit(value if '://' in value else 'https://'+value)
        return u.hostname if u.hostname and re.fullmatch(r'[A-Za-z0-9.-]+',u.hostname) else None
    except ValueError:return None


def build_report(records, total_records=None):
    requests=[analyze_request(device,request) for device,request in records]
    counts=Counter();priorities=Counter();platforms=Counter();sources=Counter()
    for request in requests:
        counts.update(request['ruleCounts']);platforms[request['platform']]+=1;sources[request['source']]+=1
        for rule,_,priority,_,_,_ in RULES:priorities[priority]+=request['ruleCounts'].get(rule,0)
    network=sum(r['evidenceState']=='captured_request' for r in requests)
    return {'schemaVersion':1,'ruleVersion':RULE_VERSION,'createdAt':time.time(),
            'summary':{'scannedRequests':len(requests),'totalAvailableRequests':total_records if total_records is not None else len(requests),
                       'capturedRequests':network,'recordedContexts':len(requests)-network,
                       'truncatedRequests':sum(r['truncated'] for r in requests),'bodyBytes':sum(r['bodyBytes'] for r in requests),
                       'candidateOccurrences':sum(counts.values()),'requestsWithCandidates':sum(bool(r['ruleCounts']) for r in requests),
                       'reviewPriorityCounts':dict(priorities),'ruleCounts':dict(counts),'platforms':dict(platforms),'sources':dict(sources)},
            'rules':[{'id':r,'title':t,'priority':p,'owasp':o} for r,t,p,_,o,_ in RULES],
            'requests':requests,'limitations':LIMITATIONS,'rawTextIncluded':False,
            'coverage':{'fullStoredBodiesScanned':True,'sampled':total_records is not None and total_records>len(requests),
                        'businessSecretsSemanticReview':False,'recipientAcceptance':False,'authorizationDetermined':False}}


def model_review_packet(report):
    """No source body, snippets, candidate values or device names leave this boundary."""
    return {'ruleVersion':report['ruleVersion'],'summary':report['summary'],
            'requests':[{'id':r['id'],'platform':r['platform'],'evidenceState':r['evidenceState'],
                         'bodySHA256':r['bodySHA256'],'bodyBytes':r['bodyBytes'],'truncated':r['truncated'],
                         'wireLengthMatched':r['wireLengthMatched'],'categoriesBytes':r['categoriesBytes'],
                         'ruleCounts':r['ruleCounts'],'locations':[{'ruleId':f['ruleId'],'jsonPointer':review_pointer(f['jsonPointer']),
                         'charStart':f['charStart'],'charEnd':f['charEnd'],'offsetBasis':f['offsetBasis'],'role':f['role']}
                         for f in r['findings'][:20]]} for r in report['requests'][:20]],
            'limitations':report['limitations'],'rawTextIncluded':False}


def review_pointer(pointer):
    if not pointer:return None
    allowed={'messages','content','text','role','tools','tool_calls','function','arguments','name','model','system','prompt','input'}
    return '/'.join(part if part in allowed or part.isdigit() else '[field]' for part in pointer.split('/'))
