import json
import os
from pathlib import Path
import tempfile
import unittest
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
try:
    from PySide6.QtWidgets import QApplication
    from desktop_main import Window
except ImportError:
    QApplication=None
from sessionlens.core import Collector

@unittest.skipUnless(QApplication,'desktop dependencies unavailable')
class LiveUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
    def test_monitor_switch_and_follow_new_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);log=root/'task.jsonl';window=Window(root)
            log.write_text(json.dumps({'type':'message','sessionId':'s','role':'user','content':'上海天气'})+'\n');c=Collector(root/'collector.db');c.scan(log,source='workbuddy');window.store.advance(realtime=True);window.reload()
            self.assertTrue(window.live.isEnabled());self.assertTrue(window.live.isChecked());self.assertIn('上海天气',window.middle.toPlainText())
            window.history.click();window.search.setText('查上海天气');window.reload();self.assertEqual(window.task_list.count(),1)
            window.live.click();self.assertEqual(window.mode,'live')
            with log.open('a') as f:f.write(json.dumps({'type':'message','sessionId':'s','role':'user','content':'新的任务'})+'\n')
            c.scan(log,source='workbuddy');window.store.advance(realtime=True);window.refresh();self.assertIn('新的任务',window.middle.toPlainText());self.assertTrue(window.selected_event)
            window.close();c.db.close()

    def test_chat_preserves_collection_window_and_restores_conversation(self):
        from sessionlens.chat_window import ChatWindow
        with tempfile.TemporaryDirectory() as tmp:
            window=Window(Path(tmp));chat=ChatWindow(window)
            window.show();window.close()
            self.assertFalse(window.isVisible())
            self.assertIsNotNone(window.store.db.execute('SELECT 1').fetchone())
            chat.pending='为什么重试';chat.failed('记录暂不可用')
            chat.new_chat();chat.open_chat(0)
            self.assertIn('为什么重试',chat.answer.toPlainText())
            chat.close()
    def test_large_composer_above_answers_and_evidence_close(self):
        from sessionlens.chat_window import ChatWindow
        from PySide6.QtCore import QUrl
        with tempfile.TemporaryDirectory() as tmp:
            window=Window(Path(tmp));chat=ChatWindow(window);chat.show();self.app.processEvents()
            self.assertLess(chat.composer.mapTo(chat,chat.composer.rect().topLeft()).y(),chat.answer.mapTo(chat,chat.answer.rect().topLeft()).y())
            self.assertGreaterEqual(chat.input.height(),118)
            chat.evidence(QUrl('sample:weather'));self.assertIn('上海天气',chat.input.toPlainText())
            chat.input.setPlainText('多行问题\n具体参数');self.assertIn('\n',chat.input.toPlainText())
            self.assertIsNone(chat.source.currentData());chat.source.setCurrentIndex(1);self.assertEqual(chat.source.currentData(),'workbuddy')
            chat.grab().save('/private/tmp/sessionlens-input-prototype-implemented.png');chat.close()
    def test_question_remains_in_prototype_input_after_send_and_restore(self):
        from sessionlens.chat_window import ChatWindow
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            window=Window(Path(tmp));chat=ChatWindow(window)
            q='那次 SSH 动画是怎么生成的？';chat.input.setPlainText(q)
            with patch('sessionlens.chat_window.threading.Thread') as worker:
                chat.send();self.assertEqual(chat.input.toPlainText(),q);worker.return_value.start.assert_called_once()
            chat.failed('测试失败');self.assertEqual(chat.input.toPlainText(),q)
            chat.new_chat();self.assertEqual(chat.input.toPlainText(),'')
            chat.open_chat(0);self.assertEqual(chat.input.toPlainText(),q);chat.close()
