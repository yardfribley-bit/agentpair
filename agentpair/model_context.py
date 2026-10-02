"""Partition recorded model inputs by explicit boundaries; never guess provenance."""
import hashlib,json,math,re
NAMES={'SOUL.md':'身份与行为规则','IDENTITY.md':'身份与行为规则','USER.md':'用户资料','MEMORY.md':'长期记忆','AGENTS.md':'项目规范','CLAUDE.md':'项目规范'}
HEADER=re.compile(r'(?m)^#{1,6}[ \t]+(?:`)?(SOUL|IDENTITY|USER|MEMORY|AGENTS|CLAUDE)\.md(?:`)?[ \t]*(?:\r?\n|$)')

def validate_request(r):
    if not isinstance(r,dict) or r.get('source') not in ('workbuddy_generation_context','workbuddy_network_context') or not re.fullmatch('[0-9a-f]{64}',r.get('id','')):raise ValueError('Invalid identity')
    body=r.get('body')
    if not isinstance(body,str) or len(body.encode())>1000000:raise ValueError('Invalid input size')
    stamp=r.get('timestamp',0)
    if isinstance(stamp,bool) or not isinstance(stamp,(int,float)) or not math.isfinite(stamp):raise ValueError('Invalid timestamp')
    digest=hashlib.sha256(body.encode()).hexdigest()
    if r.get('bodySHA256') and r['bodySHA256']!=digest:raise ValueError('Body digest mismatch')
    return {'id':r['id'],'source':r['source'],'body':body,'bodySHA256':digest,'timestamp':stamp,
            'model':str(r['model'])[:100] if r.get('model') else None,'modelEvidence':str(r.get('modelEvidence') or '模型来源未记录')[:300],
            'recordStatus':r.get('recordStatus') if r.get('recordStatus') in ('parseable','truncated','unparseable','wire_length_matched','wire_length_unknown') else 'unknown',
            'destination':str(r.get('destination') or '')[:300],'wireLengthMatched':bool(r.get('wireLengthMatched')) if r['source']=='workbuddy_network_context' else False,
            'recordJSONValid':bool(r.get('recordJSONValid')),'integrityEvidence':str(r.get('integrityEvidence') or '源记录校验尚未执行')[:400],
            'sessionId':str(r.get('sessionId') or 'unknown')[:100],'sessionName':str(r.get('sessionName') or '会话未识别')[:200],
            'truncated':bool(r.get('truncated')),'complete':False}

def context_items(r):
    try:
        parsed=json.loads(r['body']);messages=parsed if isinstance(parsed,list) else parsed.get('messages',[])
    except (ValueError,AttributeError):messages=[]
    items=[]
    for mi,m in enumerate(messages):
        if not isinstance(m,dict):continue
        content=m.get('content');blocks=[content] if isinstance(content,str) else content if isinstance(content,list) else [content]
        for bi,b in enumerate(blocks):
            text=b if isinstance(b,str) else b.get('text') if isinstance(b,dict) and isinstance(b.get('text'),str) else json.dumps(b,ensure_ascii=False)
            matches=list(HEADER.finditer(text));cursor=0
            for match in matches:
                start=match.start()
                if start>cursor:items.append(make(r,mi,bi,cursor,text[cursor:start],m.get('role'),None))
                level=len(match.group(0).split(' ')[0])
                following=re.search(r'(?m)^#{1,'+str(level)+r'}[ \t]+',text[match.end():])
                end=match.end()+following.start() if following else len(text)
                name=match.group(1)+'.md';items.append(make(r,mi,bi,start,text[start:end],m.get('role'),name));cursor=end
            if cursor<len(text) or not matches:items.append(make(r,mi,bi,cursor,text[cursor:],m.get('role'),None))
    return items or [make(r,0,0,0,r['body'],None,None)]

def make(r,mi,bi,start,text,role,name):
    category=NAMES[name] if name else '系统提示' if role=='system' else '历史对话' if role=='assistant' else '未分类内容'
    basis='独立 Markdown 标题边界：'+name if name else 'system 消息角色' if role=='system' else 'assistant 消息角色；当前轮归属未验证' if role=='assistant' else '未找到可靠分类边界，保留原文，不猜测'
    return {'id':f'{r["id"]}:{mi}:{bi}:{start}','requestId':r['id'],'name':name or category,'category':category,
            'source':name or ('消息角色 '+str(role)),'rawContent':text,'bodyBytes':len(text.encode()),
            'classificationBasis':basis,'confidence':'标题边界明确' if name else '消息角色' if role in ('system','assistant') else '未确认',
            'messageIndex':mi,'blockIndex':bi,'charStart':start,'charEnd':start+len(text),
            'sourceVerified':False,'complete':False,'truncated':bool(r.get('truncated'))}
