"""Conversation-first knowledge assistant; evidence stays one click away."""
import html,json,sqlite3,threading,uuid,os
from pathlib import Path
from PySide6.QtCore import QObject,Signal,Qt,QUrl,QTimer,QEvent,QLocale
from PySide6.QtWidgets import QMainWindow,QWidget,QHBoxLayout,QVBoxLayout,QLabel,QPushButton,QPlainTextEdit,QComboBox,QTextBrowser,QListWidget,QSplitter,QDialog,QStackedWidget,QScrollArea,QFormLayout,QLineEdit,QSizePolicy
from .knowledge import ask,answer_mismatch
from .task_view import TaskView,task_title
from .supervision import event_text
from .knowledge_view import KnowledgeView,ActiveStack
from .i18n import language,set_language,t,localize_widgets
from .database import connection
from .theme import STYLE,DESKTOP_SIDEBAR,DESKTOP_INSPECTOR
from .desktop_pages import HistoryPage,CollectionPage

def local_query_context(messages,history,question,project_scope):
    """Keep project selection/retry context local, out of model answer history."""
    if history:return history[-1]
    if not messages:return None
    latest=messages[-1]
    if latest.get('selectionNeeded') and latest.get('projectDetails') and latest.get('projectScope')==project_scope:return latest
    # A retry of the same failed recommendation still refers to that project.
    # Never bridge an intervening new question or recover an unrelated task.
    for result in reversed(messages):
        if result.get('question')==question and (result.get('error') or result.get('selectionNeeded')):continue
        if result.get('projectDetails') and result.get('projectScope')==project_scope and latest.get('question')==question:return result
        break
    return None

class Signals(QObject):
    progress=Signal(str)
    ready=Signal(object)
    failed=Signal(str)

