"""Evidence search over retained collector records, independent of model analysis.

The rebuildable SQLite index is fed one source record at a time. Search loads
a page of metadata/excerpts and bounded execution projections for selected
rows; untouched source JSON remains available on demand.
Upload-account ownership scopes identities. It never identifies an employee or
turns a source session, a text address or a successful command into a task,
network connection or security finding.
"""
import base64
import hashlib
from contextlib import contextmanager
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import shlex
import sqlite3
import threading
import time
import weakref
from urllib.parse import urlencode, urlsplit

from .collection_view import event_seconds
from . import capability_evidence, execution_evidence
from .model_context import context_items
from .session_packets import user_text
from .source_turn_identity import extract_turn_identity

VERSION = '5'
REQUEST_EVIDENCE_VERSION = 1
_LOCKS = weakref.WeakValueDictionary()
_LOCKS_GUARD = threading.Lock()
CALLS = {'tool_call', 'command_execution', 'mcp_execution', 'extension_execution'}
KINDS = {'user': '用户提问', 'context': '上下文记录', 'tool_call': '工具调用',
         'tool_result': '工具返回', 'reasoning': '已记录思路', 'reply': 'Agent 回复', 'http': '捕获的模型请求'}
LOCATIONS = {'user': '用户提问', 'context': '上下文正文', 'arguments': '工具参数',
             'result': '工具返回', 'reply': 'Agent 回复', 'metadata': '源记录字段'}
FIELDS = {'tool', 'function', 'command', 'ip', 'domain', 'session', 'device',
          'app', 'collector', 'kind', 'location', 'executor', 'target_domain', 'target_ip',
          'skill', 'mcp', 'mcp_method'}
PARAMS = {'q', 'collector', 'application', 'device', 'kind', 'location', 'after', 'before', 'page', 'pageSize', 'snapshot', 'object', 'group', 'groupId'}
DUPLICATES = '''NOT EXISTS(SELECT 1 FROM dc_records d JOIN dc_scope ds ON ds.device=d.device AND ds.owner=d.owner
    WHERE d.collector=r.collector AND d.owner=r.owner AND d.ident=r.ident AND d.source=r.source
    AND ds.canonical=s.canonical AND json_extract(d.metadata,'$.recordDigest')=json_extract(r.metadata,'$.recordDigest')
    AND ((d.device=s.canonical AND r.device!=s.canonical) OR (d.device!=s.canonical AND r.device!=s.canonical AND d.device<r.device)))'''
TOKEN = re.compile(r'''\s+|&&|\|\||!=|=|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[^\s=&|!()"']+''')


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def _text(value):
    if isinstance(value, str):
        return value
    if value is None:
        return ''
    return _json(value)


def _readable(value):
    if isinstance(value, list):
        return '\n'.join(_readable(v) for v in value)
    if isinstance(value, dict):
        for key in ('text', 'content', 'message', 'rawContent', 'summary'):
            if key in value:
                return _readable(value[key])
    return _text(value)


