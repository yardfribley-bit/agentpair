#!/usr/bin/env python3
"""Evaluate the real collection assistant on isolated, synthetic evidence.

No production database is opened. Ground-truth labels never enter the model
packet. All fixture text, URLs, code and timestamps are synthetic. Run explicitly
to use the existing relay; --prepare-only makes no network calls.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentpair.collection_assistant import CollectionAssistant, RelayJSON
from agentpair.collection_view import CollectionView
from agentpair.devices import DeviceStore
from agentpair.session_lens import SessionStore


class CountingRelay:
    def __init__(self, token):
        self.relay = RelayJSON(token)
        self.calls = 0

    def __call__(self, system, data, max_tokens=4000):
        self.calls += 1
        return self.relay(system, data, max_tokens=max_tokens)


class Fixture:
    def __init__(self, case_id, devices, sessions):
        self.case_id = case_id
        self.sessions = sessions
        enrolled = devices.enroll(devices.pairing('collection-qa')['code'], 'Synthetic acceptance ' + str(case_id))
        self.identity = devices.identity(enrolled['token'])
        self.device = self.identity['id']
        self.records = []
        self.contexts = []
        self.session = 'synthetic-case-' + str(case_id)
        self.ids = {}
        self.previous_source = None

    def add(self, label, text='', kind='message', role='user', payload=None, **extra):
        ordinal = len(self.records) + 1
        ident = hashlib.sha256(('collection-acceptance-v1:' + str(self.case_id) + ':' + label).encode()).hexdigest()
        source_id = 'synthetic-' + str(self.case_id) + '-' + label
        body = dict(payload) if payload is not None else {'content': text}
        body.update(id=source_id)
        if self.previous_source:
            body.setdefault('parentId', self.previous_source)
        event = {'schemaVersion': 1, 'id': ident, 'source': 'workbuddy', 'sessionId': self.session,
                 'kind': kind, 'role': role, 'timestamp': 1791100000 + self.case_id * 1000 + ordinal,
                 'payload': body,
                 'evidence': {'path': 'synthetic-' + str(self.case_id) + '.jsonl',
                              'fileIdentity': 'fixture-file-' + str(self.case_id), 'epoch': 0,
                              'byteStart': ordinal * 1024, 'byteEnd': ordinal * 1024 + 1024,
                              'sha256': hashlib.sha256(json.dumps(body, ensure_ascii=False).encode()).hexdigest()},
                 **extra}
        self.records.append(event)
        self.ids[label] = ident
        self.previous_source = source_id
        return ident

    def context(self, label, devices, current, history=''):
        ident = hashlib.sha256(('collection-acceptance-context:' + str(self.case_id) + ':' + label).encode()).hexdigest()
        messages = [{'role': 'system', 'content': '合成测试背景，不是真实任务。' + history},
                    {'role': 'user', 'content': current}]
        item = {'id': ident, 'sessionId': self.session, 'timestamp': self.records[-1]['timestamp'],
                'source': 'workbuddy_generation_context',
                'body': json.dumps({'messages': messages}, ensure_ascii=False)}
        with devices.connect() as db:
            db.execute('INSERT INTO applens_model_context VALUES(?,?,?,?)',
                       (self.device, ident, json.dumps(item, ensure_ascii=False), item['timestamp']))
        self.contexts.append(item)
        self.ids[label] = ident
        return ident

    def flush(self):
        # Exercise shuffled receipt order too: source timestamp/offset remains
        # the execution-order evidence, not the server insertion row ID.
        shuffled = self.records[::2] + self.records[1::2]
        for start in range(0, len(shuffled), 30):
            self.sessions.ingest(self.identity, {'schemaVersion': 1, 'events': shuffled[start:start + 30]})

    def call(self, label, name, arguments, call_id):
        return self.add(label, kind='tool_call', role=None, name=name, callId=call_id,
                        payload={'name': name, 'arguments': arguments})

    def result(self, label, output, call_id):
        return self.add(label, kind='tool_result', role=None, callId=call_id, payload={'output': output})

    def expected(self, goal, goal_words, turns, calls, forbidden=(), required_contexts=()):
        self.truth = {'goalId': self.ids[goal], 'goalWords': list(goal_words),
                      'turnIds': [self.ids[label] for label in turns],
                      'callResults': {'sessionlens:' + self.ids[call]: ['sessionlens:' + self.ids[result]] for call, result in calls},
                      'forbiddenIds': [self.ids[label] for label in forbidden],
                      'requiredContextIds': [self.ids[label] for label in required_contexts]}


def fixtures(devices, sessions):
    cases = []
    weather = Fixture(1, devices, sessions)
    weather.add('goal', '查一下上海现在的天气和温度')
    weather.add('thinking', '需要上海当前天气，先访问实时天气接口再读取温度字段。', 'reasoning', None)
    weather.call('call', 'Bash', {'command': 'curl -fsS https://weather.example.test/current?city=Shanghai', 'timeout': 15}, 'weather-1')
    weather.result('result', {'stdout': '{"city":"Shanghai","temperature":22,"condition":"cloudy"}', 'exitCode': 0}, 'weather-1')
    weather.add('reply', '上海当前多云，22℃；以上是天气接口返回。', role='assistant')
    weather.expected('goal', ['上海', '天气'], ['goal'], [('call', 'result')])
    weather.question = '前面那次气象查询，是怎么拿到上海实时气温的？'
    cases.append(weather)

    login = Fixture(2, devices, sessions)
    login.add('goal', '为会员系统设计登录功能，先比较密码登录与邮箱验证码方案')
    login.add('proposal', '第一种是密码登录，第二种是邮箱验证码。邮箱验证码不保存用户密码，需记录认证审计日志。', role='assistant')
    login.add('choice', '后者挺合适，就以它为准，同时把失败尝试也记下来')
    login.add('agreed', '采用邮箱验证码，并记录登录成功与失败审计事件。', role='assistant')
    login.add('weather', '先插一件事，查一下上海天气')
    login.call('weather-call', 'WebSearch', {'query': '上海当前天气'}, 'login-weather')
    login.result('weather-result', {'city': '上海', 'temperature': 22, 'condition': '多云'}, 'login-weather')
    login.add('weather-reply', '上海多云，22℃。', role='assistant')
    login.add('resume', '回到会员身份验证，沿着我们选定的方式继续')
    login.add('resume-proposal', '继续邮箱验证码登录方案，下一步写登录页面与审计模块。', role='assistant')
    login.add('execute', '这回不用再讨论了，把它落地给我看')
    login.add('thinking', '按已选邮箱验证码方案实现页面，认证失败和成功均写入审计日志。', 'reasoning', None)
    login.call('call', 'Write', {'path': 'qa-fixture/members/login.py',
                               'content': 'def login(email, code):\n    audit.record(email=email, method="email_otp")\n'}, 'login-write')
    login.result('result', {'path': 'qa-fixture/members/login.py', 'written': True}, 'login-write')
    login.add('reply', '邮箱验证码登录页面和认证审计模块已经写入；此记录没有运行测试。', role='assistant')
    login.expected('goal', ['登录'], ['goal', 'choice', 'resume', 'execute'], [('call', 'result')],
                   forbidden=['weather', 'weather-call', 'weather-result'])
    login.question = '会员身份认证从方案讨论到写代码，中间怎么确认并继续的？'
    cases.append(login)

    video = Fixture(3, devices, sessions)
    video.add('goal', '做一个五秒SSH协议动画，展示客户端和服务器的密钥交换过程')
    video.add('proposal', '先查找视频生成工具，再编写客户端、服务器和密钥交换的动画提示词。', role='assistant')
    video.add('revision', '画面标明双方身份，不要把密钥本身画出来')
    video.add('thinking', '先定位VideoGen能力，再用示意箭头展示交换过程，避免显示真实密钥。', 'reasoning', None)
    video.call('search', 'ToolSearch', {'query': 'VideoGen 短视频生成 duration resolution'}, 'video-search')
    video.result('search-result', {'name': 'VideoGen', 'parameters': ['prompt', 'duration', 'resolution']}, 'video-search')
    video.call('call', 'VideoGen', {'prompt': 'SSH client and server key exchange animation; labels Client and Server; abstract arrows; no real secret keys',
                                  'duration': 5, 'resolution': '720p'}, 'video-generate')
    video.result('result', {'status': 'completed', 'url': 'https://media.example.test/synthetic-ssh-5s.mp4', 'duration': 5}, 'video-generate')
    video.add('reply', '生成工具返回了五秒动画的文件链接。没有独立播放或内容核验记录。', role='assistant')
    video.expected('goal', ['SSH', '动画'], ['goal', 'revision'], [('search', 'search-result'), ('call', 'result')])
    video.question = '那段展示安全远程连接的短片如何生成，最后给出了什么？'
    cases.append(video)

    context = Fixture(4, devices, sessions)
    context.add('old-ssh', '此前的任务：生成五秒SSH密钥交换短片')
    context.call('old-call', 'VideoGen', {'prompt': 'SSH key exchange', 'duration': 5}, 'old-video')
    context.result('old-result', {'url': 'https://media.example.test/old-synthetic-ssh.mp4'}, 'old-video')
    context.add('old-reply', '旧SSH任务的链接已返回。', role='assistant')
    context.add('goal', '现在查上海的天气，告诉我气温')
    context.add('thinking', '现在的目标是上海天气，旧SSH动画与本次查询没有执行关系。', 'reasoning', None)
    context.call('call', 'Bash', {'command': 'curl -fsS https://weather.example.test/current?city=Shanghai'}, 'context-weather')
    context.result('result', {'stdout': '{"city":"Shanghai","temperature":22}', 'exitCode': 0}, 'context-weather')
    context.add('reply', '上海22℃。这次使用了实时天气接口。', role='assistant')
    context.context('context', devices, '现在查上海的天气，告诉我气温',
                    history='历史摘要提到：此前已生成五秒SSH密钥交换短片；这是旧任务背景，不是当前要求。')
    context.expected('goal', ['上海', '天气'], ['goal'], [('call', 'result')],
                     forbidden=['old-ssh', 'old-call', 'old-result'], required_contexts=['context'])
    context.question = '查上海天气这一轮，AppLens记录对应哪一个任务，调用了什么网址？'
    cases.append(context)
    for case in cases:
        case.flush()
    return cases


def evaluate(result, case):
    truth = case.truth
    candidates = result.get('candidates', [])
    evidence = result.get('evidence', [])
    selected = next((task for task in candidates if task.get('taskId') == truth['goalId']), None)
    known_records = {'sessionlens:' + record['id'] for record in case.records} | {context['id'] for context in case.contexts}
    refs = {record.get('ref'): record for record in evidence}
    citations = result.get('answer', {}).get('evidenceRefs', [])
    known_evidence = bool(evidence) and all(record.get('recordId') in known_records for record in evidence)
    grounded_refs = bool(citations) and all(ref in refs for ref in citations)
    own_turns = selected.get('turnIds', []) if selected else []
    exact_turns = own_turns == truth['turnIds']
    forbidden = set(truth['forbiddenIds'])
    candidate_members = [identity for task in candidates for identity in task.get('turnIds', [])]
    candidate_records = [identity.removeprefix('sessionlens:') for task in candidates for identity in task.get('recordIds', [])]
    mixed = [identity for identity in candidate_members + candidate_records if identity in forbidden]
    mix_rate = len(mixed) / max(1, len(candidate_members) + len(candidate_records))
    executions = {action.get('recordId'): action for action in selected.get('executions', [])} if selected else {}
    tools_owned = all(call in executions and set(returns).issubset(set(executions[call].get('resultRecordIds', [])))
                      for call, returns in truth['callResults'].items())
    title = (selected.get('title', '') if selected else '').lower()
    goal_terms = all(term.lower() in title for term in truth['goalWords'])
    attached = selected.get('contexts', []) if selected else []
    contexts_owned = all(any(context.get('recordId') == ident and context.get('status') == 'supported' for context in attached)
                         for ident in truth['requiredContextIds'])
    other_tasks_context = any(context.get('recordId') in truth['requiredContextIds'] and context.get('status') == 'supported'
                              for task in candidates if task.get('taskId') != truth['goalId'] for context in task.get('contexts', []))
    checks = {'goalRetrieved': selected is not None, 'goalTermsMatch': goal_terms,
              'associationSupported': bool(selected and selected.get('status') == 'supported' and
                                            all(turn.get('status') == 'supported' for turn in selected.get('requirements', []))),
              'exactRequirementTurns': exact_turns, 'toolsAndReturnsOwned': tools_owned,
              'noTaskMixing': mix_rate == 0, 'evidenceExistsInFixture': known_evidence,
              'answerReferencesProvidedEvidence': grounded_refs,
              'currentContextOwned': contexts_owned and not other_tasks_context}
    return {'passed': all(checks.values()), 'checks': checks, 'taskMixingRate': mix_rate,
            'expectedRequirementTurns': len(truth['turnIds']), 'actualRequirementTurns': len(own_turns),
            'expectedToolCalls': len(truth['callResults']), 'actualToolCalls': len(executions),
            'selectedCandidateCount': len(candidates), 'selectedEvidenceCount': len(evidence)}


def safe_directory(path):
    directory = Path(path).expanduser().resolve()
    allowed = [Path('/tmp').resolve(), Path('/var/lib/agentpair/qa').resolve()]
    if not any(base != directory and base in directory.parents for base in allowed):
        raise ValueError('Evaluation directory must be a child of /tmp or /var/lib/agentpair/qa')
    if directory.exists() and any(directory.iterdir()):
        raise ValueError('Use a fresh empty evaluation directory')
    directory.mkdir(parents=True, exist_ok=True)
    directory.chmod(0o700)
    return directory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', required=True, help='Fresh directory under /tmp or /var/lib/agentpair/qa')
    parser.add_argument('--private', default='/etc/agentpair/private.json', help='Read relayToken in memory; never copy it')
    parser.add_argument('--prepare-only', action='store_true', help='Build fixtures and truth labels without network calls')
    args = parser.parse_args()
    directory = safe_directory(args.directory)
    devices = DeviceStore(directory / 'devices.db')
    sessions = SessionStore(directory / 'sessions.db')
    cases = fixtures(devices, sessions)
    labels = {'schemaVersion': 1, 'syntheticOnly': True, 'createdAt': time.time(),
              'cases': [{'caseId': case.case_id, 'deviceId': case.device, 'question': case.question,
                         'truth': case.truth, 'sourceRecordCount': len(case.records),
                         'contextRecordCount': len(case.contexts)} for case in cases]}
    (directory / 'ground-truth.json').write_text(json.dumps(labels, ensure_ascii=False, indent=2), encoding='utf-8')
    print('已准备 ' + str(len(cases)) + ' 组合成用例，' + str(sum(len(case.records) for case in cases)) + ' 条会话记录，' +
          str(sum(len(case.contexts) for case in cases)) + ' 条上下文记录。', flush=True)
    if args.prepare_only:
        (directory / 'results.json').write_text(json.dumps({'syntheticOnly': True, 'status': 'prepared',
          'modelCalls': 0, 'questionsRun': 0, 'cases': len(cases)}, ensure_ascii=False, indent=2), encoding='utf-8')
        return 0
    private = json.loads(Path(args.private).read_text(encoding='utf-8'))
    token = private.get('relayToken')
    if not isinstance(token, str) or not token:
        raise ValueError('Private configuration has no relayToken')
    relay = CountingRelay(token)
    del token, private
    assistant = CollectionAssistant(directory / 'assistant.db', CollectionView(devices, sessions), relay)
    results = []
    for case in cases:
        print('开始用例 ' + str(case.case_id) + '/' + str(len(cases)) + '。', flush=True)
        stages = [0]
        def progress(_text):
            stages[0] += 1
            print('用例 ' + str(case.case_id) + '：处理阶段 ' + str(stages[0]) + '。', flush=True)
        started = time.monotonic()
        calls = relay.calls
        try:
            result = assistant.answer('collection-qa', case.device, case.question, progress)
            metrics = evaluate(result, case)
            item = {'caseId': case.case_id, 'question': case.question, 'metrics': metrics,
                    'elapsedSeconds': round(time.monotonic() - started, 3), 'modelCalls': relay.calls - calls,
                    'result': result}
        except Exception as error:
            # Relay errors are bounded public messages. Do not print request
            # payloads, HTTP headers, credentials or source text to the console.
            item = {'caseId': case.case_id, 'metrics': {'passed': False}, 'errorType': type(error).__name__,
                    'error': str(error)[:300], 'elapsedSeconds': round(time.monotonic() - started, 3),
                    'modelCalls': relay.calls - calls}
        results.append(item)
        passed = sum(item['metrics']['passed'] for item in results)
        output = {'schemaVersion': 1, 'syntheticOnly': True, 'status': 'completed' if len(results) == len(cases) else 'running',
                  'questionsRun': len(results), 'passed': passed, 'failed': len(results) - passed,
                  'modelCalls': relay.calls, 'results': results,
                  'limits': ['Synthetic fixtures only; not a real-history retrieval accuracy claim.',
                             'Evidence identity checks do not prove every answer sentence is semantically correct.',
                             'This evaluation makes no cloud-machine creation or production database changes.']}
        (directory / 'results.json').write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding='utf-8')
        print('已完成 ' + str(len(results)) + '/' + str(len(cases)) + '，通过 ' + str(passed) + '，未通过 ' + str(len(results) - passed) + '。', flush=True)
    return 0 if all(item['metrics']['passed'] for item in results) else 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, OSError) as error:
        print('评估未启动：' + str(error), file=sys.stderr, flush=True)
        raise SystemExit(2)
