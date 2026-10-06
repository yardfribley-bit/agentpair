"""Regressions for the conversation-first native assistant shell."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

try:
    from PySide6.QtCore import QEvent, Qt, QUrl
    from PySide6.QtGui import QInputMethodEvent
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QComboBox, QWidget
    from desktop_main import Window
    from sessionlens.chat_window import ChatWindow
except ImportError:
    QApplication = None

from sessionlens.core import Collector
from sessionlens.task_queries import local_task_query


@unittest.skipUnless(QApplication, 'desktop dependencies unavailable')
class ChatShellTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='sessionlens-shell-test-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.collector = Window(self.root)
        self.collector.config['ui'] = {'language': 'zh'}
        self.chat = ChatWindow(self.collector)
        self.collector.timer.stop()
        self.chat.knowledge_timer.stop()
        self.addCleanup(self.close_windows)

        rows = [
            {'type': 'message', 'role': 'user', 'sessionId': 'inventory',
             'content': '生成库存盘点摘要'},
            {'type': 'reasoning', 'sessionId': 'inventory',
             'content': '先读取库存数量，再写出盘点摘要。'},
            {'type': 'function_call', 'sessionId': 'inventory', 'name': 'Read',
             'callId': 'inventory-read',
             'arguments': {'file_path': '/work/orchard/inventory.csv'}},
            {'type': 'function_call_result', 'sessionId': 'inventory',
             'callId': 'inventory-read', 'output': 'item,count\nbracket,12'},
            {'type': 'function_call', 'sessionId': 'inventory', 'name': 'Write',
             'callId': 'inventory-write',
             'arguments': {'file_path': '/work/orchard/report.txt',
                           'content': '库存盘点摘要：支架 12 件。'}},
            {'type': 'function_call_result', 'sessionId': 'inventory',
             'callId': 'inventory-write', 'output': '摘要已写入'},
            {'type': 'message', 'role': 'assistant', 'sessionId': 'inventory',
             'content': '已整理库存盘点摘要。'},
            {'type': 'message', 'role': 'user', 'sessionId': 'maintenance',
             'content': '整理设备维保清单'},
            {'type': 'function_call', 'sessionId': 'maintenance', 'name': 'Read',
             'callId': 'maintenance-read',
             'arguments': {'file_path': '/work/orchard/maintenance.csv'}},
            {'type': 'function_call_result', 'sessionId': 'maintenance',
             'callId': 'maintenance-read', 'output': '设备检修记录'},
        ]
        log = self.root / 'arbitrary-tasks.jsonl'
        log.write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n'
                               for row in rows), encoding='utf-8')
        reader = Collector(self.root / 'collector.db')
        try:
            reader.scan(log, source='workbuddy')
            self.collector.store.advance(realtime=True)
            self.collector.store.repair_links(10)
        finally:
            reader.db.close()
        tasks = self.collector.store.db.execute(
            'SELECT id,prompt FROM task_groups').fetchall()
        self.task_ids = {prompt: identity for identity, prompt in tasks}
        self.question = '这次库存盘点任务用了哪些工具，怎么做的？'
        self.result = local_task_query(
            self.root, self.question, selected=self.task_ids['生成库存盘点摘要'])
        self.assertEqual(len(self.result['presentation']['calls']), 2)

    def close_windows(self):
        self.chat.close()
        self.chat.deleteLater()
        self.collector.deleteLater()
        self.app.processEvents()
        self.app.sendPostedEvents(None, QEvent.DeferredDelete)

    def show_native_answer(self):
        self.chat.input.setPlainText(self.question)
        self.chat.pending = self.question
        self.chat.received(self.result)
        self.chat.show()
        self.app.processEvents()
        self.assertIs(self.chat.results.currentWidget(), self.chat.knowledge_view)
        return self.chat.knowledge_view

    def test_large_composer_is_above_native_answer_without_scope_dropdowns(self):
        self.chat.show()
        self.app.processEvents()
        self.assertIs(self.chat.results.currentWidget(), self.chat.knowledge_view)
        self.assertGreaterEqual(self.chat.input.height(), 72)
        composer_top = self.chat.composer.mapTo(
            self.chat, self.chat.composer.rect().topLeft()).y()
        answer_top = self.chat.knowledge_view.mapTo(
            self.chat, self.chat.knowledge_view.rect().topLeft()).y()
        self.assertLess(composer_top + self.chat.composer.height(), answer_top)
        self.assertFalse(any(box.isVisible() and box is not getattr(self.chat, 'language_choice', None)
                             for box in self.chat.findChildren(QComboBox)))
        self.show_native_answer()
        self.assertFalse(self.chat.source.isVisible())
        self.assertFalse(self.chat.project_scope.isVisible())

    def test_answer_receipt_keeps_question_and_any_new_pending_draft(self):
        for draft in (self.question, '还需要核对摘要里是否有缺失物料？'):
            with self.subTest(draft=draft):
                self.chat.new_chat()
                self.chat.input.setPlainText(self.question)
                with patch('sessionlens.chat_window.threading.Thread') as worker:
                    self.chat.send(selected_task=self.result['taskId'])
                    worker.return_value.start.assert_called_once()
                    self.assertTrue(self.chat.busy)
                    self.assertEqual(self.chat.input.toPlainText(), self.question)
                self.chat.input.setPlainText(draft)
                self.chat.received(self.result)
                self.app.processEvents()
                self.assertFalse(self.chat.busy)
                self.assertEqual(self.chat.input.toPlainText(), draft)
                self.assertEqual(self.chat.messages[-1]['question'], self.question)
                self.assertIs(self.chat.results.currentWidget(), self.chat.knowledge_view)

    def test_draft_survives_replay_steps_evidence_folding_and_idle_refresh(self):
        view = self.show_native_answer()
        self.assertTrue(view.summary.isVisible())
        self.assertTrue(view.summary.text())
        self.assertFalse(view.proof_fold.toggle.isChecked())
        self.assertTrue(view.proof_fold.content.isHidden())
        self.assertFalse(view.step_details.toggle.isChecked())
        self.assertTrue(view.step_details.content.isHidden())
        view.play_button.click()
        self.assertTrue(view.timer.isActive())
        draft = '再解释一下摘要中数量合计的依据。'
        self.chat.input.setPlainText(draft)
        view.timer.timeout.emit()
        self.app.processEvents()
        self.assertEqual(view.index, 1)
        self.assertEqual(self.chat.input.toPlainText(), draft)
        view.proof_fold.toggle.click()
        view.step_details.toggle.click()
        self.app.processEvents()
        self.assertEqual(self.chat.input.toPlainText(), draft)
        ref = next(fragment['evidenceId']
                   for fragment in self.result['packet']['fragments']
                   if fragment['eventId'] == self.result['presentation']['calls'][1]['id'])
        self.chat.evidence(QUrl('proof:0:' + ref))
        self.assertTrue(self.chat.proof_panel.isVisible())
        self.assertIn('/work/orchard/report.txt', self.chat.proof.toPlainText())
        self.chat.proof_panel.hide()
        view.proof_fold.toggle.click()
        view.stop()
        for _ in range(5):
            self.chat.refresh_knowledge()
            self.chat.fit_result()
            self.chat.render()
            self.app.processEvents()
        self.assertEqual(self.chat.input.toPlainText(), draft)
        self.assertEqual(view.index, 1)
        self.assertFalse(view.timer.isActive())

    def test_selected_unrelated_question_clears_old_native_task_during_busy_and_failure(self):
        view = self.show_native_answer()
        view.play_button.click()
        self.assertTrue(view.timer.isActive())
        self.chat.input.setPlainText('设备维保清单为什么这样整理？')
        with patch('sessionlens.chat_window.threading.Thread') as worker:
            self.chat.send(selected_task=self.task_ids['整理设备维保清单'])
            worker.return_value.start.assert_called_once()
        self.app.processEvents()
        self.assertIs(self.chat.results.currentWidget(), self.chat.answer)
        self.assertFalse(view.isVisible())
        self.assertFalse(view.timer.isActive())
        self.assertFalse(self.chat.proof_panel.isVisible())
        self.assertIn('设备维保清单', self.chat.answer.toPlainText())
        self.assertNotIn('库存盘点摘要', self.chat.answer.toPlainText())
        self.chat.failed('维保查询暂不可用')
        self.app.processEvents()
        self.assertIs(self.chat.results.currentWidget(), self.chat.answer)
        self.assertIn('维保查询暂不可用', self.chat.answer.toPlainText())
        self.assertNotIn('库存盘点摘要', self.chat.answer.toPlainText())
        self.assertEqual(self.chat.input.toPlainText(), '设备维保清单为什么这样整理？')

    def test_idle_refresh_retains_native_widgets_and_selected_step(self):
        view = self.show_native_answer()
        view.select_step(1)
        view.proof_fold.toggle.click()
        self.app.processEvents()
        widgets = tuple(view.findChildren(QWidget))
        answer = view.plain_text()
        draft = '查看其他物料之前先保留这条草稿。'
        self.chat.input.setPlainText(draft)
        for _ in range(12):
            self.chat.refresh_knowledge()
            self.chat.fit_result()
            self.chat.render()
            self.app.processEvents()
        self.assertEqual(widgets, tuple(view.findChildren(QWidget)))
        self.assertEqual(answer, view.plain_text())
        self.assertEqual(view.index, 1)
        self.assertTrue(view.proof_fold.toggle.isChecked())
        self.assertEqual(self.chat.input.toPlainText(), draft)

    def test_enter_submits_shift_enter_adds_line_and_ime_enter_does_not_submit(self):
        self.chat.show()
        self.chat.input.setPlainText('核对库存摘要')
        self.chat.input.setFocus()
        self.app.processEvents()
        with patch.object(self.chat, 'send') as send:
            QTest.keyClick(self.chat.input, Qt.Key_Return)
            send.assert_called_once_with()
            self.assertEqual(self.chat.input.toPlainText(), '核对库存摘要')
            send.reset_mock()
            self.chat.input.moveCursor(self.chat.input.textCursor().MoveOperation.End)
            QTest.keyClick(self.chat.input, Qt.Key_Return, Qt.ShiftModifier)
            send.assert_not_called()
            self.assertEqual(self.chat.input.toPlainText(), '核对库存摘要\n')
            self.app.sendEvent(self.chat.input, QInputMethodEvent('确认', []))
            self.assertTrue(self.chat.composing)
            QTest.keyClick(self.chat.input, Qt.Key_Return)
            send.assert_not_called()
            self.app.sendEvent(self.chat.input, QInputMethodEvent('', []))
            self.assertFalse(self.chat.composing)
            QTest.keyClick(self.chat.input, Qt.Key_Enter)
            send.assert_called_once_with()


if __name__ == '__main__':
    unittest.main()
