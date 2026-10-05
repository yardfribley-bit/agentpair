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
            self.assertGreaterEqual(chat.input.height(),72)
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

    def test_new_question_hides_previous_answer_while_loading_and_after_failure(self):
        from sessionlens.chat_window import ChatWindow
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            window=Window(Path(tmp));chat=ChatWindow(window)
            old={'question':'生成 SSH 视频','error':'旧回答中的 SSH 视频内容'}
            chat.messages=[old];chat.input.setPlainText('查上海天气')
            with patch('sessionlens.chat_window.threading.Thread'):
                chat.send()
            self.assertIn('查上海天气',chat.answer.toPlainText())
            self.assertNotIn('SSH',chat.answer.toPlainText())
            self.assertEqual(chat.results.currentWidget(),chat.answer)
            chat.failed('临时查询失败')
            self.assertNotIn('SSH',chat.answer.toPlainText());chat.close()

    def test_reopening_wrong_cached_answer_requires_requery(self):
        from sessionlens.chat_window import ChatWindow
        with tempfile.TemporaryDirectory() as tmp:
            window=Window(Path(tmp));chat=ChatWindow(window)
            log=Path(tmp)/'task.jsonl'
            log.write_text(json.dumps({'type':'message','sessionId':'a','role':'user','content':'生成 SSH 视频'})+'\n'+json.dumps({'type':'message','sessionId':'b','role':'user','content':'上海天气'})+'\n')
            collector=Collector(Path(tmp)/'collector.db');collector.scan(log,source='workbuddy');window.store.advance(realtime=True)
            video=window.store.db.execute("SELECT id FROM tasks WHERE prompt='生成 SSH 视频'").fetchone()[0]
            result={'question':'查上海天气调用哪些工具','taskId':video,'presentation':{'taskId':video,'prompt':'生成 SSH 视频'}}
            chat.cache.execute('INSERT INTO chats VALUES(?,?,?,?)',('old','天气问题',json.dumps([result]),1));chat.cache.commit();chat.refresh_chats();chat.open_chat(0)
            self.assertIn('选错了任务',chat.answer.toPlainText());self.assertNotIn('SSH',chat.answer.toPlainText());self.assertEqual(chat.send_button.text(),'重新查询')
            self.assertEqual(chat.input.toPlainText(),result['question']);collector.db.close();chat.close()

    def test_task_clarification_offers_source_and_date_then_uses_choice(self):
        from sessionlens.chat_window import ChatWindow
        from PySide6.QtCore import QUrl
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            window=Window(Path(tmp));chat=ChatWindow(window)
            q='登录是怎么做的';chat.messages=[{'question':q,'selectionNeeded':True,'options':[{'taskId':'a','title':'实现登录','source':'workbuddy','updated':'2026-10-03T12:00:00'},{'taskId':'b','title':'实现登录','source':'codex','updated':'2026-10-04T12:00:00'}]}];chat.render()
            self.assertIn('WorkBuddy',chat.answer.toPlainText());self.assertIn('Codex',chat.answer.toPlainText());self.assertIn('2026-10-03',chat.answer.toPlainText())
            with patch.object(chat,'send') as send:
                chat.evidence(QUrl('choose:not-offered'));send.assert_not_called()
                chat.evidence(QUrl('choose:a'));send.assert_called_once_with(selected_task='a');self.assertEqual(chat.input.toPlainText(),q)
            chat.close()

    def test_unmatched_question_explains_missing_task_and_keeps_input(self):
        from sessionlens.chat_window import ChatWindow
        with tempfile.TemporaryDirectory() as tmp:
            window=Window(Path(tmp));chat=ChatWindow(window)
            q='回顾那次科考日报';chat.pending=q;chat.input.setPlainText(q)
            chat.received({'question':q,'selectionNeeded':True,'options':[],'selectionMessage':'没有找到对应的历史任务，请补充任务名。'})
            self.assertIn('没有找到对应',chat.answer.toPlainText());self.assertNotIn('未能完成回答',chat.status.text());self.assertEqual(chat.input.toPlainText(),q);chat.close()

    def test_failed_selection_breaks_followup_context(self):
        from sessionlens.chat_window import ChatWindow
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            window=Window(Path(tmp));chat=ChatWindow(window)
            chat.messages=[{'question':'旧任务','taskId':'old'},{'question':'查另一个任务','selectionNeeded':True,'options':[]}]
            chat.input.setPlainText('它用了什么工具？')
            class Immediate:
                def __init__(self,target,**kwargs):self.target=target
                def start(self):self.target()
            with patch('sessionlens.chat_window.ask',return_value={'question':'它用了什么工具？','selectionNeeded':True,'options':[]}) as ask,patch('sessionlens.chat_window.threading.Thread',Immediate):
                chat.send();self.assertEqual(ask.call_args.args[3],[])
            self.app.processEvents();chat.close()
