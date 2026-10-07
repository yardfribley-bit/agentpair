"""Native shared-design acceptance: real local records, no model/network calls."""
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from desktop_main import Window
from sessionlens.chat_window import ChatWindow
from sessionlens.core import Collector
from sessionlens.desktop import Runtime,defaults
from sessionlens.i18n import set_language
from tests.test_answer_widgets import recorded_answer


class NativeDesktopAcceptance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        set_language('zh')
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        with patch('desktop_main.defaults',side_effect=lambda:{**defaults(),'ui':{'language':'zh'}}):self.collector=Window(self.root)
        self.chat=ChatWindow(self.collector);self.chat.show();self.app.processEvents()
        self.addCleanup(self.chat.close)
        self.addCleanup(set_language,'zh')

    def seed(self):
        records=[]
        for source,session,goal in [('workbuddy','weather','查上海天气'),('codex','archive','归档三月配件')]:
            log=self.root/(source+'-history.jsonl')
            records=[{'type':'message','sessionId':session,'role':'user','content':goal},
                     {'type':'function_call','sessionId':session,'name':'Bash','callId':session,'arguments':{'command':'curl https://example.invalid/'+session}},
                     {'type':'function_call_result','sessionId':session,'callId':session,'output':'测试返回，仅本机fixture'},
                     {'type':'message','sessionId':session,'role':'assistant','content':'已读取返回，未做独立核验。'}]
            if source=='codex':
                records=[{'type':'session_meta','payload':{'id':session}},
                         {'type':'response_item','payload':{'type':'message','role':'user','content':[{'type':'input_text','text':goal}]}},
                         {'type':'response_item','payload':{'type':'function_call','name':'Bash','call_id':session,'arguments':json.dumps({'command':'curl https://example.invalid/'+session})}},
                         {'type':'response_item','payload':{'type':'function_call_output','call_id':session,'output':'测试返回，仅本机fixture'}},
                         {'type':'response_item','payload':{'type':'message','role':'assistant','content':[{'type':'output_text','text':'已读取返回，未做独立核验。'}]}}]
            log.write_text(''.join(json.dumps(row,ensure_ascii=False)+'\n' for row in records),encoding='utf-8')
            reader=Collector(self.root/'collector.db');reader.scan(log,source=source);Runtime(self.root,defaults()).index(reader);reader.db.close()
        self.collector.store.advance(realtime=True);self.collector.store.repair_links(10)

    def test_native_sidebar_composer_fixed_status_and_docked_evidence(self):
        self.assertEqual(self.chat.sidebar.width(),164)
        self.assertGreaterEqual(self.chat.input.height(),88)
        self.assertLess(self.chat.composer.mapTo(self.chat,self.chat.composer.rect().topLeft()).y(),self.chat.results.mapTo(self.chat,self.chat.results.rect().topLeft()).y())
        self.chat.messages=[recorded_answer()];self.chat.render();self.app.processEvents()
        selected=self.chat.knowledge_view.index
        self.chat.knowledge_view._step_raw();self.app.processEvents()
        self.assertTrue(self.chat.proof_panel.isVisible())
        self.assertEqual(self.chat.proof_panel.parentWidget(),self.chat.split)
        self.assertNotEqual(self.chat.proof_panel.parentWidget(),self.chat.main_scroll.widget())
        self.assertIn('archive_items_0',self.chat.proof.toPlainText())
        self.assertEqual(self.chat.knowledge_view.index,selected)
        QTest.keyClick(self.chat,Qt.Key_Escape);self.assertFalse(self.chat.proof_panel.isVisible())
        self.assertGreater(self.chat.statusBar().mapTo(self.chat,self.chat.statusBar().rect().topLeft()).y(),self.chat.height()-50)

    def test_history_filters_real_local_tasks_and_review_keeps_identity(self):
        self.seed();self.chat.open_history();self.app.processEvents()
        self.assertEqual(self.chat.pages.currentWidget(),self.chat.history_page)
        page=self.chat.history_page;self.assertEqual(page.tasks.count(),2)
        page.source.setCurrentIndex(page.source.findData('workbuddy'));page.search.setText('上海天气');page.refresh()
        self.assertEqual(page.tasks.count(),1);self.assertIn('上海天气',page.detail.toPlainText());self.assertIn('curl',page.detail.toPlainText())
        with patch.object(self.chat,'send') as send:
            page.review.click();send.assert_called_once_with(selected_task=page.selected)
        self.assertEqual(self.chat.page,'assistant')
        page.search.setText('未出现的海洋考察');page.refresh()
        self.assertEqual(page.tasks.count(),0);self.assertFalse(page.review.isEnabled());self.assertNotIn('上海天气',page.detail.toPlainText())

    def test_collection_groups_record_types_and_changes_only_this_runtime(self):
        self.seed();self.collector.runtime=Runtime(self.root,self.collector.config)
        other=Runtime(self.root/'other',self.collector.config)
        self.chat.open_collection();self.app.processEvents();page=self.chat.collection_page
        self.assertEqual(self.chat.pages.currentWidget(),page)
        self.assertIn('工具调用',page.category_labels['工具调用'].text())
        self.assertIn('2',page.category_labels['工具调用'].text())
        self.assertEqual(page.receipt.text(),'尚未取得平台回执')
        page.pause.click();self.assertTrue(self.collector.runtime.paused.is_set());self.assertFalse(other.paused.is_set())
        self.assertEqual(page.pause.text(),'恢复采集');page.pause.click();self.assertFalse(self.collector.runtime.paused.is_set())

    def test_narrow_english_dock_does_not_replace_question_or_overflow_window(self):
        self.chat.messages=[recorded_answer()];self.chat.input.setPlainText('What was archived in March?');self.chat.render()
        self.chat.language_choice.setCurrentIndex(self.chat.language_choice.findData('en'));self.chat.resize(880,650)
        self.chat.show_raw('工具参数与原始返回','command: curl https://example.invalid/parts\nRaw fixture return');self.app.processEvents()
        self.assertEqual(self.chat.split.orientation(),Qt.Vertical)
        self.assertEqual(self.chat.input.toPlainText(),'What was archived in March?')
        self.assertEqual(self.chat.send_button.text(),'Ask')
        self.assertEqual(self.chat.centralWidget().width(),880)
        self.assertLessEqual(self.chat.proof_panel.width(),self.chat.width()-164)
        self.chat.new_chat();self.assertFalse(self.chat.proof_panel.isVisible());self.assertEqual(self.chat.input.toPlainText(),'')
        self.assertEqual(self.chat.knowledge_view.data['kind'],'home')

    def test_navigation_actions_cannot_replace_a_pending_question(self):
        self.seed();self.chat.open_history()
        self.chat.input.setPlainText('正在核对库存数量的依据')
        self.chat.pending='正在核对库存数量的依据';self.chat.busy=True
        self.chat.messages=[{'question':'先前的问题','projectChoices':[{'id':'project-b','name':'另一个项目'}]}]
        with patch.object(self.chat,'send') as send:
            self.chat.review_history_task('another-task')
            self.chat.followup_question('新的视频任务')
            self.chat.open_task_answer('another-task')
            self.chat.open_project_answer('project-b')
            send.assert_not_called()
        self.assertEqual(self.chat.input.toPlainText(),'正在核对库存数量的依据')
        self.assertEqual(self.chat.pending,'正在核对库存数量的依据')
        self.assertIn('当前问题正在处理',self.chat.statusBar().currentMessage())
        self.assertEqual(self.chat.page,'history')


