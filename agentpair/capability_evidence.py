"""Bounded, static capability evidence from recorded tool invocations.

This module never executes JavaScript, shell commands, or tool callbacks. Its
small JS grammar only follows eager, top-level expressions. Function bodies,
branches, loops, dynamic member names and interpolated strings are excluded.
The original wrapper record remains the authority for execution/return status.
"""
import json
import math
from pathlib import Path
import re
import shlex

VERSION = 1
_UNKNOWN = object()
_MAX_SOURCE = 64000
_MAX_CALLS = 64
_MAX_DEPTH = 40
_WORD = re.compile(r'[A-Za-z_$][\w$]*|(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?')
_ACTION_PREFIXES = {'get', 'list', 'search', 'find', 'run', 'create', 'update', 'delete', 'set', 'read', 'write', 'open', 'close', 'fetch', 'send', 'add', 'remove', 'check', 'execute', 'call'}


def skill_path(path):
    """Accept literal SKILL.md reads in installed or custom directories."""
    if not isinstance(path, str) or len(path) > 4096 or any(c in path for c in '$`\n\r\0'):
        return None
    parts = Path(path.replace('\\', '/')).parts
    if len(parts) < 2 or parts[-1] != 'SKILL.md' or parts[-2] in ('.', '..'):
        return None
    # A custom reference directory is legitimate; fixture/sample trees are not
    # evidence that an agent read its operating instructions.
    if any(re.search(r'(?:^|[-_.])(test|tests|fixture|fixtures|sample|samples|audit)(?:$|[-_.])|(?:skill|read)test$', p, re.I)
           for p in parts[:-2]):
        return None
    return parts[-2]


def literal_shell_commands(command):
    delimiter = None
    for line in (command or '').splitlines():
        if delimiter is not None:
            if line.strip() == delimiter:
                delimiter = None
            continue
        heredoc = re.search(r'''(?<!<)<<-?\s*(['"]?)([A-Za-z_]\w*)\1''', line)
        if heredoc:
            delimiter = heredoc.group(2)
            continue
        if '$' in line or '`' in line or '\\' in line:
            continue
        try:
            lexer = shlex.shlex(line, posix=True, punctuation_chars=';&|<>()')
            lexer.whitespace_split = True
            groups = [[]]
            for token in lexer:
                if token in (';', '&&', '||', '|', '&'):
                    groups.append([])
                else:
                    groups[-1].append(token)
        except ValueError:
            continue
        groups = [g[:-3] if len(g) >= 3 and g[-3:] in (['2', '>&', '1'], ['1', '>&', '2']) else g for g in groups]
        controls = {'if', 'then', 'else', 'elif', 'fi', 'for', 'while', 'until', 'do', 'done', 'case', 'esac', 'function'}
        if any(t in controls or t in ('(', ')', '<', '>', '>>', '<<', '>&') for g in groups for t in g):
            continue
        for tokens in groups:
            while tokens and re.fullmatch(r'[A-Za-z_]\w*=[^\s]*', tokens[0]):
                tokens = tokens[1:]
            if tokens:
                yield tokens


def _string(source):
    body = source[1:-1]
    if source.startswith('`') and '${' in body:
        return _UNKNOWN
    escapes = {'n': '\n', 'r': '\r', 't': '\t', 'b': '\b', 'f': '\f', 'v': '\v', '0': '\0'}
    result = []; i = 0
    while i < len(body):
        char = body[i]; i += 1
        if char != '\\':
            result.append(char); continue
        if i >= len(body):
            return _UNKNOWN
        char = body[i]; i += 1
        if char in ('u', 'x'):
            size = 4 if char == 'u' else 2
            value = body[i:i + size]
            if len(value) != size or not re.fullmatch('[0-9a-fA-F]+', value):
                return _UNKNOWN
            result.append(chr(int(value, 16))); i += size
        elif char == '\n':
            continue
        elif char == '\r':
            if body[i:i + 1] == '\n': i += 1
        else:
            result.append(escapes.get(char, char))
    value = ''.join(result)
    if any(0xD800 <= ord(char) <= 0xDFFF for char in value):
        try: value = value.encode('utf-16', 'surrogatepass').decode('utf-16')
        except UnicodeDecodeError: return _UNKNOWN
    return value