def _object(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return value
    return value


def _inner(event):
    payload = event.get('payload') or {}
    if not isinstance(payload, dict):
        return {}
    item = payload.get('item', payload)
    return item if isinstance(item, dict) else {}


def _ident(value):
    return value if isinstance(value, str) and value else None


def _key(collector, owner, device, ident):
    return 'dc_' + base64.urlsafe_b64encode(_json([collector, owner, device, ident]).encode()).decode().rstrip('=')


def _decode(key):
    if not isinstance(key, str) or not key.startswith('dc_') or len(key) > 1800:
        raise ValueError('采集记录标识无效')
    try:
        value = json.loads(base64.b64decode(key[3:] + '=' * (-len(key[3:]) % 4), altchars=b'-_', validate=True))
    except (ValueError, UnicodeError):
        raise ValueError('采集记录标识无效') from None
    if (not isinstance(value, list) or len(value) != 4 or value[0] not in ('applens', 'sessionlens')
            or any(not isinstance(v, str) or not v or len(v) > 300 for v in value)):
        raise ValueError('采集记录标识无效')
    return value


def parse_query(query):
    """OR of AND clauses. Adjacent terms mean AND; no parentheses/wildcards.

    Text terms match literal case-insensitive substrings. Fields use exact
    values, except command=NAME may also match an explicitly recorded program.
    ip/domain match complete address tokens, never address substrings.
    """
    if not isinstance(query, str) or len(query) > 4096:
        raise ValueError('搜索表达式最多 4096 字')
    tokens = []
    position = 0
    for match in TOKEN.finditer(query):
        if match.start() != position:
            raise ValueError('搜索语法错误；支持关键词、field=value、!=、&&、||，暂不支持括号')
        position = match.end()
        if not match.group().isspace():
            tokens.append(match.group())
    if position != len(query) or len(tokens) > 96:
        raise ValueError('搜索语法错误或条件过多')
    if not tokens:
        return []

    def literal(value):
        if value[:1] in ('"', "'"):
            quote = value[0]
            value = re.sub(r'\\(.)', r'\1', value[1:-1])
            if not value:
                raise ValueError('搜索条件不能为空')
        if value in ('&&', '||', '=', '!=') or not value or len(value) > 512:
            raise ValueError('搜索条件无效或过长')
        return value

    groups = [[]]
    cursor = 0
    expect_term = True
    while cursor < len(tokens):
        token = tokens[cursor]
        if token in ('&&', '||'):
            if expect_term:
                raise ValueError('布尔运算符缺少搜索条件')
            if token == '||':
                groups.append([])
            expect_term = True
            cursor += 1
            continue
        value = literal(token)
        if cursor + 1 < len(tokens) and tokens[cursor + 1] in ('=', '!='):
            field = value.lower()
            if field not in FIELDS or token[:1] in ('"', "'"):
                raise ValueError('不支持的搜索字段：' + value)
            if cursor + 2 >= len(tokens):
                raise ValueError('搜索字段缺少值')
            op = tokens[cursor + 1]
            value = literal(tokens[cursor + 2])
            if field in ('ip', 'target_ip'):
                try:
                    ipaddress.ip_address(value)
                except ValueError:
                    raise ValueError('ip 字段需要完整的 IPv4 或 IPv6 地址') from None
            if field in ('domain', 'target_domain') and not re.fullmatch(r'[A-Za-z0-9\u0080-\uffff](?:[A-Za-z0-9\u0080-\uffff.-]*[A-Za-z0-9\u0080-\uffff])?\.?', value):
                raise ValueError('domain 字段需要完整域名，不包含协议或路径')
            groups[-1].append((field, op, value))
            cursor += 3
        else:
            groups[-1].append(('keyword', '=', value))
            cursor += 1
        expect_term = False
    if expect_term:
        raise ValueError('布尔运算符缺少搜索条件')
    return groups


def _address_pattern(value, field):
    if field == 'ip':
        # IPv4 host:port is valid; dotted prefixes/suffixes and alphanumeric
        # identifiers are not the queried address. IPv6 needs colon boundaries.
        ipv4 = isinstance(ipaddress.ip_address(value), ipaddress.IPv4Address)
        chars = r'\w.' if ipv4 else r'\w.:'
    else:
        chars = r'\w.-'
        value = value.rstrip('.')
    return re.compile(r'(?<![' + chars + '])' + re.escape(value) + r'(?![' + chars + '])', re.I)


def _matches(text, value, field='keyword'):
    if field == 'keyword':
        try:
            ipaddress.ip_address(value)
            field = 'ip'
        except ValueError:
            pass
    if field in ('ip', 'domain'):
        return bool(_address_pattern(value, field).search(text or ''))
    return value.casefold() in (text or '').casefold()


def _snippet(text, value=None, field='keyword', size=280):
    text = text or ''
    start = 0
    if value:
        if field in ('ip', 'domain'):
            match = _address_pattern(value, field).search(text)
            at = match.start() if match else 0
        else:
            at = max(0, text.casefold().find(value.casefold()))
        start = max(0, at - 65)
    end = min(len(text), start + size)
    return ('…' if start else '') + text[start:end] + ('…' if end < len(text) else '')


def _command(arguments, item):
    for obj in (arguments, item):
        if isinstance(obj, dict):
            value = obj.get('cmd', obj.get('command'))
            if isinstance(value, str):
                return value
            if isinstance(value, list) and all(isinstance(v, str) for v in value):
                return shlex.join(value)
    return None


def _arguments(item):
    value = item.get('arguments', item.get('input', item.get('command')))
    function = item.get('function')
    if value is None and isinstance(function, dict):
        value = function.get('arguments')
    return _object(value)


# Compatibility exports keep capability parsing isolated from presentation.
_skill_path = capability_evidence.skill_path
_literal_shell_commands = capability_evidence.literal_shell_commands
_capabilities = capability_evidence.capabilities


def _programs(command):
    if not command:
        return []
    result = []
    shell_lines = []; delimiter = None
    for line in command.splitlines():
        if delimiter is not None:
            if line.strip() == delimiter:delimiter = None
            continue
        shell_lines.append(line)
        heredoc = re.search(r'''(?<!<)<<-?\s*(['"]?)([A-Za-z_]\w*)\1''',line)
        if heredoc:delimiter = heredoc.group(2)
    for part in re.split(r'\n|&&|\|\||[;|]', '\n'.join(shell_lines)):
        try:
            tokens = shlex.split(part)
        except ValueError:
            continue
        while tokens and (re.match(r'^\w+=', tokens[0]) or tokens[0] in ('sudo', 'env')):
            tokens.pop(0)
        if tokens:
            candidate = Path(tokens[0]).name
            # Shell control words, options, variable expansions and fragments
            # of URLs are not identifiable program names. Preserve the full
            # command rather than pretending to resolve shell evaluation.
            if (len(candidate)<=256 and re.fullmatch(r'[A-Za-z0-9_+][A-Za-z0-9_+.-]*',candidate)
                    and candidate not in {'if','then','else','elif','fi','for','while','until','do','done','case','esac','in','function'}):
                result.append(candidate)
    return sorted(set(result))


def _targets(arguments, command):
    result = []
    def walk(value, depth=0):
        if depth > 12 or not isinstance(value, dict):
            return
        for key, member in value.items():
            if key.lower() in ('prompt', 'negative_prompt', 'text', 'content', 'messages', 'body', 'result', 'output'):
                continue
            if key.lower() in ('url', 'uri', 'endpoint', 'href') and isinstance(member, str):
                try:parsed = urlsplit(member)
                except ValueError:continue
                if parsed.scheme in ('http', 'https', 'ssh', 'ftp') and parsed.hostname:
                    result.append(member)
            elif isinstance(member, dict):
                walk(member, depth + 1)
    walk(arguments)
    # An URL in a prompt or an echo command is only a mention. These explicit
    # command tools expose an intended target, not connection/success evidence.
    delimiter = None
    lines = []
    for line in (command or '').splitlines():
        if delimiter:
            if line.strip() == delimiter: delimiter = None
            continue
        lines.append(line)
        match = re.search(r'''(?<!<)<<-?\s*(['"]?)([A-Za-z_]\w*)\1''', line)
        if match: delimiter = match.group(2)
    for part in lines:
        try:
            lexer = shlex.shlex(part, posix=True, punctuation_chars=';&|<>')
            lexer.whitespace_split = True
            groups = [[]]
            for token in lexer:
                if token in (';', '&&', '||', '|', '&'): groups.append([])
                else: groups[-1].append(token)
        except ValueError:
            continue
        for tokens in groups:
            while tokens and (tokens[0] in ('sudo', 'env') or re.match(r'^\w+=', tokens[0])):
                tokens = tokens[1:]
            if not tokens or Path(tokens[0]).name not in ('curl', 'wget', 'http', 'https'):
                continue
            # Header, proxy, referer, body and output arguments are not the
            # request target. Keep only explicit URL/positional target values.
            value_options = {'-H','--header','-e','--referer','-x','--proxy','--preproxy',
                '-d','--data','--data-raw','--data-binary','--data-urlencode','-F','--form','--form-string',
                '-o','--output','--output-document','-T','--upload-file','-u','--user',
                '-X','--request','--connect-to','--resolve','--cacert','--cert','--key','-K','--config',
                '--cookie','-b','-c','--cookie-jar','--max-time','--connect-timeout','--retry','--user-agent','-A'}
            index = 1
            while index < len(tokens):
                token = tokens[index]
                if token in value_options or token in ('>', '>>', '<', '<<'):
                    index += 2; continue
                if token == '--url' and index + 1 < len(tokens):
                    index += 1; token = tokens[index]
                elif token.startswith('--url='): token = token[6:]
                elif token.startswith('-'):
                    index += 1; continue
                if re.match(r'^(?:https?|ftp)://[^\s]+$', token): result.append(token)
                index += 1
    return list(dict.fromkeys(result))


def _target_host(target):
    if not isinstance(target,str):return None
    # Captured request destinations may be host/path without a recorded scheme.
    # Parse the authority without inventing HTTP versus HTTPS.
    try: return urlsplit(target if '://' in target or target.startswith('//') else '//'+target).hostname
    except ValueError:return None


def _target_hosts(targets):
    domains, ips = [], []
    for target in targets:
        host = _target_host(target)
        if not host: continue
        try: ips.append(str(ipaddress.ip_address(host)))
        except ValueError: domains.append(host.casefold().rstrip('.'))
    return list(dict.fromkeys(domains)), list(dict.fromkeys(ips))


def _target_equals(target, value, field):
    host = _target_host(target)
    if not host:return False
    if field=='ip':
        try:return ipaddress.ip_address(host)==ipaddress.ip_address(value)
        except ValueError:return False
    return host.casefold().rstrip('.')==value.casefold().rstrip('.')


PARAMETER_LABELS = {'command':'命令','cmd':'命令','cwd':'工作目录','workdir':'工作目录',
    'prompt':'提示词','negative_prompt':'反向提示词','resolution':'分辨率','aspect_ratio':'画面比例',
    'enable_audio':'生成音频','image':'输入图片','image_path':'输入图片','image_url':'输入图片地址','output_dir':'输出目录',
    'output_path':'输出路径','path':'文件路径','url':'请求地址','uri':'请求地址','endpoint':'请求地址',
    'query':'查询内容','description':'操作说明','duration':'时长','timeout':'超时设置'}


def _result_view(result, command=None):
    """Readable facts from the recorded return; no inferred task completion."""
    view = {'status':'unknown','label':'未取得返回','facts':[],'outputs':[], 'sourceTruncated':False}
    if result is None: return view
    view['label'] = '已取得工具返回'
    decoded = _object(result)
    if isinstance(decoded, dict) and 'text' in decoded:
        decoded = _object(decoded['text'])
    elif isinstance(decoded, list):
        blocks = [_readable(member) for member in decoded]
        decoded = _object('\n'.join(blocks))
    view['parsed'] = decoded
    if isinstance(decoded, str):
        # WorkBuddy's Bash transcript wraps stdout, stderr and the actual
        # command exit code. Parse these labelled sections rather than treating
        # the entire transcript as returned business data.
        sections = list(re.finditer(r'(?m)^(Command|Stdout|Stderr|Exit Code|Signal):\s*', decoded))
        parts = {match.group(1):decoded[match.end():sections[i+1].start() if i+1<len(sections) else len(decoded)].strip()
                 for i, match in enumerate(sections)}
        if 'Stdout' in parts:
            view['stdout'] = parts['Stdout']
            view['stderr'] = parts.get('Stderr')
            code = parts.get('Exit Code', '')
            if re.fullmatch(r'-?\d+', code): view['exitCode'] = int(code)
            decoded = _object(parts['Stdout'])
            if not isinstance(decoded,str): view['parsed'] = decoded
        elif command:
            view['stdout'] = decoded
    if isinstance(decoded, dict):
        if view.get('exitCode') is None:
            for field in ('exit_code','exitCode'):
                if isinstance(decoded.get(field), int) and not isinstance(decoded[field], bool):
                    view['exitCode'] = decoded[field]; break
        for field in ('stdout','stderr'):
            if isinstance(decoded.get(field), str): view[field] = decoded[field]
        status = decoded.get('status')
        if isinstance(status, str):
            view['status'] = status
            view['label'] = {'completed':'工具返回：完成','failed':'工具返回：失败','error':'工具返回：错误',
                             'success':'工具返回：成功','pending':'工具返回：等待中'}.get(status, '工具状态：' + status)
        for field in ('error','message'):
            if decoded.get(field): view[field] = decoded[field]
        for field in ('videos','images','files','outputs','artifacts'):
            members = decoded.get(field)
            if isinstance(members, list):
                for member in members[:12]:
                    if isinstance(member, str): view['outputs'].append({'path':member,'label':member})
                    elif isinstance(member, dict):
                        path = member.get('path') or member.get('localPath') or member.get('local_path') or member.get('file_path') or member.get('output_path') or member.get('url')
                        if path: view['outputs'].append({'path':path,'label':member.get('name') or path})
        for field in ('path','localPath','local_path','file_path','output_path','video_path','video_url','download_url'):
            if isinstance(decoded.get(field), str): view['outputs'].append({'path':decoded[field],'label':decoded[field]})
        view['outputs'] = list({str(v['path']):v for v in view['outputs']}.values())
    if 'exitCode' in view:
        view['status'] = 'exit_zero' if view['exitCode'] == 0 else 'exit_nonzero'
        view['label'] = '命令退出码 ' + str(view['exitCode'])
    # Read a complete current_condition value even when `head -c` truncated
    # later fields. Only JSON decoding produces weather facts; no regex guesses
    # about temperature or completion of the whole response.
    weather = _object(view['stdout']) if isinstance(view.get('stdout'),str) else decoded
    if isinstance(weather, str):
        match = re.search(r'"current_condition"\s*:\s*', weather)
        if match:
            try:
                conditions, _ = json.JSONDecoder().raw_decode(weather[match.end():])
                weather = {'current_condition': conditions}
                view['sourceTruncated'] = True
            except ValueError: pass
    if isinstance(weather, dict) and isinstance(weather.get('current_condition'), list) and weather['current_condition']:
        condition = weather['current_condition'][0]
        if isinstance(condition, dict):
            for field, label, unit in (('temp_C','气温','℃'),('FeelsLikeC','体感温度','℃'),('humidity','湿度','%')):
                if isinstance(condition.get(field),(str,int,float)):
                    view['facts'].append({'label':label,'value':str(condition[field])+unit})
            description = condition.get('lang_zh') or condition.get('weatherDesc')
            if isinstance(description, list) and description and isinstance(description[0], dict) and description[0].get('value'):
                view['facts'].append({'label':'天气','value':str(description[0]['value'])})
            if view['facts']: view['type'] = 'weather'
    if command and re.search(r'\bhead\s+(?:-[^\s]+\s+)*-c\s*\d+',command):
        view['sourceTruncated'] = True
    return view


def _presentation(arguments=None, result=None, command=None, content=None):
    values = arguments.get('params') if isinstance(arguments,dict) and isinstance(arguments.get('params'),dict) else arguments
    parameters = []
    prompt = None
    if isinstance(values, dict):
        prompt = values.get('prompt') if isinstance(values.get('prompt'),str) else None
        for key, value in values.items():
            if key != 'prompt': parameters.append({'key':key,'label':PARAMETER_LABELS.get(key,key),'value':value})
    elif values is not None:
        parameters.append({'key':'input','label':'输入参数','value':values})
    return {'prompt':prompt,'parameters':parameters,'result':_result_view(result,command),
            'bodyText':_readable(content)}


def _summary(presentation, kind, tool, command, destination):
    outcome = {k:v for k,v in presentation['result'].items() if k not in ('parsed','stdout','stderr')}
    preview = '；'.join(f["label"]+' '+f["value"] for f in outcome['facts'])
    if not preview: preview = _snippet(presentation['result'].get('stdout') or _readable(presentation['result'].get('parsed')),'',size=180)
    parameters = [dict(p,value=p['value'] if p['value'] is None or isinstance(p['value'],(bool,int,float))
                       else _snippet(_text(p['value']),size=160)) for p in presentation['parameters'][:8]]
    for field in ('error','message'):
        if field in outcome: outcome[field] = _snippet(_text(outcome[field]),size=240)
    outcome['status'] = _snippet(outcome['status'],size=80)
    outcome['label'] = _snippet(outcome['label'],size=160)
    outcome['outputs'] = [{k:_snippet(_text(v),size=500) for k,v in output.items()} for output in outcome['outputs']]
    description = next((p['value'] for p in presentation['parameters'] if p['key']=='description' and isinstance(p['value'],str)),None)
    action = _snippet(description,size=100) if description else ('执行命令 · '+', '.join(_programs(command)[:3]) if command else ('调用工具 · '+tool if tool else KINDS.get(kind,kind)))
    return {'action':_snippet(action,size=180),'target':destination,
            'promptPreview':_snippet(presentation['prompt'],size=200),'parameters':parameters,
            'outcome':outcome,'resultPreview':preview}


def _bounded(value, limit=60000):
    remaining = [limit]; truncated = [False]
    def walk(member):
        if isinstance(member,str):
            take = min(len(member),max(0,remaining[0]));remaining[0] -= take
            if take < len(member):truncated[0] = True
            return member[:take]
        if isinstance(member,list):return [walk(v) for v in member]
        if isinstance(member,dict):return {k:walk(v) for k,v in member.items()}
        return member
    return walk(value), truncated[0]


def _meaningful_request(value):
    """Keep short confirmations visible as conversation, not task descriptions."""
    if not isinstance(value,str) or not value.strip():return False
    clean=re.sub(r'[\s，。！!?.？,]+','',value).casefold()
    return bool(clean) and clean not in {'做','继续','对','对的','好的','好','是','是的','嗯','嗯嗯','ok','okay','yes','go','done',
                         '确认','允许','已发','已发送','已经发送','已经连接','已连接','已经发布','已发布',
                         '已经安装','已安装','完成','已经完成','继续做','开始做','补齐'}


def _request_metadata(event):
    identity=extract_turn_identity(event);item=_inner(event);parents=[]
    if event.get('kind')=='session_metadata' or event.get('sourceType')=='session_meta':
        source=item.get('source');source=source if isinstance(source,dict) else {}
        sub=source.get('subagent');sub=sub if isinstance(sub,dict) else {}
        spawn=sub.get('thread_spawn');spawn=spawn if isinstance(spawn,dict) else {}
        parents=list(dict.fromkeys(v for v in (item.get('parent_thread_id'),spawn.get('parent_thread_id'))
                                  if isinstance(v,str) and 0<len(v)<=300))
    identity.update(parentSessionId=parents[0] if len(parents)==1 else None,
                    parentSessionConflict=len(parents)>1,requestEvidenceVersion=REQUEST_EVIDENCE_VERSION)
    return identity


def _request_text(event):
    value,control=user_text(event)
    return re.sub(r'^\s*##\s*My request:\s*','',value).strip(),control


def _activity(meta,summary,request=None):
    """Readable capability facts. An outer return never authenticates child returns."""
    calls=[dict(c) for c in meta.get('capabilities',[]) if c.get('type') in ('skill','mcp')][:16]
    if not calls:return None
    first=calls[0];kind=first['type'];name=first.get('serviceName') or first['name']
    action=('加载 ' if first.get('evidence')=='loaded' else '读取 ') if kind=='skill' else '调用 '
    title=action+name+(' 说明' if kind=='skill' and first.get('evidence')!='loaded' else '')
    if kind=='mcp' and first.get('method'):title+=' · '+first['method']
    if len(calls)>1:title+=' 等 '+str(len(calls))+' 项能力'
    arguments=first.get('arguments');parameters=[]
    if isinstance(arguments,dict):
        parameters=[{'key':k,'label':PARAMETER_LABELS.get(k,k),'value':_bounded(v,1200)[0]} for k,v in list(arguments.items())[:6]]
    elif arguments is not None:parameters=[{'key':'input','label':'调用输入','value':_bounded(arguments,1200)[0]}]
    if not parameters:parameters=summary.get('parameters',[])[:6]
    if first.get('path'):parameters=[{'key':'path','label':'说明文件','value':first['path']}]+parameters
    wrapped=any(c.get('sourceOffset') is not None for c in calls)
    returned=bool(summary.get('resultRecordId') or meta.get('hasResult'))
    outcome=summary.get('outcome') or {}
    failed=outcome.get('status') in ('failed','error','failure','exit_nonzero') or bool(outcome.get('error'))
    error_text=str(outcome.get('error') or outcome.get('message') or summary.get('resultPreview') or '请查看完整返回')[:180]
    if wrapped:
        return_kind='wrapper_result' if returned else 'missing'
        brief=('外层执行返回失败：'+error_text+'；子调用返回尚未确认。' if failed else '已取得外层执行返回；子调用的独立返回尚未配对。') if returned else '子调用已出现在执行请求中；尚未取得对应返回。'
    elif kind=='skill':
        return_kind=('tool_result' if failed else 'instructions') if returned else 'missing'
        brief=('Skill 返回失败：'+error_text if failed else '已取得 Skill 操作说明；后续动作见关联过程。') if returned else '已有 Skill 调用记录；尚未取得返回。'
    else:
        return_kind='tool_result' if returned else 'missing'
        brief=summary.get('resultPreview') or ('已取得对应工具返回。' if returned else '尚未取得对应工具返回。')
    return {'title':title,'kind':'skill_load' if kind=='skill' and first.get('evidence')=='loaded' else 'skill_read' if kind=='skill' else 'mcp_call',
            'name':name,'method':first.get('method'),'parameters':parameters,'calls':calls,
            'returnKind':return_kind,'returnSummary':brief[:300],
            'request':request or {'text':None,'association':'unknown','basis':'需求关联未确认'}}


def _session_projection(event):
    item = _inner(event)
    role = event.get('role') or item.get('role')
    source_kind = event.get('kind', 'source_record')
    fields = []
    user = None
    control = None
    arguments = result = command = tool = function = executor = None
    kind = 'context'
    label = '源日志记录 · ' + str(source_kind)
    if source_kind == 'user_message' or (source_kind == 'message' and role == 'user'):
        kind, label = 'user', KINDS['user']
        user, control = _request_text(event)
        if control:
            label = '用户角色的' + ('授权记录' if control == 'approval' else '系统或后台通知')
        fields.append(('user', _readable(item.get('content', item.get('message', item.get('text', item))))))
    elif source_kind == 'assistant_message' or (source_kind == 'message' and role == 'assistant'):
        kind, label = 'reply', KINDS['reply']
        fields.append(('reply', _readable(item.get('content', item.get('message', item.get('text', item))))))
    elif source_kind in CALLS:
        kind, label = 'tool_call', KINDS['tool_call']
        tool = _ident(event.get('name') or item.get('name') or item.get('tool_name') or item.get('toolName'))
        function_value = item.get('function')
        function = _ident(function_value.get('name') if isinstance(function_value, dict) else function_value)
        if not tool:
            tool = function
        if not function and (event.get('sourceType') == 'function_call' or event.get('sourceSubtype') == 'function_call' or item.get('type') == 'function_call'):
            function = tool
        arguments = _arguments(item)
        if (tool and tool.rsplit('.',1)[-1].casefold() == 'deferexecutetool'
                and isinstance(arguments,dict) and _ident(arguments.get('toolName'))):
            executor, tool = tool, arguments['toolName']
        command = _command(arguments, item)
        fields.append(('arguments', _text(arguments if arguments is not None else item)))
        result = item.get('aggregated_output', item.get('stdout', item.get('result')))
        if result is not None:
            fields.append(('result', _text(result)))
    elif source_kind == 'tool_result':
        kind, label = 'tool_result', KINDS['tool_result']
        tool = _ident(event.get('name') or item.get('name'))
        result = item.get('output', item.get('result', item.get('content', item)))
        fields.append(('result', _text(result)))
    elif source_kind == 'reasoning':
        kind, label = 'reasoning', KINDS['reasoning']
        fields.append(('context', _readable(item.get('content') or item.get('rawContent') or item.get('summary') or item.get('text') or '')))
    else:
        fields.append(('context', _text(event.get('payload'))))
    # All retained fields are indexed, including provider metadata and unknown
    # payload extensions. Known content is separately searchable by location.
    fields.append(('metadata', _json(event)))
    targets = _targets(arguments, command)
    target_domains, target_ips = _target_hosts(targets)
    provider = item.get('providerData') or {}
    if not isinstance(provider, dict):
        provider = {}
    source_fields = event.get('sourceFields') or {}
    if not isinstance(source_fields,dict):source_fields = {}
    metadata = {'sourceKind': source_kind, 'kindLabel': label, 'userInput': user or None, 'control': control,
                'tool': tool, 'executor':executor,'function': function, 'command': command, 'programs': _programs(command),
                'destination': targets[0] if targets else None, 'destinations': targets,
                'targetDomains':target_domains,'targetIps':target_ips,
                'hasResult':result is not None,
                'addressBasis': 'tool_argument_target' if targets else 'not_collected',
                'callId': _ident(event.get('callId') or item.get('callId') or item.get('call_id')),
                'sourceMessageId': _ident(item.get('id')), 'parentMessageId': _ident(item.get('parentId')),
                'turnId': _ident(item.get('turn_id') or item.get('turnId') or source_fields.get('turn_id')),
                'requestId': _ident(provider.get('conversationRequestId')),
                'role': role}
    metadata['capabilities'] = _capabilities(tool, arguments, item, event, command, kind)
    metadata.update(_request_metadata(event))
    metadata['capabilityEvidenceVersion'] = capability_evidence.VERSION
    metadata['summary'] = _summary(_presentation(arguments,result,command),kind,tool,command,metadata['destination'])
    title = label
    if kind == 'user' and user:
        title += ' · ' + re.sub(r'\s+', ' ', user)[:70]
    elif tool:
        title += ' · ' + tool[:100]
    if command:
        title += ' · ' + ', '.join(metadata['programs'][:3])
    return kind, title, metadata, fields


def _app_projection(record):
    network = record.get('source') == 'workbuddy_network_context'
    kind = 'http' if network else 'context'
    destination = record.get('destination') or None
    target_domains,target_ips = _target_hosts([destination] if network and destination else [])
    metadata = {'sourceKind': record.get('source'), 'kindLabel': KINDS[kind],
                'destination': destination, 'destinations': [destination] if destination else [],
                'addressBasis': 'captured_request_target' if network and destination else 'not_collected',
                'tool': None, 'executor':None,'function': None, 'command': None, 'programs': [], 'callId': None,
                'targetDomains':target_domains,'targetIps':target_ips,
                'sourceMessageId': None, 'parentMessageId': None, 'turnId': None, 'requestId': None,
                'userInput': None, 'role': None, 'control': None, 'capabilities': []}
    metadata['summary'] = _summary(_presentation(),kind,None,None,destination)
    body = record.get('body', '')
    fields = [('context', body), ('metadata', _json({k: v for k, v in record.items() if k != 'body'}))]
    parsed = _object(body)
    messages = parsed if isinstance(parsed, list) else parsed.get('messages', []) if isinstance(parsed, dict) else []
    if isinstance(messages, list):
        for message in messages:
            if not isinstance(message, dict):
                continue
            role = message.get('role')
            location = {'user': 'user', 'assistant': 'reply', 'tool': 'result'}.get(role, 'context')
            fields.append((location, _readable(message.get('content'))))
            for call in message.get('tool_calls', []) if isinstance(message.get('tool_calls'), list) else []:
                if isinstance(call, dict):
                    function = call.get('function')
                    fields.append(('arguments', _text(function.get('arguments', call) if isinstance(function,dict) else call)))
    return kind, KINDS[kind] + (' · ' + destination[:100] if destination else ''), metadata, fields


class DataCenter:
    def __init__(self, collections):
        self.collections = collections
        self.devices = collections.devices
        self.sessions = collections.sessions
        self.path = Path(self.sessions.path).parent / 'data-center.db'
        # Platform GET and the lazily created analysis retriever can share this
        # file. Serialize incremental index writes across those instances too.
        with _LOCKS_GUARD:
            self.lock = _LOCKS.setdefault(str(self.path.resolve()),threading.RLock())
        with self._db() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS dc_state(name TEXT PRIMARY KEY,value TEXT);
                CREATE TABLE IF NOT EXISTS dc_records(key TEXT PRIMARY KEY,collector TEXT,owner TEXT,device TEXT,
                    ident TEXT,source TEXT,session TEXT,kind TEXT,title TEXT,stamp REAL,received REAL,
                    seq INTEGER,metadata TEXT,UNIQUE(collector,owner,device,ident));
                CREATE INDEX IF NOT EXISTS dc_records_scope ON dc_records(owner,device,source,session,stamp,seq);
                CREATE INDEX IF NOT EXISTS dc_records_recent ON dc_records(stamp DESC,key);
                CREATE INDEX IF NOT EXISTS dc_records_duplicates ON dc_records(collector,owner,ident,source);
                CREATE INDEX IF NOT EXISTS dc_records_physical ON dc_records(collector,seq);
                CREATE INDEX IF NOT EXISTS dc_records_capability_version ON dc_records(kind,coalesce(json_extract(metadata,'$.capabilityEvidenceVersion'),0),seq);
                CREATE INDEX IF NOT EXISTS dc_records_call_id ON dc_records(owner,device,source,session,json_extract(metadata,'$.callId'),kind);
                CREATE INDEX IF NOT EXISTS dc_records_message_id ON dc_records(owner,device,source,session,json_extract(metadata,'$.sourceMessageId'));
                CREATE INDEX IF NOT EXISTS dc_records_parent_id ON dc_records(owner,device,source,session,json_extract(metadata,'$.parentMessageId'));
                CREATE INDEX IF NOT EXISTS dc_records_turn_id ON dc_records(owner,device,source,session,json_extract(metadata,'$.turnId'),kind);
                CREATE INDEX IF NOT EXISTS dc_records_user_stamp ON dc_records(owner,device,source,session,stamp DESC,seq DESC)
                    WHERE collector='sessionlens' AND kind='user';
                CREATE INDEX IF NOT EXISTS dc_records_request_version ON dc_records(coalesce(json_extract(metadata,'$.requestEvidenceVersion'),0),seq)
                    WHERE collector='sessionlens';
                CREATE INDEX IF NOT EXISTS dc_records_session_parent ON dc_records(owner,device,source,session,seq)
                    WHERE collector='sessionlens' AND json_extract(metadata,'$.sourceKind')='session_metadata';
                CREATE TABLE IF NOT EXISTS dc_fields(record TEXT,ordinal INTEGER,location TEXT,value TEXT,
                    PRIMARY KEY(record,ordinal));
                CREATE INDEX IF NOT EXISTS dc_fields_location ON dc_fields(record,location);
            ''')
            row = db.execute("SELECT value FROM dc_state WHERE name='version'").fetchone()
            if not row or row[0] != VERSION:
                db.execute('DELETE FROM dc_fields');db.execute('DELETE FROM dc_records');db.execute('DELETE FROM dc_state')
                db.execute("INSERT INTO dc_state VALUES('version',?)", (VERSION,))
        os.chmod(self.path, 0o600)

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=20)
        db.row_factory = sqlite3.Row
        db.create_function('dc_match', 3, lambda text, value, field: int(_matches(text, value, field)), deterministic=True)
        db.create_function('dc_snippet', 3, _snippet, deterministic=True)
        try:
            with db:
                yield db
        finally:
            db.close()

    def _scope(self, db, owner=None):
        with self.devices.connect() as source:
            rows = source.execute('''SELECT id AS device,owner,id AS canonical,name FROM devices
                WHERE revoked=0 AND id NOT IN (SELECT alias FROM device_aliases)
                UNION ALL SELECT a.alias AS device,a.owner,d.id AS canonical,d.name
                FROM device_aliases a JOIN devices d ON d.id=a.canonical AND d.owner=a.owner
                WHERE d.revoked=0''').fetchall()
        db.execute('CREATE TEMP TABLE dc_scope(device TEXT,owner TEXT,canonical TEXT,name TEXT,PRIMARY KEY(device,owner))')
        db.executemany('INSERT OR IGNORE INTO dc_scope VALUES(?,?,?,?)',
            [(r['device'], r['owner'], r['canonical'], r['name']) for r in rows if owner is None or r['owner'] == owner])

    @staticmethod
    def _state(db, name, default='0'):
        row = db.execute('SELECT value FROM dc_state WHERE name=?', (name,)).fetchone()
        return row[0] if row else default

    @staticmethod
    def _set_state(db, name, value):
        value = str(value)
        old = db.execute('SELECT value FROM dc_state WHERE name=?',(name,)).fetchone()
        if not old or old[0]!=value:db.execute('INSERT OR REPLACE INTO dc_state VALUES(?,?)', (name, value))

    @staticmethod
    def _put(db, collector, owner, device, ident, source, session, raw, seq, received):
        key = _key(collector,owner,device,ident)
        digest = hashlib.sha256(raw.encode()).hexdigest()
        old = db.execute("SELECT key,received,json_extract(metadata,'$.recordDigest') AS digest FROM dc_records WHERE collector=? AND seq=?",(collector,seq)).fetchone()
        if old and old['key']==key and old['digest']==digest:
            if old['received'] != received:
                db.execute('UPDATE dc_records SET received=? WHERE key=?',(received,key))
            return True
        try:
            record = json.loads(raw)
            if not isinstance(record, dict):
                return False
            if collector == 'sessionlens':
                if record.get('source') not in ('codex', 'workbuddy'):
                    return False
                kind, title, metadata, fields = _session_projection(record)
            else:
                kind, title, metadata, fields = _app_projection(record)
            stamp = event_seconds(record.get('timestamp')) or None
        except (ValueError, TypeError, AttributeError):
            return False
        metadata['recordDigest'] = digest
        # AppLens canonical merging can move an existing physical row without
        # changing its receipt; seq is stable and removes its old alias index.
        if old and old['key'] != key:
            db.execute('DELETE FROM dc_fields WHERE record=?', (old['key'],))
            db.execute('DELETE FROM dc_records WHERE key=?', (old['key'],))
        db.execute('INSERT OR REPLACE INTO dc_records VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (key, collector, owner, device, ident, source, session, kind, title, stamp, received, seq, _json(metadata)))
        db.execute('DELETE FROM dc_fields WHERE record=?', (key,))
        db.executemany('INSERT INTO dc_fields VALUES(?,?,?,?)',
            [(key, i, location, text) for i, (location, text) in enumerate(fields) if text])
        return True

    def _sync(self, db):
        """Incremental raw cursors; bounded memory, no source writes/model calls."""
        with self.sessions.connect() as raw:
            maximum = raw.execute('SELECT coalesce(max(rowid),0) FROM session_events').fetchone()[0]
            self._set_state(db, 'session_raw_count', raw.execute('SELECT count(*) FROM session_events').fetchone()[0])
            last = int(self._state(db, 'session_seq'))
            if maximum < last:
                keys = [r[0] for r in db.execute("SELECT key FROM dc_records WHERE collector='sessionlens'")]
                db.executemany('DELETE FROM dc_fields WHERE record=?', [(k,) for k in keys])
                db.execute("DELETE FROM dc_records WHERE collector='sessionlens'");last = 0
            for row in raw.execute("SELECT rowid,owner,device,id,session,event,json_extract(CASE WHEN json_valid(event) THEN event ELSE '{}' END,'$.source') AS source FROM session_events WHERE rowid>? AND rowid<=? ORDER BY rowid", (last, maximum)):
                self._put(db, 'sessionlens', row['owner'], row['device'], row['id'],
                          row['source'],
                          row['session'], row['event'], row['rowid'], None)
            self._set_state(db, 'session_seq', maximum)
        with self.devices.connect() as raw:
            maximum, receipt = raw.execute('SELECT coalesce(max(rowid),0),coalesce(max(received),0) FROM applens_model_context').fetchone()
            self._set_state(db, 'app_raw_count', raw.execute('SELECT count(*) FROM applens_model_context').fetchone()[0])
            last = int(self._state(db, 'app_seq')); previous = float(self._state(db, 'app_receipt'))
            # Alias mapping changed: stream AppLens rows again, preserving raw
            # identity after a source row was moved to its canonical device.
            alias_signature = _json([list(r) for r in raw.execute('SELECT alias,canonical,owner FROM device_aliases ORDER BY alias')])
            changed_aliases = alias_signature != self._state(db, 'aliases', '')
            if maximum < last or changed_aliases:
                db.execute("DELETE FROM dc_fields WHERE record IN (SELECT key FROM dc_records WHERE collector='applens')")
                db.execute("DELETE FROM dc_records WHERE collector='applens'");last = 0;previous = 0
            rows = raw.execute('''SELECT c.rowid,c.device_id,c.request_id,c.data,c.received,d.owner
                FROM applens_model_context c JOIN devices d ON d.id=c.device_id
                WHERE c.rowid<=? AND (c.rowid>? OR c.received>=?) ORDER BY c.rowid''', (maximum, last, previous))
            for row in rows:
                try:record = json.loads(row['data'])
                except (ValueError,TypeError):continue
                if not isinstance(record,dict):continue
                self._put(db, 'applens', row['owner'], row['device_id'], row['request_id'],
                          'workbuddy', str(record.get('sessionId') or 'unknown'), row['data'], row['rowid'], row['received'])
            self._set_state(db, 'app_seq', maximum);self._set_state(db, 'app_receipt', receipt)
            self._set_state(db, 'aliases', alias_signature)
        # Small bounded catch-up only; deployment may explicitly finish the
        # migration without rebuilding text fields or source watermarks.
        self._refresh_capabilities(db, limit=12, max_seconds=.025)
        self._refresh_request_evidence(db,limit=12,max_seconds=.025)

    def _refresh_request_evidence(self,db,limit=100,max_seconds=.1):
        start=time.monotonic();updated=0
        rows=db.execute("SELECT key,kind FROM dc_records INDEXED BY dc_records_request_version WHERE collector='sessionlens' AND coalesce(json_extract(metadata,'$.requestEvidenceVersion'),0)<? ORDER BY coalesce(json_extract(metadata,'$.requestEvidenceVersion'),0),seq LIMIT ?",(REQUEST_EVIDENCE_VERSION,limit)).fetchall()
        for row in rows:
            if updated and time.monotonic()-start>=max_seconds:break
            metadata=json.loads(db.execute('SELECT metadata FROM dc_records WHERE key=?',(row['key'],)).fetchone()[0])
            raw=db.execute("SELECT CASE WHEN length(value)<=4000000 THEN value END FROM dc_fields WHERE record=? AND location='metadata' ORDER BY ordinal DESC LIMIT 1",(row['key'],)).fetchone()
            try:event=json.loads(raw[0]) if raw and raw[0] else None
            except (ValueError,TypeError):event=None
            if isinstance(event,dict):
                metadata.update(_request_metadata(event))
                if row['kind']=='user':
                    text,control=_request_text(event);metadata.update(userInput=text or None,control=control)
            else:metadata.update(requestEvidenceVersion=REQUEST_EVIDENCE_VERSION,requestEvidenceUnavailable=True)
            db.execute('UPDATE dc_records SET metadata=? WHERE key=?',(_json(metadata),row['key']))
            updated+=1
        remaining=db.execute("SELECT count(*) FROM dc_records INDEXED BY dc_records_request_version WHERE collector='sessionlens' AND coalesce(json_extract(metadata,'$.requestEvidenceVersion'),0)<?",(REQUEST_EVIDENCE_VERSION,)).fetchone()[0]
        return {'updated':updated,'remaining':remaining,'version':REQUEST_EVIDENCE_VERSION}

    def refresh_request_evidence(self,limit=100,max_seconds=.1):
        """Resumable metadata-only repair; retained text and source cursors stay intact."""
        if not isinstance(limit,int) or not 1<=limit<=1000 or not 0<max_seconds<=5:raise ValueError('需求关联迁移批次无效')
        with self.lock,self._db() as db:return self._refresh_request_evidence(db,limit,max_seconds)

    @staticmethod
    def _capability_remaining(db):
        return db.execute("SELECT count(*) FROM dc_records WHERE kind='tool_call' AND coalesce(json_extract(metadata,'$.capabilityEvidenceVersion'),0)<?", (capability_evidence.VERSION,)).fetchone()[0]

    def _refresh_capabilities(self, db, limit=100, max_seconds=.1):
        start = time.monotonic(); changed = 0
        rows = db.execute("SELECT key,metadata FROM dc_records WHERE kind='tool_call' AND coalesce(json_extract(metadata,'$.capabilityEvidenceVersion'),0)<? ORDER BY coalesce(json_extract(metadata,'$.capabilityEvidenceVersion'),0),seq LIMIT ?", (capability_evidence.VERSION, limit)).fetchall()
        for row in rows:
            if changed and time.monotonic() - start >= max_seconds: break
            metadata = json.loads(row['metadata'])
            raw = db.execute("SELECT value FROM dc_fields WHERE record=? AND location='metadata' ORDER BY ordinal DESC LIMIT 1", (row['key'],)).fetchone()
            evidence = []
            if raw:
                try:
                    event = json.loads(raw[0]); item = _inner(event); arguments = _arguments(item)
                    evidence = _capabilities(metadata.get('tool'), arguments, item, event, _command(arguments, item), 'tool_call')
                except (ValueError, TypeError, AttributeError): pass
            metadata['capabilities'] = evidence
            metadata['capabilityEvidenceVersion'] = capability_evidence.VERSION
            db.execute('UPDATE dc_records SET metadata=? WHERE key=?', (_json(metadata), row['key']))
            changed += 1
        return {'updated': changed, 'remaining': self._capability_remaining(db), 'version': capability_evidence.VERSION}

    def refresh_capabilities(self, limit=100, max_seconds=.1):
        """Explicit resumable metadata migration; never deletes source/text rows."""
        if not isinstance(limit, int) or not 1 <= limit <= 1000 or not 0 < max_seconds <= 5:
            raise ValueError('能力索引迁移批次无效')
        with self.lock, self._db() as db:
            return self._refresh_capabilities(db, limit, max_seconds)

    @staticmethod
    def _validate(params):
        if not isinstance(params, dict) or set(params) - PARAMS:
            raise ValueError('不支持的搜索参数')
        values = {k: str(v) for k, v in params.items()}
        for key, allowed in [('collector', {'all', 'applens', 'sessionlens'}),
                             ('application', {'all', 'codex', 'workbuddy'}),
                             ('object', {'all', 'tool', 'skill', 'mcp'}),
                             ('group', {'record', 'request'}),
                             ('kind', {'all', *KINDS}), ('location', {'all', *LOCATIONS} - {'metadata'})]:
            if values.get(key, 'record' if key=='group' else 'all') not in allowed:
                raise ValueError('不支持的筛选：' + key)
        if 'groupId' in values and (values.get('group')!='request' or not re.fullmatch(r'rq_[0-9a-f]{64}',values['groupId'])):
            raise ValueError('需求分组标识无效')
        try:
            page, size = int(values.get('page', 1)), int(values.get('pageSize', 20))
        except ValueError:
            raise ValueError('分页参数无效') from None
        if not 1 <= page <= 1000000 or not 1 <= size <= 100:
            raise ValueError('page 至少为 1，pageSize 为 1–100')
        dates = {}
        for field in ('after', 'before'):
            if values.get(field):
                stamp = event_seconds(values[field])
                if not stamp:
                    raise ValueError(field + ' 需要有效的时间戳或 ISO 日期')
                dates[field] = stamp
        if dates.get('after', 0) > dates.get('before', float('inf')):
            raise ValueError('起始时间不能晚于结束时间')
        return values, page, size, dates

    def _snapshot(self, db, values, owner, purpose='search'):
        query = {k:v for k,v in values.items() if k not in ('snapshot','page','pageSize','groupId')}
        query.setdefault('q','')
        query.setdefault('group','record')
        for key in ('collector','application','device','kind','location','object'):
            query.setdefault(key,'all')
        query.setdefault('after','');query.setdefault('before','')
        fingerprint = hashlib.sha256(_json([owner,query,purpose]).encode()).hexdigest()
        if values.get('snapshot'):
            encoded = values['snapshot']
            if len(encoded)>650 or not re.fullmatch('[A-Za-z0-9_-]+',encoded):
                raise ValueError('查询快照无效，请重新搜索')
            try:
                snapshot = json.loads(base64.b64decode(encoded+'='*(-len(encoded)%4),altchars=b'-_',validate=True))
            except (ValueError,UnicodeError):
                raise ValueError('查询快照无效，请重新搜索') from None
            if (not isinstance(snapshot,dict) or set(snapshot)!={'v','sessionMax','appMax','appReceipt','query'}
                or type(snapshot.get('v')) is not int or snapshot.get('v')!=1 or snapshot.get('query')!=fingerprint
                or any(type(snapshot.get(k)) is not int or not 0<=snapshot[k]<=2**63-1 for k in ('sessionMax','appMax'))
                or type(snapshot.get('appReceipt')) not in (int,float) or not math.isfinite(snapshot['appReceipt'])
                or not 0<=snapshot['appReceipt']<253402300800):
                raise ValueError('查询快照无效或筛选已变化，请重新搜索')
            if (snapshot['sessionMax']>int(self._state(db,'session_seq'))
                or snapshot['appMax']>int(self._state(db,'app_seq'))
                or snapshot['appReceipt']>float(self._state(db,'app_receipt'))):
                raise ValueError('查询快照已失效，请重新搜索')
        else:
            snapshot = {'v':1,'sessionMax':int(self._state(db,'session_seq')),
                        'appMax':int(self._state(db,'app_seq')),'appReceipt':float(self._state(db,'app_receipt')),
                        'query':fingerprint}
            encoded = base64.urlsafe_b64encode(_json(snapshot).encode()).decode().rstrip('=')
        return encoded,snapshot

    @staticmethod
    def _term_sql(term, location, args):
        field, op, value = term
        if field in ('keyword', 'ip', 'domain'):
            scope = ' AND f.location=?' if location != 'all' else ''
            if scope:
                args.append(location)
            args.extend([value, field])
            sql = 'EXISTS(SELECT 1 FROM dc_fields f WHERE f.record=r.key' + scope + ' AND dc_match(f.value,?,?))'
        elif field == 'location':
            args.append(value.lower())
            sql = 'EXISTS(SELECT 1 FROM dc_fields f WHERE f.record=r.key AND f.location=?)'
        elif field == 'device':
            args.extend([value, value, value])
            sql = '(s.canonical=? OR s.canonical IN (SELECT canonical FROM dc_scope WHERE device=?) OR lower(s.name)=lower(?))'
        elif field == 'command':
            args.extend([value, value])
            sql = "(lower(json_extract(r.metadata,'$.command'))=lower(?) OR EXISTS(SELECT 1 FROM json_each(r.metadata,'$.programs') p WHERE lower(p.value)=lower(?)))"
        elif field in ('target_domain', 'target_ip'):
            value = str(ipaddress.ip_address(value)) if field == 'target_ip' else value.casefold().rstrip('.')
            args.append(value)
            property_name = 'targetIps' if field == 'target_ip' else 'targetDomains'
            sql = "EXISTS(SELECT 1 FROM json_each(r.metadata,'$."+property_name+"') target WHERE lower(target.value)=lower(?))"
        elif field in ('tool', 'function', 'executor'):
            args.append(value)
            sql = "lower(json_extract(r.metadata,'$." + field + "'))=lower(?)"
        elif field in ('skill', 'mcp', 'mcp_method'):
            capability = 'skill' if field == 'skill' else 'mcp'
            if field == 'mcp' and value.startswith('mcp__'):
                value = value[5:]
            args.extend([capability, value])
            member = 'method' if field == 'mcp_method' else 'name'
            match = "lower(json_extract(c.value,'$." + member + "'))=lower(?)"
            if field == 'mcp':
                args.append(value)
                match = '(' + match + " OR EXISTS(SELECT 1 FROM json_each(c.value,'$.serviceAliases') alias WHERE lower(alias.value)=lower(?)))"
            sql = ("EXISTS(SELECT 1 FROM json_each(r.metadata,'$.capabilities') c "
                   "WHERE json_extract(c.value,'$.type')=? AND " + match + ")")
        else:
            args.append(value)
            column = {'app': 'source', 'session': 'session'}.get(field, field)
            sql = 'lower(r.' + column + ')=lower(?)'
        return '(NOT coalesce(' + sql + ',0))' if op == '!=' else '(' + sql + ')'

    def _query_scope(self, db, values, dates, groups, snapshot):
        location = values.get('location', 'all')
        # Deduplication sees the same retained watermarks as the query itself.
        bounded_duplicates = DUPLICATES.replace('WHERE d.collector=r.collector',
            "WHERE ((d.collector='sessionlens' AND d.seq<=?) OR (d.collector='applens' AND d.seq<=? AND d.received<=?)) AND d.collector=r.collector")
        args = [snapshot['sessionMax'], snapshot['appMax'], snapshot['appReceipt']]
        where = [bounded_duplicates,
                 "((r.collector='sessionlens' AND r.seq<=?) OR (r.collector='applens' AND r.seq<=? AND r.received<=?))"]
        args.extend([snapshot['sessionMax'], snapshot['appMax'], snapshot['appReceipt']])
        for key, column in [('collector', 'r.collector'), ('application', 'r.source'), ('kind', 'r.kind')]:
            if values.get(key, 'all') != 'all':
                where.append(column + '=?'); args.append(values[key])
        if values.get('device', 'all') != 'all':
            where.append('(s.canonical=? OR s.canonical IN (SELECT canonical FROM dc_scope WHERE device=?))')
            args.extend([values['device']] * 2)
        if location != 'all':
            where.append('EXISTS(SELECT 1 FROM dc_fields l WHERE l.record=r.key AND l.location=?)'); args.append(location)
        object_type = values.get('object', 'all')
        if object_type == 'tool':
            where.append("r.kind='tool_call' AND json_extract(r.metadata,'$.tool') IS NOT NULL")
        elif object_type in ('skill', 'mcp'):
            where.append("EXISTS(SELECT 1 FROM json_each(r.metadata,'$.capabilities') c WHERE json_extract(c.value,'$.type')=?)")
            args.append(object_type)
        for key, operator in [('after', '>='), ('before', '<=')]:
            if key in dates:
                where.append('r.stamp' + operator + '?'); args.append(dates[key])
        join = ' FROM dc_records r JOIN dc_scope s ON s.device=r.device AND s.owner=r.owner'
        scope_records = db.execute('SELECT count(*)' + join + ' WHERE ' + ' AND '.join(where), args).fetchone()[0]
        if groups:
            where.append('(' + ' OR '.join('(' + ' AND '.join(self._term_sql(t, location, args) for t in group) + ')' for group in groups) + ')')
        return join, ' WHERE ' + ' AND '.join(where), args, scope_records

    @staticmethod
    def _facets(db, join):
        return {'devices': [{'id': r[0], 'name': r[1]} for r in db.execute('SELECT DISTINCT s.canonical,s.name' + join + ' ORDER BY s.name,s.canonical')],
                'applications': [r[0] for r in db.execute('SELECT DISTINCT r.source' + join + ' ORDER BY r.source')],
                'kinds': [r[0] for r in db.execute('SELECT DISTINCT r.kind' + join + ' ORDER BY r.kind')]}

    def _request_groups(self, db, matched_join, snapshot):
        """Group matched tool evidence by source request, before pagination.

        Read only identity and indexed association fields across the match set.
        Full record summaries are loaded for the selected groups' first ten
        members, or for an explicitly requested member page.
        """
        db.execute('CREATE TEMP TABLE dc_request_members(record TEXT PRIMARY KEY,group_id TEXT,request TEXT)')
        db.execute('CREATE INDEX dc_request_members_group ON dc_request_members(group_id)')
        rows = db.execute('''SELECT r.key,r.collector,r.owner,r.device,r.ident,r.source,r.session,r.kind,r.stamp,s.canonical,
            json_extract(r.metadata,'$.parentMessageId') AS parentMessageId,
            json_extract(r.metadata,'$.turnId') AS turnId,
            json_extract(r.metadata,'$.turnIdentityConflict') AS turnIdentityConflict,
            json_extract(r.metadata,'$.rootTurnId') AS rootTurnId,
            json_extract(r.metadata,'$.rootTurnIdentityConflict') AS rootTurnIdentityConflict,
            json_extract(r.metadata,'$.callId') AS callId,
            json_array_length(r.metadata,'$.capabilities') AS capabilityCount''' + matched_join)
        batch = []
        for row in rows:
            request = self._activity_request(db,row,dict(row),snapshot) if row['kind'] in ('tool_call','tool_result') or row['capabilityCount'] else {
                'text':None,'association':'unknown','basis':'需求关联未确认'}
            if request.get('recordId'):
                # Alias registrations retain the same source event identity;
                # prompt text never participates in this identity.
                anchor = _decode(request['recordId'])[3]
                identity = ['request',row['collector'],row['owner'],row['canonical'],row['source'],request.get('sourceSessionId') or row['session'],anchor,request.get('recordDigest')]
            else:
                identity = ['record',row['key']]
            group_id = 'rq_' + hashlib.sha256(_json(identity).encode()).hexdigest()
            batch.append((row['key'],group_id,_json(request)))
            if len(batch)>=200:
                db.executemany('INSERT INTO dc_request_members VALUES(?,?,?)',batch);batch=[]
        if batch:db.executemany('INSERT INTO dc_request_members VALUES(?,?,?)',batch)
        db.execute('''CREATE TEMP TABLE dc_request_groups AS
            SELECT m.group_id,count(*) AS recordCount,min(r.stamp) AS firstSeen,max(r.stamp) AS lastSeen,
                max(json_extract(m.request,'$.recordId') IS NOT NULL) AS assigned
            FROM dc_request_members m JOIN dc_records r ON r.key=m.record GROUP BY m.group_id''')
        db.execute('CREATE UNIQUE INDEX dc_request_groups_id ON dc_request_groups(group_id)')
        counts = db.execute('''SELECT count(*) AS groupTotal,coalesce(sum(assigned),0) AS requestGroupTotal,
            coalesce(sum(CASE WHEN assigned=0 THEN recordCount ELSE 0 END),0) AS unassignedRecordTotal
            FROM dc_request_groups''').fetchone()
        return dict(counts)

    def _request_group(self, db, group_row, join, query_groups, location, snapshot, preview=True):
        group_id = group_row['group_id']
        requests = db.execute('SELECT request,count(*) AS records FROM dc_request_members WHERE group_id=? GROUP BY request',
                              (group_id,)).fetchall()
        request = json.loads(requests[0]['request'])
        associations = {}
        for row in requests:
            association = json.loads(row['request'])['association']
            associations[association] = associations.get(association,0)+row['records']
        # A shared request clue does not turn its candidate members into
        # confirmed execution ownership when other members have explicit links.
        if associations.get('candidate'):
            request = next(json.loads(row['request']) for row in requests
                           if json.loads(row['request'])['association']=='candidate')
        members_join = join + ' JOIN dc_request_members m ON m.record=r.key WHERE m.group_id=?'
        devices = [{'id':r[0],'name':r[1]} for r in db.execute(
            'SELECT DISTINCT s.canonical,s.name'+members_join+' ORDER BY s.name,s.canonical',(group_id,))]
        applications = [r[0] for r in db.execute('SELECT DISTINCT r.source'+members_join+' ORDER BY r.source',(group_id,))]
        # The matched member set already carries query scope and watermarks.
        # Count every call in that set, not just the ten preview members, and
        # keep tool returns out of the invocation totals.
        tool_rows = db.execute('''SELECT min(json_extract(r.metadata,'$.tool')) AS name,count(*) AS calls'''+
            members_join+" AND r.kind='tool_call' GROUP BY lower(json_extract(r.metadata,'$.tool'))"+
            ' ORDER BY calls DESC,lower(name),name',(group_id,)).fetchall()
        tool_counts = [{'name':r['name'],'count':r['calls']} for r in tool_rows if r['name']]
        tool_total = sum(r['calls'] for r in tool_rows)
        items = []
        if preview:
            rows = db.execute('SELECT r.*,s.canonical,s.name'+members_join+
                              ' ORDER BY coalesce(r.stamp,0) DESC,r.key LIMIT 10',(group_id,)).fetchall()
            items = [self._item(db,row,query_groups,location,snapshot) for row in rows]
        return {'id':group_id,'request':request,'recordCount':group_row['recordCount'],
                'firstSeen':group_row['firstSeen'],'lastSeen':group_row['lastSeen'],
                'devices':devices,'applications':applications,'associationCounts':associations,
                'toolCallCounts':tool_counts,'toolCallTotal':tool_total,
                'items':items,'hasMore':group_row['recordCount']>10}

    def search(self, params=None, owner=None):
        params = params or {}
        values, page, size, dates = self._validate(params)
        groups = parse_query(values.get('q', '').strip())
        location = values.get('location', 'all')
        with self.lock, self._db() as db, self._execution_page():
            self._sync(db);self._scope(db, owner)
            snapshot_token,snapshot = self._snapshot(db,values,owner)
            join,predicate,args,scope_records = self._query_scope(db,values,dates,groups,snapshot)
            # Long retained contexts are matched once per query, rather than
            # rescanned for the count, distinct devices and the page itself.
            db.execute('CREATE TEMP TABLE dc_matches(key TEXT PRIMARY KEY)')
            db.execute('INSERT INTO dc_matches SELECT r.key' + join + predicate,args)
            total = db.execute('SELECT count(*) FROM dc_matches').fetchone()[0]
            matched_join = join + ' JOIN dc_matches m ON m.key=r.key'
            matched_devices = db.execute('SELECT count(DISTINCT s.canonical)' + matched_join).fetchone()[0]
            record_total = total
            grouped = {}
            if values.get('group')=='request':
                grouped = self._request_groups(db,matched_join,snapshot)
                grouped.update(group='request',recordTotal=record_total)
                if values.get('groupId'):
                    group_row = db.execute('SELECT * FROM dc_request_groups WHERE group_id=?',(values['groupId'],)).fetchone()
                    if group_row is None:raise ValueError('需求分组已不可用，请重新搜索')
                    rows = db.execute('SELECT r.*,s.canonical,s.name'+join+
                        ' JOIN dc_request_members m ON m.record=r.key WHERE m.group_id=?'+
                        ' ORDER BY coalesce(r.stamp,0) DESC,r.key LIMIT ? OFFSET ?',
                        [values['groupId'],size,(page-1)*size]).fetchall()
                    items = [self._item(db,row,groups,location,snapshot) for row in rows]
                    grouped['group'] = self._request_group(db,group_row,join,groups,location,snapshot,preview=False)
                    total = group_row['recordCount']
                else:
                    rows = db.execute('SELECT * FROM dc_request_groups ORDER BY assigned DESC,coalesce(lastSeen,0) DESC,group_id LIMIT ? OFFSET ?',
                                      [size,(page-1)*size]).fetchall()
                    grouped['groups'] = [self._request_group(db,row,join,groups,location,snapshot) for row in rows]
                    items = []
                    total = grouped['groupTotal']
            else:
                rows = db.execute('SELECT r.*,s.canonical,s.name' + matched_join + ' ORDER BY coalesce(r.stamp,0) DESC,r.key LIMIT ? OFFSET ?', [size,(page - 1) * size]).fetchall()
                items = [self._item(db, row, groups, location, snapshot) for row in rows]
            sources = [{'collector': r[0], 'total': r[1]} for r in db.execute('SELECT r.collector,count(*)' + join + ' WHERE '+DUPLICATES+' GROUP BY r.collector')]
            retained = sum(s['total'] for s in sources)
            facets = self._facets(db,join)
            unindexed = int(self._state(db,'session_raw_count')) + int(self._state(db,'app_raw_count')) - db.execute('SELECT count(*) FROM dc_records').fetchone()[0]
            return {'items': items, 'page': page, 'pageSize': size, 'total': total, 'hasMore': page * size < total,'snapshot':snapshot_token,**grouped,
                    'facets': facets, 'coverage': {'scope': 'global' if owner is None else 'owner',
                    'retainedRecords': retained, 'searchedRecords': scope_records,'scopeRecords':scope_records,
                    'sources': sources, 'fullRetainedText': unindexed==0,
                    'unindexedRecords': max(0,unindexed),
                    'capabilityEvidenceVersion': capability_evidence.VERSION, 'capabilityRemaining': self._capability_remaining(db),
                    'searchMode': 'literal_and_structured_fields', 'sourceSessionsAreTasks': False,
                    'excludedUnavailableRecords': db.execute('SELECT count(*) FROM dc_records r WHERE NOT EXISTS(SELECT 1 FROM dc_scope s WHERE s.device=r.device AND s.owner=r.owner)').fetchone()[0],
                    'duplicateAliasRecords': db.execute('SELECT count(*)'+join).fetchone()[0] - retained,
                    'limitations': ['范围为平台已接收且所属设备可用的原始记录；终端未上报的数据不在范围内。',
                                    '地址命中不代表实际连接或外传成功；账号是上传归属，实际员工未确认。',
                                    'SessionLens 未记录每条事件的服务器接收时间。',
                                    '翻页快照排除后续新上报记录；已更新或撤销的源记录需重新搜索，不冻结采集。']},
                    'summary': {'matchedRecords': record_total, 'matchedDevices': matched_devices, 'observedThreats': None}}

    def capabilities(self, params=None, owner=None):
        """Rank structured activity from the index; never expand raw records."""
        params = dict(params or {})
        params.setdefault('pageSize', 100)
        values, page, size, dates = self._validate(params)
        groups = parse_query(values.get('q', '').strip())
        with self.lock, self._db() as db:
            self._sync(db); self._scope(db, owner)
            token, snapshot = self._snapshot(db, values, owner, 'capabilities')
            join, predicate, args, scope_records = self._query_scope(db, values, dates, groups, snapshot)
            db.execute('CREATE TEMP TABLE dc_matches(key TEXT PRIMARY KEY)')
            db.execute('INSERT INTO dc_matches SELECT r.key' + join + predicate, args)
            matched_join = join + ' JOIN dc_matches m ON m.key=r.key'
            db.execute('''CREATE TEMP TABLE dc_capabilities(record TEXT,category TEXT,name TEXT,evidence TEXT,
                method TEXT,namespace TEXT,serviceName TEXT,device TEXT,owner TEXT,source TEXT,session TEXT,stamp REAL)''')
            db.execute('''INSERT INTO dc_capabilities
                SELECT r.key,'tool',json_extract(r.metadata,'$.tool'),'call',NULL,NULL,NULL,
                       s.canonical,r.owner,r.source,r.session,r.stamp''' + matched_join +
                " WHERE r.kind='tool_call' AND json_extract(r.metadata,'$.tool') IS NOT NULL")
            db.execute('''INSERT INTO dc_capabilities
                SELECT r.key,json_extract(c.value,'$.type'),json_extract(c.value,'$.name'),
                       json_extract(c.value,'$.evidence'),json_extract(c.value,'$.method'),json_extract(c.value,'$.namespace'),json_extract(c.value,'$.serviceName'),
                       s.canonical,r.owner,r.source,r.session,r.stamp''' + matched_join +
                " JOIN json_each(r.metadata,'$.capabilities') c WHERE r.kind='tool_call'")
            db.execute('CREATE INDEX dc_capabilities_names ON dc_capabilities(category,lower(name),lower(method))')
            object_type = values.get('object', 'all')
            result = {'tools': [], 'skills': [], 'mcps': [], 'snapshot': token,
                      'page': page, 'pageSize': size, 'totals': {}, 'hasMore': {}}
            for category, plural, field in [('tool', 'tools', 'tool'), ('skill', 'skills', 'skill'), ('mcp', 'mcps', 'mcp')]:
                if object_type not in ('all', category):
                    result['totals'][plural] = 0; result['hasMore'][plural] = False
                    continue
                total = db.execute('SELECT count(DISTINCT lower(name)) FROM dc_capabilities WHERE category=?', (category,)).fetchone()[0]
                rows = db.execute('''SELECT min(name) AS name,count(DISTINCT record) AS activityCount,
                    count(DISTINCT device) AS deviceCount,count(DISTINCT owner) AS accountCount,
                    count(DISTINCT json_array(owner,device,source,session)) AS sourceSessionCount,
                    max(stamp) AS lastSeen,json_group_array(DISTINCT source) AS applications,
                    json_group_array(DISTINCT namespace) AS namespaces,json_group_array(DISTINCT serviceName) AS serviceNames,
                    count(DISTINCT CASE WHEN evidence='loaded' THEN record END) AS loadCount,
                    count(DISTINCT CASE WHEN evidence='read' THEN record END) AS readCount,
                    count(DISTINCT CASE WHEN evidence='direct' THEN record END) AS directCount,
                    count(DISTINCT CASE WHEN evidence='wrapped' THEN record END) AS wrappedCount
                    FROM dc_capabilities WHERE category=? GROUP BY lower(name)
                    ORDER BY activityCount DESC,coalesce(lastSeen,0) DESC,lower(name),name LIMIT ? OFFSET ?''', (category, size, (page - 1) * size)).fetchall()
                for row in rows:
                    item = {key: row[key] for key in ('name', 'activityCount', 'deviceCount', 'accountCount', 'sourceSessionCount', 'lastSeen')}
                    item['applications'] = sorted(json.loads(row['applications']))
                    item['searchQuery'] = field + '=' + _json(row['name'])
                    if category == 'skill':
                        item.update(loadCount=row['loadCount'], readCount=row['readCount'])
                    else:
                        item['callCount'] = row['activityCount']
                    if category == 'mcp':
                        namespaces = sorted(n for n in json.loads(row['namespaces']) if n)
                        item.update(directCount=row['directCount'], wrappedCount=row['wrappedCount'], namespaces=namespaces,
                                    namespace=namespaces[0] if len(namespaces) == 1 else None,
                                    serviceNames=sorted(n for n in json.loads(row['serviceNames']) if n))
                        methods = db.execute('''SELECT min(method) AS name,count(DISTINCT record) AS callCount,
                            count(DISTINCT CASE WHEN evidence='direct' THEN record END) AS directCount,
                            count(DISTINCT CASE WHEN evidence='wrapped' THEN record END) AS wrappedCount,
                            max(stamp) AS lastSeen
                            FROM dc_capabilities WHERE category='mcp' AND lower(name)=lower(?)
                            GROUP BY lower(method) ORDER BY callCount DESC,coalesce(lastSeen,0) DESC,lower(method),method LIMIT 51''', (row['name'],)).fetchall()
                        item['methodsHasMore'] = len(methods) > 50
                        item['methods'] = [dict(method, searchQuery=item['searchQuery'] + ' && mcp_method=' + _json(method['name']))
                                           for method in methods[:50]]
                    result[plural].append(item)
                result['totals'][plural] = total; result['hasMore'][plural] = total > page * size
            sources = [{'collector': r[0], 'total': r[1]} for r in db.execute('SELECT r.collector,count(*)' + join + ' WHERE ' + DUPLICATES + ' GROUP BY r.collector')]
            retained = sum(s['total'] for s in sources)
            unindexed = int(self._state(db, 'session_raw_count')) + int(self._state(db, 'app_raw_count')) - db.execute('SELECT count(*) FROM dc_records').fetchone()[0]
            result['facets'] = self._facets(db, join)
            result['coverage'] = {'scope': 'global' if owner is None else 'owner', 'object': object_type,
                'retainedRecords': retained, 'scopeRecords': scope_records,
                'matchedRecords': db.execute('SELECT count(*) FROM dc_matches').fetchone()[0],
                'activityRecords': db.execute('SELECT count(DISTINCT record) FROM dc_capabilities').fetchone()[0],
                'unknownToolCount': db.execute('SELECT count(*)' + matched_join + " WHERE r.kind='tool_call' AND json_extract(r.metadata,'$.tool') IS NULL").fetchone()[0],
                'duplicateAliasRecords': db.execute('SELECT count(*)' + join).fetchone()[0] - retained,
                'sources': sources, 'fullRetainedText': unindexed == 0, 'unindexedRecords': max(0, unindexed),
                'sourceSessionsAreTasks': False, 'successInferred': False,
                'capabilityEvidenceVersion': capability_evidence.VERSION, 'capabilityRemaining': self._capability_remaining(db),
                'countBasis': 'distinct_source_records_with_structured_activity_evidence',
                'limitations': ['范围为已上报且所属设备可用的记录；终端未上报数据不在范围内。',
                    'Skill 加载与说明读取分别计数；只有读取说明的记录不证明 Skill 已启用。',
                    'MCP 命令封装表示明确调用命令；命令退出码不证明 MCP 服务处理成功。',
                    '源 Session 数为上传账号、设备、应用和 Session 标识的组合，不是完整任务数。',
                    '提及、写入、目录列表、审计样本和捕获请求中嵌入的历史工具声明不计入能力活动；复杂 shell 只识别明确字面命令；exec 只展开静态可确定的调用，条件分支和函数体不计入。',
                    '排行快照排除后续新上报记录；源记录更新或撤销后需刷新，快照不冻结采集。']}
            return result

    @staticmethod
    def _raw_url(row):
        return '/api/data-center/raw?' + urlencode({'id':row['key']})

    @staticmethod
    def _legacy_raw_url(row):
        request = ('sessionlens:' if row['collector'] == 'sessionlens' else '') + row['ident']
        return '/model-data/raw?' + urlencode({'scope': 'global', 'device': row['canonical'], 'request': request})

    @staticmethod
    def _canonical_turn_user(users, source):
        """Collapse only Codex's complementary representations of one input.

        Callers must scope the rows by owner/device/source/session/native turn
        and request at least three rows, so a third input cannot be hidden.
        Text is the full retained input, never a card preview or fuzzy match.
        """
        if len(users)==1:return users[0]
        if len(users)!=2 or source!='codex':return None
        metadata=[json.loads(u['metadata']) for u in users]
        if {m.get('sourceKind') for m in metadata}!={'message','user_message'}:return None
        if any(m.get('control') or m.get('turnIdentityConflict') or m.get('rootTurnIdentityConflict') for m in metadata):return None
        if not metadata[0].get('turnId') or metadata[0].get('turnId')!=metadata[1].get('turnId'):return None
        text=metadata[0].get('userInput')
        if not isinstance(text,str) or not text.strip() or text!=metadata[1].get('userInput'):return None
        return users[next(i for i,m in enumerate(metadata) if m['sourceKind']=='message')]

    def _turn_user_rows(self,db,scope,turn,snapshot=None):
        watermark=' AND seq<=?' if snapshot else ''
        end=[snapshot['sessionMax']] if snapshot else []
        return db.execute("SELECT key,stamp,session,metadata FROM dc_records INDEXED BY dc_records_turn_id WHERE collector='sessionlens' "
            "AND owner=? AND device=? AND source=? AND session=? AND kind='user' "
            "AND json_extract(metadata,'$.turnId')=? AND json_extract(metadata,'$.userInput') IS NOT NULL "
            "AND json_extract(metadata,'$.control') IS NULL"+watermark+' LIMIT 3',list(scope)+[turn]+end).fetchall()

    def _recorded_root_user(self,db,row,meta,snapshot=None):
        """Follow explicit native turn and parent-session records, never time proximity."""
        if not meta.get('turnId') or row['source']!='codex':return None
        watermark=' AND seq<=?' if snapshot else '';end=[snapshot['sessionMax']] if snapshot else []
        scope=[row['owner'],row['device'],row['source'],row['session']]
        base=" FROM dc_records WHERE collector='sessionlens' AND owner=? AND device=? AND source=? AND session=?"
        contexts=db.execute('SELECT key,metadata'+base.replace('dc_records WHERE','dc_records INDEXED BY dc_records_turn_id WHERE')+" AND json_extract(metadata,'$.turnId')=? AND json_extract(metadata,'$.sourceKind') IN ('turn_context','turn_started')"+watermark+' LIMIT 33',scope+[meta['turnId']]+end).fetchall()
        identity=[meta]+[json.loads(c['metadata']) for c in contexts]
        roots=list(dict.fromkeys(m['rootTurnId'] for m in identity if m.get('rootTurnId')))
        if len(contexts)>32 or len(roots)>1 or any(m.get('rootTurnIdentityConflict') for m in identity):
            return {'reason':'原始主轮次标识相互冲突，关联待核对','conflict':True}
        if not roots or roots[0]==meta['turnId']:return None
        root=roots[0];seen={row['session']};path=[];session=row['session']
        for _ in range(8):
            links=db.execute('SELECT key,metadata'+base.replace('dc_records WHERE','dc_records INDEXED BY dc_records_session_parent WHERE')+" AND json_extract(metadata,'$.sourceKind')='session_metadata'"+watermark+' LIMIT 3',scope[:3]+[session]+end).fetchall()
            info=[json.loads(c['metadata']) for c in links]
            parents=list(dict.fromkeys(m['parentSessionId'] for m in info if m.get('parentSessionId')))
            if len(links)>2 or len(parents)!=1 or any(m.get('parentSessionConflict') for m in info):break
            session=parents[0]
            if session in seen:break
            seen.add(session);path.extend(c['key'] for c in links)
            users=self._turn_user_rows(db,scope[:3]+[session],root,snapshot)
            user=self._canonical_turn_user(users,row['source'])
            if users and user is None:return {'reason':'主会话同一轮次有多条用户输入，关联待核对','conflict':True}
            if user is not None:return {'user':user,'rootTurnId':root,'viaRecordIds':[c['key'] for c in contexts]+path}
        return {'reason':'已记录主轮次，但未找到对应用户输入','rootTurnId':root}

    def _activity_request(self,db,row,meta,snapshot=None):
        """Bounded, indexed user input for tool evidence and capability cards.

        Parent and native turn IDs are recorded message relationships, not a
        proof that multiple rounds form one business task. A time-only match
        stays a candidate. None of these lookups reads the source JSON.
        """
        empty={'text':None,'association':'unknown','basis':'需求关联未确认'}
        if row['collector']!='sessionlens':return empty
        if meta.get('turnIdentityConflict'):
            return dict(empty,basis='原始轮次标识相互冲突，关联待核对',reason='原始轮次标识相互冲突，关联待核对',relationBasis='conflicting_recorded_links')
        scope=[row['owner'],row['device'],row['source'],row['session']]
        base=" FROM dc_records WHERE collector='sessionlens' AND owner=? AND device=? AND source=? AND session=?"
        watermark=' AND seq<=?' if snapshot else ''
        scope_end=[snapshot['sessionMax']] if snapshot else []
        paired_request=None;paired_call=None
        if row['kind']=='tool_result' and meta.get('callId'):
            # A return may not repeat its call's parent/turn IDs. Use only a
            # unique same-scope call/return pair so an intervening user input
            # cannot move the return into a different request group.
            members=db.execute('SELECT key,collector,owner,device,source,session,kind,stamp,metadata'+base.replace('dc_records WHERE','dc_records INDEXED BY dc_records_call_id WHERE')+
                " AND kind IN ('tool_call','tool_result') AND json_extract(metadata,'$.callId')=?"+
                watermark+' LIMIT 3',scope+[meta['callId']]+scope_end).fetchall()
            calls=[member for member in members if member['kind']=='tool_call']
            results=[member for member in members if member['kind']=='tool_result']
            if len(members)==2 and len(calls)==len(results)==1 and results[0]['key']==row['key']:
                paired_call=calls[0]['key']
                paired_request=self._activity_request(db,calls[0],json.loads(calls[0]['metadata']),snapshot)
                if paired_request.get('relationBasis')=='conflicting_recorded_links':
                    return dict(paired_request,viaRecordId=paired_call)
        def request_context(candidate,current,association,basis):
            text=current.get('userInput')
            if not isinstance(text,str) or not text.strip() or current.get('control'):return None
            if association!='recorded' and not _meaningful_request(text):return None
            return {'text':text[:300],'textTruncated':len(text)>300,
                'recordId':candidate['key'],'recordDigest':current.get('recordDigest'),
                'timestamp':candidate['stamp'],'rawUrl':self._raw_url(candidate),
                'sourceSessionId':candidate['session'] if 'session' in candidate.keys() else row['session'],
                'association':association,'basis':basis,'inputKind':'request' if _meaningful_request(text) else 'continuation'}
        def with_hint(request):
            if not request or request.get('inputKind')!='continuation':return request
            source=db.execute('SELECT session,stamp FROM dc_records WHERE key=?',(request['recordId'],)).fetchone()
            if not source or source['stamp'] is None:return request
            users=db.execute('SELECT key,stamp,session,metadata'+base.replace('dc_records WHERE','dc_records INDEXED BY dc_records_user_stamp WHERE')+" AND kind='user' AND stamp<? AND json_extract(metadata,'$.userInput') IS NOT NULL AND json_extract(metadata,'$.control') IS NULL"+watermark+' ORDER BY stamp DESC,seq DESC LIMIT 40',scope[:3]+[source['session'],source['stamp']]+scope_end).fetchall()
            for candidate in users:
                hint=request_context(candidate,json.loads(candidate['metadata']),'candidate','前序需求线索，尚未确认与本轮为同一任务')
                if hint:request=dict(request,contextHint=hint);break
            return request
        parent_request=None;turn_request=None;root_request=None;chain_requests=[]
        current=meta;visited=set()
        for _ in range(12):
            parent=current.get('parentMessageId')
            if not parent or parent in visited:break
            visited.add(parent)
            parents=db.execute('SELECT key,kind,stamp,metadata'+base.replace('dc_records WHERE','dc_records INDEXED BY dc_records_message_id WHERE')+" AND json_extract(metadata,'$.sourceMessageId')=?"+watermark+' LIMIT 2',scope+[parent]+scope_end).fetchall()
            if len(parents)!=1:break
            candidate=parents[0];current=json.loads(candidate['metadata'])
            if current.get('turnIdentityConflict'):
                return dict(empty,basis='父消息的原始轮次标识冲突，关联待核对',relationBasis='conflicting_recorded_links')
            if candidate['kind']=='user':
                via=candidate['key']
                if current.get('turnId'):
                    users=self._turn_user_rows(db,scope,current['turnId'],snapshot)
                    canonical=self._canonical_turn_user(users,row['source'])
                    if canonical is not None and any(u['key']==candidate['key'] for u in users):
                        candidate=canonical;current=json.loads(candidate['metadata'])
                parent_request=request_context(candidate,current,'recorded','recorded_source_parent')
                if parent_request and via!=candidate['key']:parent_request['viaRecordId']=via
                break
            if current.get('turnId'):
                users=self._turn_user_rows(db,scope,current['turnId'],snapshot)
                canonical=self._canonical_turn_user(users,row['source'])
                if canonical is not None:
                    linked=request_context(canonical,json.loads(canonical['metadata']),'recorded','recorded_parent_turn_id')
                    if linked:chain_requests.append(linked)
        if meta.get('turnId'):
            users=self._turn_user_rows(db,scope,meta['turnId'],snapshot)
            canonical=self._canonical_turn_user(users,row['source'])
            if canonical is not None:
                turn_request=request_context(canonical,json.loads(canonical['metadata']),'recorded','recorded_turn_id')
        root=self._recorded_root_user(db,row,meta,snapshot)
        if root and root.get('conflict'):
            return dict(empty,basis=root['reason'],reason=root['reason'],relationBasis='conflicting_recorded_links')
        if root and root.get('user'):
            candidate=root['user'];root_request=request_context(candidate,json.loads(candidate['metadata']),'recorded','recorded_root_turn_parent_session')
            if root_request:root_request.update(rootTurnId=root['rootTurnId'],sourceSessionId=candidate['session'],viaRecordIds=root['viaRecordIds'])
        # Parent, turn and a unique call pair are independent recorded links.
        # Do not pick one link and then expand another user's execution round.
        recorded_ids=list(dict.fromkeys(request['recordId'] for request in
            (parent_request,turn_request,root_request,paired_request,*chain_requests) if request and request.get('recordId') and request['association']=='recorded'))
        if paired_request and paired_request.get('conflictingRecordIds'):
            recorded_ids=list(dict.fromkeys([*recorded_ids,*paired_request['conflictingRecordIds']]))
        if len(recorded_ids)>1:
            conflict=dict(empty,basis='原始父消息、轮次或调用配对指向不同用户输入，关联待核对',
                conflictingRecordIds=recorded_ids,relationBasis='conflicting_recorded_links')
            if paired_call:conflict['viaRecordId']=paired_call
            return conflict
        if parent_request or turn_request or root_request:return with_hint(parent_request or turn_request or root_request)
        if paired_request and paired_request.get('recordId'):
            return dict(paired_request,viaRecordId=paired_call,relationBasis='recorded_unique_call_id')
        if root:return dict(empty,basis=root['reason'],reason=root['reason'])
        if row['stamp'] is not None:
            users=db.execute('SELECT key,stamp,metadata'+base.replace('dc_records WHERE','dc_records INDEXED BY dc_records_user_stamp WHERE')+" AND kind='user' AND stamp BETWEEN ? AND ? AND json_extract(metadata,'$.userInput') IS NOT NULL AND json_extract(metadata,'$.control') IS NULL"+watermark+' ORDER BY stamp DESC,seq DESC LIMIT 40',scope+[row['stamp']-7200,row['stamp']]+scope_end).fetchall()
            for candidate in users:
                request=request_context(candidate,json.loads(candidate['metadata']),'candidate',
                    '同一来源会话前序需求线索，尚未证明属于本次执行')
                if request:return request
        return empty

    @contextmanager
    def _execution_page(self):
        previous=getattr(self,'_execution_budget',None)
        self._execution_budget={'records':40,'characters':2000000,'spentSeconds':0.,
                                'maxSeconds':.25,'activeStartedAt':None,'cache':{}}
        try:yield
        finally:self._execution_budget=previous

    def _execution_source(self,row):
        """Read selected historical source records only, within a page budget."""
        budget=getattr(self,'_execution_budget',None)
        if budget is None:return None
        if row['key'] in budget['cache']:return budget['cache'][row['key']]
        active=budget['activeStartedAt']
        spent=budget['spentSeconds']+(time.monotonic()-active if active is not None else 0.)
        if budget['records']<=0 or budget['characters']<=0 or spent>=budget['maxSeconds']:return None
        with self.sessions.connect() as source:
            raw=source.execute('SELECT CASE WHEN length(event)<=200000 THEN event END,length(event) FROM session_events WHERE owner=? AND device=? AND id=?',
                (row['owner'],row['device'],row['ident'])).fetchone()
        budget['records']-=1
        if not raw or raw[0] is None or raw[1]>budget['characters']:return None
        budget['characters']-=raw[1]
        try:value=json.loads(raw[0])
        except (ValueError,RecursionError):return None
        budget['cache'][row['key']]=value
        return value

    def _execution_projection(self,db,row,meta,summary,snapshot=None):
        # Charge the selected source/projection work, not unrelated query waits
        # between items. A slow in-flight source read can cross this soft budget;
        # _execution_source then prevents starting another uncached read.
        budget=getattr(self,'_execution_budget',None)
        if budget is None:return self._execution_projection_value(db,row,meta,summary,snapshot)
        started=time.monotonic();previous=budget['activeStartedAt']
        budget['activeStartedAt']=started
        try:return self._execution_projection_value(db,row,meta,summary,snapshot)
        finally:
            budget['spentSeconds']+=time.monotonic()-started
            budget['activeStartedAt']=previous

    def _execution_projection_value(self,db,row,meta,summary,snapshot=None):
        if row['collector']!='sessionlens' or row['kind'] not in ('tool_call','tool_result'):return None
        tool=meta.get('tool'); call_row=row
        if row['kind']=='tool_result' and meta.get('callId'):
            watermark=' AND seq<=?' if snapshot else ''
            values=[row['owner'],row['device'],row['source'],row['session'],meta['callId']]
            if snapshot:values.append(snapshot['sessionMax'])
            members=db.execute('''SELECT * FROM dc_records INDEXED BY dc_records_call_id WHERE collector='sessionlens' AND owner=? AND device=? AND source=? AND session=?
                AND json_extract(metadata,'$.callId')=? AND kind IN ('tool_call','tool_result')'''+watermark+' LIMIT 3',values).fetchall()
            calls=[member for member in members if member['kind']=='tool_call']
            if len(members)==2 and len(calls)==1:
                call_row=calls[0];tool=json.loads(call_row['metadata']).get('tool')
        if not isinstance(tool,str) or tool.rsplit('.',1)[-1].lower()!='exec':return None
        raw=self._execution_source(call_row)
        if raw is None:return execution_evidence.unavailable(tool,meta.get('callId'))
        inner=_inner(raw);arguments=_arguments(inner)
        result=inner.get('aggregated_output',inner.get('stdout',inner.get('result')))
        return_deferred=False
        if row['kind']=='tool_result':
            returned=self._execution_source(row)
            if returned is None:return_deferred=True;result=None
            else:
                p=_inner(returned);result=p.get('output',p.get('result',p.get('content',p)))
        elif result is None and summary.get('resultRecordId'):
            paired=db.execute('SELECT * FROM dc_records WHERE key=?',(summary['resultRecordId'],)).fetchone()
            returned=self._execution_source(paired) if paired else None
            if returned is not None:
                p=_inner(returned);result=p.get('output',p.get('result',p.get('content',p)))
            else:return_deferred=True
        value=execution_evidence.project(tool,arguments,result,meta.get('callId'),
            source_truncated=bool(raw.get('truncated') or (raw.get('evidence') or {}).get('truncated')))
        if value and return_deferred:
            basis='已有外层返回，本页未展开；记录详情可查看。'
            value['coverage'].update(incomplete=True,returnProjectionUnavailable=True)
            value['coverage'].setdefault('limitations',[]).append(basis)
            value['outer'].update(statusLabel='已有返回，本页尚未展开',summary=basis)
        return value

    def _item(self, db, row, groups=(), location='all', snapshot=None, execution=True):
        meta = json.loads(row['metadata'])
        matches = []
        for group in groups:
            for field, op, value in group:
                if op != '=':
                    continue
                if field in ('keyword', 'ip', 'domain'):
                    args = [row['key']]
                    where = 'record=?'
                    if location != 'all':
                        where += ' AND location=?';args.append(location)
                    args.extend([value, field, value, field])
                    hit = db.execute('SELECT location,dc_snippet(value,?,?) AS excerpt FROM dc_fields WHERE ' + where + ' AND dc_match(value,?,?) ORDER BY ordinal LIMIT 1',
                        # SQL SELECT placeholders precede WHERE placeholders.
                        [value, field, *args[:-4], value, field]).fetchone()
                    if hit:
                        basis = {'field': hit['location'], 'label': LOCATIONS[hit['location']], 'excerpt': hit['excerpt']}
                        address_field = field
                        if field == 'keyword':
                            try:ipaddress.ip_address(value);address_field = 'ip'
                            except ValueError:pass
                        if address_field in ('ip','domain'):
                            target_match = any(_target_equals(target,value,address_field) for target in meta.get('destinations',[]))
                            target_location = hit['location'] == 'metadata' or (hit['location'] == 'arguments' and meta['addressBasis']=='tool_argument_target')
                            basis['addressBasis'] = meta['addressBasis'] if target_match and target_location else 'text_mention'
                            basis['matchedAddress'] = value
                            if basis['addressBasis']=='text_mention':basis['label'] += ' · 地址提及'
                        matches.append(basis)
                else:
                    args = []
                    sql = self._term_sql((field, op, value), location, args)
                    hit = db.execute('SELECT 1 FROM dc_records r JOIN dc_scope s ON s.device=r.device AND s.owner=r.owner WHERE r.key=? AND ' + sql,
                                     [row['key'], *args]).fetchone()
                    if hit:
                        basis = {'field': field, 'label': '结构字段 · ' + field, 'excerpt': value}
                        if field in ('target_domain','target_ip'):
                            address_field = 'domain' if field == 'target_domain' else 'ip'
                            basis.update(addressBasis=meta['addressBasis'],matchedAddress=value)
                            basis['targets'] = [t for t in meta.get('destinations',[]) if _target_equals(t,value,address_field)]
                        matches.append(basis)
                if len(matches) >= 3:
                    break
            if len(matches) >= 3:
                break
        first = db.execute('SELECT substr(value,1,280),length(value)>280 FROM dc_fields WHERE record=? ORDER BY ordinal LIMIT 1', (row['key'],)).fetchone()
        excerpt = matches[0]['excerpt'] if matches else (first[0] + ('…' if first[1] else '') if first else '')
        address_basis = meta['addressBasis']
        # This field describes the record's destination, independent of which
        # address the question matched. Each match has its own address basis.
        command = meta.get('command')
        user_input = meta.get('userInput') if row['kind']=='user' else None
        user_input_truncated = bool(snapshot and user_input and len(user_input)>300)
        if user_input_truncated:user_input = user_input[:300]
        summary = meta.get('summary') or {}
        if row['kind'] == 'tool_call' and meta.get('callId') and not meta.get('hasResult'):
            watermark = ' AND seq<=?' if snapshot else ''
            values = [row['owner'],row['device'],row['source'],row['session'],meta['callId']]
            if snapshot: values.append(snapshot['sessionMax'])
            members = db.execute('''SELECT key,kind,metadata FROM dc_records INDEXED BY dc_records_call_id WHERE collector='sessionlens'
                AND owner=? AND device=? AND source=? AND session=? AND json_extract(metadata,'$.callId')=?
                AND kind IN ('tool_call','tool_result')''' + watermark + ' LIMIT 3', values).fetchall()
            calls = [r for r in members if r['kind']=='tool_call'];results=[r for r in members if r['kind']=='tool_result']
            if len(members)==2 and len(calls)==len(results)==1:
                result_summary = json.loads(results[0]['metadata']).get('summary') or {}
                summary.update(outcome=result_summary.get('outcome',summary.get('outcome')),
                               resultPreview=result_summary.get('resultPreview',''),resultRecordId=results[0]['key'],
                               resultAssociation='recorded_unique_call_id')
        request=self._activity_request(db,row,meta,snapshot) if row['kind'] in ('tool_call','tool_result') or meta.get('capabilities') else None
        activity=_activity(meta,summary,request) if meta.get('capabilities') else None
        return {'id': row['key'], 'recordId': ('sessionlens:' if row['collector'] == 'sessionlens' else '') + row['ident'],
                'collector': row['collector'], 'application': row['source'], 'ownerAccount': row['owner'], 'operator': '未确认',
                'deviceId': row['canonical'], 'sourceDeviceId':row['device'],'deviceName': row['name'], 'kind': row['kind'], 'kindLabel': meta['kindLabel'],
                'title': row['title'], 'timestamp': row['stamp'], 'receivedAt': row['received'], 'sessionId': row['session'],
                'tool': meta.get('tool'), 'executor':meta.get('executor'),'function': meta.get('function'), 'command': command[:1200] if command else None,
                'capabilities':meta.get('capabilities', []),'activity':activity,'request':request,
                'execution':self._execution_projection(db,row,meta,summary,snapshot) if execution else None,
                'commandTruncated': bool(command and len(command) > 1200), 'destination': meta.get('destination'),
                'destinations':meta.get('destinations',[]),'summary':summary,
                'userInput':user_input,'userInputTruncated':user_input_truncated,
                'addressBasis': address_basis, 'excerpt': excerpt, 'matchBasis': matches, 'confirmation': 'recorded',
                'rawUrl': self._raw_url(row),'legacyRawUrl':self._legacy_raw_url(row)}

    def _lookup(self, db, key, owner=None):
        collector, account, device, ident = _decode(key)
        if owner is not None and account != owner:
            raise KeyError('Record not found')
        row = db.execute('''SELECT r.*,s.canonical,s.name FROM dc_records r JOIN dc_scope s
            ON s.device=r.device AND s.owner=r.owner WHERE r.key=?''', (key,)).fetchone()
        if row is None:
            raise KeyError('Record not found')
        store = self.sessions if collector == 'sessionlens' else self.devices
        with store.connect() as source:
            if collector == 'sessionlens':
                raw = source.execute('SELECT event FROM session_events WHERE owner=? AND device=? AND id=?', (account, device, ident)).fetchone()
            else:
                raw = source.execute('SELECT data FROM applens_model_context WHERE device_id=? AND request_id=?', (device, ident)).fetchone()
        if not raw:
            raise KeyError('Record not found')
        return row, json.loads(raw[0])

    def _enriched(self, db, row, owner=None):
        """A bounded readable source record for a detail portrait, never a task."""
        item = self._item(db,row,execution=False)
        _, raw = self._lookup(db,row['key'],owner)
        content, truncated = _bounded(raw.get('body') if row['collector']=='applens' else raw.get('payload'))
        if item.get('userInput') and len(item['userInput'])>60000:
            item['userInput'] = item['userInput'][:60000]
            item['userInputTruncated'] = True
            truncated = True
        source = dict(raw,payload=content) if row['collector']=='sessionlens' else raw
        inner = _inner(source)
        arguments = _arguments(inner) if row['kind']=='tool_call' else None
        result = None
        if row['kind']=='tool_call':result = inner.get('aggregated_output',inner.get('stdout',inner.get('result')))
        elif row['kind']=='tool_result':result=inner.get('output',inner.get('result',inner.get('content',inner)))
        if row['kind']=='tool_call' and result is None and item['summary'].get('resultRecordId'):
            _, paired = self._lookup(db,item['summary']['resultRecordId'],owner)
            p = _inner(paired)
            result, result_truncated = _bounded(p.get('output',p.get('result',p.get('content',p))))
            truncated = truncated or result_truncated
        item.update(content=content,arguments=arguments,result=result,contentTruncated=truncated,
                    sourceEvidence=raw.get('evidence') or {},
                    presentation=_presentation(arguments,result,item.get('command'),content))
        meta=json.loads(row['metadata']);projection_tool=item.get('tool');projection_arguments=arguments
        if row['kind']=='tool_call':projection_arguments=_arguments(_inner(raw))
        elif row['kind']=='tool_result' and meta.get('callId'):
            members=db.execute('''SELECT key,kind,metadata FROM dc_records WHERE collector='sessionlens' AND owner=? AND device=? AND source=? AND session=?
                AND json_extract(metadata,'$.callId')=? AND kind IN ('tool_call','tool_result') LIMIT 3''',
                [row['owner'],row['device'],row['source'],row['session'],meta['callId']]).fetchall()
            calls=[member for member in members if member['kind']=='tool_call']
            if len(members)==2 and len(calls)==1:
                projection_tool=json.loads(calls[0]['metadata']).get('tool')
                if isinstance(projection_tool,str) and projection_tool.rsplit('.',1)[-1].lower()=='exec':
                    _,paired=self._lookup(db,calls[0]['key'],owner);projection_arguments=_arguments(_inner(paired))
        item['execution']=execution_evidence.project(projection_tool,projection_arguments,result,meta.get('callId'),detail=True,
            source_truncated=bool(raw.get('truncated') or (raw.get('evidence') or {}).get('truncated')))
        return item

    def record(self, key, owner=None):
        with self.lock, self._db() as db:
            self._sync(db);self._scope(db, owner)
            row, raw = self._lookup(db, key, owner)
            item = self._item(db, row,execution=False)
            item.update({'raw': raw, 'content': raw.get('body') if row['collector'] == 'applens' else raw.get('payload'),
                         'arguments': None, 'result': None, 'related': [], 'relations': [], 'gaps': [], 'contextItems': [],
                         'sourceEvidence': raw.get('evidence') or {k: raw.get(k) for k in ('source', 'bodySHA256', 'integrityEvidence', 'wireLengthMatched', 'recordStatus', 'truncated')},
                         'taskContext': {'userInput': None, 'association': 'unavailable', 'basis': '尚无该记录所属业务任务的确认证据'}})
            item['sourceEvidence'] = dict(item['sourceEvidence'], receivedBasis='context_record_receipt' if row['collector'] == 'applens' else 'not_collected_per_event',
                                          sourceSessionId=row['session'])
            meta = json.loads(row['metadata'])
            if row['collector'] == 'applens':
                item['gaps'] = ['HTTP 请求与业务任务的关联尚未确认。', '请求正文不证明服务端接收、处理或数据使用。']
                try:
                    parsed = _object(raw.get('body',''))
                    if isinstance(parsed,dict) and 'messages' in parsed and not isinstance(parsed['messages'],list):
                        raise ValueError('Invalid message container')
                    item['contextItems'] = context_items(raw)
                except (ValueError,TypeError,AttributeError):
                    body = raw.get('body','')
                    item['contextItems'] = [{'id':row['ident']+':unclassified','requestId':row['ident'],
                        'name':'未分类的原始正文','category':'未分类内容','rawContent':body,'source':'原始请求正文',
                        'classificationBasis':'messages 结构无法分类，保留完整正文','sourceVerified':False,'complete':False}]
                    item['gaps'].append('messages 结构无法分类，完整请求原文仍可查看。')
                item['presentation'] = _presentation(content=item['content'])
                item['portrait'] = {'userRequest':item['taskContext'],'steps':[], 'neighbors':[],
                    'coverage':{'sourceSessionsAreTasks':False,'relatedLimit':24,'neighborLimit':8,
                                'contentLimitPerRecord':60000,'truncatedRecords':0,'relatedCapped':False}}
                return {'item': item}
            inner = _inner(raw)
            if row['kind'] == 'tool_call':
                item['arguments'] = _arguments(inner)
                item['result'] = inner.get('aggregated_output', inner.get('stdout', inner.get('result')))
                item['command'] = meta.get('command')
                item['commandTruncated'] = False
            elif row['kind'] == 'tool_result':
                item['result'] = inner.get('output', inner.get('result', inner.get('content', inner)))
            scope = (row['owner'], row['device'], row['source'], row['session'])
            base = ''' FROM dc_records r JOIN dc_scope s ON s.device=r.device AND s.owner=r.owner
                WHERE r.collector='sessionlens' AND r.owner=? AND r.device=? AND r.source=? AND r.session=?'''
            related = {}; related_rows = {}; related_capped = [False]
            source_request=({'association':'recorded','recordId':key} if row['kind']=='user'
                else item.get('request') or self._activity_request(db,row,meta))
            request_conflict=source_request.get('relationBasis')=='conflicting_recorded_links'
            request_blocked=request_conflict or bool(source_request.get('reason'))
            def include(other, association='recorded', basis='recorded_source_relation'):
                if other['key'] == key:return
                if basis in ('recorded_turn_id','recorded_unique_parent_id'):
                    other_request=self._activity_request(db,other,json.loads(other['metadata']))
                    if (other_request.get('relationBasis')=='conflicting_recorded_links'
                        or (source_request.get('association')=='recorded' and other_request.get('association')=='recorded'
                            and source_request.get('recordId')!=other_request.get('recordId'))):
                        gap='某条后续记录的用户输入关联与当前记录不一致，未纳入关联过程。'
                        if gap not in item['gaps']:item['gaps'].append(gap)
                        return
                if other['key'] in related:
                    if association=='recorded':related[other['key']].update(association=association,relationBasis=basis)
                    return
                if len(related)>=24:
                    related_capped[0] = True;return
                related_rows[other['key']] = other
                related[other['key']] = dict(self._enriched(db,other,owner),association=association,relationBasis=basis)
            if meta.get('callId'):
                counts = {r[0]:r[1] for r in db.execute('SELECT r.kind,count(*)' + base + " AND json_extract(r.metadata,'$.callId')=? AND r.kind IN ('tool_call','tool_result') GROUP BY r.kind", (*scope, meta['callId']))}
                if counts.get('tool_call') == 1 and counts.get('tool_result') == 1:
                    members = db.execute('SELECT r.*,s.canonical,s.name' + base + " AND json_extract(r.metadata,'$.callId')=? AND r.kind IN ('tool_call','tool_result') LIMIT 2", (*scope, meta['callId'])).fetchall()
                    calls = [r for r in members if r['kind'] == 'tool_call'];results = [r for r in members if r['kind'] == 'tool_result']
                    include(calls[0]);include(results[0])
                    item['relations'].append({'from': calls[0]['key'], 'to': results[0]['key'], 'relation': 'tool_call_result', 'basis': 'recorded_unique_call_id'})
                    if row['kind'] == 'tool_call':
                        _, paired = self._lookup(db, results[0]['key'], owner)
                        p = _inner(paired);item['result'] = p.get('output', p.get('result', p.get('content', p)))
                    elif row['kind'] == 'tool_result':
                        _, paired = self._lookup(db, calls[0]['key'], owner)
                        p = _inner(paired);item['arguments'] = _arguments(p)
                        call_meta = json.loads(calls[0]['metadata'])
                        item.update({'tool': call_meta.get('tool'), 'executor':call_meta.get('executor'),'function': call_meta.get('function'), 'command': call_meta.get('command'), 'commandTruncated': False})
                else:
                    item['gaps'].append('callId 未形成唯一调用与返回配对；未关联其他记录。')
            current = None if request_blocked else row;visited = {key};user = None
            if source_request.get('association')=='recorded' and source_request.get('recordId') and not request_conflict:
                anchor,_=self._lookup(db,source_request['recordId'],owner)
                anchor_meta=json.loads(anchor['metadata'])
                if anchor['kind']=='user' and anchor_meta.get('userInput'):
                    include(anchor,basis=source_request.get('basis') or 'recorded_source_relation')
                    user=(anchor,anchor_meta['userInput'],source_request.get('basis') or 'recorded_source_relation');current=None
                    item['relations'].append({'from':anchor['key'],'to':key,'relation':'native_user_round','basis':source_request.get('basis') or 'recorded_source_relation'})
            for _ in range(24):
                if current is None:break
                current_meta = json.loads(current['metadata'])
                if current['kind'] == 'user' and current_meta.get('userInput'):
                    user = (current, current_meta['userInput'], 'recorded_source_parent');break
                parent = current_meta.get('parentMessageId')
                if not parent:
                    break
                parents = db.execute('SELECT r.*,s.canonical,s.name' + base + " AND json_extract(r.metadata,'$.sourceMessageId')=? LIMIT 2", (*scope, parent)).fetchall()
                if len(parents) != 1 or parents[0]['key'] in visited:
                    item['gaps'].append('原始父消息不唯一、缺失或循环，停止关联。');break
                previous = parents[0];visited.add(previous['key']);include(previous)
                item['relations'].append({'from': previous['key'], 'to': current['key'], 'relation': 'source_parent', 'basis': 'recorded_unique_parent_id'})
                current = previous
            else:
                related_capped[0] = True
            if user is None and meta.get('turnId') and not request_blocked:
                users=self._turn_user_rows(db,scope,meta['turnId'])
                canonical=self._canonical_turn_user(users,row['source'])
                if canonical is not None:
                    u,_=self._lookup(db,canonical['key'],owner)
                    include(u);user = (u, json.loads(u['metadata'])['userInput'], 'recorded_turn_id')
                    item['relations'].append({'from': u['key'], 'to': key, 'relation': 'native_user_round', 'basis': 'recorded_unique_turn_id'})
                elif len(users) > 1:
                    item['gaps'].append('同一原生轮次标识包含多个用户提问，归属未确认。')
            if request_blocked:
                item['gaps'].append(source_request['basis'])
                item['taskContext']={'userInput':None,'association':'unknown','basis':source_request['basis'],
                    'conflictingRecordIds':source_request.get('conflictingRecordIds',[])}
            elif user:
                item['taskContext'] = {'userInput': user[1], 'userRecordId': user[0]['key'], 'association': 'recorded',
                                       'basis': user[2], 'limitation': '仅确定用户通信轮次；多轮是否属于同一业务任务仍未确认。'}
            elif row['stamp'] is not None:
                candidate = db.execute('SELECT r.*,s.canonical,s.name' + base + " AND r.kind='user' AND json_extract(r.metadata,'$.userInput') IS NOT NULL AND r.stamp<=? ORDER BY r.stamp DESC,r.seq DESC LIMIT 1", (*scope, row['stamp'])).fetchone()
                if candidate:
                    include(candidate,'candidate','inferred_source_time')
                    item['taskContext'] = {'userInput': json.loads(candidate['metadata'])['userInput'], 'userRecordId': candidate['key'],
                                           'association': 'candidate', 'basis': '同一账号、设备、来源会话中时间最近的前序提问，尚未证明属于本次执行。'}
                    item['relations'].append({'from': candidate['key'], 'to': key, 'relation': 'preceding_user_candidate', 'basis': 'inferred_source_time'})
            # Follow recorded message relationships in both directions. Stop at
            # another user message: a shared source session/root is not a task.
            queue = [] if request_conflict else [row] + [r for k,r in related_rows.items() if related[k]['association']=='recorded']
            expanded = set()
            for current in queue:
                if len(expanded)>=25:related_capped[0]=True;break
                if current['key'] in expanded:continue
                expanded.add(current['key'])
                message_id = json.loads(current['metadata']).get('sourceMessageId')
                if not message_id:continue
                identities = db.execute('SELECT r.key'+base+" AND json_extract(r.metadata,'$.sourceMessageId')=? LIMIT 2",(*scope,message_id)).fetchall()
                if len(identities)!=1:continue
                children = db.execute('SELECT r.*,s.canonical,s.name'+base+" AND json_extract(r.metadata,'$.parentMessageId')=? ORDER BY r.stamp,r.seq LIMIT 26",(*scope,message_id)).fetchall()
                if len(children)>24:related_capped[0]=True
                for child in children:
                    if child['kind']=='user' or child['key'] in expanded:continue
                    include(child,basis='recorded_unique_parent_id')
                    if child['key'] in related:
                        item['relations'].append({'from':current['key'],'to':child['key'],'relation':'source_parent','basis':'recorded_unique_parent_id'})
                        queue.append(child)
            if meta.get('turnId') and not request_conflict:
                users=self._turn_user_rows(db,scope,meta['turnId'])
                if self._canonical_turn_user(users,row['source']) is not None:
                    rounds = db.execute('SELECT r.*,s.canonical,s.name'+base+" AND json_extract(r.metadata,'$.turnId')=? ORDER BY r.stamp,r.seq LIMIT 26",(*scope,meta['turnId'])).fetchall()
                    if len(rounds)>25:related_capped[0]=True
                    for member in rounds:
                        if member['kind']!='user':include(member,basis='recorded_turn_id')
            neighbors = []
            if row['stamp'] is not None:
                for sign, order in (('<','DESC'),('>=','ASC')):
                    rows = db.execute('SELECT r.*,s.canonical,s.name'+base+
                        ' AND r.key!=? AND r.stamp BETWEEN ? AND ? AND r.stamp'+sign+'? ORDER BY r.stamp '+order+',r.seq '+order+' LIMIT 12',
                        (*scope,key,row['stamp']-600,row['stamp']+600,row['stamp'])).fetchall()
                    for other in rows:
                        if other['key'] in related:continue
                        neighbors.append(dict(self._enriched(db,other,owner),association='same_session_neighbor',relationBasis='same_scope_time_neighborhood'))
                        if len(neighbors)>= (4 if sign=='<' else 8):break
            sort_key = lambda member:(member.get('timestamp') or 0,member['id'])
            item['presentation'] = _presentation(item['arguments'],item['result'],item.get('command'),item['content'])
            item['execution']=execution_evidence.project(item.get('tool'),item['arguments'],item['result'],meta.get('callId'),detail=True,
                source_truncated=bool(raw.get('truncated') or (raw.get('evidence') or {}).get('truncated')))
            item['related'] = sorted(related.values(),key=sort_key)
            selected = {k:v for k,v in item.items() if k not in ('raw','related','relations','gaps','contextItems')}
            selected.update(association='recorded',relationBasis='selected_source_record',contentTruncated=False)
            item['portrait'] = {'userRequest':item['taskContext'],'steps':sorted([selected,*item['related']],key=sort_key),
                'neighbors':sorted(neighbors,key=sort_key),'coverage':{'relatedLimit':24,'neighborLimit':8,
                'sourceSessionsAreTasks':False,'contentLimitPerRecord':60000,
                'contentLimitAppliesTo':'related_and_neighbors','selectedRecordFullContent':True,
                'neighborWindowSeconds':600,
                'truncatedRecords':sum(int(r.get('contentTruncated',False)) for r in [*item['related'],*neighbors]),
                'relatedCapped':related_capped[0]}}
            item['gaps'].extend(['源 Session 容器不等于一个业务任务。', 'SessionLens 源日志不证明实际网络连接，也不保证完整模型上下文。', '尚无本记录与另一采集器的任务关联证据。'])
            return {'item': item}

    def raw(self, key, owner=None):
        """Untouched parsed source JSON, selected by its full scoped identity."""
        with self.lock,self._db() as db:
            self._sync(db);self._scope(db,owner)
            return self._lookup(db,key,owner)[1]
