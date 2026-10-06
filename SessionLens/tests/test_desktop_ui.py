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

    def test_history_review_is_explicit_and_preserves_selected_retry(self):
        from sessionlens.task_lineage import resolve
        from unittest.mock import patch
        from PySide6.QtCore import QUrl
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);log=root/'task.jsonl'
            rows=[{'type':'message','sessionId':'s','role':'user','content':'分析发布方案'},
                  {'type':'message','sessionId':'s','role':'assistant','content':'已经分析了发布方案。'},
                  {'type':'message','sessionId':'s','role':'user','content':'再分析一下'},
                  {'type':'message','sessionId':'s','role':'assistant','content':'Interrupted by user'}]
            log.write_text(''.join(json.dumps(r)+'\n' for r in rows));c=Collector(root/'collector.db');c.scan(log,source='workbuddy');c.db.close()
            with patch('sessionlens.relay_model.call') as model:
                window=Window(root);window.store.advance();window.set_mode('history');window.reload();model.assert_not_called()
            ids=[r[0] for r in window.store.db.execute('SELECT t.id FROM tasks t JOIN events e ON e.id=t.id ORDER BY e.rowid')]
            window.selected=ids[1];window.signature=None;window.reload()
            output={'links':[{'turnId':'T001','parentTurnId':None,'relation':'request','status':'supported','reason':'独立目标','evidenceTurnIds':['T001']},
                              {'turnId':'T002','parentTurnId':'T001','relation':'revision','status':'supported','reason':'继续发布方案分析','evidenceTurnIds':['T001','T002']}]}
            window.config['model']={'name':'test','url':'https://example.com'}
            class Immediate:
                def __init__(self,target,**kwargs):self.target=target
                def start(self):self.target()
            with patch('desktop_main.threading.Thread',Immediate),patch('sessionlens.relay_model.call',return_value=output):window.start_association(ids[1],ids)
            self.app.processEvents()
            self.assertEqual(window.selected,ids[0]);self.assertEqual(resolve(window.store.db,ids[1]),ids[0]);self.assertEqual(window.task_list.count(),1)
            self.assertIn('2 轮对话',window.task_list.item(0).text());self.assertIn('需求对话 · 2 轮',window.middle.toPlainText());self.assertIn('本轮中断',window.middle.toPlainText())
            window.follow_link(QUrl('dialogue:'+ids[1]));self.assertIn('再分析一下',window.proof.toPlainText());self.assertTrue(window.associate.isEnabled());window.close()

    def test_comparison_view_shows_each_task_and_opens_owned_evidence(self):
        from sessionlens.chat_window import ChatWindow
        from sessionlens.assistant import packet_for_task,combine_packets
        from PySide6.QtCore import QUrl
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);window=Window(root);chat=ChatWindow(window);log=root/'task.jsonl'
            rows=[{'type':'message','role':'user','sessionId':str(n),'content':'检查日报接口 '+str(n)} for n in range(3)]
            log.write_text(''.join(json.dumps(r)+'\n' for r in rows));c=Collector(root/'collector.db');c.scan(log,source='workbuddy');window.store.advance(realtime=True);window.store.repair_links(10);c.db.close()
            tasks=window.store.tasks();packet=combine_packets([packet_for_task(window.store.db,t[0]) for t in tasks]);ids=[t[0] for t in tasks]
            chat.messages=[{'question':'比较这几次接口检查','taskId':ids[0],'retrievedTaskIds':ids,'packet':packet,'retrieved':[{'taskId':t[0],'title':t[3],'source':t[1],'updated':t[4]} for t in tasks],
                            'understanding':{'overview':{'text':'这里只比较本次检索到的三次任务。','basis':'recorded','evidenceRefs':['E001']},'steps':[],'gaps':[]}}]
            chat.render();self.assertEqual(chat.results.currentWidget(),chat.answer);self.assertEqual(chat.current_task.text(),'相关任务 · 3 次')
            self.assertIn('检查日报接口 0',chat.answer.toPlainText());self.assertIn('最多比较 3 个',chat.answer.toPlainText())
            chat.evidence(QUrl('proof:0:E001'));self.assertIn('所属任务',chat.proof.toPlainText())
            with patch.object(chat,'send') as send:
                chat.evidence(QUrl('inspect-task:'+ids[1]));send.assert_called_once_with(selected_task=ids[1])
            chat.close()
    def test_project_relation_display_and_manual_independent_mode(self):
        from sessionlens.chat_window import ChatWindow
        from sessionlens.project_context import ProjectStore
        from sessionlens.assistant import packet_for_task
        from sessionlens.task_presentation import project
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QDialog,QComboBox,QPushButton
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);window=Window(root);chat=ChatWindow(window);log=root/'task.jsonl'
            rows=[{'type':'message','role':'user','sessionId':'s','cwd':'/repo/atlas','git':{'repository_url':'https://github.com/team/atlas'},'content':'修改项目说明'},
                  {'type':'function_call','sessionId':'s','name':'Bash','arguments':{'command':'git status'}}]
            log.write_text(''.join(json.dumps(r)+'\n' for r in rows));c=Collector(root/'collector.db');c.scan(log,source='workbuddy');window.store.advance(realtime=True);c.db.close();task=window.store.tasks()[0][0]
            store=ProjectStore(root/'project_context.db',filesystem=False);ctx=store.resolve(window.store.db,task)
            packet=packet_for_task(window.store.db,task,project_context=ctx);view=project(window.store.db,task);view['projectContext']=ctx
            q='项目说明修改了什么';chat.input.setPlainText(q);chat.messages=[{'question':q,'taskId':task,'packet':packet,'presentation':view,'understanding':{'overview':{'text':'记录显示读取了仓库状态。','basis':'recorded','evidenceRefs':['E001']},'steps':[]}}]
            chat.render();chat.refresh_knowledge();self.assertIn('atlas',chat.task_view.project_label.text());self.assertIn('github.com/team/atlas',chat.task_view.project_label.text());self.assertGreater(chat.project_scope.count(),2)
            def save():
                dialog=next(w for w in self.app.topLevelWidgets() if isinstance(w,QDialog) and w.windowTitle()=='修正项目关联')
                dialog.findChildren(QComboBox)[0].setCurrentIndex(1);next(b for b in dialog.findChildren(QPushButton) if b.text()=='保存关联').click()
            QTimer.singleShot(0,save);chat.correct_project();store.refresh_overrides();self.assertEqual(store.resolve(window.store.db,task)['mode'],'independent')
            self.assertEqual(chat.input.toPlainText(),q);self.assertIn('项目关联已更新',chat.answer.toPlainText());store.close();chat.close()

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

    def test_multi_turn_requirements_and_manual_association_are_accessible(self):
        from sessionlens.chat_window import ChatWindow
        from sessionlens.task_presentation import project
        from sessionlens.assistant import packet_for_task
        from sessionlens.task_lineage import history
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QDialog,QComboBox,QPushButton
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);window=Window(root);chat=ChatWindow(window);log=root/'task.jsonl'
            records=[{'type':'message','role':'user','content':s,'sessionId':'s'} for s in ('设计会员管理页面','手机号登录，会员表格','做')]
            log.write_text(''.join(json.dumps(r)+'\n' for r in records));c=Collector(root/'collector.db');c.scan(log,source='workbuddy');window.store.advance(realtime=True)
            task=window.store.db.execute("SELECT id FROM task_groups WHERE prompt='手机号登录，会员表格'").fetchone()[0]
            q='会员登录为什么这样做';chat.input.setPlainText(q)
            chat.messages=[{'question':q,'taskId':task,'presentation':project(window.store.db,task),'packet':packet_for_task(window.store.db,task),'understanding':{'overview':{'text':'需求讨论之后开始执行。','evidenceRefs':[]},'steps':[]}}]
            chat.render();self.assertFalse(chat.task_view.requirement_button.isHidden());self.assertIn('2 轮',chat.task_view.requirement_button.text())
            def save():
                dialog=next(w for w in self.app.topLevelWidgets() if isinstance(w,QDialog) and w.windowTitle()=='修正任务关联')
                combos=dialog.findChildren(QComboBox);combos[1].setCurrentIndex(1)
                next(b for b in dialog.findChildren(QPushButton) if b.text()=='保存关联').click()
            QTimer.singleShot(0,save);chat.correct_association()
            self.assertEqual(len(window.store.tasks()),1);self.assertIn('重新查询',chat.answer.toPlainText())
            self.assertEqual(chat.input.toPlainText(),q);self.assertEqual(history(window.store.db,task)[0]['text'],'设计会员管理页面')
            c.db.close();chat.close()