def _tokens(code):
    """Tokens retain offsets; strings/comments cannot introduce tool names."""
    result = []; i = 0
    while i < len(code):
        start = i; char = code[i]
        if char.isspace(): i += 1; continue
        if code.startswith('//', i):
            end = code.find('\n', i + 2); i = len(code) if end < 0 else end; continue
        if code.startswith('/*', i):
            end = code.find('*/', i + 2)
            if end < 0: break
            i = end + 2; continue
        if char == '/' and (not result or result[-1][1] in ('=', '(', '[', ',', ':', '?', 'return', '=>')):
            # Regular-expression bodies, like quoted strings, are data.
            i += 1; bracket = False
            while i < len(code):
                if code[i] == '\\': i += 2
                elif code[i] == '[': bracket = True; i += 1
                elif code[i] == ']': bracket = False; i += 1
                elif code[i] == '/' and not bracket:
                    i += 1
                    while i < len(code) and code[i].isalpha(): i += 1
                    break
                elif code[i] in '\n\r': break
                else: i += 1
            result.append(('regex', code[start:i], start, i)); continue
        if char in ('"', "'", '`'):
            quote = char; i += 1
            while i < len(code):
                if code[i] == '\\': i += 2
                elif code[i] == quote: i += 1; break
                else: i += 1
            else: break
            result.append(('string', code[start:i], start, i)); continue
        match = _WORD.match(code, i)
        if match:
            i += len(match[0]); result.append(('word', match[0], start, i)); continue
        op = next((op for op in ('===', '!==', '=>', '&&', '||', '??', '?.', '**', '==', '!=', '<=', '>=', '++', '--', '...') if code.startswith(op, i)), char)
        i += len(op); result.append(('symbol', op, start, i))
    return result


