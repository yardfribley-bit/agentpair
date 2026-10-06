import os,tempfile,unittest,json,threading
from pathlib import Path
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtWidgets import QApplication,QPushButton,QDialog,QPlainTextEdit
from PySide6.QtCore import QTimer
from desktop_main import Window
from sessionlens.chat_window import ChatWindow
from sessionlens.core import Collector
from sessionlens.task_queries import local_task_query

class KnowledgeUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
    def test_native_requirement_rounds_and_idle_refresh_keep_widgets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);window=Window(root);chat=ChatWindow(window);c=Collector(root/'collector.db');log=root/'s.jsonl'
            rows=[{'type':'message','role':'user','sessionId':'s','content':t} for t in ('写一个登录页面','好的','行','干')]
            rows+=[{'type':'function_call','sessionId':'s','name':'Write','callId':'c','arguments':{'file_path':'/work/login/main.py'}},{'type':'function_call_result','sessionId':'s','callId':'c','output':'written'}]
            log.write_text(''.join(json.dumps(r)+'\n' for r in rows));c.scan(log,source='workbuddy');window.store.advance(realtime=True);window.store.repair_links(10)
            task=window.store.tasks()[0][0];q='这个任务用户发了多少轮，Agent和大模型交互了多少次？';chat.input.setPlainText(q)
            chat.messages=[local_task_query(root,q,selected=task)];chat.render();chat.show();self.app.processEvents()
            self.assertEqual(chat.results.currentWidget(),chat.knowledge_view);self.assertIn('用户发言 4 轮',chat.knowledge_view.plain_text());self.assertIn('暂无法确认',chat.knowledge_view.plain_text())
            widgets=[id(w) for w in chat.knowledge_view.findChildren(QPushButton)]
            for _ in range(12):chat.refresh_knowledge();chat.fit_result();chat.render();self.app.processEvents()
            self.assertEqual(widgets,[id(w) for w in chat.knowledge_view.findChildren(QPushButton)]);self.assertEqual(chat.input.toPlainText(),q)
            def inspect():
                dialog=next(w for w in self.app.topLevelWidgets() if isinstance(w,QDialog) and w.windowTitle()=='需求与确认历程')
                text=dialog.findChild(QPlainTextEdit).toPlainText();self.assertIn('好的',text);self.assertIn('行',text);self.assertIn('干',text);self.assertIn('写一个登录页面',text);dialog.accept()
            QTimer.singleShot(0,inspect);chat.knowledge_view.dialogues();c.db.close();chat.close()
    def test_hidden_history_window_does_not_render_on_background_timer(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            window=Window(Path(tmp));window.hide()
            with patch.object(window,'refresh') as refresh:window.periodic_refresh();refresh.assert_not_called()
            window.close()
