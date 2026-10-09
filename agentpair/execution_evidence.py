"""Bounded read-only projections of execution requests and recorded returns.

Source code is parsed, never run. Returned source text is data, not evidence
that statements inside that text executed. Inner calls do not gain their outer
call's success state. Only two fully literal single-call templates permit an
explicitly labelled outer-single-operation inference.
"""
import json
import math
from pathlib import PurePosixPath
import re
import shlex

from .capability_evidence import javascript_calls

MAX_SOURCE = 64000
MAX_RESULT = 120000
MAX_OPERATIONS = 16
MAX_RETURNS = 12
TEXT_BLOCK_TYPES = {'text','input_text','output_text'}


def _bounded(value, limit, depth=0, remaining=None):
    remaining = [limit] if remaining is None else remaining
    if depth > 12 or remaining[0] <= 0:
        return None
    if isinstance(value, str):
        take = min(len(value), remaining[0]); remaining[0] -= take
        return value[:take]
    if isinstance(value, dict):
        return {str(k):_bounded(v, limit, depth+1, remaining) for k,v in list(value.items())[:64]}
    if isinstance(value, list):
        return [_bounded(v, limit, depth+1, remaining) for v in value[:64]]
    return value if value is None or isinstance(value, (int, float, bool)) else str(value)[:remaining[0]]


def _number(value):
    return value if isinstance(value, (int,float)) and not isinstance(value,bool) and math.isfinite(value) and value >= 0 else None


def _decoded(value):
    if not isinstance(value, str):return value
    try:return json.loads(value)
    except (ValueError, RecursionError):return value


def _string(value):
    if isinstance(value,str):return value
    try:return json.dumps(value,ensure_ascii=False,allow_nan=False)
    except (ValueError,TypeError,RecursionError):return str(value)


def _targets(command, arguments, function):
    """Only literal, recognized file-read commands identify read targets."""
    targets=[]; read=False
    if function.rsplit('.',1)[-1].lower() in ('read','read_file') and isinstance(arguments,dict):
        path=arguments.get('path') or arguments.get('file_path')
        if isinstance(path,str):targets=[{'kind':'file','value':path,'label':'读取文件'}];read=True
    if command and not any(token in command for token in ('$','`','\n',';','&&','||','|','>','<')):
        try:tokens=shlex.split(command)
        except ValueError:tokens=[]
        program=PurePosixPath(tokens[0]).name if tokens else ''
        paths=[]; line_range=None
        if program=='cat' and len(tokens)>1 and not any(v.startswith('-') for v in tokens[1:]):paths=tokens[1:]
        elif program=='sed' and len(tokens)>=4 and tokens[1]=='-n' and re.fullmatch(r'\d+(?:,\d+)?p',tokens[2]):
            paths=tokens[3:] if not any(v.startswith('-') for v in tokens[3:]) else []
            line_range=tokens[2][:-1].replace(',','–')
        elif program in ('head','tail'):
            cursor=1
            while cursor<len(tokens):
                if tokens[cursor] in ('-n','-c') and cursor+1<len(tokens) and tokens[cursor+1].isdigit():cursor+=2
                elif re.fullmatch(r'-(?:n|c)?\d+',tokens[cursor]):cursor+=1
                elif not tokens[cursor].startswith('-'):paths.append(tokens[cursor]);cursor+=1
                else:paths=[];break
        if paths:
            read=True;targets=[{'kind':'file','value':path,'label':'读取文件',**({'lineRange':line_range} if line_range else {})} for path in paths[:8]]
        elif program in ('curl','wget'):
            value_options={'-H','--header','-e','--referer','-x','--proxy','--preproxy','-d','--data','--data-raw','--data-binary','--data-urlencode','-F','--form','--form-string','-o','--output','--output-document','-T','--upload-file','-u','--user','-X','--request','--connect-to','--resolve','--cacert','--cert','--key','-K','--config','--cookie','-b','-c','--cookie-jar','--max-time','--connect-timeout','--retry','-A','--user-agent'}
            cursor=1
            while cursor<len(tokens):
                token=tokens[cursor]
                if token in value_options:cursor+=2;continue
                if token=='--url' and cursor+1<len(tokens):cursor+=1;token=tokens[cursor]
                elif token.startswith('--url='):token=token[6:]
                elif token.startswith('-'):cursor+=1;continue
                if re.fullmatch(r'https?://\S+',token):targets.append({'kind':'url','value':token,'label':'请求目标'})
                cursor+=1
    return targets,read


