"""Present captured context as evidence, without inventing responses or executions."""
import json
import re
from collections import Counter
from .model_security import analyze_request, COMPILED, PLACEHOLDERS, RULE_VERSION

CONTENT_LIMIT = 2400
SECRET_RULES = {'private_key', 'api_key', 'cloud_key', 'credential', 'bearer', 'url_credential', 'email', 'phone', 'user_path'}
CATALOG = [
    {'id':'D-01','title':'凭据进入输入','rules':['private_key','api_key','cloud_key','credential','bearer','url_credential'],'condition':'凭据格式或敏感字段命中，排除已识别占位符；真实性与发送授权需复核。','method':'本地模式扫描','requires':'输入正文','enabled':True},
    {'id':'D-02','title':'个人信息与环境信息','rules':['email','phone','user_path'],'condition':'输入出现邮箱、手机号或用户目录；出现本身不代表违规。','method':'本地模式扫描','requires':'输入正文','enabled':True},
    {'id':'I-01','title':'指令覆盖与外传语句','rules':['instruction_override','secret_extraction'],'condition':'匹配指令覆盖或凭据外传语句；引用、否定句、安全测试可能误报，未做语义和因果判断。','method':'候选线索扫描','requires':'输入正文；确认注入另需来源与行为证据','enabled':True},
    {'id':'A-01','title':'越权执行','rules':[],'condition':'实际执行动作违反当前有效授权。模型提出动作不能等同于执行。','method':'待接入策略与事件关联','requires':'授权策略、独立执行回执及效果证据','enabled':False},
    {'id':'P-01','title':'能力配置过宽','rules':[],'condition':'工具权限超出任务获准能力范围。','method':'待接入授权策略','requires':'工具清单与明确的任务授权策略','enabled':False},
    {'id':'D-03','title':'受限数据外发','rules':[],'condition':'受限数据实际发往不被策略允许的目的地。','method':'待接入分类与目的地策略','requires':'数据分级、授权范围、接收方证据','enabled':False},
    {'id':'O-01','title':'危险模型输出执行','rules':[],'condition':'模型命令或参数被执行且违反路径、命令或网络访问策略。','method':'待接入输出与执行检测','requires':'独立响应、执行记录及策略','enabled':False},
]


def redact(text):
    """Local display masking only; no source alteration or outbound model call."""
    # Mask whole PEM material before generic marker matching.
    import re
    text=re.sub(r'-----BEGIN ([A-Z ]*PRIVATE KEY)-----.*?(?:-----END \1-----|$)', '[私钥材料已隐藏]', text, flags=re.S)
    spans=[]
    for rule,_,_,pattern,_,_ in COMPILED:
        if rule not in SECRET_RULES:continue
        for m in pattern.finditer(text):
            value=m.group(1) if m.lastindex else m.group()
            if PLACEHOLDERS.fullmatch(value):continue
            spans.append(m.span(1) if m.lastindex else m.span())
    from .credential_threats import candidates
    spans.extend((a,b) for a,b,_,_ in candidates(text))
    merged=[]
    for start,end in sorted(spans):
        if merged and start<=merged[-1][1]:merged[-1][1]=max(end,merged[-1][1])
        else:merged.append([start,end])
    for start,end in reversed(merged):text=text[:start]+'[已隐藏]'+text[end:]
    return text


def preview(value,limit=CONTENT_LIMIT):
    text=value if isinstance(value,str) else json.dumps(value,ensure_ascii=False)
    text=redact(text)
    return {'text':text[:limit], 'displayTruncated':len(text)>limit,'displayLimit':limit,'displayCharacters':len(text)}


