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
