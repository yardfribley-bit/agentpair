"""Pipeline acceptance with source evidence and structurally valid fake models.

These tests exercise retrieval, boundaries and citation validation, not model
quality. Fakes return the short labels supplied by the pipeline, never raw IDs.
"""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agentpair.collection_assistant import (
    CollectionAssistant, current_context_input, validate_answer,
    validate_context_links, validate_plan, validate_selection,
)
from agentpair.collection_semantics import validate_lineage
from agentpair.collection_view import CollectionView
from agentpair.devices import DeviceStore
from agentpair.session_lens import SessionStore


class ImmediateThread:
    def __init__(self, target, args=(), **kwargs):
        self.target, self.args = target, args

    def start(self):
        self.target(*self.args)


class EvidenceModel:
    """Identify a test-stage by its input contract and echo real short labels."""
    def __init__(self):
        self.calls = []

    def __call__(self, system, data, max_tokens=4000):
        self.calls.append((system, data, max_tokens))
        if 'contexts' in data and 'userTurns' in data:
            links = []
            for context_label, context in data['contexts'].items():
                match = next((label for label, turn in data['userTurns'].items()
                              if turn['text'] == context['text']), None)
                links.append({'contextId': context_label, 'turnId': match,
                              'status': 'supported' if match else 'ambiguous',
                              'reason': '与当前用户原话一致' if match else '未找到对应当前输入'})
            return {'contextLinks': links}
        if 'turns' in data:
            links = []
            membership = None
            previous_member = None
            for turn in data['turns']:
                ident = turn['turnId']
                if not ident.startswith('T') or len(ident) > 8:
                    raise AssertionError('Pipeline must send short turn labels')
                user = turn['user']
                parent, relation = None, 'request'
                if '设计会员' in user:
                    membership = ident
                elif '采用第二种' in user:
                    parent, relation = membership, 'approval'
                elif '回到会员' in user:
                    parent, relation = membership, 'resume'
                elif '执行吧' in user:
                    parent, relation = previous_member, 'execution'
                if parent or membership == ident:
                    previous_member = ident
                links.append({'turnId': ident, 'parentTurnId': parent,
                              'relation': relation, 'status': 'supported',
                              'reason': '用户明确承接会员需求' if parent else '独立用户目标',
                              'evidenceTurnIds': [ident] + ([parent] if parent else [])})
            return {'links': links}
        if 'candidates' in data:
            question = data['question']
            target = '上海' if '上海' in question else 'SSH' if 'SSH' in question else '会员'
            found = next((item for item in data['candidates'] if target in item['text']), None)
            if found and (not found['id'].startswith('H') or len(found['id']) > 8):
                raise AssertionError('Pipeline must send short hit labels')
            return {'selected': [{'id': found['id'], 'status': 'supported', 'reason': '候选包含所问的用户目标'}] if found else []}
        if 'tasks' in data and 'evidence' in data:
            refs = [record['ref'] for record in data['evidence']]
            if not refs:
                raise AssertionError('Answer requires evidence')
            title = data['tasks'][0]['title']
            return {'answer': {'text': '记录中用户提出“' + title + '”，随后有需求讨论和相应执行。工具动作和消息数量不足以推算模型请求次数。',
                               'basis': 'recorded', 'evidenceRefs': refs[:3]}, 'gaps': []}
        question = data['question']
        if '不存在' in question:
            terms = ['深海不存在的项目代号XQJ']
        elif '上海' in question:
            terms = ['上海', '天气']
        elif 'SSH' in question:
            terms = ['SSH']
        else:
            terms = ['会员']
        return {'terms': terms}


class CollectionAssistantTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.devices = DeviceStore(self.root / 'devices.db')
        self.sessions = SessionStore(self.root / 'sessions.db')
        enrolled = self.devices.enroll(self.devices.pairing('alice')['code'], 'Alice Mac')
        self.identity = self.devices.identity(enrolled['token'])
        self.device = self.identity['id']
        self.collections = CollectionView(self.devices, self.sessions)
        self.model = EvidenceModel()
        self.assistant = CollectionAssistant(self.root / 'assistant.db', self.collections, self.model)
        self.events = []

    def tearDown(self):
        self.tmp.cleanup()

    def record(self, text='', kind='message', role='user', source='workbuddy', session='s', **metadata):
        ordinal = len(self.events) + 1
        ident = hashlib.sha256((source + session + str(ordinal) + text).encode()).hexdigest()
        payload = metadata.pop('payload', {'content': text})
        payload.setdefault('id', 'source-message-' + str(ordinal))
        event = {'schemaVersion': 1, 'id': ident, 'source': source, 'sessionId': session,
                 'kind': kind, 'role': role, 'timestamp': 1700000000 + ordinal,
                 'payload': payload, 'evidence': {'path': session + '.jsonl', 'fileIdentity': session + '-file',
                                                'epoch': 0, 'byteStart': ordinal * 100}, **metadata}
        self.events.append(event)
        return event

    def ingest(self, events=None, identity=None):
        events = events or self.events
        for start in range(0, len(events), 30):
            self.sessions.ingest(identity or self.identity, {'schemaVersion': 1, 'events': events[start:start + 30]})

    def conversation(self):
        self.origin = self.record('设计会员管理页面')
        self.record('方案一密码，方案二邮箱登录', role='assistant')
        self.revision = self.record('采用第二种，登录必须记录审计日志')
        self.record('采用邮箱登录并加入审计记录', role='assistant')
        self.weather = self.record('查上海天气')
        self.record('先搜索上海天气并核对日期', kind='reasoning', role=None)
        self.weather_call = self.record(kind='tool_call', role=None, name='WebSearch', callId='weather-call',
                                        payload={'name': 'WebSearch', 'arguments': {'query': '上海天气'}})
        self.weather_result = self.record(kind='tool_result', role=None, callId='weather-call', payload={'output': '上海阴天，20℃'})
        self.record('上海阴天，20℃', role='assistant')
        self.resume = self.record('回到会员管理，按刚才方案做')
        self.record('将沿用邮箱登录和审计日志方案', role='assistant')
        self.execution = self.record('执行吧')
        self.record('先添加邮箱登录组件，再写审计事件', kind='reasoning', role=None)
        self.member_call = self.record(kind='tool_call', role=None, name='Write', callId='member-call',
                                      payload={'arguments': {'path': 'members.py', 'content': 'email login and audit'}})
        self.member_result = self.record(kind='tool_result', role=None, callId='member-call',
                                        payload={'output': {'stdout': 'written members.py', 'exitCode': 0}})
        self.record('会员页面和审计日志代码已写完', role='assistant')
        self.ingest()

    def test_multiturn_demand_resumes_without_absorbing_interleaved_weather(self):
        self.conversation()
        result = self.assistant.answer('alice', self.device, '会员管理是怎么做的？')
        self.assertEqual(result['question'], '会员管理是怎么做的？')
        self.assertEqual(len(result['candidates']), 1)
        candidate = result['candidates'][0]
        self.assertEqual(candidate['taskId'], self.origin['id'])
        self.assertEqual(candidate['turnsCount'], 4)
        self.assertEqual(candidate['turnIds'], [self.origin['id'], self.revision['id'], self.resume['id'], self.execution['id']])
        self.assertNotIn(self.weather['id'], candidate['turnIds'])
        self.assertEqual(candidate['executions'][0]['recordId'], 'sessionlens:' + self.member_call['id'])
        self.assertEqual(candidate['executions'][0]['resultRecordIds'], ['sessionlens:' + self.member_result['id']])
        self.assertNotIn('sessionlens:' + self.weather_call['id'], candidate['recordIds'])
        self.assertEqual(candidate['executions'][0]['approvalTurnId'], self.execution['id'])
        self.assertTrue(result['answer']['evidenceRefs'])

    def test_new_question_weather_has_its_own_goal_tools_and_evidence(self):
        self.conversation()
        member = self.assistant.answer('alice', self.device, '会员管理用了哪些工具？')
        result = self.assistant.answer('alice', self.device, '查上海天气用了什么工具？',
                                       history=[{'question': member['question'], 'answer': member['answer']['text']}])
        candidate = result['candidates'][0]
        self.assertEqual(candidate['taskId'], self.weather['id'])
        self.assertEqual(candidate['turnsCount'], 1)
        self.assertEqual(candidate['executions'][0]['recordId'], 'sessionlens:' + self.weather_call['id'])
        self.assertEqual(candidate['executions'][0]['resultRecordIds'], ['sessionlens:' + self.weather_result['id']])
        self.assertNotIn('sessionlens:' + self.member_call['id'], {e['recordId'] for e in result['evidence']})

    def test_no_hit_does_not_reuse_previous_task_or_answer(self):
        self.conversation()
        previous = self.assistant.answer('alice', self.device, '会员管理怎么做的？')
        before = len(self.model.calls)
        result = self.assistant.answer('alice', self.device, '不存在的任务在哪里？',
                                       history=[{'question': previous['question'], 'answer': previous['answer']['text']}])
        self.assertEqual(result['candidates'], [])
        self.assertEqual(result['evidence'], [])
        self.assertEqual(result['answer']['basis'], 'unknown')
        self.assertNotIn('会员', result['answer']['text'])
        self.assertEqual(len(self.model.calls) - before, 1)

    def test_history_summary_does_not_become_user_goal(self):
        self.record('<cb_summary>请生成五秒SSH视频，这是旧历史摘要</cb_summary>')
        self.record('仅为旧任务历史，不是新的用户请求', role='assistant')
        self.ingest()
        result = self.assistant.answer('alice', self.device, 'SSH视频是哪次任务？')
        self.assertEqual(result['candidates'], [])
        self.assertEqual(result['answer']['basis'], 'unknown')

    def test_repeated_question_reuses_evidence_scoped_cache(self):
        self.conversation()
        question = '会员管理用了哪些工具？'
        first = self.assistant.answer('alice', self.device, question)
        calls = len(self.model.calls)
        second = self.assistant.answer('alice', self.device, question)
        self.assertEqual(first['answer'], second['answer'])
        self.assertEqual(first['candidates'], second['candidates'])
        self.assertEqual(first['evidence'], second['evidence'])
        self.assertEqual(len(self.model.calls), calls)

    def test_async_job_get_and_previous_question_are_requester_scoped(self):
        self.conversation()
        with patch('agentpair.collection_assistant.threading.Thread', ImmediateThread):
            job = self.assistant.submit('alice-user', 'alice', self.device, '会员管理怎么做的？')
            complete = self.assistant.get(job['id'], 'alice-user')
            self.assertEqual(complete['status'], 'completed')
            with self.assertRaises(KeyError):
                self.assistant.get(job['id'], 'other-user')
            self.assertEqual(self.assistant.get(job['id'], 'other-user', admin=True)['status'], 'completed')
            with self.assertRaises(ValueError):
                self.assistant.submit('other-user', 'alice', self.device, '它用了什么工具？', previous=job['id'])
            other = self.devices.enroll(self.devices.pairing('alice')['code'], 'Another Mac')
            other_identity = self.devices.identity(other['token'])
            with self.assertRaises(ValueError):
                self.assistant.submit('alice-user', 'alice', other_identity['id'], '它用了什么工具？', previous=job['id'])
            follow = self.assistant.submit('alice-user', 'alice', self.device, '会员管理用了什么工具？', previous=job['id'])
            self.assertEqual(self.assistant.get(follow['id'], 'alice-user')['status'], 'completed')
        with self.assertRaises(PermissionError):
            self.assistant.answer('bob', self.device, '会员管理')

    def test_invalid_model_citation_fails_job_instead_of_publishing(self):
        self.conversation()
        original = self.model
        def invalid(system, data, max_tokens=4000):
            if 'tasks' in data and 'evidence' in data:
                return {'answer': {'text': '未经支持的答案', 'basis': 'recorded', 'evidenceRefs': ['E999']}, 'gaps': []}
            return original(system, data, max_tokens)
        self.assistant.model = invalid
        with patch('agentpair.collection_assistant.threading.Thread', ImmediateThread):
            job = self.assistant.submit('alice-user', 'alice', self.device, '会员管理怎么做的？')
        result = self.assistant.get(job['id'], 'alice-user')
        self.assertEqual(result['status'], 'failed')
        self.assertNotIn('result', result)
        self.assertIn('未提供的证据', result['error'])

    def test_selected_model_excerpts_mask_secrets_without_rewriting_logs(self):
        secret = 'sk-SyntheticAcceptanceCredential123456'
        self.record('设计会员管理页面，api_key=' + secret)
        self.record('会员页面已按要求建立', role='assistant')
        self.ingest()
        result = self.assistant.answer('alice', self.device, '会员管理怎么做的？')
        self.assertTrue(result['candidates'])
        self.assertNotIn(secret, json.dumps(self.model.calls, ensure_ascii=False))
        self.assertIn('[已隐藏]', json.dumps(self.model.calls, ensure_ascii=False))
        with self.sessions.connect() as db:
            raw = db.execute('SELECT event FROM session_events WHERE id=?', (self.events[0]['id'],)).fetchone()[0]
        self.assertIn(secret, raw)

    def test_contexts_attach_current_input_not_historical_weather_mentions(self):
        self.conversation()
        context = {'id': 'ctx-member', 'sessionId': 's', 'timestamp': self.resume['timestamp'] + 1,
                   'body': json.dumps({'messages': [{'role': 'user', 'content': '查上海天气'},
                                                   {'role': 'assistant', 'content': '上海阴天'},
                                                   {'role': 'user', 'content': '回到会员管理，按刚才方案做'}]}, ensure_ascii=False)}
        with self.devices.connect() as db:
            db.execute('INSERT INTO applens_model_context VALUES(?,?,?,?)', (self.device, context['id'], json.dumps(context), context['timestamp']))
        result = self.assistant.answer('alice', self.device, '会员管理怎么做的？')
        task = result['candidates'][0]
        self.assertEqual(task['contexts'][0]['status'], 'supported')
        evidence = next(e for e in result['evidence'] if e['collector'] == 'applens')
        self.assertEqual(evidence['text'], '回到会员管理，按刚才方案做')
        self.assertNotIn('上海', evidence['text'])

    def independent_goals_with_adversarial_context_model(self):
        original = self.model
        def model(system, data, max_tokens=4000):
            if 'turns' in data:
                original.calls.append((system, data, max_tokens))
                links = []
                active = None
                for turn in data['turns']:
                    ident = turn['turnId']
                    parent = active if turn['user'] == '执行吧' else None
                    links.append({'turnId': ident, 'parentTurnId': parent,
                                  'relation': 'execution' if parent else 'request', 'status': 'supported',
                                  'reason': '短指令承接当前独立目标' if parent else '独立目标',
                                  'evidenceTurnIds': [ident] + ([parent] if parent else [])})
                    if parent is None:
                        active = ident
                return {'links': links}
            if 'contexts' in data and 'userTurns' in data:
                original.calls.append((system, data, max_tokens))
                links = []
                for label, context in data['contexts'].items():
                    # Deliberately overconfident: matching the first same text
                    # would assign weather's short instruction to selected login.
                    match = next((key for key, turn in data['userTurns'].items()
                                  if turn['text'] == context['text']), None)
                    links.append({'contextId': label, 'turnId': match,
                                  'status': 'supported' if match else 'ambiguous',
                                  'reason': '仅按输入字面相同强行确认' if match else '无对应输入'})
                return {'contextLinks': links}
            return original(system, data, max_tokens)
        self.assistant.model = model

    def add_current_context(self, ident, text):
        context = {'id': ident, 'sessionId': 's', 'timestamp': self.events[-1]['timestamp'] + 1,
                   'body': json.dumps({'messages': [{'role': 'user', 'content': text}]}, ensure_ascii=False)}
        with self.devices.connect() as db:
            db.execute('INSERT INTO applens_model_context VALUES(?,?,?,?)',
                       (self.device, ident, json.dumps(context), context['timestamp']))
        return context

    def test_same_short_input_of_weather_and_login_cannot_attach_weather_context_to_login(self):
        login = self.record('设计会员管理页面')
        self.record('将写邮箱登录页面', role='assistant')
        self.record('执行吧')
        self.record('会员登录页面已写入', role='assistant')
        self.record('查上海天气')
        self.record('将查询天气接口', role='assistant')
        self.record('执行吧')
        self.record('上海多云，22℃', role='assistant')
        self.ingest()
        context = self.add_current_context('ctx-weather-short-instruction', '执行吧')
        self.independent_goals_with_adversarial_context_model()
        result = self.assistant.answer('alice', self.device, '会员登录页面怎么做的？')
        candidate = result['candidates'][0]
        self.assertEqual(candidate['taskId'], login['id'])
        link = next(link for link in candidate['contexts'] if link['recordId'] == context['id'])
        self.assertEqual(link['status'], 'ambiguous')
        self.assertNotIn(context['id'], {record['recordId'] for record in result['evidence']})
        packet = next(data for _, data, _ in self.model.calls if 'contexts' in data)
        self.assertTrue(next(iter(packet['contexts'].values()))['repeatedInput'])

    def test_unique_weather_input_still_supports_context_after_unrelated_login(self):
        self.record('设计会员管理页面')
        self.record('将写邮箱登录页面', role='assistant')
        self.record('执行吧')
        self.record('会员登录页面已写入', role='assistant')
        weather = self.record('查上海天气')
        self.record('上海多云，22℃', role='assistant')
        self.ingest()
        context = self.add_current_context('ctx-unique-weather', '查上海天气')
        self.independent_goals_with_adversarial_context_model()
        result = self.assistant.answer('alice', self.device, '上海天气是哪次需求？')
        candidate = result['candidates'][0]
        self.assertEqual(candidate['taskId'], weather['id'])
        self.assertEqual(candidate['contexts'][0]['status'], 'supported')
        self.assertIn(context['id'], {record['recordId'] for record in result['evidence']})
        packet = next(data for _, data, _ in self.model.calls if 'contexts' in data)
        self.assertFalse(next(iter(packet['contexts'].values()))['repeatedInput'])

    def test_duplicate_short_input_outside_selected_turn_window_remains_ambiguous(self):
        self.record('查上海天气')
        self.record('准备查询天气', role='assistant')
        earlier = self.record('执行吧')
        self.record('旧天气任务已回复', role='assistant')
        for ordinal in range(28):
            self.record('新的独立临时任务 topic-' + str(ordinal))
            self.record('这个临时任务已回复', role='assistant')
        login = self.record('设计会员管理页面')
        self.record('将写邮箱登录页面', role='assistant')
        current = self.record('执行吧')
        self.record('会员登录页面已写入', role='assistant')
        self.ingest()
        context = self.add_current_context('ctx-duplicate-outside-window', '执行吧')
        self.independent_goals_with_adversarial_context_model()
        result = self.assistant.answer('alice', self.device, '会员登录页面怎么做的？')
        candidate = result['candidates'][0]
        self.assertEqual(candidate['taskId'], login['id'])
        self.assertIn(current['id'], candidate['turnIds'])
        self.assertNotIn(earlier['id'], candidate['turnIds'])
        lineage_packet = next(data for _, data, _ in self.model.calls if 'turns' in data)
        self.assertLessEqual(len(lineage_packet['turns']), 24)
        self.assertEqual(sum(turn['user'] == '执行吧' for turn in lineage_packet['turns']), 1)
        packet = next(data for _, data, _ in self.model.calls if 'contexts' in data)
        self.assertEqual(sum(turn['text'] == '执行吧' for turn in packet['userTurns'].values()), 1)
        self.assertTrue(next(iter(packet['contexts'].values()))['repeatedInput'])
        link = next(link for link in candidate['contexts'] if link['recordId'] == context['id'])
        self.assertEqual(link['status'], 'ambiguous')
        self.assertNotIn(context['id'], {record['recordId'] for record in result['evidence']})

    def test_long_requirement_tail_reaches_candidate_selection_and_lineage(self):
        request = self.record('背景资料 ' * 900 + '请生成一个五秒SSH视频，重点展示密钥交换')
        self.record('将查找视频生成工具', role='assistant')
        self.ingest()
        result = self.assistant.answer('alice', self.device, '那次SSH视频任务是什么要求？')
        self.assertEqual(result['candidates'][0]['taskId'], request['id'])
        self.assertIn('密钥交换', result['candidates'][0]['requirements'][0]['text'])
        select_input = next(data for _, data, _ in self.model.calls if 'candidates' in data)
        self.assertTrue(any('密钥交换' in c['text'] for c in select_input['candidates']))

    def test_reverse_upload_preserves_demand_and_execution_source_order(self):
        self.conversation()
        with self.sessions.connect() as db:
            db.execute('DELETE FROM session_events')
        self.ingest(list(reversed(self.events)))
        result = self.assistant.answer('alice', self.device, '会员管理怎么做的？')
        candidate = result['candidates'][0]
        self.assertEqual(candidate['taskId'], self.origin['id'])
        self.assertEqual(candidate['turnIds'], [self.origin['id'], self.revision['id'], self.resume['id'], self.execution['id']])
        self.assertEqual(candidate['executions'][0]['resultRecordIds'], ['sessionlens:' + self.member_result['id']])

    def test_answer_contract_keeps_model_requests_distinct_from_messages(self):
        self.conversation()
        self.assistant.answer('alice', self.device, '会员任务与模型交互了多少次？')
        instructions = [system for system, data, _ in self.model.calls if 'tasks' in data and 'evidence' in data]
        self.assertEqual(len(instructions), 2)
        self.assertTrue(all('消息条数和模型请求次数不同' in text for text in instructions))
        self.assertTrue(all('缺少实际请求标识' in text for text in instructions))

    def test_seven_record_round_keeps_two_tool_pairs_reasoning_and_delivery(self):
        goal = self.record('请生成五秒SSH视频，展示密钥交换')
        reasoning = self.record('先找到生成工具，再提交动画提示词', kind='reasoning', role=None)
        search = self.record(kind='tool_call', role=None, name='ToolSearch', callId='find-video',
                             payload={'arguments': {'query': 'SSH VideoGen'}})
        search_result = self.record(kind='tool_result', role=None, callId='find-video',
                                    payload={'output': {'name': 'VideoGen', 'duration': [5, 10]}})
        generate = self.record(kind='tool_call', role=None, name='VideoGen', callId='generate-video',
                               payload={'arguments': {'prompt': 'SSH key exchange animation', 'duration': 5}})
        generate_result = self.record(kind='tool_result', role=None, callId='generate-video',
                                      payload={'output': {'url': 'https://media.example.test/ssh.mp4', 'duration': 5}})
        delivery = self.record('生成工具返回SSH动画文件链接，未独立播放核验', role='assistant')
        self.ingest()
        result = self.assistant.answer('alice', self.device, 'SSH短片怎么做出来的？')
        task = result['candidates'][0]
        self.assertEqual(task['taskId'], goal['id'])
        actions = {action['recordId']: action for action in task['executions']}
        self.assertEqual(actions['sessionlens:' + search['id']]['resultRecordIds'], ['sessionlens:' + search_result['id']])
        self.assertEqual(actions['sessionlens:' + generate['id']]['resultRecordIds'], ['sessionlens:' + generate_result['id']])
        ids = {record['recordId'] for record in result['evidence']}
        self.assertEqual(ids, {'sessionlens:' + record['id'] for record in [goal, reasoning, search, search_result, generate, generate_result, delivery]})

    def test_answer_model_receives_only_stages_in_selected_evidence_budget(self):
        self.record('设计会员管理页面')
        self.record('方案一密码，方案二邮箱登录', role='assistant')
        for ordinal in range(12):
            self.record('采用第二种，会员页面第' + str(ordinal) + '部分按照这个方案做')
            self.record('为会员页面补充第' + str(ordinal) + '部分', kind='reasoning', role=None)
            self.record(kind='tool_call', role=None, name='Write', callId='member-part-' + str(ordinal),
                        payload={'arguments': {'path': 'members-' + str(ordinal) + '.py', 'content': 'email login'}})
            self.record(kind='tool_result', role=None, callId='member-part-' + str(ordinal),
                        payload={'output': {'written': True, 'part': ordinal}})
            self.record('会员页面第' + str(ordinal) + '部分已写入', role='assistant')
        self.ingest()
        original_model = self.model
        def choose_origin(system, data, max_tokens=4000):
            if 'candidates' in data:
                item = next(candidate for candidate in data['candidates'] if candidate['text'].startswith('设计会员'))
                original_model.calls.append((system, data, max_tokens))
                return {'selected': [{'id': item['id'], 'status': 'supported', 'reason': '选择原始会员需求'}]}
            return original_model(system, data, max_tokens)
        self.assistant.model = choose_origin
        result = self.assistant.answer('alice', self.device, '会员管理怎么从需求做完的？')
        self.assertLessEqual(len(result['evidence']), 60)
        self.assertEqual(result['candidates'][0]['turnsCount'], 13)
        packets = [data for _, data, _ in self.model.calls if 'tasks' in data and 'evidence' in data]
        self.assertEqual(len(packets), 2)
        for packet in packets:
            supplied = {record['recordId'] for record in packet['evidence']}
            for task in packet['tasks']:
                self.assertTrue(all(stage['recordId'] in supplied for stage in task['stages']))


