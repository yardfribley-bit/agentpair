import copy
from contextlib import ExitStack
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QLabel,
                              QPushButton, QToolButton, QComboBox, QTabWidget,
                              QPlainTextEdit, QTextBrowser, QLineEdit)
from sessionlens.i18n import set_language, language, t, localize_widgets, answer_language
from sessionlens.core import Collector
from sessionlens.supervision import TaskStore
from sessionlens.project_inventory import ProjectInventory, inventory_question
from sessionlens.project_queries import local_project_query
from sessionlens.task_queries import local_task_query
from sessionlens.interactions import interaction_question
from sessionlens.relay_model import answer


class I18nTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.old_language = language()
        self.addCleanup(set_language, self.old_language)

    def test_language_detection_catalog_and_unknown_records(self):
        set_language('en')
        self.assertEqual(t('提问'), 'Ask')
        self.assertEqual(t('播放过程', 'zh'), '播放过程')
        self.assertEqual(t('查看 4 轮完整需求与确认历程'), 'View all 4 request and confirmation turns')
        self.assertEqual(t('最近任务时间：2026-10-06T09:14:00'), 'Latest task time: 2026-10-06T09:14:00')
        for original in ('orchard-kit', '/work/中文/summary.py', '用户说：请完成盘点。', '工具原始返回：已写入'):
            self.assertEqual(t(original), original)
        self.assertEqual(answer_language('Which projects has WorkBuddy worked on?'), 'en')
        self.assertEqual(answer_language('What tasks are in 盘点项目?'), 'en')
        self.assertEqual(answer_language('WorkBuddy 开发了哪些项目？'), 'zh')
        self.assertEqual(answer_language('Why did it change code?', {'responseLanguage': 'zh'}), 'zh')
        self.assertEqual(answer_language('为什么这样改？', {'responseLanguage': 'en-US'}), 'en')

    def test_widget_switch_preserves_sources_editor_bodies_and_raw_content(self):
        root = QWidget()
        self.addCleanup(root.close)
        root.setWindowTitle('SessionLens · 智能助手')
        layout = QVBoxLayout(root)
        heading = QLabel('任务历史')
        count = QLabel('查看 2 轮完整需求与确认历程')
        button = QPushButton('提问')
        fold = QToolButton()
        fold.setText('播放过程')
        combo = QComboBox()
        combo.addItem('全部 Agent', 'all')
        tabs = QTabWidget()
        tabs.addTab(QWidget(), '需求与反馈')
        edit = QPlainTextEdit('提问\n用户原话不变')
        edit.setPlaceholderText('问项目、一次任务，或当时为什么这样做…')
        line = QLineEdit('查询')
        browser = QTextBrowser()
        browser.setPlainText('播放过程\n原始返回：已写入')
        raw = QLabel('播放过程')
        raw.setProperty('i18nSkip', True)
        project = QLabel('orchard-kit 项目')
        for widget in (heading, count, button, fold, combo, tabs, edit, line, browser, raw, project):
            layout.addWidget(widget)
        for _ in range(3):
            localize_widgets(root, 'en')
            self.assertEqual(root.windowTitle(), 'SessionLens · Assistant')
            self.assertEqual(heading.text(), 'Task history')
            self.assertEqual(count.text(), 'View all 2 request and confirmation turns')
            self.assertEqual(button.text(), 'Ask')
            self.assertEqual(fold.text(), 'Play process')
            self.assertEqual(combo.itemText(0), 'All agents')
            self.assertEqual(combo.currentData(), 'all')
            self.assertEqual(tabs.tabText(0), 'Requests and feedback')
            self.assertTrue(edit.placeholderText().startswith('Ask about a project'))
            self.assertEqual(edit.toPlainText(), '提问\n用户原话不变')
            self.assertEqual(line.text(), '查询')
            self.assertEqual(browser.toPlainText(), '播放过程\n原始返回：已写入')
            self.assertEqual(raw.text(), '播放过程')
            self.assertEqual(project.text(), 'orchard-kit 项目')
            localize_widgets(root, 'zh')
            self.assertEqual(heading.text(), '任务历史')
            self.assertEqual(count.text(), '查看 2 轮完整需求与确认历程')
        count.setText('查看 5 轮完整需求与确认历程')
        localize_widgets(root, 'en')
        self.assertEqual(count.text(), 'View all 5 request and confirmation turns')
        localize_widgets(root, 'zh')
        self.assertEqual(count.text(), '查看 5 轮完整需求与确认历程')

    def test_english_inventory_project_followup_and_selected_task_counts_keep_originals(self):
        with tempfile.TemporaryDirectory() as tmp, ExitStack() as resources:
            root = Path(tmp)
            collector = Collector(root / 'collector.db')
            store = TaskStore(root / 'collector.db')
            inventory = ProjectInventory(root / 'project_inventory.db', filesystem=False)
            resources.callback(inventory.close)
            resources.callback(store.close)
            resources.callback(collector.db.close)
            original = '完善 orchard-kit 的库存清单'
            rows = [
                {'type': 'message', 'role': 'user', 'content': original},
                {'type': 'message', 'role': 'user', 'content': '好的'},
                {'type': 'function_call', 'name': 'Write', 'callId': 'c1',
                 'arguments': {'file_path': '/work/orchard-kit/src/inventory.py', 'content': 'print("原文")'}},
                {'type': 'function_call_result', 'callId': 'c1', 'output': '已写入库存脚本'},
                {'type': 'function_call', 'name': 'Write', 'arguments': {'file_path': '/work/orchard-kit/pyproject.toml', 'content': '[project]'}},
            ]
            log = root / 'records.jsonl'
            log.write_text(''.join(json.dumps({'sessionId': 's', **row}, ensure_ascii=False) + '\n' for row in rows))
            collector.scan(log, source='workbuddy')
            store.advance()
            store.advance(realtime=True)
            while store.repair_links(10):
                pass
            while inventory.sync(store.db, 1000):
                pass
            inventory.sync(store.db, 1000, live=True)
            task = store.tasks()[0][0]
            with patch('sessionlens.relay_model.call') as model:
                self.assertTrue(inventory_question('Which projects has WorkBuddy worked on?'))
                projects = local_project_query(root, 'Which projects has WorkBuddy worked on?')
                self.assertEqual(projects['projectInventory']['counts']['identified'], 1)
                detail = local_project_query(root, 'What tasks are in orchard-kit?')
                self.assertEqual(detail['projectDetails']['name'], 'orchard-kit')
                self.assertEqual(detail['projectDetails']['tasks'][0]['prompt'], original)
                followup = local_project_query(root, 'What tasks are in it?', previous=detail)
                self.assertEqual(followup['projectDetails']['id'], detail['projectDetails']['id'])
                question = 'How many user turns and model calls were there?'
                self.assertTrue(interaction_question(question))
                counts = local_task_query(root, question, selected=task)
                self.assertEqual(counts['queryKind'], 'task_interactions')
                self.assertEqual(counts['interactions']['userTurns'], 2)
                self.assertIn('2 linked user turns', counts['understanding']['overview']['text'])
                self.assertIn('cannot be confirmed', counts['understanding']['overview']['text'])
                self.assertEqual(counts['presentation']['prompt'], original)
                self.assertEqual(counts['presentation']['calls'][0]['returns'][0]['text'], '已写入库存脚本')
                self.assertEqual(local_task_query(root, question, previous=counts)['taskId'], task)
                steps = local_task_query(root, 'Which tools were used and how was this task done?', selected=task)
                self.assertEqual(steps['queryKind'], 'task_detail')
                self.assertIn(original, steps['understanding']['overview']['text'])
                self.assertIsNone(local_task_query(root, 'Why did it change code?', selected=task))
                model.assert_not_called()

    def test_model_cache_isolated_by_response_language(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = {'url': 'https://example.invalid/v1', 'name': 'test-model'}
            packet = {'fragments': [{'evidenceId': 'E001', 'text': '原始返回：写入 /work/摘要.txt'}]}
            original = copy.deepcopy(packet)
            def value(text):
                return {'overview': {'text': text, 'basis': 'recorded', 'evidenceRefs': ['E001']}, 'steps': [], 'gaps': [], 'toolExplanations': []}
            with patch('sessionlens.relay_model.call', side_effect=[value('The file was written.'), {'issues': [], 'corrected': None}, value('文件已写入。'), {'issues': [], 'corrected': None}]) as model:
                en = answer(tmp, {**config, 'responseLanguage': 'en'}, '文件写入了吗？', packet, [])
                zh = answer(tmp, {**config, 'responseLanguage': 'zh'}, '文件写入了吗？', packet, [])
                self.assertEqual(en['overview']['text'], 'The file was written.')
                self.assertEqual(zh['overview']['text'], '文件已写入。')
                self.assertEqual(answer(tmp, {**config, 'responseLanguage': 'en'}, '文件写入了吗？', packet, []), en)
                self.assertEqual(answer(tmp, {**config, 'responseLanguage': 'zh'}, '文件写入了吗？', packet, []), zh)
                self.assertEqual(model.call_count, 4)
            self.assertEqual(packet, original)

    def test_model_answer_repair_review_and_review_repair_use_english(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = {'url': 'https://example.invalid/v1', 'name': 'test-model'}
            packet = {'fragments': [{'evidenceId': 'E001', 'text': '原始参数：/work/摘要.txt'}]}
            valid = {'overview': {'text': 'A write was recorded.', 'basis': 'recorded', 'evidenceRefs': ['E001']}, 'steps': [], 'gaps': [], 'toolExplanations': []}
            responses = [{'overview': 'invalid', 'steps': []}, copy.deepcopy(valid), {'issues': ['Repair needed.'], 'corrected': {}}, copy.deepcopy(valid)]
            with patch('sessionlens.relay_model.call', side_effect=responses) as model:
                answer(tmp, config, 'How was the file written?', packet, [])
                self.assertEqual(model.call_count, 4)
                for invocation in model.call_args_list:
                    prompt = invocation.args[1]
                    self.assertIn('in English', prompt)
                    self.assertNotIn('普通中文', prompt)
                    self.assertNotIn('英文提示词用中文概括', prompt)
                    self.assertEqual(invocation.args[2]['records'], packet)


if __name__ == '__main__':
    unittest.main()