class _JS:
    """A non-evaluating expression reader; unknown semantics lose evidence."""
    def __init__(self, code):
        self.code = code; self.t = _tokens(code); self.i = 0; self.depth = 0

    def peek(self, offset=0):
        return self.t[self.i + offset][1] if self.i + offset < len(self.t) else ''

    def take(self):
        token = self.t[self.i]; self.i += 1; return token

    def skip_balanced(self):
        opening = self.peek(); closing = {'(': ')', '[': ']', '{': '}'}.get(opening)
        if not closing: self.i += 1; return
        self.i += 1
        while self.i < len(self.t):
            if self.peek() == closing: self.i += 1; return
            if self.peek() in ('(', '[', '{'): self.skip_balanced()
            else: self.i += 1

    def after_group(self, start):
        stack = []; closing = {'(': ')', '[': ']', '{': '}'}
        for index in range(start, len(self.t)):
            value = self.t[index][1]
            if value in closing: stack.append(closing[value])
            elif stack and value == stack[-1]:
                stack.pop()
                if not stack: return index + 1
        return len(self.t)

    def body_after(self, start, depth):
        if start >= len(self.t): return start
        if self.t[start][1] == '{': return self.after_group(start)
        end = self.statement_end(start, depth)
        return end + (end < len(self.t) and self.t[end][1] == ';')

    def statement_end(self, start, depth=0):
        if depth > _MAX_DEPTH: raise ValueError('nested statements')
        word = self.t[start][1] if start < len(self.t) else ''
        if word in ('if', 'for', 'while', 'with', 'switch'):
            header = start + 1
            if header < len(self.t) and self.t[header][1] == 'await': header += 1
            if header < len(self.t) and self.t[header][1] == '(':
                end = self.body_after(self.after_group(header), depth + 1)
                if word == 'if' and end < len(self.t) and self.t[end][1] == 'else':
                    end = self.body_after(end + 1, depth + 1)
                return end
        if word == 'do':
            end = self.body_after(start + 1, depth + 1)
            if end + 1 < len(self.t) and self.t[end][1] == 'while' and self.t[end + 1][1] == '(':
                end = self.after_group(end + 1)
            return end
        if word == 'try':
            end = self.body_after(start + 1, depth + 1)
            while end < len(self.t) and self.t[end][1] in ('catch', 'finally'):
                end += 1
                if end < len(self.t) and self.t[end][1] == '(': end = self.after_group(end)
                end = self.body_after(end, depth + 1)
            return end
        depth = 0; end = start
        while end < len(self.t):
            value = self.t[end][1]
            if depth == 0:
                if value == ';': return end
                if end > start and '\n' in self.code[self.t[end - 1][3]:self.t[end][2]]:
                    if self.t[end - 1][1] not in ('=', ',', '.', 'await', '&&', '||', '+', '-') and value not in ('.', '?.', '(', '[', '+', '-'):
                        return end
            if value in ('(', '[', '{'): depth += 1
            elif value in (')', ']', '}'): depth -= 1
            end += 1
        return end

    def expression(self, stop=(',', ';', ')', ']', '}'), eligible=True):
        self.depth += 1
        if self.depth > _MAX_DEPTH: raise ValueError('nested source')
        value, name, calls = self.primary(eligible)
        while self.peek() and self.peek() not in stop:
            op = self.peek()
            if op in ('.', '?.'):
                self.take()
                if self.peek() == '(':  # optional invocation is conditional
                    self.skip_balanced(); value = _UNKNOWN; name = None; continue
                key = self.take()[1] if self.peek() else ''
                name = name + '.' + key if name and op == '.' else None; value = _UNKNOWN
            elif op == '[':
                # Dynamic member lookup is never a recorded tool identity.
                self.take(); _, _, nested = self.expression(eligible=eligible)
                calls.extend(nested)
                if self.peek() == ']': self.take()
                value = _UNKNOWN; name = None
            elif op == '(':
                start = self.t[self.i - 3][2] if name and name.startswith('tools.') and self.i >= 3 else self.t[self.i][2]
                self.take(); args = []; nested = []
                while self.peek() and self.peek() != ')':
                    before = self.i
                    arg, _, found = self.expression(eligible=eligible)
                    args.append(arg); nested.extend(found)
                    if self.peek() == ',': self.take()
                    elif self.i == before: self.take()
                    else: break
                end = self.take()[3] if self.peek() == ')' else None
                calls.extend(nested)
                if eligible and end is not None and name and re.fullmatch(r'tools\.[A-Za-z_$][\w$]*', name):
                    calls.append({'originalName': name[6:], 'arguments': args[0] if len(args) == 1 and args[0] is not _UNKNOWN else None,
                                  'sourceOffset': start, 'sourceEnd': end})
                value = _UNKNOWN; name = None
            elif op == '=>':
                self.take()
                if self.peek() == '{': self.skip_balanced()
                else: self.expression(stop=stop, eligible=False)
                value = _UNKNOWN; name = None; calls = []
            elif op in ('&&', '||', '??', '?'):
                # Only the left expression is eager. Neither branch is proven.
                self.take(); self.expression(stop=stop, eligible=False)
                value = _UNKNOWN; name = None
            else:
                self.take()
                if op == ':' or op == '=' or op in ('+', '-', '*', '/', '%', '==', '===', '!=', '!==', '<', '>', '<=', '>=', '**'):
                    _, _, nested = self.expression(stop=stop, eligible=eligible)
                    calls.extend(nested)
                value = _UNKNOWN; name = None
        self.depth -= 1
        return value, name, calls

    def primary(self, eligible):
        if not self.peek(): return _UNKNOWN, None, []
        token = self.take(); word = token[1]
        if token[0] == 'string': return _string(word), None, []
        if word in ('true', 'false', 'null'): return {'true': True, 'false': False, 'null': None}[word], None, []
        if re.fullmatch(r'(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?', word):
            try:
                value = json.loads(word)
                return value if not isinstance(value, float) or math.isfinite(value) else _UNKNOWN, None, []
            except ValueError: return _UNKNOWN, None, []
        if word in ('await', '!', '~', '+', '-'):
            value, name, calls = self.primary(eligible)
            if word in ('+', '-') and isinstance(value, (int, float)) and not isinstance(value, bool):
                value = value if word == '+' else -value
            elif word != 'await': value = _UNKNOWN
            return value, name, calls
        if word == '(':
            value, name, calls = self.expression(eligible=eligible)
            if self.peek() == ')': self.take()
            return value, name, calls
        if word in ('function', 'class') or word == 'async' and self.peek() == 'function':
            while self.peek() and self.peek() != '{':
                if self.peek() == '(': self.skip_balanced()
                else: self.take()
            if self.peek() == '{': self.skip_balanced()
            return _UNKNOWN, None, []
        if word == '[':
            values = []; calls = []; known = True
            while self.peek() and self.peek() != ']':
                before = self.i; value, _, found = self.expression(eligible=eligible)
                calls.extend(found); values.append(value); known &= value is not _UNKNOWN
                if self.peek() == ',': self.take()
                elif before == self.i: self.take()
                else: break
            if self.peek() == ']': self.take()
            else: known = False
            return values if known else _UNKNOWN, None, calls
        if word == '{':
            values = {}; calls = []; known = True
            while self.peek() and self.peek() != '}':
                key = self.take(); key_value = _string(key[1]) if key[0] == 'string' else key[1]
                if self.peek() != ':':
                    known = False
                    # Object methods/accessors contain function bodies.
                    while self.peek() and self.peek() not in (',', '}'):
                        if self.peek() in ('(', '[', '{'): self.skip_balanced()
                        else: self.take()
                else:
                    self.take(); value, _, found = self.expression(eligible=eligible)
                    calls.extend(found); known &= value is not _UNKNOWN and key_value is not _UNKNOWN
                    if key_value is not _UNKNOWN: values[key_value] = value
                if self.peek() == ',': self.take()
                else: break
            if self.peek() == '}': self.take()
            else: known = False
            return values if known else _UNKNOWN, None, calls
        return _UNKNOWN, word if token[0] == 'word' else None, []

    def calls(self):
        result = []
        while self.i < len(self.t) and len(result) < _MAX_CALLS:
            start = self.i; end = self.statement_end(start)
            word = self.peek()
            if word in ('if', 'for', 'while', 'do', 'switch', 'try', 'catch', 'with', 'function', 'class', 'export', 'return', 'throw') or word == 'async' and self.peek(1) == 'function':
                self.i = end
            else:
                if word in ('const', 'let', 'var'): self.take()
                _, _, found = self.expression(stop=(';',), eligible=True)
                result.extend(found)
            # expression() cannot cross an automatic semicolon boundary.
            if self.i < end: self.i = end
            if self.peek() == ';': self.take()
            if self.i == start: self.i += 1
        return result[:_MAX_CALLS]


