"""Conversation-first knowledge assistant; evidence stays one click away."""
import html,json,sqlite3,threading,uuid
from pathlib import Path
from PySide6.QtCore import QObject,Signal,Qt,QUrl,QTimer
from PySide6.QtWidgets import QMainWindow,QWidget,QHBoxLayout,QVBoxLayout,QLabel,QPushButton,QPlainTextEdit,QComboBox,QTextBrowser,QListWidget,QSplitter,QDialog,QStackedWidget,QScrollArea
from .knowledge import ask,answer_mismatch
from .task_view import TaskView,task_title
from .supervision import event_text

class Signals(QObject):
    progress=Signal(str)
    ready=Signal(object)
    failed=Signal(str)

class ChatWindow(QMainWindow):
    def __init__(self,collector):
        super().__init__();self.collector=collector;self.root=collector.root;self.busy=False;self.messages=[];self.chat_id=None;collector.hide_on_close=True
        self.setWindowTitle('SessionLens · 工作记忆助手');self.resize(1360,1000)
        self.cache=sqlite3.connect(self.root/'conversations.db');self.cache.execute('CREATE TABLE IF NOT EXISTS chats(id TEXT PRIMARY KEY,title TEXT,content TEXT,updated INTEGER)');self.cache.commit()
        body=QWidget();self.setCentralWidget(body);outer=QHBoxLayout(body);outer.setContentsMargins(0,0,0,0)
        side=QWidget();side.setFixedWidth(220);side.setStyleSheet('QPushButton{background:transparent;color:#233247;border:0;text-align:left;} QPushButton:checked{background:#e9f0fb;color:#275eb2;}');v=QVBoxLayout(side);v.setContentsMargins(18,24,18,20)
        brand=QLabel('SessionLens');brand.setStyleSheet('font-size:22px;font-weight:600');v.addWidget(brand);v.addWidget(QLabel('工作记忆'));v.addSpacing(18)
        assistant=QPushButton('智能助手');assistant.setChecked(True);assistant.setCheckable(True);assistant.clicked.connect(self.raise_);v.addWidget(assistant)
        records=QPushButton('历史任务');records.clicked.connect(self.open_history);v.addWidget(records)
        collection=QPushButton('采集与同步');collection.clicked.connect(self.open_collection);v.addWidget(collection)
        new=QPushButton('＋ 新对话');new.clicked.connect(self.new_chat);v.addWidget(new);v.addSpacing(20);v.addWidget(QLabel('当前任务'));self.current_task=QLabel('还没有选中任务');self.current_task.setWordWrap(True);self.current_task.setStyleSheet('background:#edf4fb;color:#1769ef;padding:10px;border-radius:8px;');v.addWidget(self.current_task);v.addSpacing(18);v.addWidget(QLabel('最近对话'))
        self.chats=QListWidget();self.chats.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff);self.chats.currentRowChanged.connect(self.open_chat);v.addWidget(self.chats,1);outer.addWidget(side)
        main=QWidget();m=QVBoxLayout(main);m.setContentsMargins(28,26,28,20)
        head=QLabel('智能助手');head.setStyleSheet('font-size:20px;font-weight:600');m.addWidget(head)
        sub=QLabel('先回答你的问题，再展开相关思路、动作与结果。');sub.setWordWrap(True);m.addWidget(sub)
        scope=QHBoxLayout();scope.addWidget(QLabel('查询范围'));self.source=QComboBox();self.source.addItem('全部 Agent',None);self.source.addItem('WorkBuddy','workbuddy');self.source.addItem('Codex','codex');scope.addWidget(self.source)
        scope.addWidget(QLabel('时间'));self.period=QComboBox();self.period.addItem('全部历史',0);self.period.addItem('最近 7 天',7);self.period.addItem('最近 30 天',30);scope.addWidget(self.period);scope.addStretch();scope_container=QWidget(main);scope_container.setLayout(scope);scope_container.hide()
        self.composer=QWidget();self.composer.setObjectName('assistantComposer');self.composer.setStyleSheet('QWidget#assistantComposer{background:white;border:1px solid #E6E8EC;border-radius:12px;}')
        cv=QVBoxLayout(self.composer);cv.setContentsMargins(20,16,20,16);prompt=QLabel('你想了解什么？');prompt.setStyleSheet('font-size:17px;font-weight:600');prompt.hide()
        self.input=QPlainTextEdit();self.input.setFixedHeight(72);self.input.setStyleSheet('QPlainTextEdit{border:0;background:transparent;font-size:16px;padding:6px;}');self.input.setPlaceholderText('描述你想找的任务，或者直接问：当时为什么这样修改？用了什么工具？最后做成了吗？');cv.addWidget(self.input)
        row=QHBoxLayout();hint=QLabel('发送相关证据给已配置的模型分析');hint.setWordWrap(True);hint.setStyleSheet('color:#667589;font-size:12px');row.addWidget(hint,1);self.send_button=QPushButton('查询');self.send_button.setMinimumWidth(88);self.send_button.clicked.connect(self.send);row.insertWidget(0,self.send_button);cv.addLayout(row);m.addWidget(self.composer)
        self.status=QLabel('从你的任务记录中寻找答案');self.status.setWordWrap(True);m.addWidget(self.status)
        self.split=QSplitter(Qt.Horizontal);self.split.setChildrenCollapsible(False);self.answer=QTextBrowser();self.answer.setOpenLinks(False);self.answer.anchorClicked.connect(self.evidence);self.results=QStackedWidget();self.results.addWidget(self.answer);self.task_view=TaskView();self.task_view.contentChanged.connect(lambda:QTimer.singleShot(0,self.fit_result));self.task_view.evidenceRequested.connect(lambda ref:self.evidence(QUrl("proof:"+str(len(self.messages)-1)+":"+ref)));self.results.addWidget(self.task_view);self.split.addWidget(self.results)
        proof_panel=QWidget();pv=QVBoxLayout(proof_panel);pv.setContentsMargins(0,0,0,0);pr=QHBoxLayout();pr.addWidget(QLabel('原始依据'));pr.addStretch();close=QPushButton('收起');close.clicked.connect(proof_panel.hide);pr.addWidget(close);pv.addLayout(pr);self.proof=QTextBrowser();self.proof.setMinimumWidth(240);pv.addWidget(self.proof);self.proof_panel=proof_panel;self.split.addWidget(proof_panel);proof_panel.hide();m.addWidget(self.split,1)
        foot=QHBoxLayout();self.collection_status=QLabel('本地任务库 · 采集状态可查看');foot.addWidget(self.collection_status,1);view=QPushButton('查看采集进度');view.clicked.connect(self.open_collection);foot.addWidget(view);m.addLayout(foot);self.results.setSizePolicy(__import__('PySide6.QtWidgets',fromlist=['QSizePolicy']).QSizePolicy.Expanding,__import__('PySide6.QtWidgets',fromlist=['QSizePolicy']).QSizePolicy.Minimum);main_scroll=QScrollArea();main_scroll.setWidgetResizable(True);main_scroll.setFrameShape(QScrollArea.NoFrame);main_scroll.setWidget(main);outer.addWidget(main_scroll,1)
        self.signals=Signals(self);self.signals.progress.connect(self.status.setText);self.signals.ready.connect(self.received);self.signals.failed.connect(self.failed)
        self.refresh_chats();self.new_chat()
    def fit_result(self):
        if self.results.currentWidget()==self.task_view:self.split.setMinimumHeight(max(300,self.task_view.layout().totalHeightForWidth(max(720,self.results.width()))))
    def resizeEvent(self,event):
        super().resizeEvent(event)
        if hasattr(self,'task_view'):QTimer.singleShot(0,self.fit_result)
    def open_collector(self):self.collector.show();self.collector.raise_()
    def open_history(self):
        self.collector.history.click();self.open_collector()
    def open_collection(self):
        dialog=QDialog(self);dialog.setWindowTitle('采集与同步');dialog.resize(660,400);layout=QVBoxLayout(dialog)
        layout.addWidget(QLabel('当前机器上的日志 → 本地任务库 → 平台接收'))
        runtime=self.collector.runtime
        if runtime:
            with runtime.lock:status=dict(runtime.status)
            text='本地采集运行中\n\n'+str(status.get('task_index','历史整理中'))+'\n'+str(status.get('upload','仅本地保存'))
        else:text='采集尚未启动'
        info=QLabel(text);info.setWordWrap(True);layout.addWidget(info)
        layout.addWidget(QLabel('已整理内容：用户提问、回复、已记录的思路、工具调用与结果'))
        location=QLabel('原始记录保存在本机：'+str(self.root/'collector.db'));location.setWordWrap(True);layout.addWidget(location)
        row=QHBoxLayout();settings=QPushButton('上报与采集设置');settings.clicked.connect(lambda:(dialog.accept(),self.collector.configure()));row.addWidget(settings);done=QPushButton('关闭');done.clicked.connect(dialog.accept);row.addWidget(done);layout.addLayout(row);dialog.exec()
    def prefill(self,question):self.input.setPlainText(question);self.input.setFocus()
    def refresh_chats(self):
        self.chat_rows=self.cache.execute('SELECT id,title FROM chats ORDER BY updated DESC').fetchall();self.chats.blockSignals(True);self.chats.clear();self.chats.addItems([r[1] for r in self.chat_rows]);self.chats.blockSignals(False)
    def new_chat(self):
        if self.busy:return
        self.chat_id=uuid.uuid4().hex;self.messages=[];self.current_task.setText('还没有选中任务');self.input.clear();self.proof_panel.hide();self.send_button.setText('查询');self.render()
    def open_chat(self,index):
        if self.busy or index<0:return
        self.chat_id=self.chat_rows[index][0];self.messages=json.loads(self.cache.execute('SELECT content FROM chats WHERE id=?',(self.chat_id,)).fetchone()[0])
        with sqlite3.connect(self.root/'collector.db',timeout=10) as db:
            for result in self.messages:
                mismatch=answer_mismatch(db,result)
                if mismatch:result['selectionMismatch']=mismatch
        self.input.setPlainText(self.messages[-1]['question'] if self.messages else '');self.proof_panel.hide();self.render()
    def render(self):
        if self.busy:
            self.task_view.stop();self.split.setMinimumHeight(150);self.results.setCurrentWidget(self.answer);self.current_task.setText('正在查找相关任务');self.proof_panel.hide()
            self.answer.setHtml('<h3>正在查找这次问题的相关记录</h3><p>'+html.escape(self.pending)+'</p>');self.send_button.setText('查询中…');return
        latest=self.messages[-1] if self.messages else {}
        if latest.get('selectionMismatch'):
            self.task_view.stop();self.split.setMinimumHeight(150);self.results.setCurrentWidget(self.answer);self.current_task.setText('任务需要重新核对');self.proof_panel.hide();self.send_button.setText('重新查询')
            self.status.setText('此前的回答没有对应到你问的任务')
            self.answer.setHtml('<h3>'+html.escape(latest['selectionMismatch'])+'</h3><p>问题已保留在上方。点击“重新查询”，根据这次问题重新寻找任务和证据。</p>');return
        self.send_button.setText('查询')
        if latest.get('selectionNeeded'):
            self.task_view.stop();self.split.setMinimumHeight(300);self.results.setCurrentWidget(self.answer);self.current_task.setText('选择要查看的任务');self.status.setText('找到多个相近任务，请选择具体的一次')
            content='<h3>你想了解哪一次任务？</h3><p>'+html.escape(latest['question'])+'</p>'
            for option in latest['options']:
                date=option['updated'][:16].replace('T',' ')
                name={'workbuddy':'WorkBuddy','codex':'Codex'}.get(option['source'],option['source'])
                content+='<p><a href="choose:'+html.escape(option['taskId'])+'"><b>'+html.escape(name+' · '+date)+'</b><br>'+html.escape(option['title'][:100])+'</a></p>'
            self.answer.setHtml(content);return
        if self.messages and self.messages[-1].get('presentation') and not self.busy:
            self.current_task.setText(task_title(self.messages[-1]['presentation']['prompt']));self.task_view.load(self.messages[-1]);self.results.setCurrentWidget(self.task_view);QTimer.singleShot(0,self.fit_result);return
        self.task_view.stop();self.split.setMinimumHeight(150);self.results.setCurrentWidget(self.answer)
        esc=html.escape
        content='<style>body{color:#233247}p{line-height:150%}a{color:#3265a8;text-decoration:none}h3{font-size:16px}</style>'
        if not self.messages and not self.busy:content+='<h3>答案将在这里展开</h3><p style="color:#758397">相关任务、处理经过与原始依据，随提问显示。</p><p><a href="sample:weather">查上海天气时遇到了什么问题？</a></p>'
        # Show only the latest question here. A failed/new query must not leave
        # the previous task at the top of the visible answer panel.
        for n,r in list(enumerate(self.messages))[-1:]:
            content+='<a name="answer-'+str(n)+'"></a><hr><h3>你</h3><p>'+esc(r['question'])+'</p><h3>SessionLens</h3>'
            if r.get('error'):content+='<p>'+esc(r['error'])+'</p>';continue
            value=r['understanding']
            for block in [value['overview']]+value['steps']:
                if block.get('title'):content+='<h3>'+esc(block['title'])+'</h3>'
                content+='<p>'+esc(({'inferred':'推断：','unknown':'记录未能确认：'}.get(block['basis'],''))+block['text'])+'</p>'
                content+='<p>'+ '　'.join(f'<a href="proof:{n}:{ref}">{ref} 查看依据</a>' for ref in block['evidenceRefs'])+'</p>'
            for gap in value.get('gaps',[]):content+='<p style="color:#88663b">'+esc(str(gap))+'</p>'
            content+='<p style="color:#758397">'+esc(' · '.join(x['source']+' / '+x['title'] for x in r.get('retrieved',[])))+'</p>'
            packet=r.get('packet',{});fragments=packet.get('fragments',[])
            content+='<p style="color:#758397">本次读取 '+str(len(fragments))+' / '+str(packet.get('totalRecords',len(fragments)))+' 条记录'+('，部分正文已截取' if any(f.get('truncated') for f in fragments) else '')+'；引用可核对原文。</p>'
            content+='<p style="color:#758397">已参考 '+str(len(r.get('retrievedTaskIds',[])))+' 个任务 · 当前已整理知识库中的相关证据</p>'
        if self.busy:content+='<hr><h3>你</h3><p>'+esc(self.pending)+'</p><p style="color:#758397">正在查找与核对记录…</p>'
        self.answer.setHtml(content)
    def send(self,selected_task=None):
        if not isinstance(selected_task,str):selected_task=None
        q=self.input.toPlainText().strip()
        if len(q)>2000:self.status.setText('问题过长，请缩短到 2000 字以内。');return
        if not q or self.busy:return
        self.busy=True;self.send_button.setEnabled(False);self.chats.setEnabled(False);self.pending=q;history=[r for r in self.messages if not r.get('error') and not r.get('selectionMismatch') and not r.get('selectionNeeded')];self.status.setText('正在查找相关工作记录…')
        self.source.setEnabled(False);self.period.setEnabled(False)
        source=self.source.currentData();days=self.period.currentData()
        self.render()
        def work():
            try:self.signals.ready.emit(ask(self.root,self.collector.config.get('model',{}),q,history,self.signals.progress.emit,source=source,days=days,selected_task=selected_task))
            except Exception as exc:self.signals.failed.emit(str(exc)[:300])
        threading.Thread(target=work,daemon=True).start()
    def received(self,result):
        if result.get('question')!=self.pending:
            self.failed('返回的回答与本次问题不一致，请重新查询');return
        self.messages.append(result);self.finish('回答已完成 · 点击引用核对依据')
    def failed(self,error):self.messages.append({'question':self.pending,'error':error});self.finish('本次未能完成回答，可补充信息后重试')
    def finish(self,status):
        self.busy=False;self.source.setEnabled(True);self.period.setEnabled(True);self.send_button.setEnabled(True);self.chats.setEnabled(True);self.status.setText(status)
        self.cache.execute('INSERT OR REPLACE INTO chats VALUES(?,?,?,?)',(self.chat_id,self.messages[0]['question'][:30],json.dumps(self.messages,ensure_ascii=False),__import__('time').time_ns()));self.cache.commit();self.refresh_chats();self.render()
        self.answer.scrollToAnchor('answer-'+str(len(self.messages)-1))
    def evidence(self,url):
        if url.toString()=='sample:weather':self.prefill('之前 WorkBuddy 查上海天气遇到了什么问题，后来怎么解决的？');return
        if url.toString().startswith('choose:'):
            latest=self.messages[-1] if self.messages else {};identity=url.toString()[7:]
            if latest.get('selectionNeeded') and identity in {option['taskId'] for option in latest['options']}:
                self.input.setPlainText(latest['question']);self.send(selected_task=identity)
            return
        _,n,ref=url.toString().split(':');r=self.messages[int(n)];f=next(x for x in r['packet']['fragments'] if x['evidenceId']==ref)
        with sqlite3.connect(self.root/'collector.db') as db:raw=db.execute('SELECT event FROM events WHERE id=?',(f['eventId'],)).fetchone()
        if not raw:self.status.setText('这条原始记录暂不可用；回答引用的摘录仍保存在本次对话中。');return
        e=json.loads(raw[0]);esc=html.escape;loc=e.get('evidence',{})
        self.proof.setHtml('<h3>'+esc(ref)+' · 原始证据</h3><p>'+esc(str(e.get('name') or e['kind']))+'</p><pre style="white-space:pre-wrap">'+esc(event_text(e)[:20000])+'</pre><hr><p>'+esc(str(loc.get('path','')))+ '</p><p>字节 '+str(loc.get('byteStart'))+'–'+str(loc.get('byteEnd'))+'</p>');self.proof_panel.show();self.split.setSizes([650,350])
    def closeEvent(self,event):
        self.task_view.timer.stop()
        if self.collector.runtime:self.collector.runtime.stop.set()
        self.collector.hide_on_close=False;self.collector.close();self.cache.close();event.accept()