def _operation(call,index,detail):
    arguments=call.get('arguments'); function=call['originalName']
    command=arguments.get('cmd',arguments.get('command')) if isinstance(arguments,dict) else None
    if not isinstance(command,str):command=None
    cwd=arguments.get('workdir',arguments.get('cwd')) if isinstance(arguments,dict) else None
    if not isinstance(cwd,str):cwd=None
    targets,read=_targets(command,arguments,function)
    action='读取文件' if read else '执行命令' if command else '调用 '+function
    short=function.rsplit('.',1)[-1].lower()
    if short=='apply_patch' and isinstance(arguments,str):
        targets=[{'kind':'file','value':match[2],'label':{'Add':'新增文件','Update':'修改文件','Delete':'删除文件'}[match[1]]}
            for match in re.finditer(r'(?m)^\*\*\* (Add|Update|Delete) File: (.+)$',arguments)][:8]
        action='变更文件'
    elif short=='write_stdin' and isinstance(arguments,dict):action='发送运行输入' if arguments.get('chars') else '读取运行输出'
    elif short=='view_image' and isinstance(arguments,dict) and isinstance(arguments.get('path'),str):
        action='查看图片';targets=[{'kind':'file','value':arguments['path'],'label':'图片文件'}]
    elif command and not read and not any(v in command for v in ('$','`','\n',';','&&','||','|','>','<')):
        try:tokens=shlex.split(command)
        except ValueError:tokens=[]
        program=PurePosixPath(tokens[0]).name if tokens else ''
        if program=='node' and '--check' in tokens:action='检查 JavaScript 语法'
        elif program in ('pytest','unittest') or (re.fullmatch(r'python(?:3(?:\.\d+)?)?',program) and '-m' in tokens and any(v in ('pytest','unittest') for v in tokens)) or program=='node' and any(v.startswith('test') or '/tests/' in v for v in tokens[1:]):action='运行测试'
        elif program=='rg':action='搜索文件内容'
        elif program=='git' and len(tokens)>1 and tokens[1] in ('status','diff'):action='查看代码仓库状态' if tokens[1]=='status' else '查看代码改动'
    if arguments is None:action+='（参数为动态表达式）'
    object_summary='、'.join(t['value'] if t['kind']=='url' else PurePosixPath(t['value'].replace('\\','/')).name or t['value'] for t in targets)
    if targets and targets[0].get('lineRange'):object_summary+=' · 第 '+targets[0]['lineRange']+' 行'
    if not object_summary and command:object_summary=command.splitlines()[0][:160]
    limit=60000 if detail else 1400
    bounded_args=_bounded(arguments,limit)
    return {'id':'operation-'+str(index+1),'function':function,'arguments':bounded_args,
        'argumentsTruncated':_string(arguments)!=_string(bounded_args),'command':command if detail else command[:800] if command else None,
        'commandTruncated':bool(command and not detail and len(command)>800),'cwd':cwd,
        'action':action,'objectSummary':object_summary,'targets':targets,'isFileRead':read,
        'sourceOffset':call.get('sourceOffset'),'sourceEnd':call.get('sourceEnd'),'sourceBasis':'static_outer_code',
        'return':{'association':'unconfirmed','basis':'尚无内层调用与返回的独立配对证据','status':'unknown','statusLabel':'返回尚未逐项配对'}}


def _single_template(code,calls):
    if len(calls)!=1 or calls[0].get('arguments') is None or len(code)>MAX_SOURCE:return False
    call=calls[0]; start=call.get('sourceOffset'); end=call.get('sourceEnd')
    if not isinstance(start,int) or not isinstance(end,int):return False
    skeleton=re.sub(r'\s+','',code[:start]+'<CALL>'+code[end:])
    if re.fullmatch(r'text\(await<CALL>\);?',skeleton):return True
    return bool(re.fullmatch(r'(?:const|let|var)([A-Za-z_$][\w$]*)=await<CALL>;text\(\1\);?',skeleton))