def javascript_calls(code):
    if not isinstance(code, str): return []
    # Add explicit statement separators for newline-terminated expressions.
    parser = _JS(code[:_MAX_SOURCE])
    result = []; original = parser.t
    try:
        while parser.i < len(original) and len(result) < _MAX_CALLS:
            start = parser.i; end = parser.statement_end(start)
            statement = original[start:end]
            if statement:
                one = _JS(parser.code); one.t = statement
                result.extend(one.calls())
            parser.i = end + (end < len(original) and original[end][1] == ';')
            if parser.i == start: parser.i += 1
    except (ValueError, RecursionError, IndexError):
        return []
    # Local rebinding invalidates the host identity; lexical scope is not
    # simulated. Inspect tokens so strings/comments cannot cause rejection.
    for index in range(len(original) - 1):
        if original[index][1] == 'tools' and original[index + 1][1] == '=': return []
        if original[index][1] in ('const', 'let', 'var', 'function', 'class'):
            cursor = index + 1
            if original[cursor][1] == 'tools': return []
            if original[cursor][1] in ('{', '['):
                end = parser.after_group(cursor)
                if any(t[1] == 'tools' for t in original[cursor:end]): return []
    return result[:_MAX_CALLS]


def mcp_identity(original, namespace=None):
    direct = re.fullmatch(r'mcp__(.+?)__(.+)', original or '')
    if direct: server, method = direct.groups(); namespace = 'mcp__' + server
    elif isinstance(namespace, str) and namespace.startswith('mcp__') and original:
        server, method = namespace[5:], original
    else: return None
    if len(server) > 512 or len(method) > 512: return None
    value = {'type': 'mcp', 'name': server, 'namespace': namespace, 'method': method, 'originalName': original}
    if server == 'codex_apps' and re.fullmatch(r'[a-z][a-z0-9]*_[a-z][a-z0-9_]*', method):
        alias = method.split('_', 1)[0]
        # A method such as get_profile is an action, with no service prefix.
        # This guard is independent of connector names and keeps aliases an
        # explicit inference alongside the authoritative original namespace.
        if alias not in _ACTION_PREFIXES:
            value.update(serviceName=alias, serviceAliases=[alias], aliasBasis='namespace_method_prefix')
    return value