class ChatWindow(QMainWindow):
    def __init__(self,collector):
        super().__init__();self.collector=collector;self.root=collector.root;self.busy=False;self.messages=[];self.chat_id=None;collector.hide_on_close=True
        requested=collector.config.get('ui',{}).get('language')
        set_language(requested if requested in ('zh','en') else ('zh' if QLocale.system().name().startswith('zh') else 'en'))
        self.setWindowTitle('SessionLens · 智能助手');self.resize(1240,850)
        self.setMinimumSize(880,650);self.composing=False;self.page='assistant';self.closed=False
        self.setStyleSheet(STYLE)
        self.cache=sqlite3.connect(self.root/'conversations.db');self.cache.execute('CREATE TABLE IF NOT EXISTS chats(id TEXT PRIMARY KEY,title TEXT,content TEXT,updated INTEGER)');self.cache.commit()
        body=QWidget();body.setObjectName('assistantShell');self.setCentralWidget(body);outer=QHBoxLayout(body);outer.setContentsMargins(0,0,0,0);outer.setSpacing(0)
        side=QWidget();side.setObjectName('assistantSide');side.setFixedWidth(DESKTOP_SIDEBAR);self.sidebar=side;v=QVBoxLayout(side);v.setContentsMargins(12,22,12,16);v.setSpacing(6)
        brand=QLabel('SessionLens');brand.setStyleSheet('font-size:17px;font-weight:600;color:#172334;');v.addWidget(brand);v.addSpacing(20)
        self.navigation={}
        for key,title,slot in [('assistant','智能助手',lambda:self.show_page('assistant')),('history','历史任务',self.open_history),('collection','采集与同步',self.open_collection)]:
            button=QPushButton(title);button.setCheckable(True);button.setChecked(key=='assistant');button.clicked.connect(slot);v.addWidget(button);self.navigation[key]=button
        new=QPushButton('＋ 新对话');new.clicked.connect(self.new_chat);v.addWidget(new);v.addSpacing(20)
        recent=QLabel('最近问过');recent.setStyleSheet('font-size:12px;color:#637084;padding:0 9px;');v.addWidget(recent)
        self.current_task=QLabel('还没有选中任务');self.current_task.setWordWrap(True);self.current_task.hide()
        self.chats=QListWidget();self.chats.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff);self.chats.currentRowChanged.connect(self.open_chat);v.addWidget(self.chats,1);outer.addWidget(side)
        side_hint=QLabel('本机历史知识库\nCodex · WorkBuddy');side_hint.setStyleSheet('font-size:12px;color:#637084;padding:12px 8px;');v.addWidget(side_hint)
        projects=QPushButton('管理项目归属');projects.clicked.connect(self.open_projects);v.addWidget(projects)
        self.language_choice=QComboBox();self.language_choice.setObjectName('interfaceLanguage');self.language_choice.addItem('简体中文','zh');self.language_choice.addItem('English','en');self.language_choice.setCurrentIndex(self.language_choice.findData(language()));self.language_choice.setAccessibleName('Interface language');self.language_choice.setStyleSheet('QComboBox{background:white;border:1px solid #E6E8EC;border-radius:7px;padding:6px;font-size:12px;}');self.language_choice.currentIndexChanged.connect(self.change_language);v.addWidget(self.language_choice)
        main=QWidget();main.setObjectName('assistantMain');m=QVBoxLayout(main);m.setContentsMargins(28,28,28,22);m.setSpacing(18)
        head=QLabel('智能助手');head.setObjectName('pageTitle');m.addWidget(head)
        scope=QHBoxLayout();scope.addWidget(QLabel('查询范围'));self.source=QComboBox();self.source.addItem('全部 Agent',None);self.source.addItem('WorkBuddy','workbuddy');self.source.addItem('Codex','codex');scope.addWidget(self.source)
        scope.addWidget(QLabel('时间'));self.period=QComboBox();self.period.addItem('全部历史',0);self.period.addItem('最近 7 天',7);self.period.addItem('最近 30 天',30);scope.addWidget(self.period);scope.addStretch();scope_container=QWidget(main);scope_container.setLayout(scope);scope_container.hide()
        self.composer=QWidget();self.composer.setObjectName('assistantComposer')
        cv=QVBoxLayout(self.composer);cv.setContentsMargins(18,18,18,14);cv.setSpacing(8)
        self.input=QPlainTextEdit();self.input.setObjectName('questionInput');self.input.setFixedHeight(88);self.input.setPlaceholderText('问项目、一次任务，或当时为什么这样做…');self.input.setAccessibleName('向历史任务提问');self.input.installEventFilter(self);cv.addWidget(self.input)
        row=QHBoxLayout();hint=QLabel('查找本机任务记录 · Enter 提问，Shift + Enter 换行');hint.setWordWrap(True);hint.setObjectName('metadata');row.addWidget(hint,1);self.send_button=QPushButton('提问');self.send_button.setObjectName('primaryButton');self.send_button.setMinimumWidth(82);self.send_button.clicked.connect(self.send);row.addWidget(self.send_button)
        # Project scope is inferred from the question; no preparatory dropdown.
        self.project_scope=QComboBox(self);self.project_scope.addItem('全部任务',None);self.project_scope.hide();cv.addLayout(row);m.addWidget(self.composer);self.project_catalog_seen=None
        suggestions=QHBoxLayout();suggestions.setSpacing(7)
        for title,q in [('有哪些项目','做过哪些项目？'),('为什么这样改代码','最近的代码修改任务，为什么这样改？'),('视频怎么生成','视频生成任务是怎么完成的？')]:
            b=QPushButton(title);b.clicked.connect(lambda checked=False,q=q:self.followup_question(t(q)));suggestions.addWidget(b)
        suggestions.addStretch();m.addLayout(suggestions)
        self.status=QLabel('从你的任务记录中寻找答案');self.status.setObjectName('metadata');self.status.setFixedHeight(23);self.status.setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Fixed);m.addWidget(self.status)
        self.split=QSplitter(Qt.Horizontal);self.split.setChildrenCollapsible(False);self.split.setHandleWidth(5)
        self.answer=QTextBrowser();self.answer.setOpenLinks(False);self.answer.anchorClicked.connect(self.evidence);self.results=ActiveStack();self.results.addWidget(self.answer);self.task_view=TaskView();self.task_view.contentChanged.connect(lambda:QTimer.singleShot(0,self.fit_result));self.task_view.evidenceRequested.connect(lambda ref:self.evidence(QUrl("proof:"+str(len(self.messages)-1)+":"+ref)));self.results.addWidget(self.task_view)
        self.knowledge_view=KnowledgeView();self.results.addWidget(self.knowledge_view);self.knowledge_view.projectRequested.connect(self.open_project_answer);self.knowledge_view.taskRequested.connect(self.open_task_answer);self.knowledge_view.questionRequested.connect(self.followup_question);self.knowledge_view.evidenceRequested.connect(lambda ref:self.evidence(QUrl('proof:'+str(len(self.messages)-1)+':'+ref)));self.knowledge_view.raw_handler=self.show_raw;self.task_view.raw_handler=self.show_raw
        self.task_view.associationRequested.connect(self.correct_association)
        self.task_view.projectRequested.connect(self.correct_project)
        self.knowledge_view.associationRequested.connect(self.correct_association)
        self.knowledge_view.projectCorrectionRequested.connect(self.correct_project)
        self.knowledge_view.contentChanged.connect(lambda:QTimer.singleShot(0,self.fit_result))
        self.results.setSizePolicy(QSizePolicy.Expanding,QSizePolicy.Minimum);m.addWidget(self.results,1)
        main_scroll=QScrollArea();main_scroll.setWidgetResizable(True);main_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded);main_scroll.setFrameShape(QScrollArea.NoFrame);main_scroll.setWidget(main);self.main_scroll=main_scroll
        self.pages=QStackedWidget();self.pages.addWidget(main_scroll);self.history_page=HistoryPage(collector);self.history_page.taskRequested.connect(self.review_history_task);self.pages.addWidget(self.history_page);self.collection_page=CollectionPage(collector);self.pages.addWidget(self.collection_page);self.split.addWidget(self.pages)
        proof_panel=QWidget();proof_panel.setObjectName('evidencePanel');proof_panel.setMinimumWidth(250);proof_panel.setMaximumWidth(580);pv=QVBoxLayout(proof_panel);pv.setContentsMargins(14,18,14,12);pr=QHBoxLayout();self.proof_heading=QLabel('原始依据');self.proof_heading.setObjectName('sectionTitle');pr.addWidget(self.proof_heading);pr.addStretch();close=QPushButton('收起');close.setObjectName('textButton');close.clicked.connect(self.close_evidence);pr.addWidget(close);pv.addLayout(pr);self.proof=QTextBrowser();self.proof.setMinimumWidth(0);pv.addWidget(self.proof,1);self.proof_panel=proof_panel;self.split.addWidget(proof_panel);proof_panel.hide();outer.addWidget(self.split,1)
        self.collection_status=QLabel('本地任务库 · 采集状态可查看');self.collection_status.setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Fixed);self.collection_status.setMinimumWidth(100);self.statusBar().setSizeGripEnabled(False);self.statusBar().addWidget(self.collection_status,1)
        view=QPushButton('查看采集进度');view.setObjectName('textButton');view.clicked.connect(self.open_collection);self.statusBar().addPermanentWidget(view)
        self.render_signature=None
        self.signals=Signals(self);self.signals.progress.connect(lambda message:self.status.setText(t(message)));self.signals.ready.connect(self.received);self.signals.failed.connect(self.failed)
        self.knowledge_timer=QTimer(self);self.knowledge_timer.timeout.connect(self.refresh_knowledge);self.knowledge_timer.start(1200)
        self.refresh_chats();self.new_chat()
        localize_widgets(self.collector)
    def change_language(self):
        set_language(self.language_choice.currentData())
        config=dict(self.collector.config);config['ui']={**config.get('ui',{}),'language':language()}
        # Persist only the interface preference; preserve model and collector settings.
        path=self.root/'settings.json';temporary=self.root/'settings-language.tmp'
        try:
            with temporary.open('w',encoding='utf-8') as handle:
                os.chmod(temporary,0o600);handle.write(json.dumps(config,ensure_ascii=False,indent=2))
            temporary.replace(path);self.collector.config=config
        except OSError:
            temporary.unlink(missing_ok=True)
            self.status.setText(t('语言已切换，但未能保存偏好；下次启动可能恢复原语言。'))
        localize_widgets(self);localize_widgets(self.collector)
        self.history_page.refresh();self.collection_page.refresh()
        self.knowledge_view.refresh_language()
        if hasattr(self.collector,'refresh_language'):self.collector.refresh_language()
        for flow in self.knowledge_view.findChildren(QWidget):
            if flow.__class__.__name__=='RelationFlow':flow.update()
        QTimer.singleShot(0,self.fit_result)
    def refresh_knowledge(self):
        runtime=self.collector.runtime
        if runtime:
            with runtime.lock:state=dict(runtime.status)
            text=state.get('knowledge','任务知识库正在准备…')
            if getattr(runtime,'paused',None) and runtime.paused.is_set():text='已暂停采集 · '+text
            if self.collection_status.text()!=t(text):self.collection_status.setText(t(text))
            self.collection_status.setToolTip(t(state.get('upload','未配置上报')))
        else:self.collection_status.setText(t('本地任务库 · 采集尚未启动'))
        if self.page=='collection':self.collection_page.refresh()
    def fit_result(self):
        if self.results.currentWidget()==self.knowledge_view:
            height=max(300,self.knowledge_view.layout().totalHeightForWidth(max(320,self.results.width())))
            if self.results.minimumHeight()!=height:self.results.setMinimumHeight(height)
        elif self.results.currentWidget()==self.task_view:
            height=max(300,self.task_view.layout().totalHeightForWidth(max(720,self.results.width())))
            if self.results.minimumHeight()!=height:self.results.setMinimumHeight(height)
    def resizeEvent(self,event):
        super().resizeEvent(event)
        if hasattr(self,'split'):
            direction=Qt.Vertical if self.width()<1060 and self.proof_panel.isVisible() else Qt.Horizontal
            if self.split.orientation()!=direction:
                self.split.setOrientation(direction);self.proof_panel.setMaximumWidth(16777215 if direction==Qt.Vertical else 580)
                self.split.setSizes([500,300] if direction==Qt.Vertical else [max(450,self.split.width()-DESKTOP_INSPECTOR),DESKTOP_INSPECTOR])
        if hasattr(self,'task_view'):QTimer.singleShot(0,self.fit_result)
    def keyPressEvent(self,event):
        if event.key()==Qt.Key_Escape and self.proof_panel.isVisible():self.close_evidence();event.accept();return
        super().keyPressEvent(event)
    def eventFilter(self,watched,event):
        if watched is getattr(self,'input',None):
            if event.type()==QEvent.InputMethod:self.composing=bool(event.preeditString())
            if event.type()==QEvent.KeyPress and event.key() in (Qt.Key_Return,Qt.Key_Enter) and not event.modifiers()&Qt.ShiftModifier and not self.composing:
                self.send();return True
        return super().eventFilter(watched,event)
    def stop_playback(self):
        self.task_view.stop()
        self.knowledge_view.stop()
    def open_collector(self):localize_widgets(self.collector);self.collector.show();self.collector.raise_()
    def open_history(self):
        self.show_page('history');self.history_page.refresh()
    def show_page(self,page):
        self.stop_playback();self.page=page;self.close_evidence()
        self.pages.setCurrentIndex({'assistant':0,'history':1,'collection':2}[page])
        for key,button in self.navigation.items():button.setChecked(key==page)
        if page=='collection':self.collection_page.refresh(force=True)
    def review_history_task(self,identity):
        if not self.can_start_query():return
        self.show_page('assistant');self.prefill(t('这次任务是怎么做的？'));self.send(selected_task=identity)
    def open_projects(self):
        self.stop_playback()
        from .project_window import ProjectWindow
        dialog=ProjectWindow(self.root,self)
        def inspect(identity):
            self.collector.open_project_task(identity);dialog.accept();self.open_collector()
        dialog.taskRequested.connect(inspect);localize_widgets(dialog);dialog.exec()
    def open_collection(self):
        self.show_page('collection')
    def close_evidence(self):
        self.proof_panel.hide()
        if self.split.orientation()!=Qt.Horizontal:self.split.setOrientation(Qt.Horizontal)
    def show_raw(self,title,text):
        self.stop_playback();self.proof_heading.setText(t(title));self.proof.setPlainText(str(text));self.show_evidence_panel()
    def show_evidence_panel(self):
        self.proof_panel.show()
        vertical=self.width()<1060
        self.split.setOrientation(Qt.Vertical if vertical else Qt.Horizontal)
        self.proof_panel.setMaximumWidth(16777215 if vertical else 580)
        self.split.setSizes([500,300] if vertical else [max(450,self.split.width()-DESKTOP_INSPECTOR),DESKTOP_INSPECTOR])
    def prefill(self,question):self.input.setPlainText(question);self.input.setFocus()
    def can_start_query(self):
        if not self.busy:return True
        message=t('当前问题正在处理，完成后再查询其他任务。')
        self.status.setText(message);self.statusBar().showMessage(message,6000)
        return False
    def followup_question(self,question):
        if not self.can_start_query():return
        self.prefill(question);self.send()
    def open_project_answer(self,identity):
        if not self.can_start_query():return
        latest=self.messages[-1] if self.messages else {};offered=latest.get('projectChoices',latest.get('projectInventory',{}).get('projects',[]))+([latest['projectDetails']] if latest.get('projectDetails') else []);p=next((p for p in offered if p['id']==identity),None)
        if p:self.prefill('What tasks were completed in '+p['name']+'?' if language()=='en' else p['name']+' 里面做过哪些任务？');self.send(selected_project=identity)
    def open_task_answer(self,identity):
        if not self.can_start_query():return
        latest=self.messages[-1] if self.messages else {}
        question=latest.get('question') if latest.get('selectionNeeded') else ('How was this task completed?' if language()=='en' else '这次任务是怎么做的？')
        self.prefill(question);self.send(selected_task=identity)
    def correct_project(self):
        if self.busy or not self.messages:return
        result=self.messages[-1];task=result.get('taskId')
        if not task:return
        context=result.get('packet',{}).get('projectContext') or {};items=context.get('projects',[]);first=items[0] if items else {}
        dialog=QDialog(self);dialog.setWindowTitle('修正项目关联');dialog.resize(640,320);form=QFormLayout(dialog)
        form.addRow(QLabel('调整历史任务的知识归属，原始证据保留。项目名、目录或仓库至少填写一项。'))
        mode=QComboBox();mode.addItem('关联到项目','linked');mode.addItem('设为独立任务','independent');mode.addItem('恢复自动判断','automatic');form.addRow('归属',mode)
        name=QLineEdit(first.get('name',''));directory=QLineEdit(first.get('root') or '');repo=QLineEdit(first.get('repository') or '')
        form.addRow('项目名称',name);form.addRow('项目目录',directory);form.addRow('仓库地址',repo);status=QLabel('');status.setWordWrap(True);form.addRow(status)
        row=QHBoxLayout();save=QPushButton('保存关联');cancel=QPushButton('取消');row.addWidget(save);row.addWidget(cancel);form.addRow(row);cancel.clicked.connect(dialog.reject)
        def apply():
            from .project_context import ProjectStore
            store=None
            try:
                store=ProjectStore(self.root/'project_context.db');store.set_override(task,mode.currentData(),name.text(),directory.text(),repo.text())
            except (ValueError,OSError,sqlite3.Error) as error:status.setText(str(error));return
            finally:
                if store:store.close()
            result['selectionMismatch']='项目关联已更新，请重新查询当前任务';self.render();dialog.accept();self.refresh_knowledge()
        save.clicked.connect(apply);localize_widgets(dialog);dialog.exec()
    def correct_association(self):
        if self.busy or not self.messages:return
        from .task_lineage import history,set_override
        result=self.messages[-1];store=self.collector.store;turns=history(store.db,result['taskId'])
        if not turns:return
        dialog=QDialog(self);dialog.setWindowTitle('修正任务关联');dialog.resize(680,340);v=QVBoxLayout(dialog)
        v.addWidget(QLabel('选择一轮提问，将它关联到更早的任务，或从这里另起任务。原始记录保留。'))
        turn=QComboBox();turn.addItems([r['label']+' · '+r['text'].replace('\n',' ')[:90] for r in turns]);v.addWidget(turn)
        target=QComboBox();v.addWidget(target);status=QLabel('');status.setWordWrap(True);v.addWidget(status)
        def choices(index):
            target.clear();target.addItem('从这一轮开始作为独立任务',None)
            for ident,prompt in store.db.execute('SELECT g.id,g.prompt FROM task_groups g JOIN events e ON e.id=g.id WHERE g.source=? AND g.session=? AND e.rowid<? ORDER BY e.rowid DESC LIMIT 100',(result['presentation']['source'],result['presentation']['session'],turns[index]['seq'])):
                target.addItem(prompt.replace('\n',' ')[:100],ident)
        turn.currentIndexChanged.connect(choices);choices(0)
        row=QHBoxLayout();save=QPushButton('保存关联');cancel=QPushButton('取消');row.addWidget(save);row.addWidget(cancel);v.addLayout(row);cancel.clicked.connect(dialog.reject)
        def apply():
            try:set_override(store.db,turns[turn.currentIndex()]['eventId'],target.currentData())
            except (ValueError,sqlite3.Error) as exc:status.setText(str(exc));return
            result['selectionMismatch']='任务关联已更新，请重新查询完整过程';self.collector.signature=None;self.render();dialog.accept()
        save.clicked.connect(apply);localize_widgets(dialog);dialog.exec()
    def refresh_chats(self):
        self.chat_rows=self.cache.execute('SELECT id,title FROM chats ORDER BY updated DESC').fetchall();self.chats.blockSignals(True);self.chats.clear();self.chats.addItems([r[1] for r in self.chat_rows]);self.chats.blockSignals(False)
    def new_chat(self):
        if self.busy:return
        self.show_page('assistant');self.chat_id=uuid.uuid4().hex;self.messages=[];self.current_task.setText('还没有选中任务');self.input.clear();self.send_button.setText('提问');self.render()
    def open_chat(self,index):
        if self.busy or index<0:return
        self.show_page('assistant')
        self.chat_id=self.chat_rows[index][0];self.messages=json.loads(self.cache.execute('SELECT content FROM chats WHERE id=?',(self.chat_id,)).fetchone()[0])
        with connection(self.root/'collector.db',timeout=10) as db:
            for result in self.messages:
                mismatch=answer_mismatch(db,result)
                if mismatch:result['selectionMismatch']=mismatch
        self.stop_playback();self.input.setPlainText(self.messages[-1]['question'] if self.messages else '');self.proof_panel.hide();self.render()
    def render(self):
        try:self._render()
        finally:localize_widgets(self)
    def _render(self):
        latest=self.messages[-1] if self.messages else {}
        signature=(self.chat_id,len(self.messages),self.busy,getattr(self,'pending','') if self.busy else '',id(latest),latest.get('selectionMismatch'))
        if signature==self.render_signature:return
        self.render_signature=signature
        if self.busy:
            self.stop_playback();self.results.setMinimumHeight(150);self.results.setCurrentWidget(self.answer);self.current_task.setText('正在查找相关任务');self.proof_panel.hide()
            self.answer.setHtml('<h3>'+html.escape(t('正在查找这次问题的相关记录'))+'</h3><p>'+html.escape(self.pending)+'</p>');self.send_button.setText('查询中…');return
        latest=self.messages[-1] if self.messages else {}
        if not latest:
            self.stop_playback();self.results.setCurrentWidget(self.knowledge_view);self.knowledge_view.load({'home':True});self.results.setMinimumHeight(240);self.send_button.setText('提问');QTimer.singleShot(0,self.fit_result);return
        if not latest.get('selectionMismatch') and (latest.get('projectInventory') or latest.get('projectDetails') or 'projectChoices' in latest or latest.get('presentation')):
            self.stop_playback();self.results.setCurrentWidget(self.knowledge_view);self.knowledge_view.load(latest);self.proof_panel.hide();self.send_button.setText('提问');self.current_task.setText(latest.get('projectDetails',{}).get('name') or latest.get('presentation',{}).get('prompt','项目与任务')[:30]);QTimer.singleShot(0,self.fit_result);return
        if 'projectChoices' in latest or latest.get('projectDetails'):
            self.stop_playback();self.results.setCurrentWidget(self.answer);self.results.setMinimumHeight(360);self.proof_panel.hide();self.send_button.setText('查询');esc=html.escape
            if 'projectChoices' in latest:
                self.current_task.setText('确认项目');self.status.setText('根据问题识别项目')
                content='<h3>'+esc(latest['projectMessage'])+'</h3>'
                for p in latest['projectChoices']:content+='<p><a href="project:'+p['id']+'">'+esc(p['name']+' · '+p['source'])+'</a><br>'+esc(' / '.join(p['roots']))+'</p>'
                self.answer.setHtml(content);return
            from .project_answers import render_project_answer
            project=latest['projectDetails'];self.current_task.setText(project['name']);self.status.setText('SessionLens 本机项目知识 · 任务与文件都有依据')
            self.answer.setHtml(render_project_answer(latest));return
        if latest.get('projectInventory'):
            from html import escape
            snap=latest['projectInventory'];c=snap['counts'];self.stop_playback();self.results.setCurrentWidget(self.answer);self.results.setMinimumHeight(300);self.proof_panel.hide();self.current_task.setText('项目总览')
            self.send_button.setText('查询');self.status.setText('本机项目统计 · 点击项目查看开发依据')
            text=f'<h3>已识别 {c["identified"]+c["confirmed"]} 个项目；待确认 {c["candidate"]} 个开发目录</h3>'
            text+='<p>'+('本次统计已覆盖当前整理出的历史。' if snap['complete'] else '历史尚未整理完，当前数量会继续增加。')+'</p><p><a href="projects:">打开项目总览：确认、合并或排除目录 →</a></p>'
            text+='<ol>'+''.join('<li><a href="project:'+p['id']+'">'+escape(p['name'])+'</a> · '+str(p['taskCount'])+' 个任务 · '+({'confirmed':'人工确认','identified':'项目标记与源码记录','candidate':'待确认'}[p['state']])+'</li>' for p in snap['projects'] if p['state']!='excluded')+'</ol><p>'+escape(snap['coverage'])+'</p>'
            self.answer.setHtml(text);return
        if latest.get('selectionMismatch'):
            self.stop_playback();self.results.setMinimumHeight(150);self.results.setCurrentWidget(self.answer);self.current_task.setText('任务需要重新核对');self.proof_panel.hide();self.send_button.setText('重新查询')
            self.status.setText('此前的回答没有对应到你问的任务')
            self.answer.setHtml('<h3>'+html.escape(t(latest['selectionMismatch']))+'</h3><p>'+html.escape(t('问题已保留在上方。点击“重新查询”，根据这次问题重新寻找任务和证据。'))+'</p>');return
        self.send_button.setText('查询')
        if latest.get('selectionNeeded'):
            self.stop_playback();self.results.setMinimumHeight(300);self.results.setCurrentWidget(self.answer);self.current_task.setText('选择要查看的任务');self.status.setText('找到多个相近任务，请选择具体的一次')
            message=latest.get('selectionMessage')
            if message:self.current_task.setText('等待补充任务信息');self.status.setText('尚未确认对应任务')
            content='<h3>'+html.escape(t(message or '你想了解哪一次任务？'))+'</h3><p>'+html.escape(latest['question'])+'</p>'
            if latest.get('taskId') and latest.get('presentation'):content+='<p><a href="associate:">修正任务关联</a></p>'
            for option in latest['options']:
                date=option['updated'][:16].replace('T',' ')
                name={'workbuddy':'WorkBuddy','codex':'Codex'}.get(option['source'],option['source'])
                content+='<p><a href="choose:'+html.escape(option['taskId'])+'"><b>'+html.escape(name+' · '+date)+'</b><br>'+html.escape(option['title'][:100])+'</a></p>'
            self.answer.setHtml(content);return
        if self.messages and self.messages[-1].get('presentation') and not self.busy:
            self.current_task.setText(task_title(self.messages[-1]['presentation']['prompt']));self.task_view.load(self.messages[-1]);self.results.setCurrentWidget(self.task_view);QTimer.singleShot(0,self.fit_result);return
        self.stop_playback();self.results.setMinimumHeight(150);self.results.setCurrentWidget(self.answer)
        if latest.get('packet',{}).get('scope')=='multiple':self.current_task.setText(f'相关任务 · {len(latest.get("retrievedTaskIds",[]))} 次')
        esc=html.escape
        content='<style>body{color:#233247}p{line-height:150%}a{color:#3265a8;text-decoration:none}h3{font-size:16px}</style>'
        if not self.messages and not self.busy:content+='<h3>答案将在这里展开</h3><p style="color:#758397">相关任务、处理经过与原始依据，随提问显示。</p><p><a href="sample:weather">查上海天气时遇到了什么问题？</a></p>'
        # Show only the latest question here. A failed/new query must not leave
        # the previous task at the top of the visible answer panel.
        for n,r in list(enumerate(self.messages))[-1:]:
            content+='<a name="answer-'+str(n)+'"></a><hr><h3>'+esc(t('你'))+'</h3><p>'+esc(r['question'])+'</p><h3>SessionLens</h3>'
            if r.get('error'):content+='<p>'+esc(t(r['error']))+'</p>';continue
            value=r['understanding']
            for block in [value['overview']]+value['steps']:
                if block.get('title'):content+='<h3>'+esc(block['title'])+'</h3>'
                content+='<p>'+esc(t({'inferred':'推断：','unknown':'记录未能确认：'}.get(block['basis'],''))+block['text'])+'</p>'
                content+='<p>'+ '　'.join(f'<a href="proof:{n}:{ref}">{ref} '+esc(t('查看依据'))+'</a>' for ref in block['evidenceRefs'])+'</p>'
            for gap in value.get('gaps',[]):content+='<p style="color:#88663b">'+esc(str(gap))+'</p>'
            if r.get('packet',{}).get('scope')=='multiple':
                content+='<h3>'+esc(t('本次参考的任务'))+'</h3>'
                for item in r.get('retrieved',[]):
                    content+='<p><b>'+esc(item['source']+' · '+str(item.get('updated',''))[:16].replace('T',' '))+'</b><br>'+esc(item['title'][:140])+f'<br><a href="inspect-task:{esc(item["taskId"])}">'+esc(t('查看这次任务过程'))+'</a></p>'
                content+='<p style="color:#758397">'+esc(t('最多比较 3 个检索到的任务，受所选来源、时间和当前整理进度限制。'))+'</p>'
            content+='<p style="color:#758397">'+esc(' · '.join(x['source']+' / '+x['title'] for x in r.get('retrieved',[])))+'</p>'
            packet=r.get('packet',{});fragments=packet.get('fragments',[])
            coverage=('Read '+str(len(fragments))+' / '+str(packet.get('totalRecords',len(fragments)))+' records'+('; some excerpts are truncated' if any(f.get('truncated') for f in fragments) else '')+'. Check citations against original records.' if language()=='en' else '本次读取 '+str(len(fragments))+' / '+str(packet.get('totalRecords',len(fragments)))+' 条记录'+('，部分正文已截取' if any(f.get('truncated') for f in fragments) else '')+'；引用可核对原文。')
            count=len(r.get('retrievedTaskIds',[]));scope_note=f'Referenced {count} tasks · Relevant evidence in the current local index' if language()=='en' else f'已参考 {count} 个任务 · 当前已整理知识库中的相关证据'
            content+='<p style="color:#758397">'+esc(coverage)+'</p><p style="color:#758397">'+esc(scope_note)+'</p>'
        if self.busy:content+='<hr><h3>你</h3><p>'+esc(self.pending)+'</p><p style="color:#758397">正在查找与核对记录…</p>'
        self.answer.setHtml(content)
    def send(self,selected_task=None,selected_project=None):
        if not isinstance(selected_task,str):selected_task=None
        q=self.input.toPlainText().strip()
        if len(q)>2000:self.status.setText(t('问题过长，请缩短到 2000 字以内。'));return
        if not q or self.busy:return
        history=[]
        for r in self.messages:
            if r.get('error') or r.get('selectionMismatch') or r.get('selectionNeeded') or 'projectChoices' in r or r.get('projectInventory'):history=[]
            else:history.append(r)
        self.busy=True;self.send_button.setEnabled(False);self.chats.setEnabled(False);self.pending=q;self.status.setText(t('正在查找相关工作记录…'))
        self.source.setEnabled(False);self.period.setEnabled(False)
        source=self.source.currentData();days=self.period.currentData()
        project_id=self.project_scope.currentData();self.project_scope.setEnabled(False)
        if history and history[-1].get('projectScope')!=project_id:history=[]
        previous=local_query_context(self.messages,history,q,project_id)
        self.render()
        config={**self.collector.config.get('model',{}),'embedding':self.collector.config.get('embedding',{}),'responseLanguage':language()}
        def work():
            try:
                from .project_queries import local_project_query
                from .task_queries import local_task_query
                local_task=local_task_query(self.root,q,previous=previous,selected=selected_task)
                if local_task is not None:local_task['projectScope']=project_id;self.signals.ready.emit(local_task);return
                if not selected_task:
                    local=local_project_query(self.root,q,source=source,previous=previous,selected=selected_project)
                    if local is not None:local['projectScope']=project_id;self.signals.ready.emit(local);return
                result=ask(self.root,config,q,history,self.signals.progress.emit,source=source,days=days,selected_task=selected_task,project_id=project_id);result['projectScope']=project_id;self.signals.ready.emit(result)
            except Exception as exc:self.signals.failed.emit(str(exc)[:300])
        threading.Thread(target=work,daemon=True).start()
    def received(self,result):
        if result.get('question')!=self.pending:
            self.failed('返回的回答与本次问题不一致，请重新查询');return
        self.messages.append(result);self.finish('回答已完成 · 点击引用核对依据')
    def failed(self,error):self.messages.append({'question':self.pending,'error':error});self.finish('本次未能完成回答，可补充信息后重试')
    def finish(self,status):
        self.busy=False;self.source.setEnabled(True);self.period.setEnabled(True);self.project_scope.setEnabled(True);self.send_button.setEnabled(True);self.chats.setEnabled(True);self.status.setText(t(status))
        self.cache.execute('INSERT OR REPLACE INTO chats VALUES(?,?,?,?)',(self.chat_id,self.messages[0]['question'][:30],json.dumps(self.messages,ensure_ascii=False),__import__('time').time_ns()));self.cache.commit();self.refresh_chats();self.render()
        self.answer.scrollToAnchor('answer-'+str(len(self.messages)-1))
    def evidence(self,url):
        if url.toString().startswith('project:'):
            ident=url.toString()[len('project:'):];latest=self.messages[-1] if self.messages else {}
            offered=latest.get('projectChoices',latest.get('projectInventory',{}).get('projects',[]))
            if any(p['id']==ident for p in offered):
                name=next(p['name'] for p in offered if p['id']==ident);self.input.setPlainText(name+' 项目有多少任务，里面做了什么？');self.send(selected_project=ident)
            return
        if url.toString().startswith('project-task:'):
            ident=url.toString()[len('project-task:'):];latest=self.messages[-1] if self.messages else {}
            if ident in latest.get('projectDetails',{}).get('taskIds',[]):self.open_task_answer(ident)
            return
        if url.toString()=='projects:':self.open_projects();return
        if url.toString()=='associate:':self.correct_association();return
        if url.toString()=='sample:weather':self.prefill('之前 WorkBuddy 查上海天气遇到了什么问题，后来怎么解决的？');return
        if url.toString().startswith('choose:'):
            latest=self.messages[-1] if self.messages else {};identity=url.toString()[7:]
            if latest.get('selectionNeeded') and identity in {option['taskId'] for option in latest['options']}:
                self.input.setPlainText(latest['question']);self.send(selected_task=identity)
            return
        if url.toString().startswith('inspect-task:'):
            identity=url.toString()[len('inspect-task:'):];latest=self.messages[-1] if self.messages else {}
            if identity in latest.get('retrievedTaskIds',[]):
                self.input.setPlainText('这一次任务的要求、执行过程和结果是什么？');self.send(selected_task=identity)
            return
        parts=url.toString().split(':',2)
        if len(parts)!=3 or parts[0]!='proof' or not parts[1].isdigit():return
        _,n,ref=parts
        if int(n)>=len(self.messages):return
        r=self.messages[int(n)];f=next((x for x in r.get('packet',{}).get('fragments',[]) if x['evidenceId']==ref),None)
        if not f:
            self.status.setText(t('这条记录不在本次回答的引用摘录里；可在对应步骤查看原始参数与返回。'));return
        self.stop_playback()
        from .message_graph import bounded_event
        with connection(self.root/'collector.db') as db:e=bounded_event(db,f['eventId'])
        if not e:self.status.setText(t('这条原始记录暂不可用；回答引用的摘录仍保存在本次对话中。'));return
        esc=html.escape;loc=e.get('evidence',{})
        owner=next((x['title'] for x in r.get('retrieved',[]) if x.get('taskId')==f.get('taskId')),None)
        self.proof_heading.setText(t('原始依据'))
        self.proof.setHtml('<h3>'+esc(ref)+' · '+esc(t('原始证据'))+'</h3>'+('<p>'+esc(t('大记录仅显示已索引摘录，完整原文仍保留在本机。'))+'</p>' if e.get('_bodyTruncated') else '')+('<p>'+esc(t('所属任务：'))+esc(owner[:160])+'</p>' if owner else '')+'<p>'+esc(str(e.get('name') or e['kind']))+'</p><pre style="white-space:pre-wrap">'+esc(event_text(e)[:20000])+'</pre><hr><p>'+esc(str(loc.get('path','')))+ '</p><p>'+esc(t('字节'))+' '+str(loc.get('byteStart'))+'–'+str(loc.get('byteEnd'))+'</p>');self.show_evidence_panel()
    def closeEvent(self,event):
        if self.closed:event.accept();return
        self.closed=True
        self.knowledge_timer.stop()
        self.stop_playback()
        if self.collector.runtime:self.collector.runtime.stop.set()
        self.collector.hide_on_close=False;self.collector.close();self.cache.close();event.accept()
