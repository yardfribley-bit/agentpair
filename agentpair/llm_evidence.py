"""Classify redacted observations without asserting network transmission."""
import hashlib,json,re

def clean(text):
    text=re.sub(r'(?i)Bearer\s+\S+','Bearer [REDACTED]',text)
    text=re.sub(r'\bsk-[A-Za-z0-9_-]{12,}','[REDACTED:KEY]',text)
    text=re.sub(r'(?i)((?:password|api[_-]?key|secret|token|authorization)\s*["\x27]?\s*[:=]\s*["\x27]?)[^\s,"\x27;]+',r'\1[REDACTED:CREDENTIAL]',text)
    text=re.sub(r'(?<!\d)1[3-9]\d{9}(?!\d)','[REDACTED:PHONE]',text)
    text=re.sub(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}','[REDACTED:EMAIL]',text)
    return text[:16000]

def classify(event):
    if not isinstance(event,dict) or event.get('source') not in ('workbuddy_transcript','workbuddy_hook'):raise ValueError('Unsupported evidence source')
    kind=event.get('event');role=event.get('role');body=event.get('evidence')
    if not isinstance(body,dict):raise ValueError('Missing evidence')
    raw=json.dumps(body,ensure_ascii=False);safe=clean(raw)
    sensitive=safe!=raw or '[REDACTED' in raw
    category='用户输入' if role=='user' or kind=='UserPromptSubmit' else '模型可见回复' if role=='assistant' else '工具结果' if kind in ('function_call_result','PostToolUse','PostToolUseFailure') else '工具参数'
    tool=event.get('toolName')
    source='WorkBuddy 会话记录' if event['source']=='workbuddy_transcript' else 'WorkBuddy Hook'+(' · '+str(tool)[:100] if tool else '')
    identity=hashlib.sha256(json.dumps(event,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    return {'id':identity,'name':category+' · '+identity[:8],'category':category,'source':source,
            'observedAt':event.get('time') or event.get('observedAt'),'sessionHash':str(event.get('sessionHash',''))[:64],
            'sensitivity':'sensitive' if sensitive else 'unknown','sensitivityLabel':'敏感（规则命中）' if sensitive else '待确认',
            'sendEvidence':'local','sendEvidenceLabel':'本地观察 · 发送未确认',
            'cleaningLabel':'上传前脱敏','preSendCleaned':False,'redactedContent':safe,
            'classificationBasis':'字段/事件类型分类；凭证、手机号、邮箱规则脱敏。未命中不代表无敏感数据。',
            'provenance':source+' → 本地采集 → 脱敏上传；没有完整模型请求证据'}
