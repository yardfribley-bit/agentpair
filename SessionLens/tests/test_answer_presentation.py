import copy
import json
from pathlib import Path
import tempfile
import unittest

from sessionlens.answer_presentation import present


class AnswerPresentationTests(unittest.TestCase):
    def task(self):
        return {
            'question': '为什么这样校验索引？', 'taskId': 'task-kappa',
            'understanding': {
                'overview': {'text': 'Agent 先读取清单，再检查索引。', 'basis': 'recorded', 'evidenceRefs': ['E02']},
                'steps': [{'title': '检查目录', 'text': '可能为了确认索引范围。', 'basis': 'inferred', 'evidenceRefs': ['E02']}],
                'gaps': ['未记录最终验收'], 'followups': ['检查工具参数'],
            },
            'packet': {'taskId': 'task-kappa', 'source': 'codex', 'fragments': [
                {'evidenceId': 'E01', 'eventId': 'user', 'kind': 'message', 'role': 'user', 'text': '校验 Kappa 索引', 'timestamp': '2026-01-03T01:00:00Z'},
                {'evidenceId': 'E02', 'eventId': 'reason', 'kind': 'reasoning', 'text': '先读取实际清单，核对索引范围。'},
                {'evidenceId': 'E03', 'eventId': 'call', 'kind': 'tool_call', 'text': '{"path":"/repo/kappa/manifest.json"}'},
                {'evidenceId': 'E04', 'eventId': 'returned', 'kind': 'tool_result', 'text': '{"status":"completed","count":3,"files":["/tmp/kappa-index.json"]}'},
            ]},
            'presentation': {
                'taskId': 'task-kappa', 'source': 'codex', 'prompt': '校验 Kappa 索引', 'updated': '2026-01-03',
                'requirements': [{'eventId': 'user', 'text': '校验 Kappa 索引', 'label': '原始需求', 'kind': 'request', 'reason': '独立需求', 'association': 'provisional'}],
                'reasoning': [{'id': 'reason', 'text': '先读取实际清单，核对索引范围。'}],
                'calls': [{'id': 'call', 'name': 'IndexProbe', 'callId': 'invocation-k',
                           'arguments': {'path': '/repo/kappa/manifest.json'},
                           'fields': [{'label': '路径', 'value': '/repo/kappa/manifest.json', 'key': 'path'}],
                           'decisionLink': {'reasoningEvent': 'reason', 'basis': 'source_parent_path'},
                           'returns': [{'id': 'returned', 'text': '{"status":"completed","count":3,"files":["/tmp/kappa-index.json"]}'}]}],
                'messageGraph': {'nodes': [{'eventId': 'call', 'kind': 'tool_call'}, {'eventId': 'returned', 'kind': 'tool_result'}],
                                 'edges': [{'from': 'call', 'to': 'returned', 'relation': 'call_result', 'basis': 'recorded'}]},
                'included': 4, 'total': 4,
            },
        }

    def test_reason_answer_and_recorded_reasoning_keep_distinct_bases(self):
        result = self.task()
        original = copy.deepcopy(result)
        view = present(result)
        self.assertEqual(view['kind'], 'task_reason')
        self.assertEqual(view['title'], 'Agent 先读取清单，再检查索引。')
        self.assertEqual(view['summary'], '')
        self.assertEqual(view['decisions'][0]['basis'], 'inferred')
        self.assertEqual(view['decisions'][0]['label'], '分析推断')
        self.assertEqual(view['decisions'][1]['basis'], 'source_parent_path')
        self.assertEqual(view['requirements'][0]['time'], '2026-01-03T01:00:00Z')
        self.assertEqual(view['steps'][0]['reasoning'], '先读取实际清单，核对索引范围。')
        self.assertEqual(result, original)
        self.assertIs(view['raw'], result)

    def test_actual_json_return_is_readable_and_raw_is_retained(self):
        result = self.task()
        view = present(result)
        step = view['steps'][0]
        self.assertIn('返回状态：completed', step['returnText'])
        self.assertIn('数量：3', step['returnText'])
        self.assertIn('/tmp/kappa-index.json', step['returnText'])
        self.assertEqual(step['returnIds'], ['E04'])
        self.assertEqual(step['call'], 'IndexProbe')
        self.assertEqual(step['inputFields'][0]['value'], '/repo/kappa/manifest.json')
        self.assertEqual(view['artifacts'][0]['path'], '/tmp/kappa-index.json')
        self.assertEqual(view['artifacts'][0]['source'], 'tool_return')
        self.assertEqual(view['artifacts'][0]['refs'], ['E04'])
        self.assertNotIn('已核验', json.dumps(view, ensure_ascii=False))

    def test_missing_return_and_reasoning_do_not_invent_success_or_model_flow(self):
        result = self.task()
        result['question'] = '这次执行过程是什么？'
        result['presentation']['calls'][0]['returns'] = []
        result['presentation']['calls'][0]['decisionLink'] = None
        result['presentation']['messageGraph']['edges'] = []
        view = present(result)
        step = view['steps'][0]
        self.assertEqual(view['kind'], 'task_process')
        self.assertIn('未记录关联返回', step['returnText'])
        self.assertEqual(step['reasoningBasis'], '未记录关联思路')
        self.assertEqual(step['returnRelations'], [])
        self.assertEqual(step['nextRefs'], [])
        self.assertEqual(step['modelLabel'], '模型身份未知')
        self.assertEqual(len(step['flowEdges']), 1)
        self.assertFalse(any('model' in (x['from'], x['to']) for x in step['flowEdges']))
        self.assertEqual(view['artifacts'], [])

    def test_return_does_not_imply_a_model_received_it(self):
        result = self.task()
        result['presentation']['replies'] = [{'id': 'reply', 'text': '检查结束。'}]
        view = present(result)
        self.assertIn('未确认', view['steps'][0]['nextText'])
        self.assertEqual(view['steps'][0]['nextRefs'], [])
        self.assertNotIn('送入', view['steps'][0]['nextText'])
        self.assertNotIn('调用模型', view['steps'][0]['route'])
        self.assertEqual(view['steps'][0]['modelLabel'], '模型身份未知')
        self.assertFalse(any('model' in (x['from'], x['to']) for x in view['steps'][0]['flowEdges']))

    def test_only_source_graph_can_connect_the_return_to_a_following_record(self):
        result = self.task()
        result['presentation']['reasoning'].append({'id': 'next-reason', 'text': '已得到清单，继续核对缺失项。'})
        result['presentation']['messageGraph']['nodes'].append({'eventId': 'next-reason', 'kind': 'reasoning'})
        result['presentation']['messageGraph']['edges'].append({'from': 'returned', 'to': 'next-reason', 'relation': 'source_parent', 'basis': 'recorded'})
        step = present(result)['steps'][0]
        self.assertIn('原始消息链关联后续记录', step['nextText'])
        self.assertEqual(step['nextRefs'], ['next-reason'])
        self.assertEqual(step['nextRelations'][0]['relation'], 'source_parent')
        self.assertEqual(step['flowEdges'][-1]['from'], 'agent')
        self.assertEqual(step['flowEdges'][-1]['to'], 'model')
        self.assertEqual(step['flowEdges'][-1]['basis'], 'source_parent')
        self.assertEqual(step['flowEdges'][-1]['label'], '源消息链关联后续模型记录')
        self.assertEqual(step['modelLabel'], '已记录模型消息')
        self.assertEqual(step['model'], '已记录模型消息')
        self.assertIn('不能证明实际网络 API 请求次数', step['route'])

    def test_source_graph_does_not_link_across_new_user_request(self):
        result = self.task()
        result['presentation']['reasoning'].append({'id': 'other-reason', 'text': '处理另一个要求。'})
        graph = result['presentation']['messageGraph']
        graph['nodes'] += [{'eventId': 'new-user', 'kind': 'message', 'role': 'user'}, {'eventId': 'other-reason', 'kind': 'reasoning'}]
        graph['edges'] += [{'from': 'returned', 'to': 'new-user', 'relation': 'source_parent', 'basis': 'recorded'},
                           {'from': 'new-user', 'to': 'other-reason', 'relation': 'source_parent', 'basis': 'recorded'}]
        self.assertEqual(present(result)['steps'][0]['nextRefs'], [])

    def test_order_candidate_and_inferred_summary_are_labelled(self):
        result = self.task()
        result['understanding']['overview']['basis'] = 'inferred'
        result['presentation']['calls'][0]['decisionLink']['basis'] = 'sequence_candidate'
        result['presentation']['messageGraph']['edges'].append({'from': 'reason', 'to': 'call', 'relation': 'source_parent', 'basis': 'recorded'})
        view = present(result)
        self.assertTrue(view['title'].startswith('分析推断：'))
        self.assertIn('待核对', view['steps'][0]['reasoningBasis'])
        self.assertEqual(view['steps'][0]['modelLabel'], '模型身份未知')
        self.assertFalse(any('model' in (x['from'], x['to']) for x in view['steps'][0]['flowEdges']))

    def test_recorded_reasoning_parent_path_confirms_message_flow_without_api_claim(self):
        result = self.task()
        result['presentation']['source'] = 'workbuddy'
        graph = result['presentation']['messageGraph']
        graph['nodes'] += [{'eventId': 'reason', 'kind': 'reasoning'},
                           {'eventId': 'assistant', 'kind': 'message', 'role': 'assistant'}]
        graph['edges'] += [{'from': 'reason', 'to': 'assistant', 'relation': 'source_parent', 'basis': 'recorded'},
                           {'from': 'assistant', 'to': 'call', 'relation': 'source_parent', 'basis': 'recorded'}]
        view = present(result)
        step = view['steps'][0]
        self.assertEqual(step['flowEdges'][0], {'from': 'model', 'to': 'agent',
                                               'label': '日志思路与工具调用关联',
                                               'basis': 'source_parent_path', 'refs': ['E02', 'E03']})
        self.assertEqual(len(step['reasoningRelations']), 2)
        self.assertEqual(step['modelLabel'], '已记录模型消息')
        self.assertEqual(step['model'], '已记录模型消息')
        self.assertEqual(step['agentLabel'], 'WorkBuddy')
        self.assertIn('仅表示日志消息关联', view['notice'])
        self.assertIn('不能证明实际网络 API 请求次数', view['notice'])
        self.assertNotIn('网络请求已完成', step['route'])

    def test_unrecorded_parent_basis_does_not_confirm_model_node(self):
        result = self.task()
        result['presentation']['messageGraph']['edges'].append({'from': 'reason', 'to': 'call', 'relation': 'source_parent', 'basis': 'inferred'})
        step = present(result)['steps'][0]
        self.assertEqual(step['agentLabel'], 'Codex')
        self.assertEqual(step['modelLabel'], '模型身份未知')
        self.assertFalse(any('model' in (x['from'], x['to']) for x in step['flowEdges']))

    def test_native_assistant_reply_source_parent_confirms_following_message(self):
        result = self.task()
        result['presentation']['replies'] = [{'id': 'native-reply', 'text': '清单校验结果见返回。'}]
        graph = result['presentation']['messageGraph']
        graph['nodes'].append({'eventId': 'native-reply', 'kind': 'assistant_message'})
        graph['edges'].append({'from': 'returned', 'to': 'native-reply', 'relation': 'source_parent', 'basis': 'recorded'})
        step = present(result)['steps'][0]
        self.assertEqual(step['flowEdges'][-1]['to'], 'model')
        self.assertEqual(step['flowEdges'][-1]['basis'], 'source_parent')
        self.assertEqual(step['nextRefs'], ['native-reply'])
        self.assertIn('清单校验结果', step['nextText'])

    def test_literal_multi_turn_counts_leave_model_api_unknown(self):
        result = self.task()
        result['question'] = '用户发了多少轮，模型调用几次？'
        result['interactions'] = {'userTurns': 5, 'approvalTurns': 2, 'executionTurns': 1,
                                  'agentReplyRecords': 18, 'toolCalls': 7,
                                  'modelCalls': None, 'modelMessageIdentifiers': 12,
                                  'modelCallsReason': '日志没有逐次模型请求记录。'}
        view = present(result)
        self.assertEqual(view['kind'], 'task_counts')
        self.assertIn('5 轮用户发言', view['title'])
        self.assertIn('2 轮方案确认', view['summary'])
        self.assertIn('1 轮开始执行', view['summary'])
        self.assertEqual(view['counts']['agentReplyRecords'], 18)
        self.assertIn('模型 API 调用次数无法确认', view['title'])
        self.assertIn('均计入用户发言', view['summary'])
        self.assertNotIn('12', view['summary'])
        self.assertNotIn('8 轮', view['summary'])

    def test_project_inventory_uses_actual_titles_and_paths_without_business_purpose(self):
        project = {'id': 'p-847', 'name': 'Rho', 'source': 'codex', 'state': 'identified',
                   'roots': ['/arbitrary/rho'], 'taskCount': 4, 'fileCount': 9,
                   'latestTasks': [{'taskId': 't-1', 'prompt': '修复序列化缓存', 'updated': '2026-02-02'},
                                   {'taskId': 't-2', 'prompt': '加上结构校验', 'updated': '2026-02-01'}]}
        view = present({'projectInventory': {'projects': [project], 'counts': {'identified': 1, 'confirmed': 0, 'candidate': 0}, 'complete': True}})
        self.assertEqual(view['kind'], 'projects')
        self.assertEqual(view['projects'][0]['name'], 'Rho')
        self.assertIn('修复序列化缓存', view['projects'][0]['description'])
        self.assertIn('2026-02-02', view['projects'][0]['latest'])
        project.pop('latestTasks')
        row = present({'projectInventory': {'projects': [project]}})['projects'][0]
        self.assertIn('/arbitrary/rho', row['description'])
        self.assertIn('未记录最近任务内容', row['latest'])
        self.assertNotIn('管理平台', row['description'])

    def test_project_detail_contains_linked_task_ids_and_recorded_files(self):
        project = {'id': 'p-2', 'name': 'Sigma', 'taskCount': 1, 'source': 'workbuddy',
                   'tasks': [{'taskId': 't-9', 'prompt': '修复校验错误', 'updated': '2026-02-09'}],
                   'components': [{'name': 'src/parser', 'fileCount': 1}],
                   'files': ['/work/sigma/src/parser/check.py'],
                   'evidence': [{'eventId': 'write-1', 'path': '/work/sigma/src/parser/check.py'}],
                   'coverage': '只包括已关联记录。'}
        view = present({'projectDetails': project})
        self.assertEqual(view['kind'], 'project')
        self.assertEqual(view['projectId'], 'p-2')
        self.assertEqual(view['projects'][0]['taskId'], 't-9')
        self.assertIn('src/parser', view['summary'])
        self.assertEqual(view['files'][0]['refs'], ['write-1'])

    def test_home_errors_and_choices_have_the_same_complete_contract(self):
        required = {'kind', 'title', 'summary', 'meta', 'status', 'notice', 'projects', 'requirements',
                    'decisions', 'steps', 'files', 'followups', 'artifacts', 'taskId', 'projectId', 'raw'}
        for result, kind in [(None, 'home'), ({'error': '读取失败'}, 'error'),
                             ({'projectChoices': [{'id': 'a', 'name': 'Alpha', 'source': 'codex', 'roots': ['/p/a']}]}, 'choices'),
                             ({'selectionNeeded': True, 'options': [{'taskId': 'b', 'title': '任务乙'}]}, 'choices')]:
            view = present(result)
            self.assertEqual(view['kind'], kind)
            self.assertTrue(required.issubset(view))

    def test_inventory_query_attaches_latest_original_task_metadata(self):
        from sessionlens.core import Collector
        from sessionlens.supervision import TaskStore
        from sessionlens.project_inventory import ProjectInventory
        from sessionlens.project_queries import local_project_query
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            records = [
                {'type': 'message', 'role': 'user', 'content': '修复 ArbitraryDelta 的边界解析', 'sessionId': 'arbitrary'},
                {'type': 'function_call', 'name': 'Write', 'sessionId': 'arbitrary', 'arguments': {'file_path': '/repo/ArbitraryDelta/src/bounds.py', 'content': 'x=1'}},
                {'type': 'function_call', 'name': 'Write', 'sessionId': 'arbitrary', 'arguments': {'file_path': '/repo/ArbitraryDelta/pyproject.toml', 'content': '[project]'}},
            ]
            log = root / 'records.jsonl'
            log.write_text(''.join(json.dumps(row) + '\n' for row in records),encoding='utf-8')
            collector = Collector(root / 'collector.db')
            collector.scan(log, source='workbuddy')
            collector.db.close()
            store = TaskStore(root / 'collector.db')
            store.advance()
            store.advance(realtime=True)
            store.repair_links(10)
            inventory = ProjectInventory(root / 'project_inventory.db', filesystem=False)
            try:
                while inventory.sync(store.db, 1000):
                    pass
                inventory.sync(store.db, 1000, live=True)
                result = local_project_query(root, 'WorkBuddy 一共开发了多少项目？')
                latest = result['projectInventory']['projects'][0]['latestTasks']
                self.assertEqual(latest[0]['prompt'], '修复 ArbitraryDelta 的边界解析')
                self.assertTrue(latest[0]['taskId'])
                self.assertEqual(set(latest[0]), {'taskId', 'prompt', 'updated'})
                self.assertIn('边界解析', present(result)['projects'][0]['description'])
            finally:
                inventory.close()
                store.close()

    def test_write_edit_paths_are_requests_without_success_assertion(self):
        result = self.task()
        result['question'] = '修改了哪些文件？'
        call = result['presentation']['calls'][0]
        call.update(name='WriteFile', arguments={'file_path': '/repo/unusual/new_module.py'}, returns=[])
        result['presentation']['messageGraph']['edges'] = []
        view = present(result)
        self.assertEqual(view['files'][0]['path'], '/repo/unusual/new_module.py')
        self.assertIn('请求涉及', view['files'][0]['description'])
        self.assertIn('需返回核验', view['files'][0]['description'])
        self.assertEqual(view['files'][0]['refs'], ['E03'])
        self.assertNotIn('已修改', view['files'][0]['description'])

    def test_answer_heading_uses_answer_and_retains_remaining_overview(self):
        result = self.task()
        result['understanding']['overview']['text'] = '先检查真实目录。然后按照工具返回继续校验。'
        view = present(result)
        self.assertEqual(view['title'], '先检查真实目录。')
        self.assertEqual(view['summary'], '然后按照工具返回继续校验。')
        self.assertNotEqual(view['title'], result['question'])


if __name__ == '__main__':
    unittest.main()