def build_audit(device, request):
    report=analyze_request(device,request)
    try:
        body=json.loads(request['body'])
        messages=body if isinstance(body,list) else body.get('messages',[]) if isinstance(body,dict) else []
        if not isinstance(messages,list):messages=[]
    except (ValueError,RecursionError):body=None;messages=[]
    from .model_security import segments
    try: values=dict((pointer,text) for pointer,text,_ in segments(body)) if body is not None else {'':request['body']}
    except (ValueError,RecursionError):values={'':request['body']}
    for finding in report['findings']:
        original=values.get(finding['jsonPointer'] or '', '')
        start,end=finding['charStart'],finding['charEnd']
        finding['contextPreview']=redact(original[max(0,start-70):start])+' [命中片段已隐藏] '+redact(original[end:end+90])
    prefix='' if isinstance(body,list) else '/messages'
    counts=Counter(report['ruleCounts']);events=[];message_findings=set()
    for index,message in enumerate(messages):
        if not isinstance(message,dict):continue
        role=message.get('role')
        role=role if role in ('user','assistant','system','developer','tool','function') else 'unknown'
        pointer=prefix+'/'+str(index)
        findings=[f for f in report['findings'] if f['jsonPointer']==pointer or (f['jsonPointer'] or '').startswith(pointer+'/')]
        message_findings.update(f['id'] for f in findings)
        content=message.get('content')
        blocks=[]
        if isinstance(content,list):
            for block in content:
                if isinstance(block,str):blocks.append(block)
                elif isinstance(block,dict) and isinstance(block.get('text'),str):blocks.append(block['text'])
                else:blocks.append('[非文本内容；未进行图像、音频或附件分析]')
        elif isinstance(content,str):blocks=[content]
        elif content is not None:blocks=[json.dumps(content,ensure_ascii=False)]
        tools=message.get('tool_calls') or []
        if isinstance(tools,list):
            for tool in tools:
                if not isinstance(tool,dict):continue
                function=tool.get('function') or {}
                if isinstance(function,dict):blocks.append('工具调用请求：'+str(function.get('name','未知'))+'\n'+str(function.get('arguments','')))
        if isinstance(message.get('function_call'),dict):blocks.append('函数调用请求：'+json.dumps(message['function_call'],ensure_ascii=False))
        relation={'system':'Agent 附加上下文','developer':'Agent 附加上下文','user':'用户角色消息','assistant':'上下文中的历史模型消息','tool':'上下文中的工具结果','function':'上下文中的函数结果','unknown':'未识别消息'}[role]
        joined='\n'.join(blocks) or '[无文本内容]'
        queries=list(re.finditer(r'<user_query>([\s\S]*?)</user_query>',joined)) if role=='user' else []
        extracted='\n\n'.join(m.group(1).strip() for m in queries)
        query_preview=preview(extracted,1400) if queries else None
        attached_tags=list(dict.fromkeys(re.findall(r'<([A-Za-z][A-Za-z0-9_-]*)\b[^>]*>',joined))) if queries else []
        events.append({'userQuery':query_preview,'userQueryBasis':'user_query 标记边界；非独立界面采集' if queries else None,
                       'attachedContextTags':[t for t in attached_tags if t!='user_query'],'userQueryOffsets':[{'start':m.start(1),'end':m.end(1)} for m in queries],
                       'index':index,'role':role,'label':relation,'pointer':pointer,'content':preview(joined,500 if role in ('system','developer') else 1400),
                       'findings':findings,'toolCallCount':len(tools) if isinstance(tools,list) else 0,
                       'executionVerified':False,'currentResponse':False})
    # Source metadata also comes from captured input and is untrusted.
    user=next((e for e in reversed(events) if e['role']=='user'),None)
    tools=body.get('tools',[]) if isinstance(body,dict) else []
    tool_names=[]
    for tool in tools if isinstance(tools,list) else []:
        if isinstance(tool,dict):
            function=tool.get('function')
            name=function.get('name') if isinstance(function,dict) else tool.get('name')
            if isinstance(name,str):tool_names.append(redact(name)[:100])
    actual_model=body.get('model') if isinstance(body,dict) else None
    params={key:body[key] for key in ('temperature','max_tokens','stream','reasoning_effort') if isinstance(body,dict) and key in body and isinstance(body[key],(str,int,float,bool))}
    user_content=(user.get('userQuery') or user['content']) if user else None
    rules=[]
    for rule in CATALOG:
        occurrences=sum(counts[r] for r in rule['rules'])
        rules.append({**rule,'occurrences':occurrences,'status':('待复核' if occurrences else '已扫描 · 未命中') if rule['enabled'] else '未评估 · 证据或策略未接入'})
    result={'request':{k:report[k] for k in ('id','deviceId','source','timestamp','bodySHA256','bodyBytes','evidenceState','truncated','jsonParseable','destination','candidateOccurrences','findingLocationsOmitted')},
            'sessionId':request.get('sessionId') or 'unknown','sessionName':redact(request.get('sessionName') or '会话未识别'),
            'model':redact(str(actual_model or request.get('model') or '未采集'))[:100],
            'requestOverview':{'messageRoles':dict(Counter(e['role'] for e in events)),'toolNames':tool_names,'parameters':params,'modelBasis':'请求正文 model 字段' if actual_model else '采集元数据' if request.get('model') else '未采集'},
            'taskBasis':user.get('userQueryBasis') or '完整 user 角色消息；用户任务与自动附加内容未分离' if user else '无 user 角色消息','ruleVersion':RULE_VERSION,'events':events,'task':{**user_content,'text':user_content['text'][:1000],'displayTruncated':user_content['displayCharacters']>1000,'displayLimit':1000} if user_content else None,
            'unassignedFindings':[f for f in report['findings'] if f['id'] not in message_findings],
            'unparsed':preview(request['body']) if not events else None,'rules':rules,
            'coverage':{'currentResponse':False,'independentToolExecution':False,'historicalAssistantMessages':sum(e['role']=='assistant' for e in events),'semanticReview':False},
            'limitations':['按单份输入快照还原历史；不把不同快照猜测拼成完整会话。','正文按本地规则脱敏显示；未识别的敏感内容仍可能存在。','当前响应和独立执行事件未采集，不能确认最终结果或越权执行。']}

    from .security_investigation import build_investigation
    result['investigation']=build_investigation(result)
    return result