def _returns(result,detail):
    outer={'status':'unknown','statusLabel':'已取得外层返回' if result is not None else '尚未取得外层返回','durationSeconds':None}
    if result is None:return outer,[],False
    text=_string(result); truncated=len(text)>MAX_RESULT
    value=_bounded(result,MAX_RESULT);truncated=truncated or _string(value)!=text
    nontext=[]
    if isinstance(value,list) and value and all(isinstance(v,dict) and 'type' in v for v in value):
        nontext=[v for v in value if v.get('type') not in TEXT_BLOCK_TYPES or not isinstance(v.get('text'),str)]
        value='\n'.join(v['text'] for v in value if v.get('type') in TEXT_BLOCK_TYPES and isinstance(v.get('text'),str))
    if isinstance(value,dict) and isinstance(value.get('text'),str):value=value['text']
    elif isinstance(value,dict) and isinstance(value.get('content'),list):
        nontext=[v for v in value['content'] if not isinstance(v,dict) or v.get('type') not in TEXT_BLOCK_TYPES or not isinstance(v.get('text'),str)]
        value='\n'.join(v['text'] for v in value['content'] if isinstance(v,dict) and v.get('type') in TEXT_BLOCK_TYPES and isinstance(v.get('text'),str))
    outer['nonTextBlocks']=len(nontext)
    if isinstance(value,str):
        wrapped=re.match(r'^Script completed\s*\nWall time:?\s*(\d+(?:\.\d+)?)\s*(?:seconds|s)\s*\nOutput:\s*\n?',value)
        if wrapped:
            outer.update(status='completed',statusLabel='外层脚本已完成',durationSeconds=float(wrapped[1]));value=value[wrapped.end():]
        elif re.match(r'^Script running with cell ID\b',value):
            outer.update(status='running',statusLabel='外层脚本仍在运行')
            output_marker=re.search(r'(?m)^Output:\s*\n?',value)
            if not output_marker:return outer,[],truncated or bool(nontext)
            value=value[output_marker.end():];outer['partial']=True
        truncated=truncated or bool(re.search(r'(?m)^Warning: truncated output\b',value))
        value=_decoded(value)
    # Multiple JSON objects printed by text() are retained as distinct return
    # entries; order, i and chunk_id never pair them with static inner calls.
    values=value if isinstance(value,list) else [value]
    if isinstance(value,str):
        # raw_decode permits pretty JSON and adjacent JSON text blocks, with a
        # hard return limit. Any ordinary text stops structural decoding.
        cursor=0;decoded=[];decoder=json.JSONDecoder()
        try:
            while cursor<len(value) and len(decoded)<=MAX_RETURNS:
                while cursor<len(value) and value[cursor].isspace():cursor+=1
                if cursor>=len(value):break
                member,end=decoder.raw_decode(value,cursor);decoded.append(member);cursor=end
            if decoded and (cursor==len(value) or len(decoded)>MAX_RETURNS):values=decoded;truncated=truncated or cursor<len(value)
        except (ValueError,RecursionError):pass
    values=[*values,*nontext]
    returns=[]
    for index,member in enumerate(values[:MAX_RETURNS]):
        envelope=member if isinstance(member,dict) and 'output' in member and any(k in member for k in ('chunk_id','exit_code','wall_time_seconds')) else {}
        content=envelope.get('output') if envelope else member
        exit_code=envelope.get('exit_code')
        if isinstance(exit_code,bool) or not isinstance(exit_code,int):exit_code=None
        pending=bool(envelope.get('session_id')) and exit_code is None
        status='running' if pending else 'exit_zero' if exit_code==0 else 'exit_nonzero' if exit_code is not None else 'unknown'
        content=_decoded(content)
        rendered=_string(content); empty=content is None or isinstance(content,str) and not content.strip()
        content_truncated=isinstance(content,str) and bool(re.search(r'(?m)^Warning: truncated output\b',content))
        non_text=any(member is block for block in nontext)
        content_type='empty' if empty else 'json' if isinstance(content,(dict,list)) else 'text'
        content_limit=60000 if detail else 1200
        body=_bounded(content,content_limit)
        summary='返回非文本内容 · '+str(member.get('type','未知类型')) if non_text else _return_summary(content,exit_code)
        returns.append({'id':'return-'+str(index+1),'association':'unassigned','basis':'外层返回，未与内层调用逐项配对',
            'status':status,'statusLabel':'命令仍在运行' if pending else '退出码 '+str(exit_code) if exit_code is not None else '已取得返回内容',
            'exitCode':exit_code,'durationSeconds':_number(envelope.get('wall_time_seconds')),'contentType':'non_text' if non_text else content_type,
            'summary':summary,
            'preview':rendered[:400] if not empty and not non_text else '',**({'content':body} if detail else {}),
            'lineCount':len(content.splitlines()) if isinstance(content,str) else None,
            'firstLine':next((line.strip() for line in content.splitlines() if line.strip()),'')[:100] if isinstance(content,str) else None,
            'truncated':truncated or content_truncated or _string(body)!=rendered,'partial':outer.get('partial',False),
            'technical':{k:v for k,v in {'chunkId':envelope.get('chunk_id'),'sessionId':envelope.get('session_id'),'index':member.get('i') if isinstance(member,dict) else None}.items() if v is not None}})
    return outer,returns,truncated or len(values)>MAX_RETURNS