class CollectionEvidenceBudgetTests(unittest.TestCase):
    def dataset(self):
        records = []
        tasks = []
        for task_id in ('a', 'b'):
            task_records = []
            def add(ident, kind, **extra):
                record = {'recordId': ident, 'kind': kind, 'text': ident + ' synthetic text',
                          'seq': len(records) + 1, 'taskId': task_id, 'source': 'workbuddy',
                          'sessionId': 'session-' + task_id, **extra}
                task_records.append(record)
                records.append(record)
                return record
            user = add(task_id + '-user', 'user_message', role='user')
            add(task_id + '-reasoning', 'reasoning')
            for ordinal in range(30):
                call = add(task_id + '-call-' + str(ordinal), 'tool_call', tool='Bash', callId=task_id + '-c-' + str(ordinal))
                add(task_id + '-result-' + str(ordinal), 'tool_result', callId=task_id + '-c-' + str(ordinal),
                    callRecordId=call['recordId'])
            reply = add(task_id + '-reply', 'assistant_message', role='assistant')
            context = add(task_id + '-context', '模型输入中的当前用户消息', collector='applens')
            tasks.append({'taskId': task_id, 'requirements': [{'recordId': user['recordId']}],
                          'recordIds': [record['recordId'] for record in task_records],
                          'contexts': [{'recordId': context['recordId'], 'status': 'supported'}],
                          'stages': [{'recordId': record['recordId'], 'kind': record['kind']} for record in task_records],
                          'dialogues': [{'agentProposalRecordId': reply['recordId']}],
                          'executions': [{'recordId': task_id + '-call-' + str(ordinal),
                                          'resultRecordIds': [task_id + '-result-' + str(ordinal)]} for ordinal in range(30)]})
        return records, tasks

    def test_budget_balances_tasks_and_keeps_requirements_context_final_reply_and_anchors(self):
        from agentpair.collection_assistant import evidence_subset
        records, tasks = self.dataset()
        anchors = ['a-call-29', 'b-result-27']
        chosen, omitted = evidence_subset(records, tasks, anchors, limit=60)
        self.assertLessEqual(len(chosen), 60)
        self.assertEqual(omitted, len(records) - len(chosen))
        selected = {record['recordId'] for record in chosen}
        required = {'a-user', 'b-user', 'a-context', 'b-context', 'a-reply', 'b-reply', *anchors}
        self.assertTrue(required <= selected)
        self.assertIn('a-result-29', selected)
        self.assertIn('b-call-27', selected)
        counts = {task_id: sum(record['kind'] == 'tool_call' and record['taskId'] == task_id for record in chosen)
                  for task_id in ('a', 'b')}
        self.assertGreaterEqual(min(counts.values()), 2)
        self.assertLessEqual(abs(counts['a'] - counts['b']), 2)

    def test_budget_never_splits_available_tool_call_return_pairs(self):
        from agentpair.collection_assistant import evidence_subset
        records, tasks = self.dataset()
        chosen, _ = evidence_subset(records, tasks, ['a-call-29', 'b-result-27'], limit=60)
        selected = {record['recordId'] for record in chosen}
        for task_id in ('a', 'b'):
            for ordinal in range(30):
                self.assertEqual(task_id + '-call-' + str(ordinal) in selected,
                                 task_id + '-result-' + str(ordinal) in selected)