class RuntimePauseAcceptance(unittest.TestCase):
    def test_upload_pause_blocks_http_until_explicit_resume(self):
        import io
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);log=root/'records.jsonl';log.write_text(json.dumps({'type':'message','sessionId':'s','role':'user','content':'上传暂停fixture'})+'\n')
            cfg={'sources':{'workbuddy':{'enabled':True,'roots':[]}},'endpoint':'https://example.invalid/events'}
            runtime=Runtime(root/'state',cfg,token='local-test-token');reader=Collector(runtime.path);reader.scan(log,source='workbuddy');reader.db.close();runtime.set_paused(True)
            def receipt(request,timeout):
                ids=[event['id'] for event in json.loads(request.data)['events']]
                runtime.stop.set()
                return io.BytesIO(json.dumps({'ids':ids}).encode())
            opener=Mock();opener.open.side_effect=receipt
            with patch('sessionlens.desktop.urllib.request.build_opener',return_value=opener):
                thread=threading.Thread(target=runtime.upload);thread.start()
                try:
                    time.sleep(.25);opener.open.assert_not_called()
                    runtime.set_paused(False);thread.join(2);self.assertFalse(thread.is_alive());opener.open.assert_called_once()
                    with sqlite_connection(runtime.path) as db:self.assertEqual(db.execute('SELECT count(*) FROM deliveries').fetchone()[0],1)
                finally:runtime.stop.set();thread.join(1)

    def test_paused_client_reads_no_logs_and_stop_unblocks_waiter(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);logs=root/'logs';logs.mkdir();log=logs/'source.jsonl'
            log.write_text(json.dumps({'type':'message','sessionId':'s','role':'user','content':'仅本机暂停测试'})+'\n')
            cfg={'sources':{'workbuddy':{'enabled':True,'roots':[str(logs)]}},'endpoint':''}
            runtime=Runtime(root/'state',cfg);runtime.set_paused(True)
            thread=threading.Thread(target=runtime.collect);thread.start()
            try:
                time.sleep(.3)
                with sqlite_connection(runtime.path) as db:self.assertEqual(db.execute('SELECT count(*) FROM events').fetchone()[0],0)
                runtime.set_paused(False)
                end=time.monotonic()+3
                while time.monotonic()<end:
                    with sqlite_connection(runtime.path) as db:count=db.execute('SELECT count(*) FROM events').fetchone()[0]
                    if count:break
                    time.sleep(.05)
                self.assertEqual(count,1)
                runtime.set_paused(True);runtime.stop.set();thread.join(1);self.assertFalse(thread.is_alive())
            finally:runtime.stop.set();thread.join(1)


def sqlite_connection(path):
    from sessionlens.database import connection
    return connection(path)
