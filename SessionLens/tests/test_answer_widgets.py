"""Native answer cards keep source boundaries and transient UI state."""
import copy
import json
import os
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from sessionlens.answer_widgets import ClickRow, Fold
from sessionlens.knowledge_view import KnowledgeView
from sessionlens.i18n import language, set_language


def recorded_answer(question='这次物料归档怎么执行，用了什么工具？', count=6):
    calls, nodes, edges, fragments = [], [], [], []
    for index in range(count):
        call_id, return_id = f'call-{index}', f'return-{index}'
        calls.append({'id': call_id, 'name': 'archive_items_' + str(index),
                      'callId': 'binding-' + str(index),
                      'arguments': {'collection': '铝合金配件', 'limit': index + 2},
                      'fields': [{'label': '集合', 'value': '铝合金配件'},
                                 {'label': '读取数量', 'value': str(index + 2)}],
                      'returns': [{'id': return_id, 'text': json.dumps(
                          {'count': index + 2, 'status': 'returned'}, ensure_ascii=False)}]})
        nodes.extend([{'eventId': call_id, 'kind': 'tool_call', 'role': 'assistant'},
                      {'eventId': return_id, 'kind': 'tool_result', 'role': 'tool'}])
        edges.append({'from': call_id, 'to': return_id, 'relation': 'call_result', 'basis': 'call_id'})
        fragments.extend([{'eventId': call_id, 'evidenceId': 'C' + str(index)},
                          {'eventId': return_id, 'evidenceId': 'R' + str(index)}])
    return {'question': question, 'taskId': 'parts-archive',
            'presentation': {'source': 'warehouse-agent', 'taskId': 'parts-archive',
                             'prompt': '归档铝合金配件的盘点记录', 'updated': '2026-03-20',
                             'requirements': [{'eventId': 'request-a', 'label': '提出需求',
                                               'text': '归档铝合金配件的盘点记录', 'reason': '用户原文'},
                                              {'eventId': 'request-b', 'label': '确认',
                                               'text': '只归档三月批次', 'reason': '用户补充'}],
                             'calls': calls, 'reasoning': [],
                             'messageGraph': {'nodes': nodes, 'edges': edges},
                             'projectContext': {'projects': [{'name': 'warehouse',
                                                              'repository': 'https://example.invalid/parts.git'}]}},
            'packet': {'fragments': fragments},
            'understanding': {'overview': {'text': '已读取多批配件。归档范围依据三月补充要求。',
                                           'basis': 'recorded'}, 'steps': [], 'followups': []}}


class NativeAnswerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.previous_language = language()
        set_language('zh')
        self.view = KnowledgeView()
        self.view.resize(800, 900)
        self.view.show()

    def tearDown(self):
        self.view.close()
        self.view.deleteLater()
        self.app.processEvents()
        self.app.sendPostedEvents(None, QEvent.DeferredDelete)
        set_language(self.previous_language)

    def test_steps_are_bounded_and_keep_widgets_and_folds_during_replay(self):
        result = recorded_answer()
        self.view.load(result)
        self.app.processEvents()
        self.assertEqual(len(self.view.step_buttons), 4)
        self.assertTrue(all(not fold.isExpanded() for fold in self.view.findChildren(Fold)))
        self.view.proof_fold.toggle.click()
        self.view.step_details.toggle.click()
        identities = [id(widget) for widget in self.view.findChildren(QWidget)]
        self.view.select_step(4)
        self.assertEqual(self.view.index, 4)
        self.assertIn(4, [button.property('stepIndex') for button in self.view.step_buttons])
        self.assertTrue(self.view.proof_fold.isExpanded())
        self.assertTrue(self.view.step_details.isExpanded())
        self.assertEqual(identities, [id(widget) for widget in self.view.findChildren(QWidget)])
        self.view.load(copy.deepcopy(result))
        self.assertEqual(self.view.index, 4)
        self.assertEqual(identities, [id(widget) for widget in self.view.findChildren(QWidget)])
        self.view.speed.setCurrentIndex(1)
        self.view.toggle_play()
        self.assertEqual(self.view.timer.interval(), 1750)
        for _ in range(10):
            self.view.timer.timeout.emit()
        self.assertEqual(self.view.index, 5)
        self.assertFalse(self.view.timer.isActive())
        self.assertEqual(identities, [id(widget) for widget in self.view.findChildren(QWidget)])
        self.view.next_step()
        self.assertEqual(self.view.index, 5)

    def test_missing_graph_does_not_animate_return_or_model_traffic(self):
        result = recorded_answer(count=1)
        result['presentation']['messageGraph'] = {'nodes': [], 'edges': []}
        self.view.load(result)
        self.assertEqual(self.view.flow.names['model'], '模型身份未知')
        self.assertEqual([(edge['from'], edge['to']) for edge in self.view.flow.edges], [('agent', 'tool')])
        self.assertIn('返回关系未附图证据', self.view.route_label.text())
        self.assertIn('未确认', self.view.next_label.text())
        self.view.toggle_play()
        self.assertTrue(self.view.timer.isActive())
        self.view.hide()
        self.assertFalse(self.view.timer.isActive())
        self.view.show()
        self.view.toggle_play()
        self.view.load({'home': True})
        self.assertFalse(self.view.timer.isActive())

    def test_why_always_has_three_grounded_columns_and_collapsed_sources(self):
        self.view.load(recorded_answer('为什么这样修改归档程序？', count=1))
        self.app.processEvents()
        text = self.view.plain_text()
        for value in ('用户想解决什么', '如何考虑这次修改', '执行涉及哪些部分',
                      '修改原因尚未确认', '修改文件尚未确认', '铝合金配件'):
            self.assertIn(value, text)
        self.assertNotIn('mem_sec', text)
        self.assertNotIn('SSH', text)
        self.assertTrue(self.view.proof_fold.content.isHidden())
        self.assertIn('parts.git', self.view.project_label.text())
        self.assertIn('2 轮', self.view.requirement_button.text())

    def test_project_grid_is_two_columns_and_whole_row_is_clickable(self):
        result = {'projectInventory': {'source': 'warehouse-agent', 'complete': True,
                  'projects': [{'id': 'project-' + str(index), 'name': '仓库项目 ' + str(index),
                                'state': 'identified', 'roots': ['/work/warehouse-' + str(index)],
                                'latestTask': {'prompt': '核对配件批次 ' + str(index), 'updated': '2026-03-20'}}
                               for index in range(9)]}}
        self.view.load(result)
        self.app.processEvents()
        visible = [row for row in self.view.findChildren(ClickRow) if row.isVisibleTo(self.view)]
        self.assertEqual(len(visible), 6)
        self.assertEqual(visible[0].y(), visible[1].y())
        self.assertLess(visible[0].x(), visible[1].x())
        for row in visible:
            self.assertGreaterEqual(row.height(), row.heightForWidth(row.width()))
            for text in (row.title_label, row.detail_label, row.meta_label):
                self.assertGreater(text.height(), 10)
                self.assertLess(text.geometry().bottom(), row.height())
        selected = []
        self.view.projectRequested.connect(selected.append)
        # Click below the title, in the descriptive part of the row.
        QTest.mouseClick(visible[0], Qt.LeftButton, pos=QPoint(60, visible[0].height() - 12))
        self.assertEqual(selected, ['project-0'])
        self.assertIn('核对配件批次 0', visible[0].detail_label.text())
        self.assertEqual(self.view.status.styleSheet().find('#93621c'), -1)

    def test_raw_dialog_uses_actual_parameters_and_returns_on_demand(self):
        self.view.load(recorded_answer(count=1))
        captured = []
        self.view.raw = lambda title, text: captured.append((title, text))
        self.view._step_raw()
        self.assertIn('铝合金配件', captured[0][1])
        self.assertIn('binding-0', captured[0][1])
        self.assertIn('"count": 2', captured[0][1])
        self.assertIn('call_result', captured[0][1])
        self.view.dialogues()
        self.assertEqual(captured[1][0], '需求与确认历程')
        self.assertIn('只归档三月批次', captured[1][1])

    def test_english_ui_and_language_roundtrip_preserve_literal_history(self):
        result = recorded_answer(count=1)
        result['presentation']['requirements'][0]['text'] = '模型身份未知'
        result['presentation']['calls'][0]['returns'][0]['text'] = '模型身份未知'
        self.view.load(result)
        set_language('en')
        self.view.load(copy.deepcopy(result))
        self.app.processEvents()
        self.assertEqual(self.view.origin.text(), 'Task history')
        self.assertEqual(self.view.play_button.text(), 'Play process')
        self.assertTrue(self.view.proof_fold.toggle.text().startswith('View evidence'))
        self.assertEqual(self.view.return_label.text(), '模型身份未知')
        self.view.proof_fold.toggle.click()
        self.app.processEvents()
        self.assertIn('模型身份未知', self.view.plain_text())
        set_language('zh')
        self.view.load(copy.deepcopy(result))
        self.assertEqual(self.view.origin.text(), '任务历史')
        self.assertEqual(self.view.play_button.text(), '播放过程')
