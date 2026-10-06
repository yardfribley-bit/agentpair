"""Language switching is a view preference, never a history rewrite."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget
from sessionlens.chat_window import ChatWindow
from sessionlens.i18n import set_language


class CollectorWindow(QWidget):
    def __init__(self, root, config):
        super().__init__()
        self.root, self.config, self.runtime = root, config, None


class LanguageShellTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.config = {'ui': {'language': 'zh'}, 'model': {'name': 'configured-model'},
                       'sources': {'codex': {'enabled': True}}, 'endpoint': ''}
        self.collector = CollectorWindow(self.root, self.config)
        self.chat = ChatWindow(self.collector)
        self.chat.knowledge_timer.stop()

    def tearDown(self):
        self.chat.close()
        self.chat.deleteLater()
        self.collector.deleteLater()
        self.app.processEvents()
        self.app.sendPostedEvents(None, QEvent.DeferredDelete)
        self.tmp.cleanup()
        set_language('zh')

    def test_switch_retains_draft_and_persists_only_language_preference(self):
        draft='Why did WorkBuddy change this? 尚未提交'
        self.chat.input.setPlainText(draft)
        self.chat.language_choice.setCurrentIndex(self.chat.language_choice.findData('en'))
        self.assertEqual(self.chat.input.toPlainText(), draft)
        self.assertEqual(self.chat.send_button.text(), 'Ask')
        self.assertIn('Ask about a project', self.chat.input.placeholderText())
        saved=json.loads((self.root/'settings.json').read_text())
        self.assertEqual(saved['ui']['language'], 'en')
        self.assertEqual(saved['model'], self.config['model'])
        self.assertEqual(saved['sources'], self.config['sources'])
        self.chat.language_choice.setCurrentIndex(self.chat.language_choice.findData('zh'))
        self.assertEqual(self.chat.send_button.text(), '提问')
        self.assertEqual(self.chat.input.toPlainText(), draft)

    def test_switch_preserves_current_step_and_open_raw_evidence(self):
        calls=[{'id':str(i),'name':'read_catalog','callId':str(i),
                'arguments':{'path':'/work/项目/catalog.csv'},
                'fields':[{'label':'路径','value':'/work/项目/catalog.csv'}],
                'returns':[]} for i in range(3)]
        result={'question':'How was the catalog read?', 'taskId':'catalog',
                'presentation':{'source':'workbuddy','taskId':'catalog',
                'prompt':'读取库存文件','calls':calls,'requirements':[],
                'reasoning':[],'messageGraph':{'nodes':[],'edges':[]}},
                'understanding':{'overview':{'text':'Original recorded answer',
                'basis':'recorded'},'steps':[]},'packet':{'fragments':[]}}
        self.chat.messages=[result];self.chat.input.setPlainText('A new draft');self.chat.render()
        view=self.chat.knowledge_view;view.select_step(1);view.step_details.toggle.click()
        before=json.dumps(result,ensure_ascii=False)
        self.chat.language_choice.setCurrentIndex(self.chat.language_choice.findData('en'))
        self.assertEqual(view.index, 1)
        self.assertTrue(view.step_details.isExpanded())
        self.assertEqual(self.chat.input.toPlainText(), 'A new draft')
        self.assertEqual(json.dumps(result,ensure_ascii=False), before)
        self.assertIn('/work/项目/catalog.csv', view.plain_text())

    def test_model_worker_receives_snapshotted_response_language(self):
        captured=[]
        def answer(root,config,question,*args,**kwargs):
            captured.append(config.copy())
            return {'question':question,'error':'test answer'}
        self.chat.language_choice.setCurrentIndex(self.chat.language_choice.findData('en'))
        self.chat.input.setPlainText('Why was the inventory code changed?')
        with patch('sessionlens.task_queries.local_task_query',return_value=None), \
             patch('sessionlens.project_queries.local_project_query',return_value=None), \
             patch('sessionlens.chat_window.ask',side_effect=answer):
            self.chat.send()
            for _ in range(100):
                if not self.chat.busy:break
                QTest.qWait(10)
        self.assertEqual(captured[0]['responseLanguage'], 'en')
        self.assertEqual(captured[0]['name'], 'configured-model')
        self.assertEqual(self.chat.input.toPlainText(), 'Why was the inventory code changed?')


if __name__=='__main__':unittest.main()