def capabilities(tool, arguments, item, event, command, kind):
    if kind != 'tool_call': return []
    outer = event.get('callId') or item.get('callId') or item.get('call_id')
    result = []

    def invocation(name, args, shell=None, wrapped=False, source=None, namespace=None):
        mcp = mcp_identity(name, namespace)
        short = (mcp['method'] if mcp else (name or '').rsplit('.', 1)[-1]).casefold()
        base = {'arguments': args, 'originalName': name, 'outerCallId': outer if isinstance(outer, str) else None}
        if source: base.update(source)
        if short == 'skill' and isinstance(args, dict):
            identity = args.get('skill') or args.get('name')
            if isinstance(identity, str) and 0 < len(identity) <= 512:
                result.append(dict(base, type='skill', name=identity, evidence='loaded', basis='explicit_skill_tool_argument'))
        if short in ('read', 'read_file') and isinstance(args, dict):
            path = args.get('file_path') or args.get('path'); identity = skill_path(path)
            if identity: result.append(dict(base, type='skill', name=identity, evidence='read', path=path, basis='explicit_skill_instruction_read'))
        if mcp:
            result.append(dict(base, **mcp, evidence='wrapped' if wrapped else 'direct',
                               basis='literal_javascript_tool_call' if wrapped else 'recorded_mcp_tool_identity'))
        if short not in ('bash', 'exec_command', 'run_command', 'shell'): return
        if shell is None and isinstance(args, dict): shell = args.get('cmd', args.get('command'))
        if not isinstance(shell, str): return
        for tokens in literal_shell_commands(shell):
            program = Path(tokens[0]).name; values = tokens[1:]; paths = []
            if program == 'cat' and values and not any(t.startswith('-') for t in values): paths = values
            elif program in ('head', 'tail'):
                index = 0
                while index < len(values):
                    token = values[index]
                    if token in ('-n', '-c') and index + 1 < len(values) and re.fullmatch(r'\d+', values[index + 1]): index += 2
                    elif re.fullmatch(r'-(?:n|c)?\d+', token): index += 1
                    elif not token.startswith('-'): paths.append(token); index += 1
                    else: paths = []; break
            elif program == 'sed' and len(values) >= 3 and values[0] == '-n' and re.fullmatch(r'\d+(?:,\d+)?p', values[1]):
                paths = values[2:] if not any(t.startswith('-') for t in values[2:]) else []
            for path in paths:
                identity = skill_path(path)
                if identity: result.append(dict(base, type='skill', name=identity, evidence='read', path=path, basis='literal_shell_skill_instruction_read'))
            if re.fullmatch(r'python(?:3(?:\.\d+)?)?', program):
                if values and values[0] == '-u': values = values[1:]
                if len(values) >= 4 and Path(values[0]).name == 'tencentdocs.py' and values[1] == 'tdoc_call' and not any(t in ('--help', '-h') for t in values):
                    server, method = values[2:4]
                    if all(re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,511}', v) for v in (server, method)):
                        result.append(dict(base, type='mcp', name=server, method=method, evidence='wrapped', basis='literal_tdoc_call_command'))

    invocation(tool, arguments, command, namespace=item.get('namespace') or event.get('namespace'))
    # These are orchestration tools, not arbitrary parameters containing JS.
    if (tool or '').rsplit('.', 1)[-1].casefold() == 'exec' and (item.get('namespace') or event.get('namespace')) in (None, 'functions'):
        code = arguments if isinstance(arguments, str) else arguments.get('code') if isinstance(arguments, dict) else None
        for call in javascript_calls(code):
            invocation(call['originalName'], call['arguments'], wrapped=True,
                       source=dict({k: call[k] for k in ('sourceOffset', 'sourceEnd')}, sourceOffsetUnit='javascript_character'))
    # Preserve distinct methods and source positions for detail presentation;
    # ranking counts DISTINCT source records, not inferred inner tool results.
    return list({(c['type'], c['name'], c.get('method'), c['evidence'], c.get('sourceOffset')): c for c in result}.values())