class CollectionModelValidationTests(unittest.TestCase):
    def link(self, turn, parent=None, relation='request'):
        return {'turnId': turn, 'parentTurnId': parent, 'relation': relation, 'status': 'supported',
                'reason': '需求证据一致', 'evidenceTurnIds': [turn] + ([parent] if parent else [])}

    def test_selection_answer_and_plan_reject_invented_references(self):
        with self.assertRaises(ValueError):
            validate_selection({'selected': [{'id': 'H999', 'status': 'supported', 'reason': '引用不存在'}]}, {'H1': {}})
        with self.assertRaises(ValueError):
            validate_answer({'answer': {'text': '猜测', 'basis': 'recorded', 'evidenceRefs': ['E999']}}, {'E001'})
        with self.assertRaises(ValueError):
            validate_plan({'terms': ['']})

    def test_lineage_rejects_future_turn_unknown_turn_and_cross_source(self):
        turns = [{'turnId': 'T1', 'source': 'workbuddy', 'sessionId': 's', 'deviceId': 'd'},
                 {'turnId': 'T2', 'source': 'workbuddy', 'sessionId': 's', 'deviceId': 'd'}]
        invalid = [ {'links': [self.link('T1', 'T2', 'revision'), self.link('T2')]},
                    {'links': [self.link('T1'), self.link('T2', 'T999', 'approval')]},
                    {'links': [self.link('T1')]} ]
        for result in invalid:
            with self.assertRaises(ValueError):
                validate_lineage(result, turns)
        crossed = [turns[0], dict(turns[1], source='codex')]
        with self.assertRaises(ValueError):
            validate_lineage({'links': [self.link('T1'), self.link('T2', 'T1', 'approval')]}, crossed)

    def test_context_validation_rejects_future_cross_source_and_repeated_input(self):
        contexts = {'C1': {'sessionId': 's', 'timestamp': 100, 'text': '执行吧'}}
        valid = {'contextLinks': [{'contextId': 'C1', 'turnId': 'U1', 'status': 'supported', 'reason': '当前要求一致'}]}
        base = {'source': 'workbuddy', 'sessionId': 's', 'timestamp': 100, 'text': '执行吧'}
        for turns in ({'U1': dict(base, timestamp=200)}, {'U1': dict(base, source='codex')},
                      {'U1': dict(base, sessionId='other')}, {'U1': base, 'U2': base}):
            with self.assertRaises(ValueError):
                validate_context_links(valid, contexts, turns)
        ambiguous = {'contextLinks': [{'contextId': 'C1', 'turnId': None, 'status': 'ambiguous', 'reason': '证据不足'}]}
        self.assertEqual(validate_context_links(ambiguous, contexts, {'U1': base})[0]['status'], 'ambiguous')

    def test_context_wrapper_uses_last_explicit_user_query(self):
        context = {'body': json.dumps({'messages': [{'role': 'user', 'content': '旧天气历史'},
                                                  {'role': 'user', 'content': '<system-reminder>旧背景</system-reminder><user_query>请生成五秒SSH视频</user_query>'}]}, ensure_ascii=False)}
        self.assertEqual(current_context_input(context), '请生成五秒SSH视频')

    def test_long_context_current_input_preserves_tail_goal(self):
        context = {'body': json.dumps({'messages': [{'role': 'user', 'content': '背景资料 ' * 1200 + '请生成五秒SSH视频，主题是密钥交换'}]}, ensure_ascii=False)}
        text = current_context_input(context)
        self.assertLessEqual(len(text), 1800)
        self.assertIn('密钥交换', text)


if __name__ == '__main__':
    unittest.main()