def _return_summary(content,exit_code=None):
    if content is None or isinstance(content,str) and not content.strip():return '返回内容为空'
    if isinstance(content,list):return '返回 '+str(len(content))+' 个数据条目'
    if isinstance(content,dict):
        technical={'i','chunk_id','wall_time_seconds','session_id','exit_code'}
        facts=[]
        for key,value in content.items():
            if key in technical:continue
            if isinstance(value,(str,int,float,bool)) or value is None:facts.append(str(key)+'：'+str(value)[:100])
            elif isinstance(value,list):facts.append(str(key)+'：'+str(len(value))+' 条')
            if len(facts)>=3:break
        return '；'.join(facts)[:240] or '返回结构化数据，展开查看字段'
    text=str(content)
    unittest=re.search(r'(?m)^Ran (\d+) tests? in [\d.]+s\s*\n\s*(OK|FAILED[^\n]*)',text)
    if unittest:return '测试 '+unittest[1]+' 项：'+('全部通过' if unittest[2]=='OK' else unittest[2])
    pytest=re.search(r'(?m)^={2,}\s*((?:\d+ (?:passed|failed|error|errors|skipped|xfailed|xpassed)(?:, )?)+)\s+in\s+[\d.]+s',text)
    if pytest:return '测试结果：'+pytest[1]
    lines=[line.strip() for line in text.splitlines() if line.strip() and not re.match(r'^(?:Script completed|Wall time:?|Output:|Chunk ID:|Wall time seconds:|Process exited with code|Warning: truncated output|Total output lines:)',line.strip())]
    prefix='执行失败：' if exit_code is not None and exit_code!=0 else ''
    return prefix+' / '.join(lines[:2])[:240] if lines else '返回内容为空'


def unavailable(tool,call_id=None,basis='已达到本页解析预算；完整原文仍可查看'):
    return {'outer':{'tool':tool,'callId':call_id,'status':'unknown','statusLabel':'尚未解析执行内容'},'operations':[], 'returns':[],
        'coverage':{'incomplete':True,'projectionUnavailable':True,'operationsIdentified':0,'sourceTruncated':False,'limitations':[basis]}}


def project(tool,arguments,result,call_id=None,detail=False,source_truncated=False):
    if not isinstance(tool,str) or tool.rsplit('.',1)[-1].lower()!='exec':return None
    code=arguments.get('code') if isinstance(arguments,dict) else arguments if isinstance(arguments,str) else None
    if not isinstance(code,str):return unavailable(tool,call_id,'外层执行代码未以可解析文字提供；完整原文仍可查看')
    source_truncated=source_truncated or len(code)>MAX_SOURCE
    calls=javascript_calls(code[:MAX_SOURCE])
    operations=[_operation(call,index,detail) for index,call in enumerate(calls[:MAX_OPERATIONS])]
    outer,returns,result_truncated=_returns(result,detail)
    outer.update(tool=tool,callId=call_id,summary='、'.join(op['action']+(' · '+op['objectSummary'] if op['objectSummary'] else '') for op in operations[:3]) or '执行请求已保留，内层动作尚不能静态识别')
    single=not source_truncated and not result_truncated and _single_template(code,calls) and len(returns)==1
    if single:
        returned=returns[0];operation=operations[0]
        returned.update(association='outer_single_operation',basis='完整简单脚本仅包含一项字面量调用；按外层单调用对应，非内层调用ID证据')
        if operation['isFileRead'] and returned['contentType']!='empty' and returned['status']=='exit_zero':
            extension=PurePosixPath(operation['targets'][0]['value']).suffix.lower()
            returned['contentType']='code' if extension in ('.py','.js','.ts','.tsx','.jsx','.go','.rs','.java','.sh','.sql','.swift','.c','.cpp','.h') else 'file_content'
            if returned.get('lineCount') is not None:
                count=returned['lineCount'];first=returned.get('firstLine')
                returned['summary']='返回 '+str(count)+' 行已采集'+('代码' if returned['contentType']=='code' else '文件内容')+('：'+first if first else '')
            else:returned['summary']='返回文件内容：'+returned['summary']
        operation['return']=dict(returned)
    limitations=['内层动作来自静态请求代码；没有独立内层返回标识时不证明每项调用成功。','返回中的代码是内容，不能据此认定代码中的操作已经执行。']
    if not single:limitations.append('各项内层请求与外层返回分别展示，不按顺序、i或chunk_id逐项配对。')
    return {'outer':outer,'operations':operations,'returns':returns,'coverage':{
        'incomplete':source_truncated or len(calls)>MAX_OPERATIONS or result_truncated,
        'operationsIdentified':len(operations),'operationsCapped':len(calls)>MAX_OPERATIONS,
        'sourceTruncated':source_truncated,'returnTruncated':result_truncated or any(r['truncated'] for r in returns),
        'unpairedReturns':sum(r['association']=='unassigned' for r in returns),'limitations':limitations}}
