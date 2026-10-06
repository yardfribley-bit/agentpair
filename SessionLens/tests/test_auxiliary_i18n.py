import json
import os
from pathlib import Path
import tempfile
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication, QDialogButtonBox
from desktop_main import Settings, Window
from sessionlens.core import Collector
from sessionlens.i18n import language, localize_widgets, set_language, t
from sessionlens.project_inventory import ProjectInventory
from sessionlens.project_window import ProjectWindow


class AuxiliaryLanguageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.previous = language()
        set_language('en')

    def tearDown(self):
        set_language(self.previous)

    def test_settings_switches_product_labels_and_preserves_entered_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            window = Window(Path(tmp))
            dialog = Settings(window.config, window)
            dialog.endpoint.setText('https://example.invalid/原始路径')
            dialog.model_name.setText('保存')
            self.assertEqual(dialog.windowTitle(), t('采集与上报设置'))
            self.assertNotEqual(dialog.windowTitle(), '采集与上报设置')
            self.assertEqual(dialog.sources['codex'][0].text(), 'Collect Codex')
            set_language('zh')
            localize_widgets(dialog)
            self.assertEqual(dialog.windowTitle(), '采集与上报设置')
            self.assertEqual(dialog.sources['codex'][0].text(), '采集 Codex')
            self.assertEqual(dialog.endpoint.text(), 'https://example.invalid/原始路径')
            self.assertEqual(dialog.result_config()['model']['name'], '保存')
            self.assertEqual(dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Cancel).text(), '取消')
            dialog.close()
            window.close()

    def test_history_refresh_translates_chrome_without_rewriting_question_or_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            window = Window(root)
            log = root / 'raw.jsonl'
            raw_request = '保存原始中文要求\nprint("工具调用")'
            values = [{'type': 'message', 'sessionId': 'lang', 'role': 'user', 'content': raw_request},
                      {'type': 'function_call', 'sessionId': 'lang', 'name': 'Probe', 'callId': 'language-call',
                       'arguments': {'command': 'printf "原始中文"'}}]
            log.write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in values))
            collector = Collector(root / 'collector.db')
            collector.scan(log, source='workbuddy')
            collector.db.close()
            window.store.advance(realtime=True)
            window.question.setText('为什么还没有修好？')
            window.set_mode('history')
            before = window.store.db.execute('SELECT event FROM events ORDER BY rowid').fetchall()
            window.refresh_language()
            self.assertNotEqual(t('你的原始要求'), '你的原始要求')
            self.assertIn(t('你的原始要求'), window.middle.toPlainText())
            self.assertIn('保存原始中文要求', window.middle.toPlainText())
            self.assertIn('print("工具调用")', window.middle.toPlainText())
            self.assertEqual(window.history.text(), 'Task history')
            set_language('zh')
            window.refresh_language()
            self.assertIn('你的原始要求', window.middle.toPlainText())
            self.assertEqual(window.question.text(), '为什么还没有修好？')
            self.assertEqual(before, window.store.db.execute('SELECT event FROM events ORDER BY rowid').fetchall())
            window.close()

    def test_project_refresh_localizes_metadata_and_keeps_names_tasks_and_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            host = Window(root)
            raw_request = '保存原始中文内容'
            log = root / 'project.jsonl'
            values = [{'type': 'message', 'sessionId': 'project-language', 'role': 'user', 'content': raw_request},
                      {'type': 'function_call', 'sessionId': 'project-language', 'name': 'Write',
                       'arguments': {'file_path': '/repo/保存/src/check.py', 'content': 'print("保存")'}},
                      {'type': 'function_call', 'sessionId': 'project-language', 'name': 'Write',
                       'arguments': {'file_path': '/repo/保存/pyproject.toml', 'content': '[project]'}}]
            log.write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in values))
            collector = Collector(root / 'collector.db')
            collector.scan(log, source='workbuddy')
            collector.db.close()
            host.store.advance()
            host.store.advance(realtime=True)
            host.store.repair_links(10)
            inventory = ProjectInventory(root / 'project_inventory.db', filesystem=False)
            while inventory.sync(host.store.db, 1000):
                pass
            inventory.sync(host.store.db, 1000, live=True)
            inventory.close()
            project = ProjectWindow(root, host)
            self.assertEqual(project.title.text(), '保存')
            self.assertIn(raw_request, project.tasks.item(0).text())
            self.assertIn('/repo/保存', project.meta.text())
            self.assertNotEqual(project.filter.itemText(0), '全部开发目录')
            self.assertNotIn('个项目', project.counts.text())
            self.assertNotIn('个关联任务', project.meta.text())
            set_language('zh')
            project.refresh()
            self.assertEqual(project.filter.itemText(0), '全部开发目录')
            self.assertIn('个项目', project.counts.text())
            self.assertEqual(project.title.text(), '保存')
            self.assertIn(raw_request, project.tasks.item(0).text())
            project.close()
            host.close()


if __name__ == '__main__':
    unittest.main()
